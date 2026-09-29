import anyio
import pytest

from grocer import connection
from grocer.advisor import Candidate
from grocer.livefees import LiveFees, live_optimize
from grocer.models import Offer
from grocer.quote import Quote, QuoteError, parse_instamart_bill, parse_zepto_preview, quote_instamart

# The bill get_cart returned in a real one-item quote (Swiggy One member).
REAL_INSTAMART_CART = {
    "cartTotalAmount": "₹270",
    "items": [{"spinId": "LS2J20B2NL", "skuId": "I1NNXY1UPQ", "quantity": 1}],
    "billBreakdown": {
        "lineItems": [{"label": "Item Total", "value": "₹258.00"}, {"label": "Handling Fee", "value": "₹12.00"},
                      {"label": "Delivery Partner Fee", "value": "FREE"}],
        "toPay": {"label": "To Pay", "value": "₹270"},
    },
}


def test_parse_instamart_bill_real_shape():
    q = parse_instamart_bill(REAL_INSTAMART_CART)
    assert q.item_total == 258 and q.to_pay == 270
    assert q.fees == (("Handling Fee", 12.0), ("Delivery Partner Fee", 0.0)) and q.fee_total == 12


def test_parse_instamart_bill_surge_and_discount():
    cart = {"billBreakdown": {"lineItems": [
        {"label": "Item Total", "value": "₹150"}, {"label": "Small Cart Fee", "value": "₹25"},
        {"label": "Rain Fee", "value": "₹20"}, {"label": "Delivery Fee Discount", "value": "-₹10"}],
        "toPay": {"value": "₹185"}}}
    assert parse_instamart_bill(cart).fee_total == 35


# What create_order(confirmOrder=False) actually returned for a one-item (₹29) basket.
REAL_ZEPTO_PREVIEW = {
    "isPreview": True, "items": [{"price": None, "productVariantId": "p", "storeProductId": "s", "quantity": 1}],
    "paymentMethod": "COD", "toPay": "₹59", "toPayAmount": 5900, "totalItems": 1, "deliverable": True,
    "requiresConfirmation": True, "subTotal": None, "deliveryFee": 3000, "packagingFee": None, "taxes": None,
    "discount": None,
}


def test_parse_zepto_preview_real_shape():
    q = parse_zepto_preview(REAL_ZEPTO_PREVIEW, expected_item_total=29)
    assert (q.item_total, q.to_pay) == (29, 59)
    assert q.fees == (("delivery fee", 30.0),) and q.fee_total == 30


def test_parse_zepto_preview_unitemised_charges_and_discount():
    preview = {**REAL_ZEPTO_PREVIEW, "toPay": "₹120", "toPayAmount": 12000, "subTotal": 7000,
               "deliveryFee": 3000, "packagingFee": 1100, "discount": 500}
    q = parse_zepto_preview(preview, expected_item_total=69)  # subTotal wins when present
    assert q.item_total == 70 and q.fee_total == 50
    assert ("discount", -5.0) in q.fees and ("other charges", 14.0) in q.fees  # e.g. surge/rain


def test_parse_zepto_preview_refuses_non_preview():
    with pytest.raises(QuoteError, match="not marked as a preview"):
        parse_zepto_preview({**REAL_ZEPTO_PREVIEW, "isPreview": False}, expected_item_total=29)


def test_parse_zepto_preview_text():
    text = "Cart preview\nItem Total: ₹474\nHandling Fee: ₹11\nRain Fee: ₹20\nTo Pay: ₹505"
    q = parse_zepto_preview(text, expected_item_total=474)
    assert (q.item_total, q.fee_total, q.to_pay) == (474, 31, 505)


