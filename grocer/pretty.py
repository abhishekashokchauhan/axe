"""The on-screen result: both apps side by side, the grand total, and how this split compares.

Colours are chosen to read well on BOTH light and dark Terminal themes: body text uses the
terminal's own foreground, secondary text uses "dim", and accents are mid-dark shades.
One rule: dark orange means money saved, and nothing else.
"""

from dataclasses import dataclass
from itertools import zip_longest

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from grocer.advisor import Decision
from grocer.optimizer import Plan
from grocer.quote import Quote
from grocer.report import APP_NAMES, Scenario, _amount, _money, _plan_pick, to_pay
from grocer.units import parse_size

SAVE = "bold #d75f00"   # dark orange: savings only
ACCENT = "#0087ff"      # mid blue: structure (headers, borders, bars) -- readable on white and black
OK = "bold #00af00"     # green: ✓ and FREE -- bold so it holds up on white
BAD = "#d70000"         # red: not found
BAR_W = 26              # width of the longest comparison bar
MAX_W = 96              # on a wide window the table stops growing (no huge gaps before prices)


def _rupees(x: float) -> str:
    """Totals and savings in whole rupees (the apps round the amount to pay too)."""
    return f"₹{round(x):,}"


def _app(p: str) -> str:
    return APP_NAMES.get(p, p)


def _fee_label(label: str) -> str:
    """Sentence case, keeping acronyms: "Handling Fee" / "delivery fee" -> "Handling fee",
    "Delivery fee"; "GST and Charges" -> "GST and charges"."""
    words = label.split()
    out = [w if (w.isupper() and len(w) > 1) else w.lower() for w in words]
    return (out[0][:1].upper() + out[0][1:] if out else "") + "".join(" " + w for w in out[1:])


# ---------------------------------------------------------------- the two carts
def _pair(left: Text | str, right: Text | str) -> Table:
    """A name on the left, an amount on the right that never wraps or overflows."""
    t = Table.grid(expand=True)
    t.add_column(ratio=1, overflow="fold")
    t.add_column(justify="right", no_wrap=True)
    t.add_row(left, right)
    return t


def _item_cell(d: Decision, platform: str, qty: int, line_total: float) -> Group:
    pack = d.picks[platform]
    n = qty * pack.packs
    name = Text()
    if n > 1:
        name.append(f"{n} × ")
    name.append(d.label or d.product)
    dim = parse_size(d.item.size)[0]
    notes = [_amount(dim, pack.amount) + (" each" if n > 1 else "")]
    if pack.exact and pack.packs > 1:
        notes.append(f"= your {_amount(dim, parse_size(d.item.size)[1])}")
    elif not pack.exact:
        notes.append(f"you asked {_amount(dim, parse_size(d.item.size)[1])}")
    return Group(_pair(name, Text(_money(line_total), style="bold")), Text("  " + " · ".join(notes), style="dim"))


def _cart_rows(platform: str, plan: Plan, bills: dict[str, Quote], by_name: dict[str, Decision]):
    """(item cells, fee rows as (label, amount, style), to-pay amount) for one app."""
    basket = plan.baskets.get(platform)
    if basket is None:
        return [Text("Nothing to buy here", style="dim")], [], None
    items = [_item_cell(by_name[n], platform, q, line) for n, q, line in basket.lines]
    bill = bills.get(platform)
    if bill is None:
        fees = [(_fee_label(l), _money(a), "") for l, a in basket.fees]
        return items, [("Items", _money(basket.subtotal), "")] + fees, (basket.total, "To pay (fees estimated)")
    fees = [(_fee_label(l), "FREE" if a == 0 else _money(a), OK if a == 0 else "") for l, a in bill.fees]
    return items, [("Items", _money(bill.item_total), "")] + fees, (to_pay(platform, plan, bills), "To pay")


