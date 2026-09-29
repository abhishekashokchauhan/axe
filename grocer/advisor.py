"""Let Claude decide which product on each platform is the right buy for each list item.

Code does the mechanical parts -- search, size/stock filtering, price-per-unit --
and Claude makes the judgement call (is "Dettol Original Hand Wash Refill 1.35 L" the
"Dettol liquid refill" you meant? which toor dal is best value?). Claude runs through the
local Claude Code CLI in headless mode (`claude -p`), so it uses your existing Claude
login; no API key is needed. Its picks are validated against the candidate list.
"""

import json
import re
import shutil
import tempfile
from dataclasses import dataclass

import anyio

from grocer.models import Item, Offer
from grocer.units import parse_size

SIZE_WINDOW = (0.75, 2.0)  # acceptable pack size as a multiple of the requested size
EXACT_TOLERANCE = 0.01  # "500 g" vs "0.5 kg" etc.
MAX_PACKS = 6  # most identical packs combined to make up the exact quantity (2 x 500 g = 1 kg)
MAX_CANDIDATES_PER_PLATFORM = 20
CHUNKS = 3  # parallel Claude calls per run (measured: 28 s -> 13.5 s for 11 items)
_UNIT_LABEL = {"mass": "100 g", "volume": "100 ml", "count": "piece"}


@dataclass(frozen=True)
class Candidate:
    ref: str  # short id shown to Claude, e.g. "z3" / "i12"
    offer: Offer
    amount: float  # content of ONE pack in base units (g / ml / pc)
    unit_price: float  # ₹ per 100 g / 100 ml / piece
    exact: bool  # packs x amount is exactly the requested size
    packs: int = 1  # packs bought per list quantity (2 when 2 x 500 g make up 1 kg)

    @property
    def price(self) -> float:
        """What one list quantity costs with this pack (packs x pack price)."""
        return self.offer.price * self.packs

    @property
    def total_amount(self) -> float:
        return self.amount * self.packs

    def describe(self, dim: str) -> str:
        mrp = f" (MRP ₹{self.offer.mrp:g})" if self.offer.mrp and self.offer.mrp > self.offer.price else ""
        if not self.exact:
            tag = ""
        elif self.packs == 1:
            tag = " [EXACT SIZE]"
        else:
            tag = f" [EXACT SIZE as {self.packs} packs = ₹{self.price:g}]"
        return (f"{self.ref}: {self.offer.name} | pack {self.offer.size_text}{tag} | ₹{self.offer.price:g}{mrp}"
                f" | ₹{self.unit_price:.2f} per {_UNIT_LABEL[dim]}")


@dataclass(frozen=True)
class Decision:
    item: Item
    product: str  # the product Claude settled on, in words
    picks: dict[str, Candidate | None]  # platform -> chosen pack (None = not sold there)
    reason: str
    label: str = ""  # short display name, e.g. "Tata Sampann Toor Dal"

    def size_note(self, platform: str) -> str:
        c = self.picks.get(platform)
        if c is None:
            return ""
        if c.exact:
            return "exact size" if c.packs == 1 else f"exact size as {c.packs} packs"
        return "exact size not sold here; best-value pack"


def choose_pack(packs: list[Candidate]) -> Candidate | None:
    """Deterministic -- Claude doesn't pick sizes. In order of preference:
    1. your exact size in one pack (cheapest such listing);
    2. identical packs that add up to exactly your size, e.g. 2 x 500 g for 1 kg (cheapest total);
    3. the single pack between 0.75x and 2x your size with the lowest price per unit."""
    if not packs:
        return None
    for group in ([c for c in packs if c.exact and c.packs == 1], [c for c in packs if c.exact]):
        if group:
            return min(group, key=lambda c: (c.price, c.ref))
    return min(packs, key=lambda c: (c.unit_price, c.ref))


