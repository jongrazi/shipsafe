"""Field reports and web routes, with PayPal replaced by a fake."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, field_report, main, webhooks
from app.paypal import PayPalError
from app.store import Store
from tests.helpers import CAPTURE, ORDER

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