@pytest.mark.parametrize("platform,tool,args,ok", [
    ("zepto", "create_order", {"confirmOrder": False, "userAddressId": "a"}, True),
    ("zepto", "create_order", {"confirmOrder": True, "userAddressId": "a"}, False),
    ("zepto", "create_order", {"userAddressId": "a"}, False),  # omitted is not good enough
    ("zepto", "create_order", {"confirmOrder": False, "riderTip": 20}, False),
    ("zepto", "update_cart", {"deviceId": "d", "cartItems": []}, True),
    ("instamart", "checkout", {"paymentMethod": "Cash"}, False),
    ("instamart", "clear_cart", {}, True),
    ("instamart", "update_cart", {"items": []}, True),
    ("zepto", "create_wallet_order", {}, False),
])
def test_guard(platform, tool, args, ok):
    class FakeClient:
        async def list_tools(self):
            class R:
                tools = []
            return R()

    s = connection.GuardedSession(platform, FakeClient())
    assert anyio.run(s._allowed, tool, args) is ok


def _cand(sku, price):
    return Candidate("i1", Offer("instamart", sku, "x", "", "200 g", price, None, True, {"skuId": sku + "-sku"}), 200, price / 2, True)


class FakeInstamartSession:
    def __init__(self, cart_items, fail_update=False):
        self.cart = list(cart_items)
        self.fail_update = fail_update
        self.calls = []

    async def call(self, tool, args=None):
        self.calls.append(tool)
        if tool == "get_cart":
            return {**REAL_INSTAMART_CART, "items": self.cart}
        if tool == "update_cart":
            if self.fail_update and len(self.calls) == 2:
                raise RuntimeError("boom")
            self.cart = [dict(i) for i in args["items"]]
            return {}
        if tool == "clear_cart":
            self.cart = []
            return {"verified": True}


def test_quote_instamart_restores_existing_cart_even_on_failure():
    mine = [{"spinId": "MINE", "skuId": "M", "quantity": 2}]
    s = FakeInstamartSession(mine, fail_update=True)
    with pytest.raises(RuntimeError):
        anyio.run(quote_instamart, s, "addr", [(_cand("NEW", 258), 1)])
    assert s.cart == mine


def test_quote_instamart_clears_when_cart_was_empty():
    s = FakeInstamartSession([])
    q = anyio.run(quote_instamart, s, "addr", [(_cand("NEW", 258), 1)])
    assert q.fee_total == 12 and s.cart == [] and s.calls[-1] == "clear_cart"


def test_live_optimize_quotes_winning_baskets_until_verified():
    # Instamart is cheaper per item, but small instamart baskets get a ₹40 small-cart fee
    # (unknowable from the full-basket quote). The loop must discover that and fix the plan.
    items = [("a", 1), ("b", 1), ("c", 1)]
    prices = {"a": {"zepto": 100, "instamart": 90}, "b": {"zepto": 100, "instamart": 95},
              "c": {"zepto": 100, "instamart": 120}}
    quoted = []

    def quoter(platform, fee_rule):
        async def q(names):
            quoted.append((platform, tuple(sorted(names))))
            total = sum(prices[n][platform] for n in names)
            return Quote(platform, total, (("fees", fee_rule(total)),), None)
        return q

    live = LiveFees(
        quoters={"zepto": quoter("zepto", lambda t: 0.0),
                 "instamart": quoter("instamart", lambda t: 40.0 if t < 250 else 5.0)},
        fallback={},
    )
    plan = anyio.run(live_optimize, items, prices, {}, live)
    # full baskets: zepto 300 (0 fee), instamart 305 (5 fee). a+b on instamart is 185 -> +40.
    assert set(plan.baskets) == {"zepto"} and plan.total == 300
    assert ("instamart", ("a", "b")) in quoted  # the tempting split was checked live, not assumed
    assert all(live.is_live(p, frozenset(n for n, _, _ in b.lines)) for p, b in plan.baskets.items())


def test_live_optimize_falls_back_when_app_cannot_be_quoted():
    from grocer.optimizer import Fee, FeeSchedule

    async def broken(names):
        raise QuoteError("preview format changed")

    async def ok(names):
        return Quote("instamart", 100, (("handling", 12.0),), None)

    live = LiveFees(quoters={"zepto": broken, "instamart": ok},
                    fallback={"zepto": FeeSchedule(fees=(Fee("est", 30),))})
    plan = anyio.run(live_optimize, [("a", 1)], {"a": {"zepto": 95, "instamart": 100}}, {}, live)
    assert "zepto" in live.broken and set(plan.baskets) == {"instamart"} and plan.total == 112


