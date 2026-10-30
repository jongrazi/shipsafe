"""
ShipSafe web app: the sandbox demo store and the PayPal webhook.

The review itself is not built yet. For now each paid order is reported field by field,
which settles what the review can rely on (see field_report.py).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from . import config, field_report, webhooks
from .paypal import PayPal, PayPalError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("shipsafe")

STATIC_DIR = Path(__file__).parent / "static"
ORDER_ID = re.compile(r"^[A-Z0-9]{10,40}$")
EVENT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# The price lives on the server, so a buyer's browser can't change it.
DEMO_ITEM = {"name": "Trail Backpack", "sku": "DEMO-001", "price": "149.00"}

app = FastAPI(title="ShipSafe")
paypal = PayPal(config.CLIENT_ID, config.CLIENT_SECRET)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def store() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def store_config() -> dict:
    return {"client_id": config.CLIENT_ID, "item": DEMO_ITEM}


@app.post("/api/orders")
def create_order() -> dict:
    try:
        return {"id": paypal.create_order(DEMO_ITEM)["id"]}
    except PayPalError as exc:
        log.error("create order: %s", exc)
        raise HTTPException(502, "PayPal could not create the order") from None


@app.post("/api/orders/{order_id}/capture")
def capture_order(order_id: str) -> dict:
    if not ORDER_ID.match(order_id):
        raise HTTPException(400, "invalid order id")
    try:
        order = paypal.capture_order(order_id)
    except PayPalError as exc:
        log.error("capture order: %s", exc)
        raise HTTPException(502, "PayPal could not capture the order") from None
    capture = field_report.first_capture(order)
    field_report.save_sample(config.SAMPLES_DIR, f"capture-{order_id}", capture, order)
    log.info("field report (capture response) %s: %s", order_id,
             json.dumps(field_report.build(capture, order)))
    return {"status": order.get("status")}


@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request, background: BackgroundTasks) -> dict:
    raw_body = await request.body()
    try:
        await run_in_threadpool(webhooks.verify, raw_body, request.headers, config.WEBHOOK_ID)
    except webhooks.InvalidSignature as exc:
        log.warning("webhook refused: %s", exc)
        raise HTTPException(400, "invalid signature") from None
    except requests.RequestException as exc:
        # Not the sender's fault: a non-2xx answer makes PayPal send it again.
        log.warning("webhook certificate fetch failed: %s", type(exc).__name__)
        raise HTTPException(503, "try again") from None

    event = json.loads(raw_body)
    if event.get("event_type") == "PAYMENT.CAPTURE.COMPLETED":
        background.add_task(report_capture, event)
    return {"received": True}


def report_capture(event: dict) -> None:
    """Runs after the webhook has been answered. PayPal may deliver an event more than once."""
    event_id = event.get("id", "")
    capture_id = (event.get("resource") or {}).get("id", "")
    if not EVENT_ID.match(event_id) or not ORDER_ID.match(capture_id):
        log.warning("webhook event with unusable ids ignored")
        return
    if (config.SAMPLES_DIR / f"{event_id}.json").exists():
        return
    try:
        capture = paypal.get_capture(capture_id)
        order_id = field_report.dig(capture, "supplementary_data.related_ids.order_id")
        order = paypal.get_order(order_id) if order_id else {}
    except PayPalError as exc:
        log.error("report capture %s: %s", capture_id, exc)
        return
    field_report.save_sample(config.SAMPLES_DIR, event_id, capture, order)
    log.info("field report (webhook) %s: %s", capture_id,
             json.dumps(field_report.build(capture, order)))