def carts_table(plan: Plan, platforms: list[str], bills: dict[str, Quote], by_name: dict[str, Decision],
                width: int | None = None) -> Table:
    table = Table(box=box.ROUNDED, expand=True, width=width, border_style=ACCENT, show_header=True, padding=(0, 1),
                  header_style="", show_edge=True)
    cols = []
    for p in platforms:
        n = len(plan.baskets[p].lines) if p in plan.baskets else 0
        head = Text.assemble((_app(p), f"bold {ACCENT}"), (f"  ·  {n} item{'s' if n != 1 else ''}", "dim"))
        table.add_column(head, ratio=1)
        cols.append(_cart_rows(p, plan, bills, by_name))
    # items: row i pairs the i-th item of each app, so both columns stay level
    for row in zip_longest(*(c[0] for c in cols), fillvalue=""):
        table.add_row(*row)
    table.add_section()
    for row in zip_longest(*(c[1] for c in cols), fillvalue=None):
        table.add_row(*(_pair(r[0], Text(r[1], style=r[2])) if r else "" for r in row))
    table.add_section()
    table.add_row(*(_pair(Text(c[2][1], style="bold"), Text(_rupees(c[2][0]), style="bold")) if c[2] else ""
                    for c in cols))
    return table


# ---------------------------------------------------------------- comparisons
@dataclass(frozen=True)
class Compare:
    label: str
    total: float
    saved: float | None  # vs this split (None for the split itself)
    note: str = ""       # e.g. "NOICE batter from Swiggy Instamart" / "but butter is 400 g instead of 500 g"


def comparisons(plan: Plan, by_name: dict[str, Decision], scenarios: list[Scenario], grand: float) -> list[Compare]:
    rows = [Compare("This split", grand, None)]
    for sc in scenarios:
        if sc.total is None or (sc.missing and not sc.topup) or set(plan.baskets) == {sc.platform}:
            continue
        note = ""
        if sc.missing:
            names = ", ".join(dict.fromkeys(by_name[n].label or n for n in sc.missing))
            note = f"{names} from {' and '.join(_app(q) for q in sc.topup)}"
        if sc.total < grand - 0.5:  # cheaper in rupees only because its packs are smaller
            smaller = []
            for d in by_name.values():
                mine, chosen = d.picks.get(sc.platform), _plan_pick(plan, by_name, d.item.name)[1]
                if mine and chosen and mine.total_amount < chosen.total_amount - 0.5:
                    dim = parse_size(d.item.size)[0]
                    smaller.append(f"{d.label or d.item.name} is {_amount(dim, mine.total_amount)} "
                                   f"instead of {_amount(dim, chosen.total_amount)}")
            if smaller:
                note = "; ".join(filter(None, [note, "but " + "; ".join(smaller[:2])]))
        rows.append(Compare(f"All on {_app(sc.platform)}", sc.total, sc.total - grand, note))
    mrp, paid = 0.0, 0.0
    for p, b in plan.baskets.items():
        for n, q, line in b.lines:
            c = by_name[n].picks[p]
            mrp += (c.offer.mrp or c.offer.price) * q * c.packs
            paid += line
    if mrp > paid + 0.5:
        rows.append(Compare("At MRP (items only)", mrp, mrp - paid))
    return rows


