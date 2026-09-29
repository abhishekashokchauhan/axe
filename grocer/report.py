"""What a `compare` run prints at the end: the carts side by side, how each total is made up,
insights (MRP savings, single-app alternatives), and what to do next."""

from dataclasses import dataclass
from itertools import zip_longest

from grocer.advisor import Decision
from grocer.optimizer import Plan
from grocer.quote import Quote
from grocer.units import parse_size

COL = 50  # width of one cart column
APP_NAMES = {"zepto": "Zepto", "instamart": "Swiggy Instamart"}


@dataclass(frozen=True)
class Scenario:
    """Buying the whole list on one app (from that app's live full-basket quote)."""

    platform: str
    total: float | None  # None: not possible (missing items) or quote failed
    missing: tuple[str, ...] = ()


def _money(x: float) -> str:
    return f"₹{x:,.2f}".replace(".00", "")


def _row(label: str, amount: str, width: int = COL) -> str:
    room = width - len(amount) - 1
    label = label if len(label) <= room else label[: room - 1] + "…"
    return f"{label:<{room}} {amount}"


def _column(platform: str, plan: Plan, bills: dict[str, Quote], decisions: dict[str, Decision]) -> list[str]:
    title = f"{APP_NAMES.get(platform, platform).upper()}"
    basket = plan.baskets.get(platform)
    if basket is None:
        return [title, "-" * COL, "(nothing to order here)"]
    out = [title, "-" * COL]
    for name, qty, line in basket.lines:
        pack = decisions[name].picks[platform]
        out.append(_row(f"{qty * pack.packs} x {pack.offer.name}", _money(line)))
        if not pack.exact:
            note = "  (nearest size)"
        elif pack.packs > 1:
            note = f"  ({pack.packs} packs = your {decisions[name].item.size} each)" if qty > 1 \
                else f"  ({pack.packs} packs = your {decisions[name].item.size})"
        else:
            note = ""
        out.append(f"    {pack.offer.size_text}{note}")
    bill = bills.get(platform)
    out.append("-" * COL)
    if bill is None:
        out.append(_row("Items", _money(basket.subtotal)))
        out += [_row(f"+ {label}", _money(a)) for label, a in basket.fees]
        out.append(_row("TO PAY (estimated)", _money(basket.total)))
    else:
        out.append(_row("Items", _money(bill.item_total)))
        out += [_row(f"+ {label}", "FREE" if a == 0 else _money(a)) for label, a in bill.fees]
        to_pay = bill.to_pay if bill.to_pay is not None else bill.item_total + bill.fee_total
        out.append(_row("TO PAY (live bill)", _money(to_pay)))
        if abs(bill.item_total - basket.subtotal) >= 1:
            # Search price and cart price disagree (price changed, or a different pack was carted).
            out.append(f"! cart items ₹{bill.item_total:g} vs search ₹{basket.subtotal:g}: check in the app")
    return out


def to_pay(platform: str, plan: Plan, bills: dict[str, Quote]) -> float:
    bill = bills.get(platform)
    if bill is None:
        return plan.baskets[platform].total
    return bill.to_pay if bill.to_pay is not None else bill.item_total + bill.fee_total


def _amount(dim: str, base: float) -> str:
    """Base units (g / ml / pc) -> "500 g", "1.35 L", "2 pcs"."""
    if dim == "mass":
        return f"{base / 1000:g} kg" if base >= 1000 else f"{base:g} g"
    if dim == "volume":
        return f"{base / 1000:g} L" if base >= 1000 else f"{base:g} ml"
    return f"{base:g} pc{'s' if base != 1 else ''}"


def _for_your_qty(d: Decision, c) -> float:
    """What this pack costs scaled to the quantity on your list (fair across pack sizes)."""
    return c.offer.price * (parse_size(d.item.size)[1] / c.amount) * d.item.qty


