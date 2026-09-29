"""Cart operations: clear, fill, read the live bill; and quotes built from them.

This is the only way to see today's fees -- delivery/handling/small-cart fees, surge or
rain charges, free-delivery thresholds and membership waivers all change with time,
location and account, and neither app exposes them any other way.

- Instamart: update_cart (replaces cart) -> get_cart.billBreakdown -> restore
- Zepto:     update_cart -> create_order(confirmOrder=False) preview -> restore
  (the session guard refuses create_order unless confirmOrder is explicitly False)

A quote snapshots the cart first and restores it afterwards, even if the quote fails;
`compare` also checks at the end that each cart is back as it started. These MCP carts
are NOT the carts shown in the phone apps, so the tool never leaves anything in them.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grocer.advisor import Candidate
from grocer.connection import GuardedSession

DEBUG_DIR = Path(".grocer-debug")


@dataclass(frozen=True)
class Quote:
    platform: str
    item_total: float
    fees: tuple[tuple[str, float], ...]  # every bill line that isn't the item total (discounts negative)
    to_pay: float | None
    raw: Any = field(default=None, compare=False, repr=False)

    @property
    def fee_total(self) -> float:
        return sum(a for _, a in self.fees)


class QuoteError(RuntimeError):
    pass


_MONEY = re.compile(r"(-)?\s*₹?\s*(-)?\s*(\d[\d,]*(?:\.\d+)?)")


def _money(value: Any) -> float | None:
    """'₹12.00' -> 12.0, 'FREE' -> 0.0, '-₹15' -> -15.0, 1200 -> 1200.0."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    if value.strip().lower() in ("free", "₹0", "0"):
        return 0.0
    m = _MONEY.search(value)
    if not m:
        return None
    amount = float(m.group(3).replace(",", ""))
    return -amount if (m.group(1) or m.group(2)) else amount


def _is_item_total(label: str) -> bool:
    return re.sub(r"[^a-z]", "", label.lower()) in ("itemtotal", "itemstotal", "subtotal", "carttotal")


# --------------------------------------------------------------------------- Instamart

def parse_instamart_bill(cart: dict) -> Quote:
    bill = cart.get("billBreakdown") or {}
    item_total, fees = None, []
    for line in bill.get("lineItems") or []:
        label, amount = str(line.get("label", "")), _money(line.get("value"))
        if amount is None:
            continue
        if _is_item_total(label):
            item_total = amount
        else:
            fees.append((label, amount))
    if item_total is None:
        raise QuoteError(f"no item total in Instamart bill: {bill}")
    return Quote("instamart", item_total, tuple(fees), _money((bill.get("toPay") or {}).get("value")), cart)


class InstamartCart:
    """The account's Instamart cart. update_cart always replaces the whole cart."""

    platform = "instamart"

    def __init__(self, session: GuardedSession, address_id: str):
        self.session, self.address_id = session, address_id

    async def contents(self) -> list[dict]:
        return [i for i in ((await self.session.call("get_cart")).get("items") or []) if i.get("spinId")]

    @staticmethod
    def describe(item: dict) -> str:
        return f"{item.get('quantity', 1)} x {item.get('itemName') or item['spinId']} {item.get('itemVariant') or ''}".strip()

    async def clear(self) -> list[str]:
        removed = [self.describe(i) for i in await self.contents()]
        await self.session.call("clear_cart")
        return removed

    async def _put(self, items: list[dict]) -> None:
        if items:
            await self.session.call("update_cart", {"selectedAddressId": self.address_id, "items": items})
        else:
            await self.session.call("clear_cart")

    async def fill(self, packs: list[tuple[Candidate, int]]) -> None:
        await self._put([{"spinId": c.offer.sku_id, "skuId": c.offer.raw.get("skuId"), "quantity": q} for c, q in packs])

    async def bill(self, packs: list[tuple[Candidate, int]]) -> Quote:
        return parse_instamart_bill(await self.session.call("get_cart"))

    async def restore(self, saved: list[dict]) -> None:
        await self._put([{"spinId": i["spinId"], "skuId": i.get("skuId"), "quantity": i["quantity"]} for i in saved])


async def quote(cart, packs: list[tuple[Candidate, int]]) -> Quote:
    """Live bill for `packs` without leaving them in the cart."""
    saved = await cart.contents()
    try:
        await cart.fill(packs)
        return await cart.bill(packs)
    finally:
        await cart.restore(saved)


async def quote_instamart(session: GuardedSession, address_id: str, packs: list[tuple[Candidate, int]]) -> Quote:
    return await quote(InstamartCart(session, address_id), packs)


# --------------------------------------------------------------------------- Zepto

_FEE_WORDS = ("fee", "charge", "surge", "rain", "tip", "discount", "saving", "coupon", "tax", "packag", "handling", "delivery")


