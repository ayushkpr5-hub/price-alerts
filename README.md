# Price Alert System

## Quick Start (2 minutes)

1. **Create Telegram bot:**
   - Open Telegram → search `@BotFather` → send `/newbot`
   - Give it a name → copy the **bot token**

2. **Get your chat ID:**
   - Search `@userinfobot` → send `/start` → copy your **ID number**

3. **Configure:**
   - Edit `config.json` → paste `telegram_bot_token` and `telegram_chat_id`

4. **Run:**
   ```bash
   pip install yfinance
   python alert_engine.py
   ```

## Adding/Removing Stocks

Edit `config.json` — the engine reloads it every cycle (no restart needed):

```json
{
  "symbol": "RELIANCE",
  "name": "Reliance Industries",
  "buy_levels": [2800, 2700],
  "stoploss": 2600,
  "notes": "Support at 2800, deep value at 2700"
}
```

## Deploy to Cloud (runs 24/7)

### Option 1: Oracle Cloud (FREE forever)
1. Sign up at cloud.oracle.com (free tier)
2. Create a VM (ARM Ampere, 1 CPU, 1GB RAM — free)
3. SSH in, install Python, copy alerts/ folder
4. `nohup python3 alert_engine.py > alert.log 2>&1 &`

### Option 2: Any VPS ($3-5/month)
DigitalOcean, Vultr, AWS Lightsail — cheapest tier is enough.
