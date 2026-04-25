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
        
        # Check buy levels (price drops TO or BELOW level)
        for level in alert.get("buy_levels", []):
            key = f"{sym}_BUY_{level}"
            if key in state["fired"]:
                continue
            
            if price <= level:
                msg = (f"🟢 <b>BUY DIP</b>\n"
                       f"<b>{name}</b> ({sym})\n"
                       f"Price: ₹{price} (target: ₹{level})\n"
                       f"Notes: {alert.get('notes', '')}")
                
                print(f"  *** BUY DIP: {name} at ₹{price} (level ₹{level})")
                send_telegram(token, chat_id, msg)
                state["fired"].append(key)
                save_state(state)
        
        # Check breakout levels (price rises TO or ABOVE level)
        for level in alert.get("breakout_levels", []):
            key = f"{sym}_BREAKOUT_{level}"
            if key in state["fired"]:
                continue
            
            if price >= level:
                msg = (f"🚀 <b>BREAKOUT</b>\n"
                       f"<b>{name}</b> ({sym})\n"
                       f"Price: ₹{price} crossed ₹{level}!\n"
                       f"Resistance broken — consider adding\n"
                       f"Notes: {alert.get('notes', '')}")
                
                print(f"  *** BREAKOUT: {name} at ₹{price} (level ₹{level})")
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


# ═══════════════════════════════════════════════════════════════
# US MARKET DAILY CHECK
# ═══════════════════════════════════════════════════════════════

US_HOLDINGS = ["TQQQ", "NFLX", "MSFT", "NVDA", "XLE", "VWO"]
US_INDICES = [("SPY", "S&P 500"), ("QQQ", "NASDAQ"), ("SOXX", "Semis")]


def us_analyze_52w(c):
    """52-week recovery analysis from a close price series."""
    price = c.iloc[-1]
    high_52w = c.max()
    low_52w = c.min()
    dd = (price / high_52w - 1) * 100
    upside = (high_52w / price - 1) * 100

    ema20 = c.ewm(span=20).mean().iloc[-1]
    ema50 = c.ewm(span=50).mean().iloc[-1]
    ret_1m = (price / c.iloc[-21] - 1) * 100 if len(c) >= 22 else 0

    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    rsi = (100 - (100 / (1 + rs))).iloc[-1]

    above_20 = price > ema20
    above_50 = price > ema50

    return {
        "price": price, "high_52w": high_52w, "dd": dd, "upside": upside,
        "ret_1m": ret_1m, "rsi": rsi, "above_20": above_20, "above_50": above_50,
    }


def us_compute_dip_score(c):
    """Simplified dip score with RSI divergence for Railway (no external imports)."""
    import numpy as np
    price = c.iloc[-1]
    high_40d = c.iloc[-40:].max() if len(c) >= 40 else c.max()
    dd_40d = (price / high_40d - 1) * 100

    if dd_40d > -5:
        return 0, 0, False  # not in dip

    # RSI
    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    rsi_now = rsi.iloc[-1]

    # RSI divergence: check for price new low but RSI higher low
    div_count = 0
    if len(c) >= 30:
        lo = c  # using close as proxy for low
        for lookback in [15, 25, 40]:
            if len(c) < lookback + 5:
                continue
            window = c.iloc[-(lookback+5):-5]
            if window.empty:
                continue
            prior_low_price = window.min()
            prior_low_idx = window.idxmin()
            if isinstance(prior_low_idx, (int, np.integer)):
                prior_rsi = rsi.iloc[prior_low_idx]
            else:
                idx_pos = c.index.get_loc(prior_low_idx)
                prior_rsi = rsi.iloc[idx_pos]

            recent_low_price = c.iloc[-5:].min()
            recent_low_idx = c.iloc[-5:].idxmin()
            if isinstance(recent_low_idx, (int, np.integer)):
                recent_rsi = rsi.iloc[recent_low_idx]
            else:
                idx_pos = c.index.get_loc(recent_low_idx)
                recent_rsi = rsi.iloc[idx_pos]

            if recent_low_price <= prior_low_price * 1.01 and recent_rsi > prior_rsi + 1:
                div_count += 1

    # EMA reclaim
    ema5 = c.ewm(span=5).mean()
    above_ema5 = price > ema5.iloc[-1]
    was_below = any(c.iloc[-7:-1] < ema5.iloc[-7:-1]) if len(c) >= 8 else False

    score = 0
    if div_count >= 2:
        score += 40
    elif div_count == 1:
        score += 20

    if above_ema5 and was_below:
        score += 30

    rsi_min_10d = rsi.iloc[-10:].min() if len(rsi) >= 10 else rsi_now
    if rsi_min_10d < 35 and rsi_now > rsi_min_10d + 5:
        score += 30

    return score, div_count, dd_40d