def _bar(value: float, top: float) -> str:
    eighths = int(round(value / top * BAR_W * 8)) if top else 0
    return "█" * (eighths // 8) + ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")[eighths % 8]


def comparison_table(rows: list[Compare]) -> Group:
    """Bars from zero (honest), the amount, and the saving in orange. Notes become footnotes
    so a long note never squeezes the numbers."""
    top = max(r.total for r in rows)
    t = Table.grid(padding=(0, 1))
    t.add_column(no_wrap=True)
    t.add_column(no_wrap=True, width=BAR_W + 1)
    t.add_column(justify="right", no_wrap=True)
    t.add_column(no_wrap=True)
    marks, notes = "¹²³⁴⁵", []
    for r in rows:
        label = r.label
        if r.note:
            label += marks[len(notes)]
            notes.append(Text(f"{marks[len(notes)]} {r.note}", style="dim"))
        if r.saved is None:
            t.add_row(Text(label, style="bold"), Text(_bar(r.total, top), style=ACCENT),
                      Text(_rupees(r.total), style="bold"), "")
            continue
        if r.saved > 0.5:
            verdict = Text(f"{_rupees(r.saved)} saved", style=SAVE)
        elif r.saved < -0.5:
            verdict = Text(f"{_rupees(-r.saved)} less", style="dim")
        else:
            verdict = Text("same", style="dim")
        t.add_row(label, Text(_bar(r.total, top), style="dim"), _rupees(r.total), verdict)
    return Group(t, *notes)


def biggest_gap(by_name: dict[str, Decision]) -> Text | None:
    """The same product in the same pack on both apps, with the biggest price difference."""
    best = None
    for d in by_name.values():
        picks = [(p, c) for p, c in d.picks.items() if c]
        if len(picks) != 2 or abs(picks[0][1].total_amount - picks[1][1].total_amount) > 0.5:
            continue
        (pa, a), (pb, b) = sorted(picks, key=lambda x: x[1].price)
        gap = (b.price - a.price) * d.item.qty
        if gap >= 1 and (best is None or gap > best[0]):
            best = (gap, d, pa, a, pb, b)
    if not best:
        return None
    gap, d, pa, a, pb, b = best
    q = d.item.qty
    return Text.assemble(("↓ ", ACCENT), (d.label or d.item.name, "bold"), f" is {_money(gap)} cheaper on {_app(pa)} ",
                         (f"({_money(a.price * q)} vs {_money(b.price * q)} on {_app(pb)})", "dim"))


# ---------------------------------------------------------------- the whole screen
def print_result(console: Console, plan: Plan, platforms: list[str], bills: dict[str, Quote],
                 decisions: list[Decision], scenarios: list[Scenario], quoted_at: str,
                 sold_out: set[str] = frozenset(), details_file: str | None = None) -> None:
    by_name = {d.item.name: d for d in decisions}
    grand = sum(to_pay(p, plan, bills) for p in plan.baskets)
    found = len(decisions) - len(plan.unavailable)

    width = min(console.width, MAX_W)
    console.print()
    console.print(carts_table(plan, platforms, bills, by_name, width))
    label = "Grand total" + (f" · {found} of {len(decisions)} items" if plan.unavailable else "")
    band = Text(f"  {label}   {_rupees(grand)}  ", style="bold white on #d75f00")
    console.print(Align.center(band, width=width))
    console.print()

    rows = comparisons(plan, by_name, scenarios, grand)
    if len(rows) > 1:
        console.print(Text("How this split compares", style=f"bold {ACCENT}"))
        console.print(comparison_table(rows))
        console.print()

    bought = [by_name[n].picks[p] for p, b in plan.baskets.items() for n, _, _ in b.lines]
    multi = sum(1 for c in bought if c.exact and c.packs > 1)
    nearest = sum(1 for c in bought if not c.exact)
    parts = [f"{found} of {len(decisions)} found", f"{len(bought) - multi - nearest} exact size"]
    if multi:
        parts.append(f"{multi} as several packs")
    if nearest:
        parts.append(f"{nearest} nearest size")
    console.print(Text.assemble(("✓ ", OK), " · ".join(parts)))
    for n in plan.unavailable:
        why = "sold out right now" if n in sold_out else "not sold on either app"
        console.print(Text.assemble(("✗ ", BAD), (n, "bold"), f": {why}"))
    gap = biggest_gap(by_name)
    if gap:
        console.print(gap)
    console.print()
    apps = " and ".join(_app(p) for p in plan.baskets)
    console.print(Text.assemble(("→ ", ACCENT), f"Add these in your {apps} app{'s' if len(plan.baskets) > 1 else ''}. ",
                                ("Nothing was ordered.", "bold")))
    stamp = f"Prices and fees live at {quoted_at}"
    console.print(Text(stamp + (f" · full details: {details_file}" if details_file else ""), style="dim"))
