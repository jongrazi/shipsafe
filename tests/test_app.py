"""Field reports and web routes, with PayPal replaced by a fake."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import config, field_report, main, review, webhooks
from app.paypal import PayPalError
from app.store import Store
from tests.helpers import CAPTURE, ORDER, make_order

BUYER_EMAIL = ORDER["payer"]["email_address"]
EVENT = {"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "CAPTURE12345"}}


class FakePayPal:
    def __init__(self):
        self.created: list[dict] = []
        self.capture = CAPTURE
        self.capture_calls = 0
        self.fail = False

    def create_order(self, item):
        self._maybe_fail()
        self.created.append(item)
        return {"id": "ORDER12345A"}

    def capture_order(self, order_id):
        self._maybe_fail()
        return ORDER

    def get_capture(self, capture_id):
        self._maybe_fail()
        self.capture_calls += 1
        return self.capture

    def get_order(self, order_id):
        return ORDER

    def _maybe_fail(self):
        if self.fail:
            raise PayPalError("HTTP 500, debug_id test")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = FakePayPal()
    monkeypatch.setattr(main, "paypal", fake)
    monkeypatch.setattr(main, "store", Store(tmp_path / "test.db"))
    monkeypatch.setattr(main, "claude", None)  # a local .env key must never reach the real API
    monkeypatch.setattr(config, "SAMPLES_DIR", tmp_path / "samples")
    return fake


@pytest.fixture
def client(fake):
    return TestClient(main.app)


@pytest.fixture
def genuine_webhooks(monkeypatch):
    monkeypatch.setattr(webhooks, "verify", lambda *args, **kwargs: None)


def post_event(client, event=EVENT):
    return client.post("/webhooks/paypal", content=json.dumps(event))


def test_dig_handles_lists_and_missing_steps():
    assert field_report.dig(ORDER, "purchase_units.0.payments.captures.0.id") == "CAPTURE12345"
    assert field_report.dig(ORDER, "purchase_units.5.payments") is None
    assert field_report.dig(ORDER, "status.deeper") is None


def test_report_shows_status_values_and_marks_missing_fields():
    report = field_report.build(CAPTURE, ORDER)
    assert report["capture.seller_protection.status"] == "ELIGIBLE"
    assert report["order.payment_source.paypal.account_status"] == "VERIFIED"
    assert report["capture.processor_response.avs_code"] == "missing"


def test_report_keeps_personal_details_out():
    report = field_report.build(CAPTURE, ORDER)
    assert report["order.payment_source.paypal.email_address"] == "present"
    assert BUYER_EMAIL not in json.dumps(report)


def test_startup_loads_shop_history_once(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "store", None)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "startup.db")
    with TestClient(main.app):
        first_count = main.store.count()
    with TestClient(main.app):
        assert main.store.count() == first_count
    assert first_count > 100


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_store_page_loads(client):
    assert "ShipSafe demo store" in client.get("/").text


def test_order_uses_the_server_price_whatever_the_browser_sends(client, fake):
    response = client.post("/api/orders", json={"price": "0.01"})
    assert response.json() == {"id": "ORDER12345A"}
    assert fake.created == [main.DEMO_ITEM]


def test_paypal_failure_gives_a_plain_502(client, fake):
    fake.fail = True
    response = client.post("/api/orders")
    assert response.status_code == 502
    assert "debug_id" not in response.text


def test_capture_saves_sample_and_returns_only_the_status(client, fake):
    response = client.post("/api/orders/ORDER12345A/capture")
    assert response.json() == {"status": "COMPLETED"}
    assert (config.SAMPLES_DIR / "capture-ORDER12345A.json").exists()


def test_capture_stores_the_order(client, fake):
    client.post("/api/orders/ORDER12345A/capture")
    assert main.store.get("ORDER12345A").amount_cents == 14900


def test_paid_order_without_ai_key_is_held_for_the_seller(client, fake):
    client.post("/api/orders/ORDER12345A/capture")
    saved = main.store.get_review("ORDER12345A")
    assert saved["action"] == "hold"
    assert saved["record"]["review"]["errors"] == ["ANTHROPIC_API_KEY is not set"]


def test_review_record_keeps_what_was_behind_the_decision(client, fake):
    client.post("/api/orders/ORDER12345A/capture")
    record = main.store.get_review("ORDER12345A")["record"]
    assert record["decision"]["model_band"] is None
    assert {signal["name"] for signal in record["signals"]} >= {"seller_protection", "order_value"}


def test_ai_review_is_used_when_available(client, fake, monkeypatch):
    def approve(_order, _found, _store, _client):
        return review.Review(model="test", band="low", reasons=["Fine"], explanation="Fine.")

    monkeypatch.setattr(review, "run", approve)
    client.post("/api/orders/ORDER12345A/capture")
    saved = main.store.get_review("ORDER12345A")
    assert saved["action"] == "ship"
    assert saved["record"]["review"]["explanation"] == "Fine."


def test_daily_limit_holds_orders_without_calling_claude(client, fake, monkeypatch):
    def must_not_run(*_args):
        raise AssertionError("Claude was called past the daily limit")

    monkeypatch.setattr(review, "run", must_not_run)
    monkeypatch.setattr(config, "DAILY_REVIEW_LIMIT", 1)
    main.store.add_review("EARLIER", "ship", "low", {})
    client.post("/api/orders/ORDER12345A/capture")
    saved = main.store.get_review("ORDER12345A")
    assert saved["action"] == "hold"
    assert "daily limit" in saved["record"]["review"]["errors"][0]


def test_reviews_older_than_a_day_do_not_count_toward_the_limit(client, fake, monkeypatch):
    calls = []

    def approve(*args):
        calls.append(args)
        return review.Review(model="test", band="low", reasons=["Fine"], explanation="Fine.")

    monkeypatch.setattr(review, "run", approve)
    monkeypatch.setattr(config, "DAILY_REVIEW_LIMIT", 1)
    main.store.add_review("YESTERDAY", "ship", "low", {}, now=time.time() - 90000)
    client.post("/api/orders/ORDER12345A/capture")
    assert len(calls) == 1


def test_store_failure_does_not_fail_a_buyer_who_has_paid(client, fake, monkeypatch):
    def fail(_order):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(main.store, "add", fail)
    assert client.post("/api/orders/ORDER12345A/capture").status_code == 200


def test_capture_refuses_a_malformed_order_id(client):
    assert client.post("/api/orders/bad-id!/capture").status_code == 400


def test_webhook_with_bad_signature_is_refused(client, fake, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ID", "WH-ID-1")
    assert post_event(client).status_code == 400
    assert fake.capture_calls == 0


def test_webhook_refused_when_webhook_id_is_not_set(client, fake, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_ID", "")
    assert post_event(client).status_code == 400


def test_certificate_fetch_failure_asks_paypal_to_retry(client, monkeypatch):
    def fail(*args, **kwargs):
        raise webhooks.requests.ConnectionError()

    monkeypatch.setattr(webhooks, "verify", fail)
    assert post_event(client).status_code == 503


def test_capture_event_is_reported(client, fake, genuine_webhooks):
    assert post_event(client).json() == {"received": True}
    sample = json.loads((config.SAMPLES_DIR / "WH-1.json").read_text(encoding="utf-8"))
    assert sample["capture"]["id"] == "CAPTURE12345"
    assert sample["order"]["id"] == "ORDER12345A"


def test_capture_event_stores_the_order(client, fake, genuine_webhooks):
    post_event(client)
    assert main.store.get("ORDER12345A") is not None


def test_capture_event_finds_the_order_through_the_up_link(client, fake, genuine_webhooks):
    fake.capture = {k: v for k, v in CAPTURE.items() if k != "supplementary_data"}
    fake.capture["links"] = [
        {"rel": "up", "href": "https://api.sandbox.paypal.com/v2/checkout/orders/ORDER12345A"}]
    post_event(client)
    assert main.store.get("ORDER12345A") is not None


def test_order_reported_by_both_paths_is_stored_once(client, fake, genuine_webhooks):
    client.post("/api/orders/ORDER12345A/capture")
    post_event(client)
    assert main.store.count() == 1


def test_repeated_event_is_reported_once(client, fake, genuine_webhooks):
    post_event(client)
    post_event(client)
    assert fake.capture_calls == 1


def test_other_events_are_acknowledged_and_skipped(client, fake, genuine_webhooks):
    response = post_event(client, {**EVENT, "event_type": "CUSTOMER.DISPUTE.CREATED"})
    assert response.status_code == 200
    assert fake.capture_calls == 0


def test_event_with_unsafe_ids_is_ignored(client, fake, genuine_webhooks):
    post_event(client, {**EVENT, "id": "../../evil"})
    assert fake.capture_calls == 0
    assert not config.SAMPLES_DIR.exists()


# â”€â”€ seller screen â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def test_seller_screen_is_served(client):
    response = client.get("/seller")
    assert response.status_code == 200
    assert "ShipSafe orders" in response.text


def test_seller_screen_never_writes_html_from_data():
    page = (main.STATIC_DIR / "seller.html").read_text(encoding="utf-8")
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page


def test_queue_shows_the_decision_and_what_the_ai_checked(client, fake, monkeypatch):
    transcript = [
        {"role": "user", "content": "Review this order."},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "tool_use", "id": "t1", "name": "get_buyer_history", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "get_shop_baseline", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[]"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t3", "name": "submit_review", "input": {}}]},
    ]

    def approve(*_args):
        return review.Review(model="test", band="low", reasons=["First order"], explanation="Fine.",
                             buyer_message="Hello", transcript=transcript)

    monkeypatch.setattr(review, "run", approve)
    client.post("/api/orders/ORDER12345A/capture")
    [entry] = client.get("/api/reviews").json()["orders"]
    assert entry["order_id"] == "ORDER12345A"
    assert entry["action"] == "ship"
    assert entry["amount"] == "149.00"
    assert entry["buyer"]["email"] == "buyer@example.com"
    assert entry["review"]["explanation"] == "Fine."
    assert entry["review"]["looked_at"] == ["get_buyer_history", "get_shop_baseline"]
    assert entry["review"]["buyer_message"] == ""   # only offered when contacting the buyer
    assert "transcript" not in entry["review"]
    assert entry["decision"]["reason"]


def test_queue_offers_the_buyer_message_when_contacting(client):
    main.store.add(make_order())
    main.store.add_review("ORDER12345A", "contact_buyer", "medium",
                          {"review": {"buyer_message": "Could you confirm your address?"}})
    [entry] = client.get("/api/reviews").json()["orders"]
    assert entry["review"]["buyer_message"] == "Could you confirm your address?"


def test_queue_shows_a_failed_review_as_held(client, fake):
    client.post("/api/orders/ORDER12345A/capture")   # no Claude client in tests
    [entry] = client.get("/api/reviews").json()["orders"]
    assert entry["action"] == "hold"
    assert entry["decision"]["model_band"] is None
    assert entry["review"]["errors"]


def test_queue_skips_reviews_without_a_stored_order(client):
    main.store.add_review("MISSING", "ship", "low", {})
    assert client.get("/api/reviews").json()["orders"] == []


def test_queue_is_newest_first(client):
    main.store.add(make_order())
    main.store.add(make_order(order_id="LATERORDER1", capture_id="LATERCAP1"))
    main.store.add_review("ORDER12345A", "ship", "low", {}, now=100)
    main.store.add_review("LATERORDER1", "ship", "low", {}, now=200)
    entries = client.get("/api/reviews").json()["orders"]
    assert [e["order_id"] for e in entries] == ["LATERORDER1", "ORDER12345A"]
