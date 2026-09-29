"""Find the cheapest split using live bills, quoting only the baskets that matter.

There are 2**n possible splits and every quote touches the real cart, so we don't quote
them all. Instead:

1. quote each app's full basket (everything it sells from the list);
2. optimise, estimating un-quoted baskets from the nearest quoted basket on that app
   (by subtotal -- fees mostly depend on it);
3. quote any basket in the winning plan that has no live bill yet, then optimise again;
4. stop when the winning plan is made only of live-quoted baskets.

If an app can't be quoted at all (full-basket quote fails), its fees fall back to the
estimate from past orders and the output says so.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

import anyio

from grocer.optimizer import FeeSchedule, Plan, optimize
from grocer.quote import Quote

MAX_ROUNDS = 6

Quoter = Callable[[frozenset[str]], Awaitable[Quote]]  # item names -> live bill for that basket


@dataclass
class LiveFees:
    quoters: dict[str, Quoter]
    fallback: dict[str, FeeSchedule]  # used for apps that can't be quoted
    quotes: dict[tuple[str, frozenset[str]], Quote | None] = field(default_factory=dict)  # None = quote failed
    broken: dict[str, str] = field(default_factory=dict)  # app -> why live quotes are unavailable
    log: list[str] = field(default_factory=list)

    def is_live(self, platform: str, names: frozenset[str]) -> bool:
        return self.quotes.get((platform, names)) is not None

    def fee_fn(self, platform: str, names: frozenset[str], subtotal: float):
        if platform in self.broken or platform not in self.quoters:
            sched = self.fallback.get(platform, FeeSchedule())
            return None if subtotal < sched.min_order else sched.fees_for(subtotal)
        key = (platform, names)
        if key in self.quotes:
            q = self.quotes[key]
            return None if q is None else list(q.fees)  # a failed quote = basket can't be ordered
        known = [q for (p, _), q in self.quotes.items() if p == platform and q is not None]
        if not known:
            return []
        above = [q for q in known if q.item_total >= subtotal]
        nearest = min(above, key=lambda q: q.item_total) if above else max(known, key=lambda q: q.item_total)
        return list(nearest.fees)

    async def quote(self, platform: str, names: frozenset[str]) -> None:
        key = (platform, names)
        if key in self.quotes or platform in self.broken or platform not in self.quoters:
            return
        try:
            q = await self.quoters[platform](names)
        except Exception as e:  # noqa: BLE001 -- any failure: record it, don't abort the run
            self.quotes[key] = None
            self.log.append(f"{platform}: quote for {len(names)} item(s) failed: {e}")
            return
        self.quotes[key] = q
        self.log.append(f"{platform}: live bill for {len(names)} item(s): items ₹{q.item_total:g}, "
                        f"fees ₹{q.fee_total:g} ({', '.join(f'{l} ₹{a:g}' for l, a in q.fees) or 'none'})")


    async def quote_many(self, pairs: Iterable[tuple[str, frozenset[str]]]) -> None:
        """Quote several baskets: apps in parallel, each app's baskets one after another
        (a quote fills that app's single cart, so two quotes on one app can't overlap)."""
        by_app: dict[str, list[frozenset[str]]] = {}
        for p, names in pairs:
            if names and names not in by_app.setdefault(p, []):
                by_app[p].append(names)

        async def one_app(p: str, baskets: list[frozenset[str]]) -> None:
            for names in baskets:
                await self.quote(p, names)

        async with anyio.create_task_group() as tg:
            for p, baskets in by_app.items():
                tg.start_soon(one_app, p, baskets)


async def live_optimize(
    items: list[tuple[str, int]],
    prices: dict[str, dict[str, float]],
    scales: dict[str, dict[str, float]],
    live: LiveFees,
) -> Plan | None:
    fulls = {p: frozenset(n for n, _ in items if p in prices.get(n, {})) for p in live.quoters}
    await live.quote_many((p, names) for p, names in fulls.items() if names)
    for platform, full in fulls.items():
        if full and live.quotes.get((platform, full)) is None:
            live.broken[platform] = "live quote failed; fees estimated from past orders"

    plan = None
    for _ in range(MAX_ROUNDS):
        plan = optimize(items, prices, live.fee_fn, scales)
        if plan is None:
            return None
        pending = [(p, frozenset(n for n, _, _ in b.lines)) for p, b in plan.baskets.items()]
        pending = [(p, names) for p, names in pending if (p, names) not in live.quotes
                   and p in live.quoters and p not in live.broken]
        if not pending:
            return plan
        await live.quote_many(pending)
    return optimize(items, prices, live.fee_fn, scales)
