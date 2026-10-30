"""
Thin client for the PayPal REST calls ShipSafe uses.

Responses come back as plain dicts. Captures ask for the full representation, because
a minimal response leaves out the seller_protection fields the review depends on.
"""
from __future__ import annotations

import time

import requests

from . import config

TIMEOUT = 20  # seconds
TOKEN_MARGIN = 60  # renew this many seconds before the token expires
FULL_RESPONSE = {"Prefer": "return=representation"}


class PayPalError(Exception):
    """A PayPal call failed. The message carries PayPal's debug_id, never credentials."""


class PayPal:
    def __init__(self, client_id: str, client_secret: str, base_url: str = config.PAYPAL_API):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url
        self._token = ""
        self._token_expires = 0.0

    def create_order(self, item: dict) -> dict:
        """item: name, sku and price (a string such as "149.00"), set by the server."""
        money = {"currency_code": "USD", "value": item["price"]}
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {**money, "breakdown": {"item_total": money}},
                "items": [{
                    "name": item["name"],
                    "sku": item["sku"],
                    "quantity": "1",
                    "category": "PHYSICAL_GOODS",
                    "unit_amount": money,
                }],
            }],
        }
        return self._call("POST", "/v2/checkout/orders", json=body, headers=FULL_RESPONSE)

    def capture_order(self, order_id: str) -> dict:
        # The request id makes a repeated capture of the same order safe.
        headers = {**FULL_RESPONSE, "PayPal-Request-Id": f"capture-{order_id}"}
        return self._call("POST", f"/v2/checkout/orders/{order_id}/capture", json={}, headers=headers)

    def get_order(self, order_id: str) -> dict:
        return self._call("GET", f"/v2/checkout/orders/{order_id}")

    def get_capture(self, capture_id: str) -> dict:
        return self._call("GET", f"/v2/payments/captures/{capture_id}")

    # ── internals ──────────────────────────────────────────────────────
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        try:
            r = requests.post(f"{self.base_url}/v1/oauth2/token",
                              auth=(self.client_id, self.client_secret),
                              data={"grant_type": "client_credentials"}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PayPalError(f"token request failed: {type(exc).__name__}") from None
        if not r.ok:
            raise PayPalError(f"token request refused: HTTP {r.status_code}")
        body = r.json()
        self._token = body["access_token"]
        self._token_expires = time.time() + int(body["expires_in"]) - TOKEN_MARGIN
        return self._token

    def _call(self, method: str, path: str, json: dict | None = None,
              headers: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}", **(headers or {})}
        try:
            r = requests.request(method, f"{self.base_url}{path}", json=json, headers=headers,
                                 timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PayPalError(f"{method} {path} failed: {type(exc).__name__}") from None
        if not r.ok:
            debug_id = r.headers.get("paypal-debug-id", "none")
            raise PayPalError(f"{method} {path}: HTTP {r.status_code}, debug_id {debug_id}")
        return r.json()
