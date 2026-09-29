from grocer.advisor import Candidate, Decision
from grocer.models import Item, Offer
from grocer.optimizer import Basket, Plan
from grocer.quote import Quote
from grocer.report import Scenario, render


def _pick(platform, name, size, price, mrp, amount, exact=True):
    return Candidate("x", Offer(platform, name, name, "", size, price, mrp, True), amount, price / amount * 100, exact)


def _sample():
    oil = Item("Tata groundnut oil", "Tata", "5L")
    butter = Item("Amul butter", "Amul", "500g")
    cheese = Item("Amul cheese block", "Amul", "200g")
    decisions = [
        Decision(oil, "Tata oil", {"zepto": _pick("zepto", "Tata Simply Better Groundnut Oil", "5 L", 1549, 1800, 5000),
                                   "instamart": _pick("instamart", "Tata Simply Better Groundnut Oil", "5 ltr", 1789, 1800, 5000)}, ""),
        Decision(butter, "Amul butter", {"zepto": _pick("zepto", "Amul Salted Butter", "500 g", 310, 310, 500),
                                         "instamart": _pick("instamart", "Amul Pasteurised Butter", "100 g x 4", 252, 280, 400, False)}, ""),
        Decision(cheese, "Amul block", {"zepto": _pick("zepto", "Amul Processed Cheese Block", "200 g", 135, 140, 200),
                                        "instamart": _pick("instamart", "Amul Processed Cheese Block", "200 g", 129, 140, 200)}, ""),
    ]
    plan = Plan({"zepto": Basket("zepto", [("Tata groundnut oil", 1, 1549), ("Amul butter", 1, 310)]),
                 "instamart": Basket("instamart", [("Amul cheese block", 1, 129)], [("Handling Fee", 12)])}, [])
    bills = {"zepto": Quote("zepto", 1859, (), 1859),
             "instamart": Quote("instamart", 129, (("Handling Fee", 12.0), ("Delivery Partner Fee", 0.0)), 141)}
    return plan, bills, decisions


def test_render_side_by_side_and_insights():
    plan, bills, decisions = _sample()
    scenarios = [Scenario("zepto", 1994.0), Scenario("instamart", 2182.0)]
    out = render(plan, ["zepto", "instamart"], bills, decisions, scenarios, 2010.0, "28 Sep 2026, 04:36 PM")
    print(out)
    assert "ZEPTO" in out and "SWIGGY INSTAMART" in out and "|" in out
    assert "GRAND TOTAL (all carts)" in out and "₹2,000" in out  # 1859 + 141
    assert "Saved on MRP: ₹262 (12%)" in out  # MRP 1800+310+140 = 2250 vs items 1549+310+129 = 1988
    assert "Everything on Zepto: ₹1,994 (live bill) -- ₹6 less in rupees, but for different pack sizes (Amul butter)" in out
    assert "Everything on Swiggy Instamart: ₹2,182 (live bill) -- you save ₹182 with this split" in out
    assert "fees make that ₹10 dearer" in out
    assert "Pack sizes differ between apps for: Amul butter" in out
    assert "Open the Zepto app and add the 2 items" in out and "add the 1 item in" in out
    assert "Expected to pay: ₹1,859" in out and "carts were NOT changed" in out and "as of 28 Sep 2026, 04:36 PM" in out
    assert "check in the app" not in out  # bills match the plan's prices


def test_render_single_app_scenarios_edge_cases():
    plan, bills, decisions = _sample()
    scenarios = [Scenario("zepto", None, ("Dettol refill",)), Scenario("instamart", None)]
    out = render(plan, ["zepto", "instamart"], bills, decisions, scenarios, None, "28 Sep 2026, 04:36 PM")
    assert "Everything on Zepto: not possible -- it doesn't sell Dettol refill" in out
    assert "Everything on Swiggy Instamart: couldn't get a live bill" in out

    # a cart price that disagrees with the search price is flagged
    bills["zepto"] = Quote("zepto", 1869, (), 1869)
    assert "! cart items ₹1869 vs search ₹1859: check in the app" in render(
        plan, ["zepto", "instamart"], bills, decisions, scenarios, None, "now")


