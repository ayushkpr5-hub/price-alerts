"""
Auto Support/Resistance Level Detector
========================================
Automatically finds key price levels from historical data.
No manual config needed — just list the stock symbols.

How it works:
1. Fetches 6 months of daily OHLCV data
2. Finds swing highs (resistance) and swing lows (support)
3. Clusters nearby levels (within 1.5% of each other)
4. Ranks by number of touches (more touches = stronger level)
5. Returns levels above price (breakout) and below price (support/buy)

Used by alert_engine.py to replace manual buy_levels/breakout_levels.
"""

import numpy as np


def find_swing_points(highs, lows, closes, window=5):
    """Find swing highs and swing lows in price data.
    
    A swing high: high[i] is the highest high in a window around it.
    A swing low: low[i] is the lowest low in a window around it.
    """
    swing_highs = []
    swing_lows = []
    
    for i in range(window, len(highs) - window):
        # Swing high: this bar's high is highest in the window
        if highs[i] == max(highs[i-window:i+window+1]):
            swing_highs.append((i, highs[i]))
        
        # Swing low: this bar's low is lowest in the window
        if lows[i] == min(lows[i-window:i+window+1]):
            swing_lows.append((i, lows[i]))
    
    return swing_highs, swing_lows


def cluster_levels(levels, threshold_pct=1.5):
    """Cluster nearby price levels together.
    
    If two levels are within threshold_pct of each other, they're the same level.
    Returns list of (avg_price, touch_count, indices).
    """
    if not levels:
        return []
    
    # Sort by price
    sorted_levels = sorted(levels, key=lambda x: x[1])
    
    clusters = []
    current_cluster = [sorted_levels[0]]
    
    for i in range(1, len(sorted_levels)):
        prev_price = current_cluster[-1][1]
        curr_price = sorted_levels[i][1]
        
        # Within threshold? Same cluster
        if abs(curr_price - prev_price) / prev_price * 100 <= threshold_pct:
            current_cluster.append(sorted_levels[i])
        else:
            # New cluster
            avg_price = np.mean([p for _, p in current_cluster])
            clusters.append({
                "price": round(float(avg_price), 2),
                "touches": len(current_cluster),
                "indices": [idx for idx, _ in current_cluster],
            })
            current_cluster = [sorted_levels[i]]
    
    # Don't forget last cluster
    if current_cluster:
        avg_price = np.mean([p for _, p in current_cluster])
        clusters.append({
            "price": round(float(avg_price), 2),
            "touches": len(current_cluster),
            "indices": [idx for idx, _ in current_cluster],
        })
    
    return clusters


def add_ema_levels(closes, current_price):
    """Add EMA levels as dynamic support/resistance."""
    levels = []
    
    if len(closes) >= 20:
        ema20 = float(closes.ewm(span=20).mean().iloc[-1])
        levels.append({"price": round(ema20, 2), "type": "EMA20", "touches": 0})
    
    if len(closes) >= 50:
        ema50 = float(closes.ewm(span=50).mean().iloc[-1])
        levels.append({"price": round(ema50, 2), "type": "EMA50", "touches": 0})
    
    if len(closes) >= 200:
        ema200 = float(closes.ewm(span=200).mean().iloc[-1])
        levels.append({"price": round(ema200, 2), "type": "EMA200", "touches": 0})
    
    return levels


