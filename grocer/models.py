from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    """One line of the weekly list, as the user writes it (exact SKU)."""

    name: str  # e.g. "Amul butter"
    brand: str  # e.g. "Amul"
    size: str  # e.g. "500g" -- parsed with units.parse_size
    qty: int = 1
    query: str | None = None  # search text override; defaults to "<name> <size>"

    @property
    def search_query(self) -> str:
        return self.query or f"{self.name} {self.size}"


@dataclass(frozen=True)
class Offer:
    """A purchasable variant returned by a platform search, normalised."""

    platform: str
    sku_id: str  # opaque id needed to add to cart on that platform
    name: str
    brand: str
    size_text: str  # raw pack-size text from the platform, e.g. "500 g"
    price: float  # selling price (what you pay), INR
    mrp: float | None
    in_stock: bool
    raw: dict = field(default_factory=dict, compare=False, repr=False)
    max_qty: int | None = None  # most packs you can buy now (stock left / per-order limit); None = unknown
