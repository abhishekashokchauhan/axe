"""Pack-size parsing so "500ml", "0.5 L" and "1 pack (500 ml)" compare equal."""

import re

# unit -> (dimension, multiplier to base unit: g / ml / pc)
_UNITS = {
    "kg": ("mass", 1000.0), "kgs": ("mass", 1000.0),
    "g": ("mass", 1.0), "gm": ("mass", 1.0), "gms": ("mass", 1.0), "gram": ("mass", 1.0), "grams": ("mass", 1.0),
    "l": ("volume", 1000.0), "ltr": ("volume", 1000.0), "litre": ("volume", 1000.0), "liter": ("volume", 1000.0),
    "ml": ("volume", 1.0),
    "pc": ("count", 1.0), "pcs": ("count", 1.0), "piece": ("count", 1.0), "pieces": ("count", 1.0),
    "unit": ("count", 1.0), "units": ("count", 1.0),
}

_MULTIPACK = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*([a-z]+)\b")  # "2 x 500 g" (Zepto)
_MULTIPACK_AFTER = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)\s*[x×]\s*(\d+)\b")  # "100 g x 4" (Instamart)
_SINGLE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)\b")


def parse_size(text: str) -> tuple[str, float] | None:
    """Return (dimension, amount in base unit) or None if no size is found.

    "2 x 500 ml" -> ("volume", 1000.0); "5 L" -> ("volume", 5000.0).
    """
    t = text.lower()
    m = _MULTIPACK.search(t)
    if m and m.group(3) in _UNITS:
        dim, mult = _UNITS[m.group(3)]
        return dim, int(m.group(1)) * float(m.group(2)) * mult
    m = _MULTIPACK_AFTER.search(t)
    if m and m.group(2) in _UNITS:
        dim, mult = _UNITS[m.group(2)]
        return dim, float(m.group(1)) * mult * int(m.group(3))
    found = [(_UNITS[u], float(n)) for n, u in _SINGLE.findall(t) if u in _UNITS]
    # Zepto writes "1 pc (5 L)" / "2 pcs (375 ml)": the count prefixes the real size.
    contents = [(dim, n * mult) for (dim, mult), n in found if dim != "count"]
    if contents:
        count = next((n for (dim, _), n in found if dim == "count"), 1.0)
        dim, amount = contents[0]
        return dim, amount * count
    if found:
        (dim, mult), n = found[0]
        return dim, n * mult
    return None


def same_size(a: str, b: str, tolerance: float = 0.01) -> bool:
    pa, pb = parse_size(a), parse_size(b)
    if not pa or not pb or pa[0] != pb[0]:
        return False
    return abs(pa[1] - pb[1]) <= tolerance * max(pa[1], pb[1])