def parse_zepto_preview(preview: Any, expected_item_total: float) -> Quote:
    """Read Zepto's create_order(confirmOrder=False) preview.

    Real shape: {isPreview: true, requiresConfirmation: true, toPay: "₹59", toPayAmount: 5900,
    subTotal: null, deliveryFee: 3000, packagingFee: null, taxes: null, discount: null, ...}
    -- amounts in paise, and item prices / subTotal often null. So the item total is the
    basket's own prices (unless subTotal is given) and fees = to-pay minus items, which also
    catches charges Zepto doesn't itemise (small-cart, surge, rain). Named fee fields are
    listed separately where present.
    """
    if isinstance(preview, str):
        return _parse_zepto_preview_text(preview)
    if not isinstance(preview, dict):
        raise QuoteError(f"unexpected Zepto preview: {str(preview)[:200]}")
    if preview.get("isPreview") is not True:
        raise QuoteError("Zepto response is not marked as a preview -- refusing to use it")

    to_pay_text = _money(preview.get("toPay"))
    to_pay_amount = preview.get("toPayAmount")
    if isinstance(to_pay_amount, (int, float)):
        scale = to_pay_amount / to_pay_text if to_pay_text else 100.0  # paise per rupee
        to_pay = to_pay_amount / scale
    elif to_pay_text is not None:
        scale, to_pay = 100.0, to_pay_text
    else:
        raise QuoteError("Zepto preview has no amount to pay")

    sub = preview.get("subTotal")
    item_total = sub / scale if isinstance(sub, (int, float)) else expected_item_total

    itemised = []
    for key, value in preview.items():
        if key in ("toPayAmount", "subTotal") or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if any(w in key.lower() for w in _FEE_WORDS):
            amount = value / scale
            if "discount" in key.lower() or "saving" in key.lower():
                amount = -abs(amount)
            itemised.append((_label(key), amount))
    residual = round(to_pay - item_total - sum(a for _, a in itemised), 2)
    fees = itemised + ([("other charges", residual)] if abs(residual) >= 0.5 else [])
    return Quote("zepto", item_total, tuple(fees), to_pay, preview)


def _label(key: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key).lower()


def _parse_zepto_preview_text(preview: str) -> Quote:
    lines = []
    for raw_line in preview.splitlines():
        m = re.match(r"\s*[-•*]?\s*([A-Za-z][A-Za-z &/()]+?)\s*[:\-–]\s*(.+)$", raw_line)
        if m and (amount := _money(m.group(2))) is not None:
            lines.append((m.group(1).strip(), amount))
    item_total = next((a for label, a in lines if _is_item_total(label)), None)
    if item_total is None:
        raise QuoteError("no item total in Zepto preview text")
    to_pay = next((a for label, a in lines if re.search(r"to pay|grand total|total bill|total amount", label, re.I)), None)
    fees = [(label, a) for label, a in lines
            if not _is_item_total(label) and any(w in label.lower() for w in _FEE_WORDS)]
    return Quote("zepto", item_total, tuple(fees), to_pay, preview)


class ZeptoCart:
    """Zepto cart via update_cart (keyed by the MCP session; deviceId is a fallback key).

    There is no clear tool: items are removed by setting their quantity to 0.
    """

    platform = "zepto"

    def __init__(self, session: GuardedSession, address_id: str):
        self.session, self.address_id = session, address_id
        self.device = str(uuid.uuid4())

    async def contents(self) -> list[dict]:
        items = (await self.session.call("view_cart")).get("items") or []
        return [i for i in items if i.get("productVariantId") and i.get("storeProductId")]

    @staticmethod
    def describe(item: dict) -> str:
        return f"{item.get('quantity', 1)} x {item.get('name') or item.get('label') or item['productVariantId']}"

    @staticmethod
    def _line(item: dict, quantity: int | None = None) -> dict:
        return {"productVariantId": item["productVariantId"], "storeProductId": item["storeProductId"],
                "quantity": item["quantity"] if quantity is None else quantity}

    async def clear(self) -> list[str]:
        current = await self.contents()
        if current:
            await self.session.call("update_cart", {"deviceId": self.device,
                                                    "cartItems": [self._line(i, 0) for i in current]})
        return [self.describe(i) for i in current]

    async def fill(self, packs: list[tuple[Candidate, int]]) -> None:
        lines = [{"productVariantId": c.offer.sku_id, "storeProductId": c.offer.raw["storeProductId"], "quantity": q}
                 for c, q in packs]
        await self.session.call("update_cart", {"deviceId": self.device, "cartItems": lines, "replaceCart": True})

    async def bill(self, packs: list[tuple[Candidate, int]]) -> Quote:
        preview = await self.session.call("create_order", {"confirmOrder": False, "userAddressId": self.address_id})
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "zepto-preview-latest.json").write_text(json.dumps(preview, indent=2, ensure_ascii=False, default=str))
        return parse_zepto_preview(preview, sum(c.offer.price * q for c, q in packs))

    async def restore(self, saved: list[dict]) -> None:
        if saved:
            await self.session.call("update_cart", {"deviceId": self.device,
                                                    "cartItems": [self._line(i) for i in saved], "replaceCart": True})
        else:
            await self.clear()


async def quote_zepto(session: GuardedSession, address_id: str, packs: list[tuple[Candidate, int]]) -> Quote:
    return await quote(ZeptoCart(session, address_id), packs)


CARTS = {"zepto": ZeptoCart, "instamart": InstamartCart}
