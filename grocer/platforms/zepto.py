"""Zepto via the official MCP (https://mcp.zepto.co.in/mcp).

select_saved_address(addressId) pins the session to that address's dark store, then
search_products(query) -> {products: [{name, packSize, price, mrp, availableQuantity,
productVariantId, isAd}], ...}. Prices are in paise. There is no brand field.
"""

from grocer.connection import GuardedSession
from grocer.fees import Observation
from grocer.models import Item, Offer

PLATFORM = "zepto"


def parse_search(payload: dict) -> list[Offer]:
    offers = []
    for p in payload.get("products") or []:
        sku = p.get("productVariantId") or p.get("id")
        if p.get("price") is None or not sku:
            continue
        qty = p.get("availableQuantity")
        offers.append(Offer(
            platform=PLATFORM,
            sku_id=str(sku),
            name=p.get("name") or "",
            brand="",  # not provided; the matcher looks for the brand in the name
            size_text=p.get("packSize") or "",
            price=p["price"] / 100,
            mrp=p["mrp"] / 100 if p.get("mrp") is not None else None,
            in_stock=qty is None or qty > 0,
            raw=p,
            max_qty=int(qty) if qty is not None else None,  # units left at your store
        ))
    return offers


async def fee_history(session: GuardedSession, limit: int = 10) -> list[Observation]:
    """billSummary is {itemTotal, totalBill, totalSaved, ...fee keys...}, in paise.

    Fee keys aren't itemised on orders so far, so any totalBill above itemTotal counts as
    one lump "fees" charge.
    """
    history = await session.call("list_order_history", {})
    out = []
    for o in (history.get("orders") or [])[:limit]:
        if o.get("formattedStatus") != "DELIVERED":
            continue
        bill = (await session.call("get_order_detail", {"orderId": o["id"]})).get("billSummary") or {}
        if "itemTotal" not in bill:
            continue
        fees = {k: v / 100 for k, v in bill.items() if isinstance(v, (int, float)) and ("fee" in k.lower() or "charge" in k.lower())}
        if not fees:
            fees = {"fees": max(0.0, (bill.get("totalBill", bill["itemTotal"]) - bill["itemTotal"]) / 100)}
        out.append(Observation(bill["itemTotal"] / 100, fees))
    return out


async def list_addresses(session: GuardedSession) -> list[dict]:
    return (await session.call("list_saved_addresses")).get("addresses") or []


class Searcher:
    def __init__(self, session: GuardedSession, address_id: str):
        self.session = session
        self.address_id = address_id

    @classmethod
    async def create(cls, session: GuardedSession, settings: dict) -> "Searcher":
        address_id = settings.get("address_id")
        if not address_id:
            addresses = await list_addresses(session)
            if not addresses:
                raise RuntimeError("No saved Zepto address; add one in the Zepto app first")
            address_id = addresses[0]["id"]
        await session.call("select_saved_address", {"addressId": address_id})
        return cls(session, address_id)

    async def search_raw(self, query: str):
        return await self.session.call("search_products", {"query": query})

    async def search(self, item: Item) -> list[Offer]:
        return parse_search(await self.search_raw(item.search_query))
