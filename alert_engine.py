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
    
    # Telegram has 4096 char limit — split if needed
    messages = []
    if len(message) <= 4000:
        messages = [message]
    else:
        # Split on double newlines to keep sections together
        parts = message.split("\n\n")
        current = ""
        for part in parts:
            if len(current) + len(part) + 2 > 4000:
                if current:
                    messages.append(current)
                current = part
            else:
                current = current + "\n\n" + part if current else part
        if current:
            messages.append(current)
    
    success = True
    for msg in messages:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()
        
        try:
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            print(f"  Telegram error: {e}")
            success = False
    
    return success


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

# Momentum Pullback Watchlist — stocks/ETFs you WANT to enter on a dip
# These are in strong uptrends; alert when they pull back 3-5% and bounce
US_MOMENTUM_WATCHLIST = [
    ("SOXX", "Semiconductors", "SOXL"),
    ("SOXL", "3x Semis", None),
    ("XLK", "Technology", "TECL"),
    ("QQQ", "NASDAQ 100", "TQQQ"),
    ("AMD", "AMD", None),
    ("AVGO", "Broadcom", None),
    ("TSM", "TSMC", None),
    ("GDX", "Gold Miners", "NUGT"),
]


def us_analyze_52w(c):
    """52-week recovery analysis from a close price series."""
    price = c.iloc[-1]
    high_52w = c.max()
    low_52w = c.min()
    dd = (price / high_52w - 1) * 100
    upside = (high_52w / price - 1) * 100

    ema5 = c.ewm(span=5).mean().iloc[-1]
    ema20 = c.ewm(span=20).mean().iloc[-1]
    ema50 = c.ewm(span=50).mean().iloc[-1]
    ret_1m = (price / c.iloc[-21] - 1) * 100 if len(c) >= 22 else 0
    ret_1w = (price / c.iloc[-5] - 1) * 100 if len(c) >= 6 else 0

    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    rsi = (100 - (100 / (1 + rs))).iloc[-1]

    above_5 = price > ema5
    above_20 = price > ema20
    above_50 = price > ema50

    # Reclaiming EMA20: was below recently, now above or very close
    was_below_20 = any(c.iloc[-5:-1] < c.ewm(span=20).mean().iloc[-5:-1]) if len(c) >= 6 else False
    near_ema20 = abs(price - ema20) / ema20 < 0.005  # within 0.5%
    reclaiming_20 = (above_20 or near_ema20) and was_below_20

    # Momentum turning: above EMA5 + positive weekly return (catches early bounces)
    momentum_turning = above_5 and ret_1w > 1

    return {
        "price": price, "high_52w": high_52w, "dd": dd, "upside": upside,
        "ret_1m": ret_1m, "ret_1w": ret_1w, "rsi": rsi,
        "above_5": above_5, "above_20": above_20, "above_50": above_50,
        "reclaiming_20": reclaiming_20, "momentum_turning": momentum_turning,
    }


