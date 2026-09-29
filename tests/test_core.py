import pytest

from grocer.connection import is_read_only
from grocer.optimizer import Fee, FeeSchedule, greedy, optimize, schedule_fees
from grocer.platforms.instamart import parse_search
from grocer.units import parse_size, same_size


@pytest.mark.parametrize("text,expected", [
    ("500ml", ("volume", 500)), ("0.5 L", ("volume", 500)), ("5 Ltr", ("volume", 5000)),
    ("1 kg", ("mass", 1000)), ("2 x 500 g", ("mass", 1000)), ("Amul Butter 500 g", ("mass", 500)),
    ("1 pack (200 gms)", ("mass", 200)), ("no size", None),
    ("1 pc (5 L)", ("volume", 5000)), ("2 pcs (375 ml)", ("volume", 750)), ("1 pack (50 x 20 g)", ("mass", 1000)),
    ("6 pcs", ("count", 6)), ("100 g x 4", ("mass", 400)), ("500 g x 2", ("mass", 1000)),
])
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_same_size_across_units():
    assert same_size("5l", "5 Litre")
    assert not same_size("500g", "500 ml")
    assert not same_size("500g", "1 kg")


@pytest.mark.parametrize("name,ok", [
    ("search_products", True), ("get_addresses", True), ("get_cart", True), ("your_go_to_items", True),
    ("update_cart", False), ("checkout", False), ("add_to_cart", False), ("confirm_order", False),
    ("apply_coupon", False), ("create_address", False), ("addToCart", False), ("placeOrder", False),
])
def test_read_only_guard(name, ok):
    assert is_read_only(name) is ok


FEES = FeeSchedule(fees=(Fee("small cart", 35, below=99), Fee("delivery", 30, below=199)))


def test_optimizer_beats_greedy_when_fees_matter():
    # Dal is ₹3 cheaper on instamart, but a ₹97 instamart basket pays ₹65 in fees.
    items = [("oil", 1), ("butter", 1), ("dal", 1)]
    prices = {"oil": {"zepto": 900, "instamart": 920}, "butter": {"zepto": 280, "instamart": 285},
              "dal": {"zepto": 100, "instamart": 97}}
    sched = schedule_fees({"zepto": FEES, "instamart": FEES})
    best, naive = optimize(items, prices, sched), greedy(items, prices, sched)
    assert set(best.baskets) == {"zepto"} and best.total == 1280
    assert naive.total == 1277 + 65


def test_optimizer_respects_hard_minimum_and_missing_items():
    items = [("a", 1), ("b", 2), ("c", 1)]
    prices = {"a": {"zepto": 50, "instamart": 40}, "b": {"zepto": 100, "instamart": 100}, "c": {}}
    sched = {"instamart": FeeSchedule(min_order=199), "zepto": FeeSchedule()}
    plan = optimize(items, prices, schedule_fees(sched))
    # instamart alone would be 240 >= 199, cheaper than zepto 250
    assert set(plan.baskets) == {"instamart"} and plan.total == 240 and plan.unavailable == ["c"]
    sched["instamart"] = FeeSchedule(min_order=500)
    assert set(optimize(items, prices, schedule_fees(sched)).baskets) == {"zepto"}


def test_instamart_parse_documented_shape():
    payload = {"success": True, "data": {"products": [{
        "displayName": "Amul Butter", "brand": "Amul", "variations": [
            {"quantityDescription": "100 g", "price": {"mrp": 62, "offerPrice": 58}, "spinId": "s1", "isInStockAndAvailable": True},
            {"quantityDescription": "500 g", "price": {"mrp": "₹295", "offerPrice": "₹285"}, "spinId": "s2", "isInStockAndAvailable": False},
        ]}]}}
    offers = parse_search(payload)
    assert [(o.sku_id, o.price, o.mrp, o.in_stock) for o in offers] == [("s1", 58, 62, True), ("s2", 285, 295, False)]


def test_zepto_parse_real_shape():
    from grocer.platforms import zepto

    payload = {"products": [
        {"name": "Amul Salted Butter", "packSize": "1 pack (500 g)", "price": 31000, "mrp": 31000,
         "availableQuantity": 2, "productVariantId": "v1"},
        {"name": "Amul Salted Butter", "packSize": "1 pack (100 g)", "price": 6300, "mrp": None,
         "availableQuantity": 0, "productVariantId": "v2"},
    ]}
    offers = zepto.parse_search(payload)
    assert [(o.sku_id, o.price, o.mrp, o.in_stock) for o in offers] == [("v1", 310, 310, True), ("v2", 63, None, False)]


def test_optimizer_compares_value_not_pack_price():
    # butter: instamart 4 x 100 g for 252 vs zepto 500 g for 310 -> zepto is cheaper per 100 g
    items = [("butter", 1)]
    prices = {"butter": {"zepto": 310, "instamart": 252}}
    scales = {"butter": {"zepto": 500 / 500, "instamart": 500 / 400}}
    plan = optimize(items, prices, schedule_fees({}), scales)
    assert set(plan.baskets) == {"zepto"} and plan.total == 310 and plan.value == 310
    assert set(optimize(items, prices, schedule_fees({})).baskets) == {"instamart"}  # without scaling


def _random_list(rnd, n):
    items = [(f"i{k}", 1) for k in range(n)]
    prices = {}
    for k in range(n):
        base = rnd.choice([30, 60, 100, 150, 300, 800, 1500])
        prices[f"i{k}"] = {"zepto": round(base * rnd.uniform(0.9, 1.1)), "instamart": round(base * rnd.uniform(0.9, 1.1))}
        if rnd.random() < 0.1:
            prices[f"i{k}"].pop(rnd.choice(["zepto", "instamart"]))
    fees = schedule_fees({
        "zepto": FeeSchedule(fees=(Fee("small", 35, below=99), Fee("delivery", 30, below=rnd.choice([149, 199, 299])))),
        "instamart": FeeSchedule(fees=(Fee("handling", 12), Fee("small", 20, below=199),
                                       Fee("delivery", 30, below=rnd.choice([199, 399])))),
    })
    return items, prices, fees


def test_long_list_search_matches_exact_answer(monkeypatch):
    import random
    from grocer import optimizer
    rnd = random.Random(7)
    for _ in range(60):
        items, prices, fees = _random_list(rnd, rnd.randint(4, 11))
        exact = optimize(items, prices, fees)
        monkeypatch.setattr(optimizer, "EXHAUSTIVE_LIMIT", 1)  # force the long-list search
        searched = optimize(items, prices, fees)
        monkeypatch.setattr(optimizer, "EXHAUSTIVE_LIMIT", 1 << 15)
        assert abs(searched.value - exact.value) < 0.01


def test_fifty_items_are_fast():
    import random
    import time
    items, prices, fees = _random_list(random.Random(1), 50)
    t = time.monotonic()
    plan = optimize(items, prices, fees)
    assert plan is not None and time.monotonic() - t < 2
    assert plan.value <= greedy(items, prices, fees).value + 1e-9
