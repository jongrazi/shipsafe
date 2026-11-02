"""Settings, read from the environment (.env locally, service settings on Render)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PAYPAL_API = "https://api-m.sandbox.paypal.com"  # sandbox only, on purpose
CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID", "")

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "data" / "samples"
DB_PATH = ROOT / "data" / "shipsafe.db"
SHOP_HISTORY = ROOT / "fixtures" / "shop_history.json"