def test_zepto_cart_clear_sets_quantities_to_zero_and_reports_names():
    from grocer.quote import ZeptoCart

    class FakeZepto:
        def __init__(self):
            self.items = [{"productVariantId": "p1", "storeProductId": "s1", "quantity": 2, "name": "Amul Gold Milk"}]
            self.sent = []

        async def call(self, tool, args=None):
            if tool == "view_cart":
                return {"items": self.items}
            if tool == "update_cart":
                self.sent.append(args)
                self.items = [i for i in self.items if not any(
                    l["productVariantId"] == i["productVariantId"] and l["quantity"] == 0 for l in args["cartItems"])]
                return {}

    z = FakeZepto()
    removed = anyio.run(ZeptoCart(z, "addr").clear)
    assert removed == ["2 x Amul Gold Milk"] and z.items == []
    assert z.sent[0]["cartItems"] == [{"productVariantId": "p1", "storeProductId": "s1", "quantity": 0}]
    assert anyio.run(ZeptoCart(z, "addr").clear) == [] and len(z.sent) == 1  # empty cart: no call


def test_quote_many_runs_apps_in_parallel_but_one_cart_at_a_time():
    running, overlap_apps, overlap_same_app = {}, [], []

    def quoter(p):
        async def q(names):
            if running.get(p):
                overlap_same_app.append(p)  # two quotes on one cart at once: must never happen
            running[p] = True
            if any(running.get(o) for o in running if o != p):
                overlap_apps.append(p)
            await anyio.sleep(0.05)
            running[p] = False
            return Quote(p, 100, (), None)
        return q

    live = LiveFees(quoters={"zepto": quoter("zepto"), "instamart": quoter("instamart")}, fallback={})
    pairs = [("zepto", frozenset("a")), ("zepto", frozenset("b")), ("instamart", frozenset("a")),
             ("instamart", frozenset("b")), ("zepto", frozenset("a"))]  # duplicate is quoted once
    anyio.run(live.quote_many, pairs)
    assert overlap_apps and not overlap_same_app
    assert len([k for k in live.quotes if k[0] == "zepto"]) == 2


def test_session_retries_when_rate_limited(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(connection, "RETRY_WAITS", (0, 0, 0, 0))
    replies = [("Error: API request failed: Too Many Requests", True), ("Too Many Requests", True), ('{"ok": 1}', False)]

    class FakeClient:
        calls = 0

        async def list_tools(self):
            return SimpleNamespace(tools=[])

        async def call_tool(self, tool, args):
            text, err = replies[FakeClient.calls]
            FakeClient.calls += 1
            return SimpleNamespace(is_error=err, structured_content=None, content=[SimpleNamespace(text=text)])

    s = connection.GuardedSession("zepto", FakeClient())
    assert anyio.run(s.call, "search_products", {"query": "x"}) == {"ok": 1} and FakeClient.calls == 3
    replies[:] = [("Too Many Requests", True)] * 4
    FakeClient.calls = 0
    with pytest.raises(RuntimeError, match="Too Many Requests"):
        anyio.run(s.call, "search_products", {"query": "x"})
    assert FakeClient.calls == 4  # gives up after the last attempt


def test_session_never_has_more_than_4_requests_in_flight():
    from types import SimpleNamespace

    state = {"open": 0, "peak": 0}

    class SlowClient:
        async def list_tools(self):
            return SimpleNamespace(tools=[])

        async def call_tool(self, tool, args):
            state["open"] += 1
            state["peak"] = max(state["peak"], state["open"])
            await anyio.sleep(0.02)
            state["open"] -= 1
            return SimpleNamespace(is_error=False, structured_content={"ok": 1}, content=[])

    s = connection.GuardedSession("zepto", SlowClient())

    async def burst():
        async with anyio.create_task_group() as tg:
            for i in range(20):
                tg.start_soon(s.call, "search_products", {"query": str(i)})

    anyio.run(burst)
    assert state["peak"] == connection.MAX_IN_FLIGHT == 4
