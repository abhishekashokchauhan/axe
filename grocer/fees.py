"""Learn each platform's fees from what your own past orders were actually charged.

Neither MCP exposes a fee policy, and a live bill needs items in the cart. Past orders
are read-only and reflect your address and membership, so each run derives a fee
schedule from them:

- a fee charged on every order                  -> always charged (median amount)
- a fee that was ₹0 on some orders               -> charged below the smallest item total
                                                    where it was ₹0 (free-delivery threshold)
- a fee never charged                            -> ignored

Fees are taken as listed on the bill, before any fee discount, so the estimate errs high.
"""

import statistics
from dataclasses import dataclass

from grocer.optimizer import Fee, FeeSchedule


@dataclass(frozen=True)
class Observation:
    item_total: float
    fees: dict[str, float]  # fee name -> amount charged (0 if waived)


@dataclass(frozen=True)
class LearnedFees:
    schedule: FeeSchedule
    summary: str


def _label(key: str) -> str:
    return "".join(" " + c.lower() if c.isupper() else c for c in key).strip()


def learn(observations: list[Observation]) -> LearnedFees:
    if not observations:
        return LearnedFees(FeeSchedule(), "no past orders found; assuming no fees")
    totals = [o.item_total for o in observations]
    span = f"{len(observations)} past orders, item totals ₹{min(totals):g}-₹{max(totals):g}"
    names = sorted({n for o in observations for n in o.fees})
    fees, notes = [], []
    for name in names:
        charged = [o.fees[name] for o in observations if o.fees.get(name, 0) > 0]
        if not charged:
            continue
        amount = round(statistics.median(charged), 2)
        free_at = [o.item_total for o in observations if o.fees.get(name, 0) == 0]
        below = min(free_at) if free_at else None
        fees.append(Fee(_label(name), amount, below))
        cond = f"below ₹{below:g}" if below is not None else "on every order"
        spread = f" (seen ₹{min(charged):g}-₹{max(charged):g})" if min(charged) != max(charged) else ""
        notes.append(f"{_label(name)} ₹{amount:g} {cond}{spread}")
    if not fees:
        return LearnedFees(FeeSchedule(), f"no fees charged in your {span}")
    return LearnedFees(FeeSchedule(fees=tuple(fees)), f"{'; '.join(notes)} -- from your {span}")
