"""
Order store (SQLite).

PayPal offers no live lookup of a buyer's past orders, so ShipSafe keeps its own. Every
history query takes the order under review and only returns orders from before it, so
replaying a past order can't see what happened after it. That guarantee lives here.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .orders import PaidOrder

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id              TEXT PRIMARY KEY,
    capture_id            TEXT NOT NULL,
    payer_id              TEXT NOT NULL,
    email                 TEXT NOT NULL,
    payer_name            TEXT NOT NULL,
    ship_name             TEXT NOT NULL,
    ship_address          TEXT NOT NULL,
    address_key           TEXT NOT NULL,
    buyer_country         TEXT NOT NULL,
    ship_country          TEXT NOT NULL,
    amount_cents          INTEGER NOT NULL,
    currency              TEXT NOT NULL,
    seller_protection     TEXT NOT NULL,
    protection_categories TEXT NOT NULL,   -- JSON list
    account_status        TEXT NOT NULL,
    created_at            REAL NOT NULL,   -- epoch s
    disputed              INTEGER NOT NULL,
    source                TEXT NOT NULL    -- paypal | seed
);
CREATE INDEX IF NOT EXISTS idx_orders_payer ON orders(payer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_address ON orders(address_key, created_at);
"""
DAY = 86400  # seconds


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        # One connection per use: requests and background tasks run on different threads.
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:  # commits, or rolls back on an exception
                yield db
        finally:
            db.close()

    def add(self, order: PaidOrder) -> bool:
        """Store an order. False if it was already there: PayPal can report one twice."""
        fields = asdict(order)
        fields["protection_categories"] = json.dumps(order.protection_categories)
        query = (f"INSERT OR IGNORE INTO orders ({','.join(fields)}) "
                 f"VALUES ({','.join('?' * len(fields))})")
        with self._db() as db:
            return db.execute(query, list(fields.values())).rowcount == 1

    def seed(self, rows: list[dict], now: float | None = None) -> int:
        """Load synthetic history, dated relative to now. Returns how many rows were new."""
        now = time.time() if now is None else now
        added = 0
        for row in rows:
            fields = {k: v for k, v in row.items() if k != "days_ago"}
            fields["protection_categories"] = tuple(fields["protection_categories"])
            added += self.add(PaidOrder(**fields, created_at=now - row["days_ago"] * DAY,
                                        source="seed"))
        return added

    def get(self, order_id: str) -> PaidOrder | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return _to_order(row) if row else None

    def count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    # ── history, always from before the order under review ─────────────
    def buyer_orders(self, order: PaidOrder) -> list[PaidOrder]:
        """Earlier orders from the same PayPal account or email, newest first."""
        return self._earlier(order, "((payer_id = ? AND payer_id != '') OR (email = ? AND email != ''))",
                             (order.payer_id, order.email))

    def address_orders(self, order: PaidOrder) -> list[PaidOrder]:
        """Earlier orders shipped to the same address, newest first."""
        return self._earlier(order, "(address_key = ? AND address_key != '')", (order.address_key,))

    def shop_baseline(self, order: PaidOrder) -> tuple[int, float]:
        """(number of earlier orders, their median amount in cents) in this order's currency."""
        amounts = [o.amount_cents for o in self._earlier(order, "currency = ?", (order.currency,))]
        return len(amounts), statistics.median(amounts) if amounts else 0.0

    def _earlier(self, order: PaidOrder, condition: str, params: tuple) -> list[PaidOrder]:
        query = (f"SELECT * FROM orders WHERE {condition} AND created_at < ? AND order_id != ? "
                 "ORDER BY created_at DESC")
        with self._db() as db:
            rows = db.execute(query, (*params, order.created_at, order.order_id)).fetchall()
        return [_to_order(row) for row in rows]


def _to_order(row: sqlite3.Row) -> PaidOrder:
    fields = dict(row)
    fields["protection_categories"] = tuple(json.loads(fields["protection_categories"]))
    fields["disputed"] = bool(fields["disputed"])
    return PaidOrder(**fields)