def candidates_for(item: Item, offers: list[Offer], prefix: str) -> list[Candidate]:
    """In-stock offers that either make up the requested size exactly (one pack, or 2-6
    identical packs) or are a single pack 0.75x-2x the requested size -- and of which you can
    buy enough (stock left / per-order limit).

    Brand and product fit are left to Claude (list lines like "Idly rice organic" have no
    brand, and spellings vary: idly/idli, batter/better); here they only order the list, so
    the most relevant products survive the per-app cap.
    """
    want = parse_size(item.size)
    words = {w for w in re.findall(r"[a-z0-9]+", item.name.lower()) if len(w) > 2}
    out, seen = [], set()
    for o in offers:
        if o.sku_id in seen or not o.in_stock:
            continue
        seen.add(o.sku_id)
        got = parse_size(o.size_text) or parse_size(o.name)
        if not got or got[0] != want[0]:
            continue
        ratio = got[1] / want[1]
        k = round(want[1] / got[1])
        if abs(ratio - 1) <= EXACT_TOLERANCE:
            packs, exact = 1, True
        elif 2 <= k <= MAX_PACKS and abs(k * got[1] - want[1]) <= EXACT_TOLERANCE * want[1]:
            packs, exact = k, True
        elif SIZE_WINDOW[0] <= ratio <= SIZE_WINDOW[1]:
            packs, exact = 1, False
        else:
            continue
        if o.max_qty is not None and o.max_qty < item.qty * packs:
            continue
        per = 100 if got[0] != "count" else 1
        relevance = len(words & set(re.findall(r"[a-z0-9]+", f"{o.brand} {o.name}".lower())))
        out.append((o, got[1], o.price / got[1] * per, exact, packs, relevance))
    out.sort(key=lambda t: (-t[5], not t[3], t[4], t[2]))  # relevant, exact, fewer packs, ₹/unit
    return [Candidate(f"{prefix}{n}", o, amt, up, ex, pk)
            for n, (o, amt, up, ex, pk, _) in enumerate(out[:MAX_CANDIDATES_PER_PLATFORM], 1)]


SYSTEM_PROMPT = """You are a grocery buyer for one household in Ahmedabad. For each item on their \
weekly list you choose which product to buy on each shopping app. The household does not \
want to be asked; your choice is final, so choose carefully.

Rules, in priority order:
1. Fit: the product must be the thing the item describes -- same kind of product \
(toor/tuver/arhar dal are the same; chana dal is not) and, if the item names a brand \
(usually its first word, e.g. "Amul butter"), that brand. Items without a brand ("tender \
coconut", "idly rice organic") may be any brand. Allow for spelling variants and typos \
(idly/idli, "idly better" means idli batter). Where the item's wording \
leaves room (e.g. "Dettol liquid refill"), choose the most common product people mean by \
it. Never choose a product that doesn't fit just because it is cheap. Format words in \
the item (pouch, tetra pack, bottle, refill, block, tub, cup, can, jar) are part of the \
product: "milk pouch" is not UHT milk in a tetra pack. If only other formats are listed, the \
item isn't available -- use empty lists.
2. Value: among products that fit, choose the best value. Compare products by the price \
per unit of the pack that would actually be bought: the [EXACT SIZE] pack if the product \
has one, else an [EXACT SIZE as N packs] option, otherwise its cheapest-per-unit pack. Premium variants (organic, etc.) get no \
preference; quality only breaks near-ties (within 2%).
3. Same product on both apps: settle on ONE product (same brand and variant) so the apps \
are compared like for like. Prefer a product sold on both apps; among those, apply rule 2. \
Only if no fitting product is sold on both apps, choose the best-value fitting product on \
each app separately.

You choose the product, not the pack size. For each app, list the ids of EVERY candidate \
that is the chosen product, in any pack size (the program then buys the exact requested \
size if it is listed, else identical packs that add up to it exactly, otherwise the \
best-value pack). Use an empty list for an app that \
doesn't sell it. Give a one-sentence reason a shopper would find useful, and a "label": a \
short display name for the chosen product -- brand and product only, no pack size, at most \
36 characters, never cut mid-word (e.g. "Tata Sampann Toor Dal", "Tata Simply Better Groundnut Oil").

Use only the candidate ids given."""