def test_render_lists_items_not_found_anywhere():
    plan, bills, decisions = _sample()
    rice = Item("Idly rice organic", "Idly", "1kg")
    decisions.append(Decision(rice, "", {"zepto": None, "instamart": None},
                              "Neither app sells an organic idli rice; only regular idli rice is listed."))
    plan.unavailable = ["Idly rice organic"]
    out = render(plan, ["zepto", "instamart"], bills, decisions, [], None, "now")
    assert "GRAND TOTAL (3 of 4 items)" in out
    assert "Your list: 4 items -- getting 3. NOT FOUND on either app (1):" in out
    assert "✗ Idly rice organic 1kg -- Neither app sells an organic idli rice" in out


def test_render_all_found():
    plan, bills, decisions = _sample()
    out = render(plan, ["zepto", "instamart"], bills, decisions, [], None, "now")
    assert "Your list: 3 items -- all 3 found" in out and "GRAND TOTAL (all carts)" in out


def test_render_insight_sections():
    plan, bills, decisions = _sample()
    # butter on Zepto: only 2 left (not reported); Dettol only on Zepto and only in 1.35 L (asked 900 ml)
    butter = decisions[1]
    butter.picks["zepto"] = Candidate("x", Offer("zepto", "b", "Amul Salted Butter", "", "500 g", 310, 310, True, max_qty=2), 500, 62, True)
    dettol = Item("Dettol liquid refill", "Dettol", "900ml")
    decisions.append(Decision(dettol, "Dettol Original refill", {
        "zepto": _pick("zepto", "Dettol Original Hand Wash Refill", "1.35 L", 180, 199, 1350, exact=False), "instamart": None}, ""))
    plan.baskets["zepto"].lines.append(("Dettol liquid refill", 1, 180))
    bills["zepto"] = Quote("zepto", 2039, (), 2039)
    out = render(plan, ["zepto", "instamart"], bills, decisions, [], None, "now")
    print(out)
    assert "  Coverage" in out and "  Money" in out and "  Heads-up" in out
    assert "Pack size: 3 exact, 1 nearest size (your size isn't sold)" in out
    assert "~ Dettol liquid refill: asked 900 ml -> getting 1.35 L (+50%) on Zepto" in out
    assert "Only Zepto has: Dettol liquid refill" in out
    assert "biggest discount: Tata groundnut oil, ₹251 off MRP (14%)" in out
    assert "Fees: ₹12 (0.6% of the total)" in out
    # oil: 1789 vs 1549 -> ₹240; butter compared for 500 g: instamart 252*500/400 = 315 vs 310 -> ₹5
    assert ("Biggest price differences: Tata groundnut oil ₹240 cheaper on Zepto; "
            "Amul cheese block ₹6 cheaper on Swiggy Instamart; Amul butter ₹5 cheaper on Zepto") in out  # largest first
    assert "Paid a little more" not in out  # every item went to its cheaper app
    assert "stock" not in out.lower()


def test_render_paid_more_to_avoid_second_order():
    plan, bills, decisions = _sample()
    # everything on Zepto, including cheese which is ₹6 cheaper on Instamart
    plan.baskets = {"zepto": Basket("zepto", [("Tata groundnut oil", 1, 1549), ("Amul butter", 1, 310),
                                              ("Amul cheese block", 1, 135)])}
    bills = {"zepto": Quote("zepto", 1994, (), 1994)}
    out = render(plan, ["zepto", "instamart"], bills, decisions, [], None, "now")
    assert "Paid a little more to keep fewer orders (avoids a second app's fees): Amul cheese block +₹6 on Zepto" in out
    assert "Fees: none in this plan" in out and "Pack size: all 3 are the exact size" in out


