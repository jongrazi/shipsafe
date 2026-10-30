"""
PayPal webhook verification, using PayPal's self-verification method.

PayPal signs "transmissionId|timeStamp|webhookId|crc32" with the key of the certificate
named in the paypal-cert-url header. That header comes from the sender, so the URL must
point at PayPal before anything is fetched from it.
"""
from __future__ import annotations

import base64
import binascii
import zlib
from datetime import datetime, timezone
from typing import Callable, Mapping
from urllib.parse import urlsplit

import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature as BadSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

TIMEOUT = 10  # seconds
_certs: dict[str, bytes] = {}  # certificates by URL, cached as PayPal advises


class InvalidSignature(Exception):
    """The webhook did not prove it came from PayPal."""


def cert_url_is_paypal(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "paypal.com" or host.endswith(".paypal.com"))


def fetch_cert(url: str) -> bytes:
    if url not in _certs:
        # No redirects: an allowed URL must not be able to send us somewhere else.
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
        r.raise_for_status()
        _certs[url] = r.content
    return _certs[url]


def signed_message(raw_body: bytes, transmission_id: str, transmission_time: str,
                   webhook_id: str) -> bytes:
    return f"{transmission_id}|{transmission_time}|{webhook_id}|{zlib.crc32(raw_body)}".encode()


def verify(raw_body: bytes, headers: Mapping[str, str], webhook_id: str,
           get_cert: Callable[[str], bytes] = fetch_cert) -> None:
    """
    Return if the webhook is genuine; raise InvalidSignature if it is not.

    raw_body must be the unparsed request body. A network failure while fetching the
    certificate raises requests.RequestException, so the caller can ask PayPal to retry.
    """
    if not webhook_id:
        raise InvalidSignature("PAYPAL_WEBHOOK_ID is not set")
    transmission_id = headers.get("paypal-transmission-id")
    transmission_time = headers.get("paypal-transmission-time")
    cert_url = headers.get("paypal-cert-url")
    signature = headers.get("paypal-transmission-sig")
    if not (transmission_id and transmission_time and cert_url and signature):
        raise InvalidSignature("missing signature headers")
    if not cert_url_is_paypal(cert_url):
        raise InvalidSignature("certificate URL is not PayPal's")

    try:
        cert = x509.load_pem_x509_certificate(get_cert(cert_url))
        signature_bytes = base64.b64decode(signature, validate=True)
    except (ValueError, binascii.Error):
        raise InvalidSignature("malformed certificate or signature") from None
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        raise InvalidSignature("certificate is outside its validity period")

    message = signed_message(raw_body, transmission_id, transmission_time, webhook_id)
    try:
        cert.public_key().verify(signature_bytes, message, padding.PKCS1v15(), hashes.SHA256())
    except (BadSignature, TypeError, ValueError):
        raise InvalidSignature("signature does not match") from None