def _schema(platforms: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "product": {"type": "string"},
                        "label": {"type": "string"},
                        **{p: {"type": "array", "items": {"type": "string"}} for p in platforms},
                        "reason": {"type": "string"},
                    },
                    "required": ["item", "product", "label", *platforms, "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["decisions"],
        "additionalProperties": False,
    }


def _prompt(items: list[Item], cands: dict[str, dict[str, list[Candidate]]]) -> str:
    parts = ["Weekly list. For each item, candidates per app (in stock, acceptable size):\n"]
    for item in items:
        dim = parse_size(item.size)[0]
        parts.append(f'## item: "{item.name}" -- requested pack {item.size}')
        for platform, cs in cands[item.name].items():
            parts.append(f"{platform}:")
            parts.extend(f"  {c.describe(dim)}" for c in cs)
            if not cs:
                parts.append("  (no candidates)")
        parts.append("")
    parts.append('Return one decision per item, with "item" set to the item name exactly as written above.')
    return "\n".join(parts)


class AdvisorError(RuntimeError):
    pass


async def _run_claude(prompt: str, schema: dict, model: str | None, effort: str | None) -> dict:
    exe = shutil.which("claude")
    if not exe:
        raise AdvisorError("Claude Code CLI (`claude`) not found on PATH")
    cmd = [exe, "-p", prompt, "--system-prompt", SYSTEM_PROMPT, "--output-format", "json",
           "--json-schema", json.dumps(schema), "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    # Run outside the project so no CLAUDE.md or project settings leak into the decision.
    with anyio.fail_after(600):
        proc = await anyio.run_process(cmd, cwd=tempfile.gettempdir(), check=False)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AdvisorError(f"claude exited {proc.returncode}: {proc.stderr.decode()[:500] or proc.stdout.decode()[:500]}")
    if out.get("is_error") or "structured_output" not in out:
        raise AdvisorError(f"claude failed: {str(out.get('result'))[:500]}")
    return out["structured_output"]


async def decide(
    items: list[Item],
    offers: dict[str, dict[str, list[Offer]]],  # item name -> platform -> search results
    model: str | None = None,
    effort: str | None = None,
) -> list[Decision]:
    platforms = sorted({p for per in offers.values() for p in per})
    cands = {
        item.name: {p: candidates_for(item, offers[item.name].get(p, []), p[0]) for p in platforms}
        for item in items
    }
    # Each item is decided on its own, so the list is split into chunks Claude works on in
    # parallel: about twice as fast as one call, same model and quality.
    chunks = [c for c in (items[i::CHUNKS] for i in range(CHUNKS)) if c]
    results: list[dict] = [{}] * len(chunks)

    async def run(k: int, chunk: list[Item]) -> None:
        results[k] = await _run_claude(_prompt(chunk, cands), _schema(platforms), model, effort)

    async with anyio.create_task_group() as tg:
        for k, chunk in enumerate(chunks):
            tg.start_soon(run, k, chunk)

    by_item = {d["item"]: d for r in results for d in r.get("decisions", [])}
    decisions = []
    for item in items:
        d = by_item.get(item.name)
        if d is None:
            decisions.append(Decision(item, "", {p: None for p in platforms}, "Claude returned no decision"))
            continue
        picks = {}
        for p in platforms:
            valid = {c.ref: c for c in cands[item.name][p]}
            refs = d.get(p) or []
            bad = [r for r in refs if r not in valid]  # hallucinated / cross-app ids: drop, don't trust
            if bad:
                d["reason"] += f" [ignored invalid {p} ids {bad}]"
            picks[p] = choose_pack([valid[r] for r in refs if r in valid])
        decisions.append(Decision(item, d["product"], picks, d["reason"], d.get("label") or d["product"]))
    return decisions