def test_render_multi_pack_line_and_insight():
    plan, bills, decisions = _sample()
    batter = Item("Noice idly better", "Noice", "1kg")
    two = Candidate("x", Offer("zepto", "b", "NOICE Idli Dosa Batter", "", "1 pack (500 g)", 85, 90, True), 500, 17, True, 2)
    decisions.append(Decision(batter, "Noice idli batter", {"zepto": two, "instamart": None}, ""))
    plan.baskets["zepto"].lines.append(("Noice idly better", 1, 170))
    out = render(plan, ["zepto", "instamart"], bills, decisions, [], None, "now")
    assert "2 x NOICE Idli Dosa Batter" in out and "(2 packs = your 1kg)" in out
    assert "Pack size: 3 exact, 1 exact using several packs" in out
    assert "= Noice idly better: asked 1 kg -> getting 2 x 500 g on Zepto (your size isn't sold as one pack)" in out


def _real_like():
    """Numbers from the real 29 Sep run (Instamart ₹726 + ₹12, Zepto ₹2,132)."""
    def pick(p, name, size, price, mrp, amount, exact=True, packs=1):
        return Candidate("x", Offer(p, name, name, "", size, price, mrp, True), amount, price / amount * 100, exact, packs)
    D = [
        Decision(Item("Amul Gold milk pouch", "Amul", "500ml"), "", {"instamart": pick("instamart", "a", "500 ml", 33, 35, 500),
                 "zepto": pick("zepto", "a", "500 ml", 35, 35, 500)}, "", "Amul Gold Milk"),
        Decision(Item("Dettol liquid refill", "Dettol", "900ml"), "", {"instamart": pick("instamart", "d", "1.35 ltr", 158, 199, 1350, False),
                 "zepto": pick("zepto", "d", "1.35 L", 179, 199, 1350, False)}, "", "Dettol Skincare Refill"),
        Decision(Item("Noice idli batter", "Noice", "1kg"), "", {"instamart": pick("instamart", "n", "500 g", 39, 45, 500, True, 2),
                 "zepto": None}, "", "NOICE Idli Dosa Batter"),
        Decision(Item("Tata groundnut oil", "Tata", "5L"), "", {"instamart": pick("instamart", "o", "5 ltr", 1789, 2099, 5000),
                 "zepto": pick("zepto", "o", "5 L", 1549, 2099, 5000)}, "", "Tata Simply Better Groundnut Oil"),
    ]
    plan = Plan({"instamart": Basket("instamart", [("Amul Gold milk pouch", 1, 33), ("Dettol liquid refill", 1, 158),
                                                   ("Noice idli batter", 1, 78)]),
                 "zepto": Basket("zepto", [("Tata groundnut oil", 1, 1549)])}, [])
    bills = {"instamart": Quote("instamart", 269, (("Handling Fee", 12.0), ("Delivery Partner Fee", 0.0)), 281),
             "zepto": Quote("zepto", 1549, (("delivery fee", 0.0),), 1549)}
    return plan, bills, D


def test_alternatives_buy_all_you_can_on_one_app_rest_on_other():
    from grocer.cli import _alternatives
    items = [("milk", 1), ("batter", 1), ("rice", 1), ("oil", 1), ("salt", 1)]
    prices = {"milk": {"zepto": 35, "instamart": 33}, "batter": {"instamart": 78}, "rice": {"zepto": 142},
              "oil": {"zepto": 1549, "instamart": 1789}, "salt": {}}  # salt sold nowhere
    alts = dict(_alternatives(items, prices, {"zepto": None, "instamart": None}))
    assert alts["zepto"] == {"zepto": frozenset({"milk", "rice", "oil"}), "instamart": frozenset({"batter"})}
    assert alts["instamart"] == {"instamart": frozenset({"milk", "batter", "oil"}), "zepto": frozenset({"rice"})}
