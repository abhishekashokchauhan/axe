import re
from typing import Any


def num(value: Any) -> float | None:
    """Coerce 123, "123.5", "₹1,299" or {"value": 123} to float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for k in ("value", "amount", "units"):
            if k in value:
                return num(value[k])
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    return float(m.group().replace(",", "")) if m else None


def first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default
