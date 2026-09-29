import anyio

from grocer import advisor
from grocer.models import Item, Offer


def _o(platform, sku, name, size, price, in_stock=True):
    return Offer(platform, sku, name, "", size, price, None, in_stock)


def test_candidates_filter_brand_size_window_and_stock():
    item = Item("Dettol liquid refill", "Dettol", "900ml")
    offers = [
        _o("zepto", "a", "Dettol Original Hand Wash Refill", "1.35 L", 180),  # 1.5x ok
        _o("zepto", "b", "Dettol Skincare Hand Wash Refill", "875 ml", 155),  # 0.97x ok
        _o("zepto", "c", "Dettol Hand Wash Pump", "2 pcs (375 ml)", 95),  # 750 ml = 0.83x ok
        _o("zepto", "d", "Dettol Sensitive Refill", "675 ml", 106),  # 0.75x boundary ok
        _o("zepto", "e", "Dettol Refill", "200 ml", 40),  # too small
        _o("zepto", "f", "Dettol Refill Jumbo", "2 L", 300),  # 2.2x too big
        _o("zepto", "g", "Lifebuoy Refill", "900 ml", 120),  # other brand: kept, ranked last for Claude
        _o("zepto", "h", "Dettol Original Refill", "900 ml", 150, in_stock=False),
        _o("zepto", "a", "Dettol Original Hand Wash Refill", "1.35 L", 180),  # duplicate sku
    ]
    cands = advisor.candidates_for(item, offers, "z")
    # "dettol" + "refill" matches rank first (by ₹/100 ml); then one-word matches, exact size first
    assert [c.offer.sku_id for c in cands] == ["a", "d", "b", "g", "c"]  # g: exact 900 ml breaks the tie
    assert [c.ref for c in cands] == ["z1", "z2", "z3", "z4", "z5"]
    assert round(cands[0].unit_price, 2) == 13.33  # ₹180 / 1350 ml * 100
    assert [c.offer.sku_id for c in cands if c.exact] == ["g"]


def test_exact_size_always_wins_over_better_value():
    item = Item("Tata Tuver dal", "Tata", "500g")
    offers = [_o("z", "kg", "Tata Sampann Toor Dal", "1 kg", 179),  # ₹17.90/100 g
              _o("z", "half", "Tata Sampann Toor Dal", "1 pack (500 g)", 103)]  # ₹20.60/100 g
    cands = advisor.candidates_for(item, offers, "z")
    assert [c.offer.sku_id for c in cands] == ["half", "kg"] and cands[0].exact  # exact listed first
    assert advisor.choose_pack(cands).offer.sku_id == "half"
    # no exact size -> best value per unit
    no_exact = advisor.candidates_for(Item("Tata Tuver dal", "Tata", "600g"), offers, "z")
    assert advisor.choose_pack(no_exact).offer.sku_id == "kg"


def test_decide_validates_claude_picks(monkeypatch):
    items = [Item("Tata Tuver dal", "Tata", "500g"), Item("Amul butter", "Amul", "500g")]
    offers = {
        "Tata Tuver dal": {
            "zepto": [_o("zepto", "zt", "Tata Sampann Unpolished Toor Dal", "500 g", 103)],
            "instamart": [_o("instamart", "it", "Tata Sampann Toor Dal", "500 g", 104)],
        },
        "Amul butter": {"zepto": [_o("zepto", "zb", "Amul Salted Butter", "500 g", 310)], "instamart": []},
    }
    seen = {"prompt": ""}

    async def fake_claude(prompt, schema, model, effort):
        seen["prompt"] += prompt  # one call per chunk
        return {"decisions": [
            {"item": "Tata Tuver dal", "product": "Tata Sampann toor dal 500 g", "zepto": ["z1"], "instamart": ["i1"],
             "reason": "same product, cheapest per kg"},
            {"item": "Amul butter", "product": "Amul salted butter 500 g", "zepto": ["z1"], "instamart": ["i7"],
             "reason": "only on zepto"},
        ]}

    monkeypatch.setattr(advisor, "_run_claude", fake_claude)
    d = anyio.run(advisor.decide, items, offers)
    assert d[0].picks["zepto"].offer.sku_id == "zt" and d[0].picks["instamart"].offer.sku_id == "it"
    assert d[1].picks["zepto"].offer.sku_id == "zb"
    assert d[1].picks["instamart"] is None and "ignored invalid instamart ids ['i7']" in d[1].reason
    assert "[EXACT SIZE]" in seen["prompt"] and "₹20.60 per 100 g" in seen["prompt"] and "(no candidates)" in seen["prompt"]


