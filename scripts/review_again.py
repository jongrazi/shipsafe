"""
Runs the AI review again on a stored order with the current prompt, and prints the result.
Use it to check a prompt change without a new purchase. Nothing is saved: an order's
stored review is never rewritten. Each run is a real API call and costs money.

    python scripts/review_again.py ORDER_ID
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, policy, review, signals  # noqa: E402
from app.store import Store  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    store = Store(config.DB_PATH)
    order = store.get(sys.argv[1])
    if order is None:
        sys.exit(f"No stored order {sys.argv[1]}")
    client = review.make_client()
    if client is None:
        sys.exit("ANTHROPIC_API_KEY is not set")

    found = signals.build(order, store)
    result = review.run(order, found, store, client)
    decision = policy.decide(order, found, result.band)
    print(f"{decision.action.upper()} (band {decision.band}, rules {decision.rule_band}, "
          f"model {decision.model_band})")
    print(f"Why: {decision.reason}")
    print(f"Explanation: {result.explanation or '(none)'}")
    for reason in result.reasons:
        print(f"  - {reason}")
    if result.buyer_message:
        print(f"Draft to buyer: {result.buyer_message}")
    if result.errors:
        print(f"Errors: {result.errors}")
    cost = review.estimated_cost(result.model, result.usage)
    print(f"{result.model}, estimated {'unknown' if cost is None else f'${cost:.4f}'}")


if __name__ == "__main__":
    main()