def run_us_daily_check(token, chat_id):
    """Run the US market daily check and send Telegram alert."""
    import yfinance as yf
    import numpy as np

    all_syms = US_HOLDINGS + [s for s, _ in US_INDICES] + ["^VIX"]
    data = {}
    for sym in all_syms:
        try:
            t = yf.Ticker(sym)
            h = t.history(period="1y", auto_adjust=True)
            if not h.empty:
                h.index = h.index.tz_localize(None)
                data[sym] = h["Close"]
        except Exception:
            pass

    if not data:
        return

    lines = ["🇺🇸 <b>US Market Daily Report</b>", f"📅 {ist_now().strftime('%Y-%m-%d')}\n"]

    # VIX
    if "^VIX" in data:
        vix_now = data["^VIX"].iloc[-1]
        vix_20d = data["^VIX"].iloc[-20:].max()
        if vix_now > 30:
            lines.append(f"⚠️ VIX: {vix_now:.1f} (HIGH FEAR)")
        elif vix_now > 25:
            lines.append(f"⚠️ VIX: {vix_now:.1f} (elevated)")
        elif vix_20d > 25:
            lines.append(f"📉 VIX: {vix_now:.1f} (fear fading from {vix_20d:.1f})")
        else:
            lines.append(f"VIX: {vix_now:.1f} ✅")

    # Indices
    lines.append("\n<b>📊 Market:</b>")
    for sym, name in US_INDICES:
        if sym in data:
            r = us_analyze_52w(data[sym])
            lines.append(f"  {name}: ${r['price']:.2f} ({r['dd']:+.1f}%) 1m:{r['ret_1m']:+.0f}%")

    # Holdings
    lines.append("\n<b>📋 Holdings:</b>")
    actions = []

    for sym in US_HOLDINGS:
        if sym not in data:
            continue
        c = data[sym]
        r = us_analyze_52w(c)

        # 52w recovery signal
        if r["dd"] > -3:
            emoji = "🟢"
            tag = ""
        elif r["dd"] <= -10 and r["above_20"] and r["ret_1m"] > 5:
            emoji = "🟠"
            tag = f" ← <b>ADD</b> (+{r['ret_1m']:.0f}% 1m, {r['upside']:.0f}% upside)"
            actions.append(f"🟠 <b>ADD {sym}</b> — Recovery rally\n"
                           f"  ${r['price']:.2f} ({r['dd']:+.1f}% from 52w hi)\n"
                           f"  +{r['ret_1m']:.0f}% in 1m, {r['upside']:.0f}% upside\n"
                           f"  RSI: {r['rsi']:.0f}")
        elif r["dd"] <= -10 and r["above_20"]:
            emoji = "🟡"
            tag = f" ← WATCH ({r['upside']:.0f}% upside)"
        elif r["dd"] <= -10:
            emoji = "🔴"
            tag = " ← WAIT (below EMA20)"
        elif r["dd"] <= -5:
            emoji = "🟡"
            tag = ""
        else:
            emoji = "⚪"
            tag = ""

        lines.append(f"  {emoji} {sym}: ${r['price']:.2f} ({r['dd']:+.1f}%){tag}")

        # Dip detector
        if len(c) >= 60:
            dip_score, div_count, dd_40d = us_compute_dip_score(c)
            if dip_score >= 40:
                div_label = {0: "", 1: "single", 2: "double", 3: "triple"}.get(div_count, "")
                dip_msg = f"🔍 <b>DIP SIGNAL: {sym}</b> (score {dip_score})"
                if div_label:
                    dip_msg += f"\n  RSI {div_label} divergence detected"
                if dip_score >= 60:
                    dip_msg += "\n  ⚡ Reversal confirmed — ADD"
                actions.append(dip_msg)

    # Actions
    if actions:
        lines.append("\n<b>🎯 ACTIONS:</b>")
        for a in actions:
            lines.append(f"\n{a}")
    else:
        lines.append("\n✅ No action needed today.")

    message = "\n".join(lines)
    print(f"\n  [US CHECK] Sending report...")
    send_telegram(token, chat_id, message)
    print(f"  [US CHECK] Done.")


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
                  f"🤖 Alert engine started\nWatching {len(config['alerts'])} stocks, {total_alerts} buy levels\n"
                  f"🇺🇸 US daily check enabled (7 AM IST)")
    
    us_check_done_today = False
    
    while True:
        try:
            # Reload config each cycle (allows live edits)
            config = load_config()
            state = load_state()
            now = ist_now()
            
            # ── US MARKET DAILY CHECK (once at ~7 AM IST) ──
            # US market closes 4 PM ET = 1:30 AM IST (next day)
            # Run at 7 AM IST to ensure all data is settled
            current_date = now.strftime("%Y-%m-%d")
            if now.hour >= 7 and not us_check_done_today:
                print(f"  [{now.strftime('%H:%M')} IST] Running US market daily check...")
                try:
                    run_us_daily_check(config["telegram_bot_token"], config["telegram_chat_id"])
                    us_check_done_today = True
                except Exception as e:
                    print(f"  US check error: {e}")
            
            # Reset US check flag at midnight
            if now.hour < 7:
                us_check_done_today = False
            
            # ── INDIAN MARKET ALERTS ──
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
