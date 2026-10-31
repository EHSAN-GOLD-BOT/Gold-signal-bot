import yfinance as yf
import pandas as pd
import numpy as np
import requests
import threading
import time
import os
from datetime import datetime, time as dtime
from flask import Flask

# ================= تنظیمات =================
# ⚠️ این دو خط رو حتماً با اطلاعات خودت عوض کن ⚠️
TELEGRAM_TOKEN = "8624639977:AAFe_wnPBkuorYHnzoYB05s8qw7WVAtTSZM"
TELEGRAM_CHAT_ID = "7290899219"
SYMBOL = "GC=F"         
INTERVAL = "15m"        
PERIOD = "5d"           
# ==========================================

app = Flask(__name__)

@app.route('/')
def health_check():
    return "SMC Bot is running 24/7!", 200

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"خطا در تلگرام: {e}")

def get_data():
    df = yf.download(SYMBOL, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

# ================= توابع تحلیل ۸ گانه =================

def find_pivots(df, window=3):
    highs, lows = [], []
    for i in range(window, len(df)-window):
        if float(df['High'].iloc[i]) == float(df['High'].iloc[i-window:i+window+1].max()):
            highs.append((i, float(df['High'].iloc[i])))
        if float(df['Low'].iloc[i]) == float(df['Low'].iloc[i-window:i+window+1].min()):
            lows.append((i, float(df['Low'].iloc[i])))
    return highs, lows

def check_fvg(df):
    for i in range(len(df)-5, len(df)-2):
        h2, l2 = float(df['High'].iloc[i-2]), float(df['Low'].iloc[i-2])
        h0, l0 = float(df['High'].iloc[i]), float(df['Low'].iloc[i])
        if l0 > h2: return "BULL"
        if h0 < l2: return "BEAR"
    return None

def check_engulfing(df):
    o1, c1 = float(df['Open'].iloc[-2]), float(df['Close'].iloc[-2])
    o2, c2 = float(df['Open'].iloc[-1]), float(df['Close'].iloc[-1])
    if c1 < o1 and c2 > o2 and c2 >= o1 and o2 <= c1: return "BULL"
    if c1 > o1 and c2 < o2 and c2 <= o1 and o2 >= c1: return "BEAR"
    return None

def check_structure(df):
    highs, lows = find_pivots(df)
    last_price = float(df['Close'].iloc[-1])
    signal = None
    
    if len(highs) >= 2 and len(lows) >= 2:
        last_high = highs[-1][1]
        last_low = lows[-1][1]

        if last_price > last_high: signal = "BULL"
        elif last_price < last_low: signal = "BEAR"
    return signal

def check_liquidity(df):
    highs, lows = find_pivots(df)
    last_price = float(df['Close'].iloc[-1])
    
    if len(highs) >= 2:
        if abs(highs[-1][1] - highs[-2][1]) / highs[-1][1] < 0.001:
            if last_price < highs[-1][1]: return "BEAR"
            
    if len(lows) >= 2:
        if abs(lows[-1][1] - lows[-2][1]) / lows[-1][1] < 0.001:
            if last_price > lows[-1][1]: return "BULL"
    return None

def check_smart_money(df):
    last_price = float(df['Close'].iloc[-1])
    for i in range(len(df)-5, len(df)-1):
        if float(df['Close'].iloc[i]) < float(df['Open'].iloc[i]):
            if last_price > float(df['High'].iloc[i]): return "BULL"
        if float(df['Close'].iloc[i]) > float(df['Open'].iloc[i]):
            if last_price < float(df['Low'].iloc[i]): return "BEAR"
    return None

def check_orb(df):
    df_ = df.copy()
    df_['_date'] = df_.index.date
    last_date = df_.index[-1].date()
    group = df_[df_['_date'] == last_date]
    
    start_dt = pd.Timestamp.combine(last_date, dtime(13, 30)).tz_localize('UTC')
    end_dt = start_dt + pd.Timedelta(minutes=30)
    
    or_win = group[(group.index >= start_dt) & (group.index < end_dt)]
    if len(or_win) > 0:
        or_high = float(or_win['High'].max())
        or_low = float(or_win['Low'].min())
        last_price = float(df['Close'].iloc[-1])
        if last_price > or_high: return "BULL"
        if last_price < or_low: return "BEAR"
    return None

# ================= موتور اصلی سیگنال =================

def analyze_market(df):
    signals = {
        "FVG": check_fvg(df),
        "Engulfing": check_engulfing(df),
        "BOS/CHoCH/MSS": check_structure(df),
        "Liquidity": check_liquidity(df),
        "Smart Money": check_smart_money(df),
        "ORB": check_orb(df)
    }

    bull_score = sum(1 for v in signals.values() if v == "BULL")
    bear_score = sum(1 for v in signals.values() if v == "BEAR")

    current_price = float(df['Close'].iloc[-1])
    recent_high = float(df['High'].tail(10).max())
    recent_low = float(df['Low'].tail(10).min())

    if bull_score >= 3:
        entry = current_price
        sl = recent_low - (recent_low * 0.001)
        risk = entry - sl
        tp = entry + (risk * 2)
        active = [k for k, v in signals.items() if v == "BULL"]
        return [f"🟢 <b>سیگنال خرید (LONG)</b>\n\n💰 ورود: {round(entry, 2)}\n🛑 حد ضرر: {round(sl, 2)}\n🎯 حد سود: {round(tp, 2)}\n\n🧠 <b>دلایل هم‌گرایی:</b>\n- " + "\n- ".join(active)]

    elif bear_score >= 3:
        entry = current_price
        sl = recent_high + (recent_high * 0.001)
        risk = sl - entry
        tp = entry - (risk * 2)
        active = [k for k, v in signals.items() if v == "BEAR"]
        return [f"🔴 <b>سیگنال فروش (SHORT)</b>\n\n💰 ورود: {round(entry, 2)}\n🛑 حد ضرر: {round(sl, 2)}\n🎯 حد سود: {round(tp, 2)}\n\n🧠 <b>دلایل هم‌گرایی:</b>\n- " + "\n- ".join(active)]

    return []

# ================= حلقه اجرای ربات =================

def bot_loop():
    last_signal = None
    send_telegram("🤖 <b>ربات هوشمند SMC روشن شد!</b>\nاز این به بعد ۲۴ ساعته با ۸ استراتژی بازار رو رصد می‌کنم.")
    
    while True:
        try:
            df = get_data()
            if not df.empty:
                signals = analyze_market(df)
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                
                if signals:
                    current_sig = signals[0]
                    if current_sig != last_signal:
                        send_telegram(f"⏰ زمان: {now}\n\n{current_sig}")
                        last_signal = current_sig
            time.sleep(900)
        except Exception as e:
            print(f"خطا: {e}")
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