def test_decide_handles_missing_decision(monkeypatch):
    items = [Item("Amul cheese block", "Amul", "200g")]

    async def fake_claude(*_):
        return {"decisions": []}

    monkeypatch.setattr(advisor, "_run_claude", fake_claude)
    (d,) = anyio.run(advisor.decide, items, {"Amul cheese block": {"zepto": []}})
    assert d.picks == {"zepto": None} and "no decision" in d.reason


def test_candidates_respect_stock_left_and_order_limit():
    item = Item("Amul butter", "Amul", "500g", qty=3)
    offers = [Offer("zepto", "few", "Amul Salted Butter", "", "500 g", 310, None, True, max_qty=2),   # only 2 left
              Offer("zepto", "ok", "Amul Salted Butter Tub", "", "500 g", 315, None, True, max_qty=39),
              Offer("zepto", "unknown", "Amul Butter", "", "500 g", 320, None, True)]                  # no data: allowed
    assert [c.offer.sku_id for c in advisor.candidates_for(item, offers, "z")] == ["ok", "unknown"]


def test_two_500g_packs_make_up_1kg_when_1kg_not_sold():
    # the real case: Noice batter sold in 500 g, list asks for 1 kg
    item = Item("Noice idly better", "Noice", "1kg")
    offers = [_o("zepto", "half", "NOICE Idli Dosa Batter (Stoneground)", "1 pack (500 g)", 85),
              _o("zepto", "big", "NOICE Idli Dosa Batter Family Pack", "1 pack (750 g)", 120),  # 0.75x: nearest size
              _o("zepto", "tiny", "NOICE Idli Dosa Batter Mini", "1 pack (200 g)", 45)]         # 5 x 200 g = 1 kg too
    cands = advisor.candidates_for(item, offers, "z")
    half = next(c for c in cands if c.offer.sku_id == "half")
    assert half.exact and half.packs == 2 and half.price == 170 and half.total_amount == 1000
    assert "[EXACT SIZE as 2 packs = ₹170]" in half.describe("mass")
    pick = advisor.choose_pack(cands)
    assert pick.offer.sku_id == "half"  # exact via 2 packs (₹170) beats 5 x 200 g (₹225) and the 750 g pack


def test_single_exact_pack_still_beats_multi_pack():
    item = Item("Noice idly better", "Noice", "1kg")
    offers = [_o("z", "one", "NOICE Batter", "1 kg", 180), _o("z", "half", "NOICE Batter", "500 g", 80)]
    assert advisor.choose_pack(advisor.candidates_for(item, offers, "z")).offer.sku_id == "one"  # even at ₹180 > ₹160


def test_multi_pack_respects_stock_for_all_packs():
    item = Item("Noice idly better", "Noice", "1kg", qty=2)  # needs 4 x 500 g
    offers = [Offer("zepto", "half", "NOICE Batter", "", "500 g", 85, None, True, max_qty=3)]
    assert advisor.candidates_for(item, offers, "z") == []


def test_second_search_only_when_no_exact_size():
    from grocer.cli import _needs_second_search
    butter = Item("Amul butter", "Amul", "500g")
    assert not _needs_second_search(butter, [_o("z", "b", "Amul Salted Butter", "1 pack (500 g)", 310)])
    assert _needs_second_search(Item("Dettol liquid refill", "Dettol", "900ml"),
                                [_o("z", "d", "Dettol Refill", "1.35 L", 180)])  # 900 ml isn't sold
    assert not _needs_second_search(Item("Noice idli batter", "Noice", "1kg"),
                                    [_o("z", "n", "NOICE Batter", "500 g", 39)])  # 2 x 500 g is exact
    assert not _needs_second_search(Item("x", "x", "1kg", query="custom"), [])  # own query: never


def test_decide_splits_list_into_parallel_chunks(monkeypatch):
    items = [Item(f"item {n}", "Amul", "500g") for n in range(7)]
    offers = {i.name: {"zepto": [_o("zepto", f"s{n}", f"Amul item {n}", "500 g", 100)]} for n, i in enumerate(items)}
    calls = []

    async def fake_claude(prompt, schema, model, effort):
        names = [line.split('"')[1] for line in prompt.splitlines() if line.startswith("## item:")]
        calls.append(names)
        await anyio.sleep(0.01)
        return {"decisions": [{"item": n, "product": n, "label": n, "zepto": ["z1"], "reason": "ok"} for n in names]}

    monkeypatch.setattr(advisor, "_run_claude", fake_claude)
    d = anyio.run(advisor.decide, items, offers)
    assert len(calls) == advisor.CHUNKS == 3 and sorted(n for c in calls for n in c) == sorted(i.name for i in items)
    assert [x.item.name for x in d] == [i.name for i in items]  # original order kept
    assert all(x.picks["zepto"] is not None for x in d)
