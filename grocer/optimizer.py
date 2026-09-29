"""Choose which platform to buy each item on so the total bill is lowest.

Buying each item where it is cheapest is optimal only when fees don't depend on the
basket. They do: a small order on one platform can pick up small-cart/delivery fees
larger than the per-item saving. With 2 platforms and ~10 items there are only
2**10 = 1024 splits, so we evaluate every one and keep the cheapest -- exact, no
heuristics.

Pack sizes can differ between apps (Instamart 4 x 100 g vs Zepto 500 g), so splits are
ranked by *value*: each line's cost scaled to the quantity you asked for, plus fees. The
printed bill and the fee thresholds use the real pack prices.
"""

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

EXHAUSTIVE_LIMIT = 1 << 15  # try every split up to this many (15 items sold on both apps); search beyond


@dataclass(frozen=True)
class Fee:
    name: str
    amount: float
    below: float | None = None  # charged only when subtotal < below; None = always

    def applies(self, subtotal: float) -> bool:
        return self.below is None or subtotal < self.below


@dataclass(frozen=True)
class FeeSchedule:
    min_order: float = 0.0  # hard minimum: the platform won't check out below this
    fees: tuple[Fee, ...] = ()

    def fees_for(self, subtotal: float) -> list[tuple[str, float]]:
        return [(f.name, f.amount) for f in self.fees if f.applies(subtotal)]


@dataclass
class Basket:
    platform: str
    lines: list[tuple[str, int, float]] = field(default_factory=list)  # (item, qty, line total)
    fees: list[tuple[str, float]] = field(default_factory=list)

    @property
    def subtotal(self) -> float:
        return sum(t for _, _, t in self.lines)

    @property
    def total(self) -> float:
        return self.subtotal + sum(a for _, a in self.fees)


@dataclass
class Plan:
    baskets: dict[str, Basket]
    unavailable: list[str]
    value: float = 0.0  # bill with every line scaled to the requested quantity; what we minimise

    @property
    def total(self) -> float:
        return sum(b.total for b in self.baskets.values())


# (platform, item names in that basket, basket subtotal) -> fee lines, or None if that
# basket can't be ordered (e.g. below a minimum order value).
FeeFn = Callable[[str, frozenset[str], float], list[tuple[str, float]] | None]


def schedule_fees(schedules: dict[str, FeeSchedule]) -> FeeFn:
    """Fee function from fixed per-platform schedules (set by hand or estimated)."""
    def fees(platform: str, names: frozenset[str], subtotal: float):
        sched = schedules.get(platform, FeeSchedule())
        return None if subtotal < sched.min_order else sched.fees_for(subtotal)
    return fees


def _scale(scales, name, platform) -> float:
    return (scales or {}).get(name, {}).get(platform, 1.0)


def _evaluate(assignment, items, prices, fee_fn: FeeFn, scales=None) -> Plan | None:
    baskets: dict[str, Basket] = {}
    value = 0.0
    for (name, qty), platform in zip(items, assignment):
        line = prices[name][platform] * qty
        baskets.setdefault(platform, Basket(platform)).lines.append((name, qty, line))
        value += line * _scale(scales, name, platform)
    for b in baskets.values():
        fees = fee_fn(b.platform, frozenset(n for n, _, _ in b.lines), b.subtotal)
        if fees is None:
            return None
        b.fees = fees
        value += sum(a for _, a in b.fees)
    return Plan(baskets, [], value)


def optimize(
    items: list[tuple[str, int]],
    prices: dict[str, dict[str, float]],
    fee_fn: FeeFn,
    scales: dict[str, dict[str, float]] | None = None,
) -> Plan | None:
    """Minimum-value plan: exact (every split tried) for lists up to EXHAUSTIVE_LIMIT splits,
    otherwise a local search from good starting plans (see _local_search).

    items: [(item name, qty)]; prices: item -> {platform: pack price} (only platforms
    where the chosen pack is in stock); scales: item -> {platform: requested amount /
    pack amount} (default 1). Returns None if no split satisfies every platform's hard
    minimum order.
    """
    buyable = [(n, q) for n, q in items if prices.get(n)]
    unavailable = [n for n, _ in items if not prices.get(n)]
    choices = [sorted(prices[n]) for n, _ in buyable]
    n_combos = 1
    for c in choices:
        n_combos *= len(c)
    if n_combos > EXHAUSTIVE_LIMIT:
        best = _local_search(buyable, choices, prices, fee_fn, scales)
        if best is not None:
            best.unavailable = unavailable
        return best

    best: Plan | None = None
    best_key = None
    for assignment in itertools.product(*choices):
        plan = _evaluate(assignment, buyable, prices, fee_fn, scales)
        if plan is None:
            continue
        # Tie-break: fewer deliveries, then alphabetical for determinism.
        key = (round(plan.value, 2), len(plan.baskets), assignment)
        if best_key is None or key < best_key:
            best, best_key = plan, key
    if best is not None:
        best.unavailable = unavailable
    return best


def _local_search(buyable, choices, prices, fee_fn, scales) -> Plan | None:
    """For long lists: start from sensible plans -- each item where it's best value, and "all
    you can on app X" for every app (which is how fees are usually avoided) -- then keep
    moving one item, or two together (e.g. to get a basket past a free-delivery threshold),
    to another app while that lowers the total. Keeps the best."""
    def best_for(i):
        n = buyable[i][0]
        return min(choices[i], key=lambda p: (prices[n][p] * _scale(scales, n, p), p))

    apps = sorted({p for c in choices for p in c})
    starts = [[best_for(i) for i in range(len(buyable))]]
    starts += [[p if p in choices[i] else best_for(i) for i in range(len(buyable))] for p in apps]
    best, best_key = None, None
    for assignment in starts:
        plan = _evaluate(assignment, buyable, prices, fee_fn, scales)
        improved = True
        while improved:
            improved = False
            for moves in (_single_moves, _pair_moves):
                for trial in moves(assignment, choices):
                    cand = _evaluate(trial, buyable, prices, fee_fn, scales)
                    if cand is not None and (plan is None or cand.value < plan.value - 1e-9):
                        assignment, plan, improved = trial, cand, True
                if improved:
                    break  # restart with single moves after any improvement
        if plan is not None:
            key = (round(plan.value, 2), len(plan.baskets), tuple(assignment))
            if best_key is None or key < best_key:
                best, best_key = plan, key
    return best


def _single_moves(a, choices):
    for i in range(len(a)):
        for p in choices[i]:
            if p != a[i]:
                yield a[:i] + [p] + a[i + 1:]


def _pair_moves(a, choices):
    for i in range(len(a)):
        for j in range(i + 1, len(a)):
            for p in choices[i]:
                for q in choices[j]:
                    if p != a[i] and q != a[j]:
                        t = a.copy()
                        t[i], t[j] = p, q
                        yield t


def greedy(items, prices, fee_fn: FeeFn, scales=None) -> Plan | None:
    """Best-value-platform-per-item, fees included -- shown alongside for comparison."""
    buyable = [(n, q) for n, q in items if prices.get(n)]
    assignment = [min(prices[n], key=lambda p: (prices[n][p] * _scale(scales, n, p), p)) for n, _ in buyable]
    plan = _evaluate(assignment, buyable, prices, fee_fn, scales)
    if plan is not None:
        plan.unavailable = [n for n, _ in items if not prices.get(n)]
    return plan
