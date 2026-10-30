"""Webhook signature checks, using a locally made key and certificate."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from app import webhooks

BODY = b'{"id":"WH-1","event_type":"PAYMENT.CAPTURE.COMPLETED"}'
WEBHOOK_ID = "WH-ID-1"
CERT_URL = "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1"


def make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_cert(key, not_before: datetime, not_after: datetime) -> bytes:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(not_before).not_valid_after(not_after)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def key():
    return make_key()


@pytest.fixture(scope="module")
def cert(key):
    now = datetime.now(timezone.utc)
    return make_cert(key, now - timedelta(days=1), now + timedelta(days=1))


def signed_headers(key, body: bytes = BODY, webhook_id: str = WEBHOOK_ID) -> dict:
    message = webhooks.signed_message(body, "tx-1", "2026-10-31T00:00:00Z", webhook_id)
    signature = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return {"paypal-transmission-id": "tx-1",
            "paypal-transmission-time": "2026-10-31T00:00:00Z",
            "paypal-cert-url": CERT_URL,
            "paypal-transmission-sig": base64.b64encode(signature).decode()}


def test_accepts_genuine_webhook(key, cert):
    webhooks.verify(BODY, signed_headers(key), WEBHOOK_ID, lambda url: cert)


def test_refuses_changed_body(key, cert):
    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY + b" ", signed_headers(key), WEBHOOK_ID, lambda url: cert)


def test_refuses_other_webhook_id(key, cert):
    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY, signed_headers(key), "WH-ID-2", lambda url: cert)


def test_refuses_signature_from_other_key(cert):
    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY, signed_headers(make_key()), WEBHOOK_ID, lambda url: cert)


def test_refuses_when_webhook_id_is_not_set(key, cert):
    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY, signed_headers(key, webhook_id=""), "", lambda url: cert)


@pytest.mark.parametrize("header", ["paypal-transmission-id", "paypal-transmission-time",
                                    "paypal-cert-url", "paypal-transmission-sig"])
def test_refuses_missing_header(key, cert, header):
    headers = signed_headers(key)
    del headers[header]
    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY, headers, WEBHOOK_ID, lambda url: cert)


@pytest.mark.parametrize("url", ["http://api.paypal.com/cert",
                                 "https://paypal.com.evil.test/cert",
                                 "https://evilpaypal.com/cert",
                                 "https://paypal.com@evil.test/cert",
                                 "https://169.254.169.254/latest"])
def test_never_fetches_certificate_from_other_hosts(key, url):
    headers = {**signed_headers(key), "paypal-cert-url": url}

    def must_not_fetch(_url):
        raise AssertionError("fetched a non-PayPal URL")

    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY, headers, WEBHOOK_ID, must_not_fetch)


def test_refuses_expired_certificate(key):
    now = datetime.now(timezone.utc)
    expired = make_cert(key, now - timedelta(days=3), now - timedelta(days=1))
    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY, signed_headers(key), WEBHOOK_ID, lambda url: expired)


def test_refuses_malformed_certificate(key):
    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY, signed_headers(key), WEBHOOK_ID, lambda url: b"not a certificate")


def test_refuses_malformed_signature(key, cert):
    headers = {**signed_headers(key), "paypal-transmission-sig": "not base64!"}
    with pytest.raises(webhooks.InvalidSignature):
        webhooks.verify(BODY, headers, WEBHOOK_ID, lambda url: cert)
