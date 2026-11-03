"""Settings, read from the environment (.env locally, service settings on Render)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PAYPAL_API = "https://api-m.sandbox.paypal.com"  # sandbox only, on purpose
SANDBOX = ".sandbox." in PAYPAL_API  # every buyer is a test account, so the AI is told so
CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID", "")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
REVIEW_MODEL = os.environ.get("SHIPSAFE_MODEL", "claude-opus-5-5")
# AI reviews allowed per 24 hours. Past it, orders are held for the seller without calling Claude.
DAILY_REVIEW_LIMIT = int(os.environ.get("SHIPSAFE_DAILY_REVIEWS", "3"))

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "data" / "samples"
DB_PATH = ROOT / "data" / "shipsafe.db"
SHOP_HISTORY = ROOT / "fixtures" / "shop_history.json"