def us_compute_dip_score(c):
    """Simplified dip score with RSI divergence for Railway (no external imports)."""
    import numpy as np
    price = c.iloc[-1]
    high_40d = c.iloc[-40:].max() if len(c) >= 40 else c.max()
    dd_40d = (price / high_40d - 1) * 100

    if dd_40d > -10:
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
        # ADD: stock is >5% below 52w high AND showing recovery signs
        # Recovery signs: above EMA20, OR reclaiming EMA20, OR momentum turning (above EMA5 + positive week)
        recovering = r["above_20"] or r["reclaiming_20"] or r["momentum_turning"]

        if r["dd"] > -3:
            emoji = "🟢"
            tag = ""
        elif r["dd"] <= -5 and recovering and (r["ret_1m"] > 3 or r["ret_1w"] > 2):
            emoji = "🟠"
            tag = f" ← <b>ADD</b> (1w:{r['ret_1w']:+.0f}%, {r['upside']:.0f}% upside)"
            actions.append(f"🟠 <b>[RECOVERY] ADD {sym}</b>\n"
                           f"  ${r['price']:.2f} ({r['dd']:+.1f}% from 52w hi)\n"
                           f"  1w:{r['ret_1w']:+.1f}% 1m:{r['ret_1m']:+.1f}%, {r['upside']:.0f}% upside\n"
                           f"  RSI: {r['rsi']:.0f}")
        elif r["dd"] <= -5 and recovering:
            emoji = "🟡"
            tag = f" ← WATCH ({r['upside']:.0f}% upside)"
        elif r["dd"] <= -5:
            emoji = "🔴"
            tag = " ← WAIT (no recovery signs)"
        elif r["dd"] <= -3:
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
                dip_msg = f"🔍 <b>[DIP] {sym}</b> (score {dip_score})"
                if div_label:
                    dip_msg += f"\n  RSI {div_label} divergence detected"
                if dip_score >= 60:
                    dip_msg += "\n  ⚡ Reversal confirmed — ADD"
                actions.append(dip_msg)

    # ── SECTOR MOMENTUM SCANNER ──
    # Note: wait_for_dip_syms is populated by momentum pullback section below.
    # We initialize it here and the sector scanner will filter at the end.
    # Map leveraged ETFs to their sector ETFs so conflicts are caught
    wait_for_dip_syms = set()
    LEVERAGED_TO_SECTOR = {
        "SOXL": "SOXX", "TECL": "XLK", "FAS": "XLF", "ERX": "XLE",
        "CURE": "XLV", "NUGT": "GDX", "TNA": "IWM", "TQQQ": "QQQ",
    }

    # Same reversal-confirmation logic as per-stock dip detector,
    # applied to sector ETFs to find entry points in sectors you don't own.
    #
    # Entry signal requires:
    #   1. Sector was in a dip (>10% below 40-day high at some point in last 20 days)
    #   2. Now showing reversal: above EMA5 + positive weekly return
    #   3. Outperforming SPY (relative strength positive)
    #
    # This would have caught SOXX around Apr 1-2 when semis bounced off
    # the March bottom with +5% weekly return and crossed above EMA5.

    SECTORS = {
        "SOXX": ("Semiconductors", "SOXL"),
        "XLK": ("Technology", "TECL"),
        "XLF": ("Financials", "FAS"),
        "XLE": ("Energy", "ERX"),
        "XLV": ("Healthcare", "CURE"),
        "XLI": ("Industrials", None),
        "XLY": ("Consumer Disc", None),
        "XLP": ("Consumer Staples", None),
        "XLU": ("Utilities", None),
        "XLB": ("Materials", None),
        "XLRE": ("Real Estate", None),
        "XLC": ("Communication", None),
        "GDX": ("Gold Miners", "NUGT"),
        "IWM": ("Small Caps", "TNA"),
        "EEM": ("Emerging Mkts", None),
    }

    sector_data = {}
    for sym in SECTORS:
        try:
            t = yf.Ticker(sym)
            h = t.history(period="6mo", auto_adjust=True)
            if not h.empty:
                h.index = h.index.tz_localize(None)
                sector_data[sym] = h["Close"]
        except Exception:
            pass

    if "SPY" not in data:
        try:
            t = yf.Ticker("SPY")
            h = t.history(period="6mo", auto_adjust=True)
            if not h.empty:
                h.index = h.index.tz_localize(None)
                data["SPY"] = h["Close"]
        except Exception:
            pass

    if sector_data and "SPY" in data:
        spy_c = data["SPY"]
        spy_1m = (spy_c.iloc[-1] / spy_c.iloc[-21] - 1) * 100 if len(spy_c) >= 22 else 0

        sector_results = []
        for sym, (name, leveraged) in SECTORS.items():
            if sym not in sector_data:
                continue
            sc = sector_data[sym]
            if len(sc) < 40:
                continue

            price = sc.iloc[-1]
            ret_1w = (price / sc.iloc[-5] - 1) * 100 if len(sc) >= 6 else 0
            ret_1m = (price / sc.iloc[-21] - 1) * 100 if len(sc) >= 22 else 0
            rs_1m = ret_1m - spy_1m

            # Was in a dip? Check if any day in last 20 was >10% below 40-day high
            high_40d = sc.iloc[-40:].max()
            dd_now = (price / high_40d - 1) * 100
            was_in_dip = False
            for d in range(max(0, len(sc)-20), len(sc)):
                h40 = sc.iloc[max(0,d-40):d+1].max()
                if (sc.iloc[d] / h40 - 1) * 100 < -10:
                    was_in_dip = True
                    break

            # Reversal signals (same logic as per-stock)
            ema5 = sc.ewm(span=5).mean().iloc[-1]
            ema20 = sc.ewm(span=20).mean().iloc[-1]
            above_ema5 = price > ema5
            above_ema20 = price > ema20

            # RSI
            delta = sc.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs_val = gain / (loss + 1e-10)
            rsi = (100 - (100 / (1 + rs_val))).iloc[-1]

            # Entry signal: was in dip + now bouncing + outperforming
            entry = "NONE"
            if was_in_dip and above_ema5 and ret_1w > 3 and rs_1m > 5:
                entry = "ENTER"  # Strong: dip recovery + outperforming SPY
            elif was_in_dip and above_ema5 and ret_1w > 2:
                entry = "WATCH"  # Bouncing but not yet outperforming
            elif not was_in_dip and rs_1m > 10 and ret_1m > 15:
                entry = "HOT"    # No dip, just massively outperforming

            sector_results.append({
                "sym": sym, "name": name, "leveraged": leveraged,
                "price": price, "ret_1w": ret_1w, "ret_1m": ret_1m,
                "rs_1m": rs_1m, "dd_now": dd_now, "was_in_dip": was_in_dip,
                "above_ema5": above_ema5, "above_ema20": above_ema20,
                "rsi": rsi, "entry": entry,
            })

        sector_results.sort(key=lambda x: -x["ret_1m"])

        # Show top 5 + any entry signals
        lines.append("\n<b>🔥 Sector Momentum:</b>")
        for i, s in enumerate(sector_results[:5]):
            lev = f" ({s['leveraged']})" if s['leveraged'] else ""
            rs_emoji = "🟢" if s["rs_1m"] > 5 else ("🟡" if s["rs_1m"] > 0 else "🔴")
            entry_tag = ""
            if s["entry"] == "ENTER" and s["sym"] not in wait_for_dip_syms:
                entry_tag = " ⚡ENTER"
            elif s["entry"] == "ENTER" and s["sym"] in wait_for_dip_syms:
                entry_tag = " ⏳wait for pullback"
            elif s["entry"] == "HOT":
                entry_tag = " 🔥HOT"
            lines.append(f"  {i+1}. {rs_emoji} {s['name']}: 1m:{s['ret_1m']:+.0f}% "
                         f"vs SPY:{s['rs_1m']:+.0f}%{lev}{entry_tag}")

        # Generate action alerts for ENTER signals
        # Skip if momentum watchlist says "wait for pullback" (avoid contradiction)
        enter_sectors = [s for s in sector_results
                         if s["entry"] == "ENTER" and s["sym"] not in wait_for_dip_syms]
        for s in enter_sectors:
            lev_msg = f"\n  Leveraged ETF: {s['leveraged']}" if s['leveraged'] else ""
            actions.append(
                f"⚡ <b>[SECTOR] {s['name']}</b> ({s['sym']})\n"
                f"  Dip recovery confirmed\n"
                f"  1w:{s['ret_1w']:+.1f}% 1m:{s['ret_1m']:+.1f}% vs SPY:{s['rs_1m']:+.1f}%\n"
                f"  Above EMA5 ✓ RSI:{s['rsi']:.0f}{lev_msg}"
            )

        # Also alert on HOT sectors (skip if in wait list)
        hot_sectors = [s for s in sector_results
                       if s["entry"] == "HOT" and s["sym"] not in wait_for_dip_syms]
        for s in hot_sectors:
            lev_msg = f"\n  Leveraged ETF: {s['leveraged']}" if s['leveraged'] else ""
            actions.append(
                f"🔥 <b>[SECTOR] HOT: {s['name']}</b> ({s['sym']})\n"
                f"  1m:{s['ret_1m']:+.1f}% (SPY:{spy_1m:+.1f}%){lev_msg}"
            )

    # ── MOMENTUM PULLBACK WATCHLIST ──
    # For stocks in strong uptrends that you want to enter on a dip.
    # Based on historical analysis: after RSI 85+, median pullback is 2-3%,
    # 25th percentile is 4-5%. Pullback typically happens within 10-14 days.
    #
    # Logic:
    #   1. Stock was recently at/near highs (within 3% of 20-day high)
    #   2. Has pulled back 3-5% from that high
    #   3. Now showing bounce (above EMA3, positive daily return)
    #   → ENTER on the pullback bounce
    #
    #   If no pullback after 14 days → "still running, consider entering anyway"

    momentum_data = {}
    for sym, name, lev in US_MOMENTUM_WATCHLIST:
        if sym not in data:
            try:
                t = yf.Ticker(sym)
                h = t.history(period="3mo", auto_adjust=True)
                if not h.empty:
                    h.index = h.index.tz_localize(None)
                    momentum_data[sym] = h["Close"]
            except Exception:
                pass
        else:
            momentum_data[sym] = data[sym]

    if momentum_data:
        pullback_signals = []

        for sym, name, lev in US_MOMENTUM_WATCHLIST:
            if sym not in momentum_data:
                continue
            mc = momentum_data[sym]
            if len(mc) < 20:
                continue

            price = mc.iloc[-1]
            high_20d = mc.iloc[-20:].max()
            dd_from_20d = (price / high_20d - 1) * 100

            # EMA3 for quick bounce detection
            ema3 = mc.ewm(span=3).mean().iloc[-1]
            above_ema3 = price > ema3

            # Daily return
            daily_ret = (price / mc.iloc[-2] - 1) * 100 if len(mc) >= 2 else 0

            # RSI
            delta_m = mc.diff()
            gain_m = delta_m.where(delta_m > 0, 0).rolling(14).mean()
            loss_m = (-delta_m.where(delta_m < 0, 0)).rolling(14).mean()
            rs_m = gain_m / (loss_m + 1e-10)
            rsi_m = (100 - (100 / (1 + rs_m))).iloc[-1]

            # 1-month return (is it in an uptrend?)
            ret_1m = (price / mc.iloc[-21] - 1) * 100 if len(mc) >= 22 else 0

            # Determine signal
            signal = "NONE"
            detail = ""

            if dd_from_20d <= -3 and dd_from_20d >= -8 and above_ema3 and daily_ret > 0.5 and ret_1m > 5:
                # Pulled back 3-8% from recent high, now bouncing, in uptrend
                signal = "ENTER"
                detail = f"Pulled back {dd_from_20d:+.1f}%, now bouncing"
            elif dd_from_20d <= -3 and dd_from_20d >= -8 and ret_1m > 5:
                # Pulled back but not bouncing yet
                signal = "WATCH"
                detail = f"Pulled back {dd_from_20d:+.1f}%, waiting for bounce"
            elif dd_from_20d > -1 and rsi_m > 80 and ret_1m > 15:
                # At highs, very overbought, strong trend — wait for pullback
                signal = "WAIT_FOR_DIP"
                detail = f"RSI {rsi_m:.0f}, at highs, wait for 3-5% pullback"
            elif dd_from_20d > -3 and ret_1m > 10:
                # Near highs, good trend, small pullback might be enough
                signal = "NEAR_HIGH"
                detail = f"Only {dd_from_20d:+.1f}% from high"

            pullback_signals.append({
                "sym": sym, "name": name, "lev": lev,
                "price": price, "dd": dd_from_20d, "rsi": rsi_m,
                "ret_1m": ret_1m, "signal": signal, "detail": detail,
            })

        # Collect symbols where momentum watchlist says "wait for pullback"
        # so sector scanner actions can be filtered
        # Also map leveraged ETFs to their underlying sector ETFs
        for s in pullback_signals:
            if s["signal"] == "WAIT_FOR_DIP":
                wait_for_dip_syms.add(s["sym"])
                # Also add the underlying sector ETF
                underlying = LEVERAGED_TO_SECTOR.get(s["sym"])
                if underlying:
                    wait_for_dip_syms.add(underlying)
            elif s["signal"] == "WATCH":
                # WATCH also means don't enter via sector scanner
                wait_for_dip_syms.add(s["sym"])
                underlying = LEVERAGED_TO_SECTOR.get(s["sym"])
                if underlying:
                    wait_for_dip_syms.add(underlying)

        # Show watchlist status
        enters = [s for s in pullback_signals if s["signal"] == "ENTER"]
        watches = [s for s in pullback_signals if s["signal"] == "WATCH"]
        waits = [s for s in pullback_signals if s["signal"] == "WAIT_FOR_DIP"]

        if enters or watches or waits:
            lines.append("\n<b>📋 Momentum Watchlist:</b>")

            for s in pullback_signals:
                if s["signal"] == "NONE":
                    continue
                lev_str = f" ({s['lev']})" if s['lev'] else ""
                if s["signal"] == "ENTER":
                    lines.append(f"  🎯 {s['name']}{lev_str}: ${s['price']:.2f} — <b>ENTER</b> {s['detail']}")
                elif s["signal"] == "WATCH":
                    lines.append(f"  🟡 {s['name']}{lev_str}: ${s['price']:.2f} — {s['detail']}")
                elif s["signal"] == "WAIT_FOR_DIP":
                    lines.append(f"  ⏳ {s['name']}{lev_str}: ${s['price']:.2f} — {s['detail']}")

        # Add ENTER signals to actions
        for s in enters:
            lev_msg = f"\n  Leveraged: {s['lev']}" if s['lev'] else ""
            actions.append(
                f"🎯 <b>[PULLBACK] {s['name']}</b> ({s['sym']})\n"
                f"  {s['detail']}\n"
                f"  1m:{s['ret_1m']:+.1f}% RSI:{s['rsi']:.0f}{lev_msg}"
            )

    # Actions — remove any sector ENTER that conflicts with momentum "wait for pullback"
    if wait_for_dip_syms:
        filtered_actions = []
        for a in actions:
            skip = False
            for sym in wait_for_dip_syms:
                if f"[SECTOR]" in a and sym in a:
                    skip = True
                    break
            if not skip:
                filtered_actions.append(a)
        actions = filtered_actions

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
                  f"🇺🇸 US check: 7:30 PM & 11 PM IST (during US market hours)")
    
    us_check_schedule = "10 PM & 1 AM IST (during US market hours)"
    
    while True:
        try:
            # Reload config each cycle (allows live edits)
            config = load_config()
            state = load_state()
            now = ist_now()
            
            # ── US MARKET DAILY CHECK (during US market hours, weekdays only) ──
            # US market (EDT): 9:30 AM - 4 PM ET = 7 PM - 1:30 AM IST
            # US market open Mon-Fri ET. In IST:
            #   Mon 7 PM IST → Mon night (US Mon)
            #   Fri 7 PM IST → Fri night (US Fri)
            #   Sat/Sun → skip (US market closed)
            # Note: Fri 11 PM IST is still US Friday session — that's fine
            current_date = now.strftime("%Y-%m-%d")
            us_hour = now.hour
            us_minute = now.minute
            us_time = us_hour * 60 + us_minute
            weekday = now.weekday()  # 0=Mon, 5=Sat, 6=Sun
            
            # Skip weekends: Sat daytime and Sun entirely have no US session
            # Mon-Fri evening (7 PM+) = US market open
            # Sat early morning (before 2 AM) = tail end of US Friday session — OK
            us_market_day = False
            if weekday <= 4 and us_time >= 19 * 60:
                us_market_day = True  # Mon-Fri evening IST
            elif weekday <= 5 and us_time <= 2 * 60:
                us_market_day = True  # Tue-Sat early morning IST (prev day's US session)
            
            if us_market_day:
                # 7:30 PM IST = 19:30 = 1170 minutes
                # 11:00 PM IST = 23:00 = 1380 minutes
                us_run_times = [19 * 60 + 30, 23 * 60]
                
                for run_time in us_run_times:
                    if abs(us_time - run_time) <= 10:
                        run_key = f"US_{current_date}_{run_time}"
                        if run_key not in state.get("fired", []):
                            print(f"  [{now.strftime('%H:%M')} IST] Running US market check (US market is open)...")
                            try:
                                run_us_daily_check(config["telegram_bot_token"], config["telegram_chat_id"])
                                state.setdefault("fired", []).append(run_key)
                                save_state(state)
                            except Exception as e:
                                print(f"  US check error: {e}")
            
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
