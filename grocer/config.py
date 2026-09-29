import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from grocer.models import Item
from grocer.optimizer import Fee, FeeSchedule
from grocer.units import parse_size

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_DIR / "grocery.yaml"

_SIZE_AT_END = re.compile(r"(\d+(?:\.\d+)?\s*(?:kg|kgs|g|gm|gms|grams?|ml|l|ltr|litres?|liters?|pcs?|pieces?))\s*$", re.I)
_QTY = re.compile(r"^(?:x\s*)?(\d+)\s*(?:x|pcs?|packs?)?$", re.I)


@dataclass
class Config:
    items: list[Item]
    platforms: dict[str, dict]
    fees: dict[str, FeeSchedule]  # only platforms whose fees are set by hand; the rest are learned
    advisor: dict = field(default_factory=dict)  # {model, effort} passed to `claude -p`
    list_file: Path | None = None


def parse_list_line(line: str) -> Item | None:
    """One grocery line -> Item. Accepts:

        Amul Gold milk pouch, 500ml
        Amul Gold milk pouch, 500ml, 2      (2 packs)
        Amul butter 500g                    (size at the end, no comma)

    The first word is taken as the brand.
    """
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    parts = [p.strip() for p in line.split(",") if p.strip()]
    qty = 1
    if len(parts) >= 2 and _QTY.match(parts[-1]):
        qty = int(_QTY.match(parts[-1]).group(1))
        parts = parts[:-1]
    if len(parts) >= 2:
        name, size = ", ".join(parts[:-1]), parts[-1]
    else:
        m = _SIZE_AT_END.search(parts[0])
        if not m:
            raise ValueError(f"no pack size found in {line!r}; write it like 'Amul butter, 500g'")
        name, size = parts[0][: m.start()].strip(), m.group(1)
    if parse_size(size) is None:
        raise ValueError(f"can't read pack size {size!r} in {line!r}; use g, kg, ml, l or pcs")
    return Item(name=name, brand=name.split()[0], size=size.replace(" ", ""), qty=qty)


def read_list_file(path: Path) -> list[Item]:
    items = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        try:
            item = parse_list_line(line)
        except ValueError as e:
            raise ValueError(f"{path.name} line {n}: {e}") from None
        if item:
            items.append(item)
    return items


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    raw = yaml.safe_load(path.read_text())
    list_file = Path(raw["list_file"]).expanduser() if raw.get("list_file") else None
    if list_file:
        if not list_file.exists():
            raise FileNotFoundError(f"grocery list not found: {list_file}")
        items = read_list_file(list_file)
    else:
        items = [Item(name=i["name"], brand=i["brand"], size=str(i["size"]), qty=int(i.get("qty", 1)), query=i.get("query"))
                 for i in raw.get("items") or []]
    if not items:
        raise ValueError(f"the grocery list {list_file or path} has no items")
    names = [i.name for i in items]
    if len(set(names)) != len(names):
        raise ValueError("each item may appear only once (use a quantity instead)")
    fees = {
        p: FeeSchedule(
            min_order=float(f.get("min_order", 0)),
            fees=tuple(Fee(x["name"], float(x["amount"]), x.get("below")) for x in f.get("fees", [])),
        )
        for p, f in (raw.get("fees") or {}).items()
        if isinstance(f, dict)  # "auto" (or absent) = learn from order history
    }
    advisor = {k: v for k, v in (raw.get("advisor") or {}).items() if k in ("model", "effort") and v}
    return Config(items, raw.get("platforms") or {}, fees, advisor, list_file)
