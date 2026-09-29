"""Swiggy Instamart via the official MCP (https://mcp.swiggy.com/im).

search_products(addressId, query) -> {success, data: {products: [{displayName, brand,
variations: [{quantityDescription, price: {mrp, offerPrice}, spinId, isInStockAndAvailable}]}]}}
The address decides the dark store, so prices/stock are for that address.
"""

from grocer.connection import GuardedSession
from grocer.fees import Observation
from grocer.models import Item, Offer
from grocer.platforms._parse import first, num

PLATFORM = "instamart"


async def list_addresses(session: GuardedSession) -> list[dict]:
    res = await session.call("get_addresses")
    data = res.get("data", res) if isinstance(res, dict) else res
    if isinstance(data, dict):
        data = first(data, "addresses", "items", default=[])
    return data or []


async def fee_history(session: GuardedSession, limit: int = 10) -> list[Observation]:
    """get_orders -> orders[].billDetails = {itemTotal, deliveryFee, packagingFee, grandTotal} (₹)."""
    res = await session.call("get_orders", {"count": limit})
    out = []
    for o in res.get("orders") or []:
        bill = o.get("billDetails") or {}
        if o.get("status") != "DELIVERED" or num(bill.get("itemTotal")) is None:
            continue
        fees = {k: num(v) or 0.0 for k, v in bill.items() if "fee" in k.lower() or "charge" in k.lower()}
        out.append(Observation(num(bill["itemTotal"]), fees))
    return out


async def resolve_address(session: GuardedSession, configured: str | None) -> str:
    if configured:
        return configured
    addresses = await list_addresses(session)
    if not addresses:
        raise RuntimeError("No saved Instamart address; add one in the Swiggy app first")
    # get_addresses is sorted by recency; use the most recent unless configured.
    return str(first(addresses[0], "id", "addressId"))


def parse_search(payload: dict) -> list[Offer]:
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    offers = []
    for p in (data.get("products") or []) + (data.get("similarProducts") or []):
        name = first(p, "displayName", "name", default="")
        brand = first(p, "brand", default="")
        for v in p.get("variations") or [p]:
            price = v.get("price") or {}
            selling = num(first(price, "offerPrice", "sellingPrice")) or num(price.get("mrp"))
            sku = first(v, "spinId", "skuId", "productId")
            if selling is None or sku is None:
                continue
            offers.append(Offer(
                platform=PLATFORM,
                sku_id=str(sku),
                name=first(v, "displayName", default=name),
                brand=brand,
                size_text=first(v, "quantityDescription", "quantity", default=""),
                price=selling,
                mrp=num(price.get("mrp")),
                in_stock=bool(first(v, "isInStockAndAvailable", "inStock", default=False)),
                raw=v,
                max_qty=int(v["maxQuantity"]) if isinstance(v.get("maxQuantity"), (int, float)) else None,  # per order
            ))
    return offers


class Searcher:
    def __init__(self, session: GuardedSession, address_id: str):
        self.session = session
        self.address_id = address_id

    @classmethod
    async def create(cls, session: GuardedSession, settings: dict) -> "Searcher":
        return cls(session, await resolve_address(session, settings.get("address_id")))

    async def search_raw(self, query: str):
        return await self.session.call("search_products", {"addressId": self.address_id, "query": query})

    async def search(self, item: Item) -> list[Offer]:
        return parse_search(await self.search_raw(item.search_query))