def detect_levels(hist_df, current_price=None):
    """
    Main function: detect support and resistance levels from price history.
    
    Args:
        hist_df: DataFrame with columns: Open, High, Low, Close, Volume
        current_price: current price (if None, uses last close)
    
    Returns:
        {
            "support": [{"price": 186, "touches": 3, "type": "swing_low"}, ...],
            "resistance": [{"price": 235, "touches": 2, "type": "swing_high"}, ...],
            "buy_levels": [186, 170, 162],  # sorted desc (closest first)
            "breakout_levels": [208, 235],  # sorted asc (closest first)
            "stoploss": 154,
        }
    """
    if len(hist_df) < 30:
        return {"support": [], "resistance": [], "buy_levels": [], "breakout_levels": [], "stoploss": None}
    
    highs = hist_df["High"].values
    lows = hist_df["Low"].values
    closes = hist_df["Close"]
    
    if current_price is None:
        current_price = float(closes.iloc[-1])
    
    # Find swing points with different windows for multi-timeframe
    all_swing_highs = []
    all_swing_lows = []
    
    for window in [3, 5, 8, 13]:
        sh, sl = find_swing_points(highs, lows, closes.values, window=window)
        all_swing_highs.extend(sh)
        all_swing_lows.extend(sl)
    
    # Cluster nearby levels
    resistance_clusters = cluster_levels(all_swing_highs, threshold_pct=1.5)
    support_clusters = cluster_levels(all_swing_lows, threshold_pct=1.5)
    
    # Add EMA levels
    ema_levels = add_ema_levels(closes, current_price)
    
    # Separate into above/below current price
    support = []
    resistance = []
    
    for c in support_clusters:
        level = {
            "price": c["price"],
            "touches": c["touches"],
            "type": f"swing_low ({c['touches']}x)",
        }
        if c["price"] < current_price * 0.995:  # below current price (with 0.5% buffer)
            support.append(level)
        else:
            resistance.append(level)
    
    for c in resistance_clusters:
        level = {
            "price": c["price"],
            "touches": c["touches"],
            "type": f"swing_high ({c['touches']}x)",
        }
        if c["price"] > current_price * 1.005:  # above current price
            resistance.append(level)
        else:
            support.append(level)
    
    # Add EMAs to appropriate side
    for ema in ema_levels:
        if ema["price"] < current_price * 0.995:
            support.append(ema)
        elif ema["price"] > current_price * 1.005:
            resistance.append(ema)
    
    # Sort: support descending (closest first), resistance ascending (closest first)
    support.sort(key=lambda x: -x["price"])
    resistance.sort(key=lambda x: x["price"])
    
    # Filter: only keep levels with 2+ touches or EMA levels
    # (single-touch levels are noise)
    strong_support = [s for s in support if s["touches"] >= 2 or "EMA" in s.get("type", "")]
    strong_resistance = [r for r in resistance if r["touches"] >= 2 or "EMA" in r.get("type", "")]
    
    # If not enough strong levels, include single-touch ones
    if len(strong_support) < 2:
        strong_support = support[:5]
    if len(strong_resistance) < 2:
        strong_resistance = resistance[:5]
    
    # Extract just the prices for buy_levels and breakout_levels
    buy_levels = [s["price"] for s in strong_support[:5]]
    breakout_levels = [r["price"] for r in strong_resistance[:5]]
    
    # Stoploss: lowest support level or 10% below current price, whichever is higher
    if buy_levels:
        stoploss = round(min(buy_levels) * 0.95, 2)  # 5% below lowest support
    else:
        stoploss = round(current_price * 0.90, 2)  # 10% below current
    
    return {
        "support": strong_support,
        "resistance": strong_resistance,
        "buy_levels": buy_levels,
        "breakout_levels": breakout_levels,
        "stoploss": stoploss,
        "current_price": current_price,
    }


def format_levels_text(sym, name, levels):
    """Format levels for display/logging."""
    cp = levels["current_price"]
    lines = [f"\n{name} ({sym}) — ₹{cp:.2f}"]
    
    if levels["resistance"]:
        res_str = ", ".join(f"₹{r['price']:.0f} ({r['type']})" for r in levels["resistance"][:3])
        lines.append(f"  Resistance: {res_str}")
    
    if levels["support"]:
        sup_str = ", ".join(f"₹{s['price']:.0f} ({s['type']})" for s in levels["support"][:3])
        lines.append(f"  Support: {sup_str}")
    
    if levels["stoploss"]:
        lines.append(f"  Stoploss: ₹{levels['stoploss']:.0f}")
    
    return "\n".join(lines)
