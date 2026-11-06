import yfinance as yf
import pandas as pd
import numpy as np
import requests
import threading
import time
import os
from datetime import datetime
from flask import Flask

# ================= تنظیمات =================
TELEGRAM_TOKEN = "9278328589:AAHq-afLrjBWgtS01gK597LeTNiAvV4gXyo"
TELEGRAM_CHAT_ID = "9242746725"
SYMBOL = "GC=F"         
# ==========================================

app = Flask(__name__)

@app.route('/')
def health_check():
    return "SMC Gold Bot is running 24/7!", 200

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error in Telegram: {e}")

def get_data():
    # گرفتن دیتای 5 دقیقه‌ای برای 2 روز اخیر (برای سرعت بالا)
    df = yf.download(SYMBOL, period="2d", interval="5m", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    else:
        df.index = df.index.tz_convert('UTC')
    return df

def resample_data(data, timeframe):
    return data.resample(timeframe, label="right", closed="right").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last"
    }).dropna()

def add_indicators(data):
    data = data.copy()
    data["EMA50"] = data["Close"].ewm(span=50, adjust=False).mean()
    data["EMA200"] = data["Close"].ewm(span=200, adjust=False).mean()
    delta = data["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs = gain.ewm(alpha=1/14, adjust=False).mean() / loss.ewm(alpha=1/14, adjust=False).mean().replace(0, np.nan)
    data["RSI"] = 100 - (100 / (1 + rs))
    prev = data["Close"].shift(1)
    tr = pd.concat([data["High"] - data["Low"], abs(data["High"] - prev), abs(data["Low"] - prev)], axis=1).max(axis=1)
    data["ATR"] = tr.ewm(alpha=1/14, adjust=False).mean()
    return data

def add_patterns(data):
    data = data.copy()
    data['SwingHigh'] = data["High"].shift(1).rolling(5).max()
    data['SwingLow'] = data["Low"].shift(1).rolling(5).min()
    data['BOS_UP'] = (data["Close"] > data.SwingHigh) & (data["Close"].shift(1) <= data.SwingHigh.shift(1))
    data['BOS_DOWN'] = (data["Close"] < data.SwingLow) & (data["Close"].shift(1) >= data.SwingLow.shift(1))
    data['FVG_UP'] = data["Low"] > data["High"].shift(2)
    data['FVG_DOWN'] = data["High"] < data["Low"].shift(2)
    data['Sweep_Low'] = (data["Low"] < data.SwingLow) & (data["Close"] > data.SwingLow)
    data['Sweep_High'] = (data["High"] > data.SwingHigh) & (data["Close"] < data.SwingHigh)
    data['OB_UP'] = (data["Close"].shift(1) < data["Open"].shift(1)) & data.BOS_UP
    data['OB_DOWN'] = (data["Close"].shift(1) > data["Open"].shift(1)) & data.BOS_DOWN
    body = abs(data["Close"] - data["Open"])
    total_range = data["High"] - data["Low"]
    upper_wick = data["High"] - data[["Open", "Close"]].max(axis=1)
    lower_wick = data[["Open", "Close"]].min(axis=1) - data["Low"]
    data['Pin_Up'] = (lower_wick > 2 * body) & (upper_wick < 0.2 * total_range) & (data["Close"] > data["Open"])
    data['Pin_Down'] = (upper_wick > 2 * body) & (lower_wick < 0.2 * total_range) & (data["Close"] < data["Open"])
    data['Engulf_Up'] = (data["Close"] > data["Open"]) & (data["Close"].shift(1) < data["Open"].shift(1)) & (data["Close"] >= data["Open"].shift(1)) & (data["Open"] <= data["Close"].shift(1))
    data['Engulf_Down'] = (data["Close"] < data["Open"]) & (data["Close"].shift(1) > data["Open"].shift(1)) & (data["Close"] <= data["Open"].shift(1)) & (data["Open"] >= data["Close"].shift(1))
    return data

def check_live_signal():
    try:
        df = get_data()
        if len(df) < 200:
            return None

        m5 = df.copy()
        m15 = resample_data(df, "15min")
        h1 = resample_data(df, "1h")

        m5 = add_patterns(add_indicators(m5))
        m15 = add_patterns(add_indicators(m15))
        h1 = add_patterns(add_indicators(h1))

        # گرفتن آخرین کندل بسته شده (نه کندل در حال تشکیل)
        idx_m5 = len(m5) - 1
        idx_m15 = len(m15) - 1
        idx_h1 = len(h1) - 1

        # H1 Trend
        h1_bull = h1['EMA50'].iloc[idx_h1] > h1['EMA200'].iloc[idx_h1]
        h1_bear = h1['EMA50'].iloc[idx_h1] < h1['EMA200'].iloc[idx_h1]

        # M15 Filter
        m15_rsi = m15['RSI'].iloc[idx_m15]
        m15_bull_ok = m15_rsi > 45
        m15_bear_ok = m15_rsi < 55

        # M5 Triggers
        row = m5.iloc[idx_m5]
        bull_triggers = sum([row['FVG_UP'], row['Sweep_Low'], row['OB_UP'], row['Pin_Up'], row['Engulf_Up']])
        bear_triggers = sum([row['FVG_DOWN'], row['Sweep_High'], row['OB_DOWN'], row['Pin_Down'], row['Engulf_Down']])

        atr = row['ATR']
        entry = row['Close']
        sl_dist = atr * 1.5

        if h1_bull and m15_bull_ok and bull_triggers >= 1:
            sl = entry - sl_dist
            tp1 = entry + sl_dist
            tp2 = entry + (sl_dist * 2)
            return f"🟢 <b>سیگنال خرید (LONG)</b>\n\n💰 ورود: {round(entry, 2)}\n🛑 حد ضرر: {round(sl, 2)}\n🎯 حد سود 1: {round(tp1, 2)}\n🎯 حد سود 2: {round(tp2, 2)}\n\n🧠 دلایل: H1 Trend + M15 RSI + {bull_triggers} الگوی M5"

        elif h1_bear and m15_bear_ok and bear_triggers >= 1:
            sl = entry + sl_dist
            tp1 = entry - sl_dist
            tp2 = entry - (sl_dist * 2)
            return f"🔴 <b>سیگنال فروش (SHORT)</b>\n\n💰 ورود: {round(entry, 2)}\n🛑 حد ضرر: {round(sl, 2)}\n🎯 حد سود 1: {round(tp1, 2)}\n🎯 حد سود 2: {round(tp2, 2)}\n\n🧠 دلایل: H1 Trend + M15 RSI + {bear_triggers} الگوی M5"

        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

def bot_loop():
    last_signal_time = None
    last_signal_text = ""
    
    send_telegram("🤖 <b>ربات هوشمند جدید روشن شد!</b>\nاز این به بعد با استراتژی ترکیبی H1/M15/M5 بازار رو رصد می‌کنم.")
    
    while True:
        try:
            signal_msg = check_live_signal()
            now = datetime.utcnow()
            
            # اگر سیگنال پیدا شد
            if signal_msg:
                # کول داون 1 ساعته (اگر 1 ساعت از سیگنال قبلی گذشته باشد و متن سیگنال تکراری نباشد)
                if last_signal_time is None or (now - last_signal_time).total_seconds() > 3600:
                    send_telegram(f"⏰ زمان: {now.strftime('%Y-%m-%d %H:%M')} (UTC)\n\n{signal_msg}")
                    last_signal_time = now
            
            time.sleep(300) # هر 5 دقیقه چک می‌کند
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
