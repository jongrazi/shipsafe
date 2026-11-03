"""
The decision: plain code, no AI. The model proposes a risk band and this file decides.

The rules set a minimum band from the signals, and the model can only raise it. Each band
maps to a fixed fraud probability p. With L the money at risk after Seller Protection and
c the cost of delaying a good order, holding pays when

    p * L > (1 - p) * c,  which is  p > c / (L + c)

So a cheap order with a mild flag ships, and an expensive one with the same flag doesn't.
With no usable AI review, the order is held for the seller.

The numbers below are starting assumptions, to be tuned on the tuning orders only.
PROTECTION_COVER stays below 1 because PayPal decides each claim itself.
"""
from __future__ import annotations

from dataclasses import dataclass

from .orders import PaidOrder
from .signals import Signal

BANDS = ("low", "medium", "high")
BAND_PROBABILITY = {"low": 0.01, "medium": 0.10, "high": 0.40}
PROTECTION_COVER = {"ELIGIBLE": 0.8, "PARTIALLY_ELIGIBLE": 0.4}  # share of the loss assumed covered
DELAY_COST_CENTS = 500


@dataclass(frozen=True)
class Decision:
    action: str               # ship | contact_buyer | hold
    band: str
    rule_band: str
    model_band: str | None    # None when the AI review failed
    probability: float
    money_at_risk_cents: int
    hold_above: float         # the probability above which holding pays
    reason: str


def rule_band(signals: list[Signal]) -> str:
    risks = sum(signal.level == "risk" for signal in signals)
    watches = sum(signal.level == "watch" for signal in signals)
    if risks >= 2:
        return "high"
    if risks == 1 or watches >= 2:
        return "medium"
    return "low"


def money_at_risk(order: PaidOrder) -> int:
    cover = PROTECTION_COVER.get(order.seller_protection, 0.0)
    return round(order.amount_cents * (1 - cover))


def decide(order: PaidOrder, signals: list[Signal], model_band: str | None) -> Decision:
    floor = rule_band(signals)
    band = floor if model_band is None else BANDS[max(BANDS.index(floor), BANDS.index(model_band))]
    probability = BAND_PROBABILITY[band]
    at_risk = money_at_risk(order)
    hold_above = DELAY_COST_CENTS / (at_risk + DELAY_COST_CENTS)

    if model_band is None:
        action = "hold"
        reason = "The AI review could not be completed, so this order needs the seller's own check."
    elif probability <= hold_above:
        action = "ship"
        reason = (f"Estimated risk of {probability:.0%} is below the {hold_above:.0%} that would "
                  f"justify a delay, with ${at_risk / 100:.2f} at risk.")
    else:
        action = "hold" if band == "high" else "contact_buyer"
        reason = (f"Estimated risk of {probability:.0%} is above the {hold_above:.0%} that would "
                  f"justify a delay, with ${at_risk / 100:.2f} at risk.")
    return Decision(action, band, floor, model_band, probability, at_risk, hold_above, reason)
