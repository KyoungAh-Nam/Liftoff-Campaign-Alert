import os
import time
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# ── Config (loaded from GitHub Secrets) ───────────────────────
ACCOUNTS = [
    {"name": os.environ["ACCOUNT_1_NAME"], "api_key": os.environ["ACCOUNT_1_KEY"], "api_secret": os.environ["ACCOUNT_1_SECRET"]},
    # {"name": os.environ["ACCOUNT_2_NAME"], "api_key": os.environ["ACCOUNT_2_KEY"], "api_secret": os.environ["ACCOUNT_2_SECRET"]},
]
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
SLACK_BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]

BASE_URL = "https://data.liftoff.io/api/v1"

# ── Alert thresholds ───────────────────────────────────────────
THRESHOLDS = {
    "ctr_change_pct":    20,   # CTR ±20% change
    "cpi_increase_pct":  20,   # CPI +20% spike
    "install_drop_pct": -20,   # Installs -20% drop
}

# ── Time range: today vs yesterday same timeframe ───────────
