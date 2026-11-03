"""
ShipSafe web app: the sandbox demo store and the PayPal webhook.

Each paid order is stored, checked by the signals, reviewed by the AI and decided by the
policy, after the buyer has been answered. Field reports stay in the log until the webhook
path has been confirmed on PayPal.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from . import config, field_report, orders, policy, review, signals, webhooks
from .paypal import PayPal, PayPalError
from .store import DAY, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("shipsafe")

STATIC_DIR = Path(__file__).parent / "static"
EVENT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# The price lives on the server, so a buyer's browser can't change it.
DEMO_ITEM = {"name": "Trail Backpack", "sku": "DEMO-001", "price": "149.00"}

paypal = PayPal(config.CLIENT_ID, config.CLIENT_SECRET)
store: Store | None = None  # opened at startup
claude = review.make_client()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store
    store = Store(config.DB_PATH)
    history = json.loads(config.SHOP_HISTORY.read_text(encoding="utf-8"))
    log.info("shop history: %d orders added, %d stored", store.seed(history), store.count())
    yield


app = FastAPI(title="ShipSafe", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def storefront() -> FileResponse:
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
def capture_order(order_id: str, background: BackgroundTasks) -> dict:
    if not orders.PAYPAL_ID.match(order_id):
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
    background.add_task(record_order, capture, order, "capture response")  # the buyer mustn't wait
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
    if not EVENT_ID.match(event_id) or not orders.PAYPAL_ID.match(capture_id):
        log.warning("webhook event with unusable ids ignored")
        return
    if (config.SAMPLES_DIR / f"{event_id}.json").exists():
        return
    try:
        capture = paypal.get_capture(capture_id)
        order_id = orders.order_id_of_capture(capture)
        order = paypal.get_order(order_id) if order_id else {}
    except PayPalError as exc:
        log.error("report capture %s: %s", capture_id, exc)
        return
    field_report.save_sample(config.SAMPLES_DIR, event_id, capture, order)
    log.info("field report (webhook) %s: %s", capture_id,
             json.dumps(field_report.build(capture, order)))
    record_order(capture, order, "webhook")


def record_order(capture: dict, order: dict, via: str) -> None:
    """
    Store and review a paid order, once per order: the capture response and the webhook
    both report it. By now the buyer has paid, so a failure here is logged, not shown to them.
    """
    paid = orders.from_paypal(capture, order)
    if paid is None:
        log.warning("order not recorded (%s): not a completed payment, or fields are missing", via)
        return
    try:
        if not store.add(paid):
            return
        found = signals.build(paid, store)
        log.info("signals %s (%s): %s", paid.order_id, via,
                 json.dumps({signal.name: signal.level for signal in found}))
        result = run_review(paid, found)
        decision = policy.decide(paid, found, result.band)
        store.add_review(paid.order_id, decision.action, decision.band, {
            "decision": asdict(decision),
            "signals": [asdict(signal) for signal in found],
            "review": asdict(result),
        })
    except sqlite3.Error as exc:
        log.error("order %s not recorded: %s", paid.order_id, type(exc).__name__)
        return
    log.info("decision %s: %s (band %s, rules %s, model %s, hold above %.0f%%)%s", paid.order_id,
             decision.action, decision.band, decision.rule_band, decision.model_band,
             decision.hold_above * 100, f" errors: {result.errors}" if result.errors else "")


def run_review(paid: orders.PaidOrder, found: list[signals.Signal]) -> review.Review:
    """The AI review, unless today's limit is used up: then no call, and the order is held."""
    recent = store.reviews_since(time.time() - DAY)
    if len(recent) >= config.DAILY_REVIEW_LIMIT:
        return review.Review(model=config.REVIEW_MODEL, errors=[
            f"daily limit of {config.DAILY_REVIEW_LIMIT} AI reviews reached"])
    return review.run(paid, found, store, claude)
