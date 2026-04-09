"""
Single-run price checker for PythonAnywhere / cron jobs.
Checks prices ONCE and sends alerts if levels are hit, then exits.

Schedule this to run every 5 minutes during market hours:
  - PythonAnywhere: Use "Tasks" tab (free = 1 daily task)
  - Cron: */5 9-15 * * 1-5 python3 /path/to/alert_check.py

For PythonAnywhere free tier (1 task/day), use the always_on version instead.
For any VPS with cron, this is the cleanest approach.
"""

import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / ".state.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_state():
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text())
            today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
            if data.get("date") != today:
                return {"date": today, "fired": []}
            return data
        except:
            pass
    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    return {"date": today, "fired": []}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2))


def send_telegram(token, chat_id, message):
    if not token or not chat_id:
        print(f"  [NO TG] {message}")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  TG error: {e}")


def fetch_prices(symbols):
    import yfinance as yf
    nse = [f"{s}.NS" for s in symbols]
    try:
        data = yf.download(nse, period="1d", interval="1m", progress=False)
        if data.empty:
            # Fallback: try daily data
            data = yf.download(nse, period="5d", progress=False)
        if data.empty:
            return {}
        prices = {}
        for sym in symbols:
            try:
                if len(nse) > 1:
                    p = data["Close"][f"{sym}.NS"].dropna().iloc[-1]
                else:
                    p = data["Close"].dropna().iloc[-1]
                prices[sym] = round(float(p), 2)
            except:
                pass
        return prices
    except Exception as e:
        print(f"  Fetch error: {e}")
        return {}


def main():
    # Check if market hours
    ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    if ist.weekday() >= 5:
        print(f"Weekend ({ist.strftime('%A')}). Skipping.")
        return
    
    hour_min = ist.hour * 60 + ist.minute
    if hour_min < 9 * 60 + 15 or hour_min > 15 * 60 + 30:
        print(f"Market closed ({ist.strftime('%H:%M')} IST). Skipping.")
        return
    
    config = load_config()
    state = load_state()
    
    symbols = [a["symbol"] for a in config["alerts"]]
    prices = fetch_prices(symbols)
    
    if not prices:
        print("No prices fetched.")
        return
    
    print(f"[{ist.strftime('%H:%M')} IST] Prices: {prices}")
    
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    
    for alert in config["alerts"]:
        sym = alert["symbol"]
        name = alert["name"]
        price = prices.get(sym)
        if not price:
            continue
        
        for level in alert["buy_levels"]:
            key = f"{sym}_BUY_{level}"
            if key not in state["fired"] and price <= level:
                msg = (f"🟢 <b>BUY ALERT</b>\n"
                       f"<b>{name}</b> ({sym})\n"
                       f"Price: ₹{price} (target: ₹{level})\n"
                       f"{alert.get('notes', '')}")
                print(f"  *** BUY: {name} ₹{price}")
                send_telegram(token, chat_id, msg)
                state["fired"].append(key)
        
        sl = alert.get("stoploss")
        if sl:
            key = f"{sym}_SL"
            if key not in state["fired"] and price <= sl:
                msg = (f"🔴 <b>STOPLOSS</b>\n"
                       f"<b>{name}</b> ({sym})\n"
                       f"Price: ₹{price} (SL: ₹{sl})")
                print(f"  *** SL: {name} ₹{price}")
                send_telegram(token, chat_id, msg)
                state["fired"].append(key)
    
    save_state(state)
    print("Done.")


if __name__ == "__main__":
    main()