def _insights(plan, decisions, by_name, bills, scenarios, naive_total, grand, fees) -> list[str]:
    bought = [(n, p, by_name[n].picks[p]) for p, b in plan.baskets.items() for n, _, _ in b.lines]
    found = len(bought)
    out = ["", "INSIGHTS", "  Coverage"]

    # -- coverage: found / not found, exact vs nearest size, single-app items
    if plan.unavailable:
        out.append(f"  • Your list: {len(decisions)} items -- getting {found}. "
                   f"NOT FOUND on either app ({len(plan.unavailable)}):")
        for name in plan.unavailable:
            d = by_name[name]
            why = d.reason if len(d.reason) <= 110 else d.reason[:109] + "…"
            out.append(f"      ✗ {name} {d.item.size} -- {why}")
    else:
        out.append(f"  • Your list: {len(decisions)} items -- all {found} found")
    nearest = [(n, p, c) for n, p, c in bought if not c.exact]
    multi = [(n, p, c) for n, p, c in bought if c.exact and c.packs > 1]
    single = found - len(nearest) - len(multi)
    if found:
        if not nearest and not multi:
            out.append(f"  • Pack size: all {found} are the exact size you asked for")
        else:
            parts = [f"{single} exact"]
            if multi:
                parts.append(f"{len(multi)} exact using several packs")
            if nearest:
                parts.append(f"{len(nearest)} nearest size (your size isn't sold)")
            out.append(f"  • Pack size: {', '.join(parts)}")
            for n, p, c in multi:
                dim, want = parse_size(by_name[n].item.size)
                out.append(f"      = {n}: asked {_amount(dim, want)} -> getting {c.packs} x {_amount(dim, c.amount)} "
                           f"on {APP_NAMES.get(p, p)} (your size isn't sold as one pack)")
            for n, p, c in nearest:
                dim, want = parse_size(by_name[n].item.size)
                change = (c.amount - want) / want
                out.append(f"      ~ {n}: asked {_amount(dim, want)} -> getting {_amount(dim, c.amount)} "
                           f"({change:+.0%}) on {APP_NAMES.get(p, p)}")
    only: dict[str, list[str]] = {}
    for d in decisions:
        sold = [p for p, c in d.picks.items() if c]
        if len(sold) == 1:
            only.setdefault(sold[0], []).append(d.item.name)
    for p, names in only.items():
        out.append(f"  • Only {APP_NAMES.get(p, p)} has: {', '.join(names)}")

    # -- money
    out.append("  Money")
    mrp = sum((c.offer.mrp or c.offer.price) * by_name[n].item.qty * c.packs for n, _, c in bought)
    items_paid = sum(b.subtotal for b in plan.baskets.values())
    if mrp > items_paid:
        out.append(f"  • Saved on MRP: {_money(mrp - items_paid)} ({(mrp - items_paid) / mrp:.0%}) -- "
                   f"items at MRP {_money(mrp)}, you pay {_money(items_paid)}")
        def off(t):
            n, _, c = t
            return ((c.offer.mrp or c.offer.price) - c.offer.price) * by_name[n].item.qty * c.packs
        top = max(bought, key=off)
        if off(top) > 0:
            n, _, c = top
            full = (c.offer.mrp or c.offer.price) * by_name[n].item.qty * c.packs
            out.append(f"      biggest discount: {n}, {_money(off(top))} off MRP ({off(top) / full:.0%})")
    out.append(f"  • Fees: {_money(fees)} ({fees / grand:.1%} of the total)" if fees > 0 and grand
               else "  • Fees: none in this plan")
    gaps, dearer = [], []
    for n, p, c in bought:
        d = by_name[n]
        others = [(q, o) for q, o in d.picks.items() if o and q != p]
        if not others:
            continue
        q, o = others[0]
        gap = _for_your_qty(d, o) - _for_your_qty(d, c)
        (gaps if gap >= 1 else dearer if gap <= -1 else []).append((abs(gap), n, p, q))
    if gaps:
        top = sorted(gaps, reverse=True)[:3]
        out.append("  • Biggest price differences: " + "; ".join(
            f"{n} {_money(g)} cheaper on {APP_NAMES.get(p, p)}" for g, n, p, _ in top))
    if dearer:
        out.append("  • Paid a little more to keep fewer orders (avoids a second app's fees): " + "; ".join(
            f"{n} +{_money(g)} on {APP_NAMES.get(p, p)}" for g, n, p, _ in sorted(dearer, reverse=True)))
    different = [d.item.name for d in decisions if len({round(c.total_amount) for c in d.picks.values() if c}) > 1]
    for sc in scenarios:
        app = APP_NAMES.get(sc.platform, sc.platform)
        if sc.missing:
            out.append(f"  • Everything on {app}: not possible -- it doesn't sell {', '.join(sc.missing)}")
        elif sc.total is None:
            out.append(f"  • Everything on {app}: couldn't get a live bill")
        elif set(plan.baskets) == {sc.platform}:
            out.append(f"  • Everything on {app}: that is this plan")
        else:
            diff = sc.total - grand
            if diff > 0.5:
                verdict = f"you save {_money(diff)} with this split"
            elif diff < -0.5:
                # Only possible when packs differ: the split was chosen on value per unit.
                verdict = (f"{_money(-diff)} less in rupees, but for different pack sizes"
                           f" ({', '.join(different)}); this split is better value per unit")
            else:
                verdict = "about the same as this split"
            out.append(f"  • Everything on {app}: {_money(sc.total)} (live bill) -- {verdict}")
    if naive_total is not None and naive_total - grand > 0.5:
        out.append(f"  • Buying each item wherever it's cheapest: {_money(naive_total)} -- "
                   f"fees make that {_money(naive_total - grand)} dearer than this plan")

    # -- heads-up
    heads = []
    if different:
        heads.append(f"  • Pack sizes differ between apps for: {', '.join(different)} "
                     "(price comparisons above are scaled to your quantity)")
    if heads:
        out += ["  Heads-up"] + heads
    return out


