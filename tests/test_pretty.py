import io

from rich.console import Console

from grocer.advisor import Candidate, Decision
from grocer.models import Item, Offer
from grocer.optimizer import Basket, Plan
from grocer.pretty import SAVE, _fee_label, comparisons, print_result
from grocer.quote import Quote
from grocer.report import Scenario


def pk(p, size, price, mrp, amount, exact=True, packs=1):
    return Candidate("x", Offer(p, size, "n", "", size, price, mrp, True), amount, price / amount * 100, exact, packs)


def real_run():
    """The 29 Sep 11:19 PM run (trimmed): Instamart ₹574 + fees, Zepto ₹2,313."""
    I = lambda n, s: Item(n, n.split()[0], s)
    D = [
        Decision(I("Dettol liquid refill", "900ml"), "", {"instamart": pk("instamart", "1.35 ltr", 158, 199, 1350, False),
                 "zepto": pk("zepto", "1.35 L", 179, 199, 1350, False)}, "", "Dettol Skincare Handwash Refill"),
        Decision(I("NOICE idly better", "1kg"), "", {"instamart": pk("instamart", "500 g", 39, 45, 500, True, 2),
                 "zepto": None}, "", "NOICE Idli Dosa Batter"),
        Decision(I("NOICE idly batter", "500g"), "", {"instamart": pk("instamart", "500 g", 39, 45, 500), "zepto": None},
                 "", "NOICE Idli Dosa Batter"),
        Decision(I("Tata groundnut oil", "5L"), "", {"instamart": pk("instamart", "5 ltr", 1852, 2099, 5000),
                 "zepto": pk("zepto", "5 L", 1549, 2099, 5000)}, "", "Tata Simply Better Groundnut Oil"),
        Decision(I("Amul butter", "500g"), "", {"instamart": pk("instamart", "100 g x 4", 252, 280, 400, False),
                 "zepto": pk("zepto", "500 g", 310, 310, 500)}, "", "Amul Salted Butter"),
        Decision(I("Amul Gold milk pouch", "500ml"), "", {"instamart": None, "zepto": None}, "", "Amul Gold Milk"),
    ]
    plan = Plan({"instamart": Basket("instamart", [("Dettol liquid refill", 1, 158), ("NOICE idly better", 1, 78),
                                                   ("NOICE idly batter", 1, 39)]),
                 "zepto": Basket("zepto", [("Tata groundnut oil", 1, 1549), ("Amul butter", 1, 310)])},
                ["Amul Gold milk pouch"])
    bills = {"instamart": Quote("instamart", 275, (("Handling Fee", 12.0), ("Delivery Partner Fee", 0.0),
                                                    ("Late Night Fee", 9.0), ("GST and Charges", 1.62)), 297.62),
             "zepto": Quote("zepto", 1859, (("delivery fee", 0.0),), 1859)}
    sc = [Scenario("zepto", 2180.0, ("NOICE idly better", "NOICE idly batter"), ("instamart",)),
          Scenario("instamart", 2400.0, (), ())]
    return plan, bills, D, sc


def render(color=False, width=80, **kw):
    plan, bills, D, sc = real_run()
    buf = io.StringIO()
    c = Console(file=buf, width=width, force_terminal=color, color_system="truecolor" if color else None,
                highlight=False)
    print_result(c, plan, ["instamart", "zepto"], bills, D, sc, "29 Sep, 11:19 PM", {"Amul Gold milk pouch"},
                 "grocery_plan.txt", **kw)
    return buf.getvalue()


def test_every_item_in_full_and_nothing_overflows():
    out = render()
    lines = out.splitlines()
    assert all(len(l) <= 80 for l in lines)
    for name in ("Dettol Skincare Handwash Refill", "2 × NOICE Idli Dosa Batter", "Amul Salted Butter"):
        assert name in out
    assert "Tata Simply Better Groundnut" in out and "…" not in out  # wraps, never cut short
    border = {l.index("│", 1) for l in lines if l.startswith("│")}
    assert len(border) == 1  # the middle border is one straight line: nothing pushed it


def test_totals_fees_and_grand_total():
    out = render()
    assert "Late night fee" in out and "GST and charges" in out and "₹1.62" in out  # small fees exact
    assert "To pay                           ₹298" in out  # totals in whole rupees
    assert "Grand total · 5 of 6 items   ₹2,157" in out  # 297.62 + 1859
    assert out.count("FREE") == 2


def test_comparison_savings_and_footnotes():
    out = render()
    assert "All on Zepto¹" in out and "₹23 saved" in out  # 2180 - 2156.62
    assert "All on Swiggy Instamart" in out and "₹243 saved" in out  # 2400 - 2156.62
    assert "At MRP (items only)" in out
    assert "¹ NOICE Idli Dosa Batter from Swiggy Instamart" in out  # named once, though two list lines map to it


def test_insight_lines():
    out = " ".join(render().split())
    assert "✓ 5 of 6 found · 3 exact size · 1 as several packs · 1 nearest size" in out  # butter is exact on Zepto
    assert "✗ Amul Gold milk pouch: sold out right now" in out
    assert "↓ Tata Simply Better Groundnut Oil is ₹303 cheaper on Zepto (₹1,549 vs ₹1,852 on Swiggy Instamart)" in out
    assert "Nothing was ordered." in out and "full details: grocery_plan.txt" in out


def test_savings_are_dark_orange_and_plain_output_has_no_codes():
    assert "\x1b[" not in render()  # the saved file stays plain text
    colored = render(color=True)
    assert "\x1b[1;38;2;215;95;0m₹23 saved" in colored and SAVE == "bold #d75f00"


def test_cheaper_single_app_is_explained_not_hidden():
    plan, bills, D, sc = real_run()
    by_name = {d.item.name: d for d in D}
    rows = comparisons(plan, by_name, [Scenario("instamart", 2100.0, (), ())], 2157.62)
    alt = next(r for r in rows if r.label == "All on Swiggy Instamart")
    assert alt.saved < 0 and "Amul Salted Butter is 400 g instead of 500 g" in alt.note


def test_fee_labels_read_naturally():
    assert _fee_label("Handling Fee") == "Handling fee"
    assert _fee_label("delivery fee") == "Delivery fee"
    assert _fee_label("GST and Charges") == "GST and charges"


def test_spinner_frames_never_reach_the_saved_file():
    import time
    from grocer.cli import Progress
    c = Console(record=True, width=80, force_terminal=True, file=io.StringIO())
    p = Progress(c)
    p._spinner_console = Console(force_terminal=True, file=io.StringIO())  # a terminal: the spinner really runs
    p.start("Searching Zepto and Swiggy Instamart")
    time.sleep(0.25)
    p.done("Searched 11 items")
    saved = c.export_text(styles=False)
    assert "Searching" not in saved and not any(ch in saved for ch in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    assert saved.startswith("✓ Searched 11 items")


def test_table_stops_growing_on_wide_windows():
    out = render(width=130)
    assert max(len(l) for l in out.splitlines() if l.startswith(("╭", "│", "╰"))) == 96
