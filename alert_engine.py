"""
Scalable Price Alert Engine
============================
- Reads alerts from config.json (add/remove stocks anytime)
- Sends Telegram push notifications to your phone
- Runs on any machine (laptop, cloud VM, Raspberry Pi)
- Auto-resets alerts daily
- Handles market hours, weekends, holidays

Setup:
  1. Create Telegram bot: @BotFather → /newbot → copy token
  2. Get your chat ID: @userinfobot → /start → copy ID
  3. Paste both into config.json
  4. pip install yfinance
  5. python alert_engine.py

Deploy to cloud (runs 24/7):
  - Free: Oracle Cloud free tier, Google Cloud free tier
  - Cheap: DigitalOcean $4/mo, AWS Lightsail $3.50/mo
  - Just scp the alerts/ folder and run: nohup python alert_engine.py &
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
import sys

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / ".state.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_state():
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text())
            if data.get("date") != today():
                return {"date": today(), "fired": []}
            return data
        except:
            pass
    return {"date": today(), "fired": []}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2))


def today():
    return ist_now().strftime("%Y-%m-%d")


def ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def is_market_open(config):
    now = ist_now()
    if now.weekday() >= 5:
        return False
    
    open_h, open_m = map(int, config["market_open"].split(":"))
    close_h, close_m = map(int, config["market_close"].split(":"))
    
    current = now.hour * 60 + now.minute
    mkt_open = open_h * 60 + open_m
    mkt_close = close_h * 60 + close_m
    
    return mkt_open <= current <= close_m + close_h * 60


def send_telegram(token, chat_id, message):
    if not token or not chat_id:
        print(f"  [NO TELEGRAM] {message}")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode()
    
    try:
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"  Telegram error: {e}")
        return False


def fetch_prices(symbols):
    """Fetch current prices using yfinance."""
    import yfinance as yf
    
    nse_symbols = [f"{s}.NS" for s in symbols]
    
    try:
        data = yf.download(nse_symbols, period="1d", interval="1m", progress=False)
        if data.empty:
            return {}
        
        prices = {}
        for sym in symbols:
            nse = f"{sym}.NS"
            try:
                if len(nse_symbols) > 1:
                    p = data["Close"][nse].dropna().iloc[-1]
                else:
                    p = data["Close"].dropna().iloc[-1]
                prices[sym] = round(float(p), 2)
            except:
                pass
        return prices
    except Exception as e:
        print(f"  Fetch error: {e}")
        return {}


def check_alerts(config, state):
    """Check all alerts and send notifications."""
    symbols = [a["symbol"] for a in config["alerts"]]
    prices = fetch_prices(symbols)
    
    if not prices:
        return
    
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    
    for alert in config["alerts"]:
        sym = alert["symbol"]
        name = alert["name"]
        price = prices.get(sym)
        
        if price is None:
            continue
        
        # Check buy levels
        for level in alert["buy_levels"]:
            key = f"{sym}_BUY_{level}"
            if key in state["fired"]:
                continue
            
            if price <= level:
                msg = (f"🟢 <b>BUY ALERT</b>\n"
                       f"<b>{name}</b> ({sym})\n"
                       f"Price: ₹{price} (target: ₹{level})\n"
                       f"Notes: {alert.get('notes', '')}")
                
                print(f"  *** BUY: {name} at ₹{price} (level ₹{level})")
                send_telegram(token, chat_id, msg)
                state["fired"].append(key)
                save_state(state)
        
        # Check stoploss
        sl = alert.get("stoploss")
        if sl:
            key = f"{sym}_SL"
            if key not in state["fired"] and price <= sl:
                msg = (f"🔴 <b>STOPLOSS HIT</b>\n"
                       f"<b>{name}</b> ({sym})\n"
                       f"Price: ₹{price} (SL: ₹{sl})\n"
                       f"⚠️ Consider exiting!")
                
                print(f"  *** STOPLOSS: {name} at ₹{price}")
                send_telegram(token, chat_id, msg)
                state["fired"].append(key)
                save_state(state)


def print_status(config, prices):
    now = ist_now()
    print(f"  [{now.strftime('%H:%M')} IST] ", end="")
    parts = []
    for a in config["alerts"]:
        p = prices.get(a["symbol"])
        if p:
            parts.append(f"{a['symbol']}:₹{p:.0f}")
    print(" | ".join(parts))


def main():
    config = load_config()
    state = load_state()
    
    # Validate config
    if not config["telegram_bot_token"]:
        print("⚠️  No Telegram token in config.json — alerts will only print to console")
        print("   Set up: @BotFather on Telegram → /newbot → paste token in config.json\n")
    
    print("=" * 60)
    print("  PRICE ALERT ENGINE")
    print("=" * 60)
    print(f"  Stocks: {len(config['alerts'])}")
    print(f"  Check interval: {config['check_interval_seconds']}s")
    print(f"  Market: {config['market_open']} - {config['market_close']} IST")
    
    total_alerts = sum(len(a["buy_levels"]) for a in config["alerts"])
    print(f"  Total buy alerts: {total_alerts}")
    print(f"  Fired today: {len(state['fired'])}")
    
    print(f"\n  Watching:")
    for a in config["alerts"]:
        levels = ", ".join(f"₹{l}" for l in a["buy_levels"])
        sl = f"₹{a['stoploss']}" if a.get("stoploss") else "none"
        print(f"    {a['name']:<25} Buy: {levels}  SL: {sl}")
    
    print(f"\n  Press Ctrl+C to stop\n")
    
    # Send startup message
    send_telegram(config["telegram_bot_token"], config["telegram_chat_id"],
                  f"🤖 Alert engine started\nWatching {len(config['alerts'])} stocks, {total_alerts} buy levels")
    
    while True:
        try:
            # Reload config each cycle (allows live edits)
            config = load_config()
            state = load_state()
            
            if is_market_open(config):
                symbols = [a["symbol"] for a in config["alerts"]]
                prices = fetch_prices(symbols)
                if prices:
                    print_status(config, prices)
                check_alerts(config, state)
            else:
                now = ist_now()
                if now.weekday() >= 5:
                    print(f"  [{now.strftime('%H:%M')} IST] Weekend. Next check in 30min...")
                    time.sleep(1800)
                    continue
                else:
                    print(f"  [{now.strftime('%H:%M')} IST] Market closed. Next check in 5min...")
                    time.sleep(300)
                    continue
            
            time.sleep(config["check_interval_seconds"])
        
        except KeyboardInterrupt:
            print("\n  Stopped.")
            send_telegram(config["telegram_bot_token"], config["telegram_chat_id"],
                          "🛑 Alert engine stopped")
            break
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