def render(
    plan: Plan,
    platforms: list[str],
    bills: dict[str, Quote],  # live bill of each final cart
    decisions: list[Decision],
    scenarios: list[Scenario],
    naive_total: float | None,
    quoted_at: str,
) -> str:
    by_name = {d.item.name: d for d in decisions}
    lines = ["", "=" * (2 * COL + 5), f"WHAT TO BUY WHERE   (live prices and fees as of {quoted_at})", "=" * (2 * COL + 5)]
    cols = [_column(p, plan, bills, by_name) for p in platforms]
    for left, right in zip_longest(*cols, fillvalue=""):
        lines.append(f"{left:<{COL}}  |  {right}")

    grand = sum(to_pay(p, plan, bills) for p in plan.baskets)
    fees = sum((bills[p].fee_total if p in bills else sum(a for _, a in b.fees)) for p, b in plan.baskets.items())
    found = len(decisions) - len(plan.unavailable)
    label = "GRAND TOTAL (all carts)" if not plan.unavailable else f"GRAND TOTAL ({found} of {len(decisions)} items)"
    lines += ["=" * (2 * COL + 5), _row(label, _money(grand), 2 * COL + 5)]

    lines += _insights(plan, decisions, by_name, bills, scenarios, naive_total, grand, fees)

    # ---------------------------------------------------------------- next steps
    lines += ["", "NEXT STEP"]
    for p in plan.baskets:
        n = len(plan.baskets[p].lines)
        lines.append(f"  → Open the {APP_NAMES.get(p, p)} app and add the {n} item{'s' if n != 1 else ''} in its column "
                     f"above (exact name and pack size). Expected to pay: {_money(to_pay(p, plan, bills))}.")
    lines.append("  Your carts were NOT changed -- the apps' carts can't be filled from here. Nothing has been ordered.")
    lines.append(f"  Prices and fees were live at {quoted_at}; they can change (surge, rain), so check the total in the app.")
    return "\n".join(lines)
