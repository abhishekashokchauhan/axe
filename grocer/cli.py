"""grocer: price your weekly list on Zepto + Instamart and pick the cheapest split.

  grocer login <platform>          browser login (phone + OTP), lists the server's tools
  grocer tools <platform>          dump tool schemas to .grocer-debug/
  grocer probe <platform> <query>  dump a raw search response to .grocer-debug/
  grocer addresses                 list Instamart saved addresses
  grocer compare                   search every item, let Claude choose the products, quote live
                                   bills and print the cheapest split (carts are left as they
                                   were; nothing is ever ordered)
  grocer quote-test <platform>     live bill for the first list item only (checks quoting works)
"""

import argparse
import json
from contextlib import AsyncExitStack
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import anyio

from grocer.advisor import Candidate, Decision, candidates_for, choose_pack, decide
from grocer.config import DEFAULT_CONFIG, Config, load_config
from grocer.connection import SERVERS, GuardedSession, connect
from grocer.fees import learn
from grocer.models import Item, Offer
from grocer.livefees import LiveFees, Quoter, live_optimize
from grocer.optimizer import FeeSchedule, greedy
from grocer.quote import CARTS, quote
from grocer.report import Scenario, render
from grocer.platforms import ADAPTERS, instamart
from grocer.units import parse_size

DEBUG_DIR = Path(".grocer-debug")


def _dump(name: str, data) -> Path:
    DEBUG_DIR.mkdir(exist_ok=True)
    path = DEBUG_DIR / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return path


async def cmd_login(platform: str) -> None:
    async with connect(platform) as s:
        tools = await s.list_tools()
    print(f"  {platform}: logged in ({len(tools)} tools available)")


async def cmd_tools(platform: str) -> None:
    async with connect(platform) as s:
        tools = await s.list_tools()
    path = _dump(f"{platform}-tools.json", [t.model_dump(mode="json", exclude_none=True) for t in tools])
    print(f"wrote {path}")


async def cmd_probe(platform: str, query: str, settings: dict) -> None:
    async with connect(platform) as s:
        searcher = await ADAPTERS[platform].Searcher.create(s, settings)
        raw = await searcher.search_raw(query)
    print(f"wrote {_dump(f'{platform}-search.json', raw)}")


async def cmd_addresses() -> None:
    async with connect("instamart") as s:
        for a in await instamart.list_addresses(s):
            print(json.dumps(a, ensure_ascii=False))


def _queries(item: Item) -> list[str]:
    # With and without the size, so nearby pack sizes show up when the exact one doesn't exist.
    return [item.query] if item.query else [item.search_query, item.name]


@dataclass
class PlatformRun:
    session: GuardedSession
    searcher: object
    offers: dict[str, list[Offer]]
    fallback: FeeSchedule  # fees by hand, or estimated from past orders if live quotes fail
    fee_note: str
    live: bool  # quote live bills (fees: auto) vs use the hand-set schedule
    cart: object  # InstamartCart / ZeptoCart
    cart_at_start: list[dict]  # restored at the end if a quote left the cart different


async def _open_platform(platform: str, cfg: Config, stack: AsyncExitStack) -> PlatformRun:
    """Log in, pick the address, note the cart, search every item, and prepare fees."""
    adapter = ADAPTERS[platform]
    s = await stack.enter_async_context(connect(platform))
    if platform in cfg.fees:
        fallback, note, live = cfg.fees[platform], "set by hand in grocery.yaml", False
    else:
        learned = learn(await adapter.fee_history(s))
        fallback, note, live = learned.schedule, f"live bill (fallback estimate: {learned.summary})", True
    searcher = await adapter.Searcher.create(s, cfg.platforms.get(platform) or {})
    cart = CARTS[platform](s, searcher.address_id)
    cart_at_start = await cart.contents()
    offers = {}
    for item in cfg.items:
        found = []
        for q in _queries(item):
            found += adapter.parse_search(await searcher.search_raw(q))
        offers[item.name] = found
    return PlatformRun(s, searcher, offers, fallback, note, live, cart, cart_at_start)


def _cart_key(items: list[dict]) -> list[tuple]:
    return sorted((i.get("spinId") or i.get("productVariantId"), i.get("quantity")) for i in items)


async def _verify_cart(p: str, run: PlatformRun) -> None:
    """Quotes restore the cart after each bill; double-check, and fix it if anything slipped."""
    try:
        if _cart_key(await run.cart.contents()) == _cart_key(run.cart_at_start):
            return
        await run.cart.restore(run.cart_at_start)
        ok = _cart_key(await run.cart.contents()) == _cart_key(run.cart_at_start)
        print(f"  ! {p}: cart differed after quoting; restored it{'' if ok else ' -- PLEASE CHECK, restore did not stick'}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! {p}: couldn't verify the cart was restored ({type(e).__name__}: {e}) -- please check it")


def _quoter(run: PlatformRun, packs_by_item: dict[str, tuple[Candidate, int]]) -> Quoter:
    async def q(names: frozenset[str]):
        return await quote(run.cart, [packs_by_item[n] for n in sorted(names)])
    return q


def _print_decisions(decisions: list[Decision], platforms: list[str]) -> None:
    for d in decisions:
        print(f"\n{d.item.name} {d.item.size}  ->  {d.product or '(nothing suitable)'}")
        for p in platforms:
            c = d.picks.get(p)
            if c:
                many = f"{c.packs} x " if c.packs > 1 else ""
                print(f"  {p:<10} {many}{c.offer.name} | {c.offer.size_text} | ₹{c.price:g}"
                      f"  (₹{c.unit_price:.2f}/unit; {d.size_note(p)})")
            else:
                print(f"  {p:<10} not available")
        print(f"  why: {d.reason}")


async def cmd_compare(cfg: Config) -> None:
    print(f"list: {cfg.list_file or 'grocery.yaml'} ({len(cfg.items)} items)")
    platforms = [p for p in cfg.platforms if p in SERVERS]
    async with AsyncExitStack() as stack:  # sessions stay open: quotes and final carts come later
        runs: dict[str, PlatformRun] = {}
        for p in platforms:  # sequential: each may need its own browser login
            print(f"{p}: logging in, searching ...")
            try:
                runs[p] = await _open_platform(p, cfg, stack)
            except Exception as e:  # one platform down shouldn't sink the comparison
                print(f"  ! {p} skipped: {type(e).__name__}: {e}")
                continue
            print(f"  fees: {runs[p].fee_note}")
        offers = {i.name: {p: r.offers[i.name] for p, r in runs.items()} for i in cfg.items}

        print("asking Claude to choose products ...")
        decisions = await decide(cfg.items, offers, **cfg.advisor)
        _print_decisions(decisions, sorted(runs))

        items = [(d.item.name, d.item.qty) for d in decisions]
        # Prices are per list quantity (2 x 500 g when that makes up your 1 kg).
        prices = {d.item.name: {p: c.price for p, c in d.picks.items() if c} for d in decisions}
        scales = {d.item.name: {p: parse_size(d.item.size)[1] / c.total_amount for p, c in d.picks.items() if c}
                  for d in decisions}
        packs = {p: {d.item.name: (d.picks[p], d.item.qty * d.picks[p].packs) for d in decisions if d.picks.get(p)}
                 for p in runs}
        live = LiveFees(
            quoters={p: _quoter(r, packs[p]) for p, r in runs.items() if r.live},
            fallback={p: r.fallback for p, r in runs.items()},
        )
        print("\ngetting live bills (each quote fills the cart, reads the bill, puts your cart back) ...")
        quoted_at = datetime.now().strftime("%d %b %Y, %I:%M %p")
        best = await live_optimize(items, prices, scales, live)
        naive = greedy(items, prices, live.fee_fn, scales)
        if naive:  # quote the naive split too, so the comparison uses real fees
            for p, b in naive.baskets.items():
                await live.quote(p, frozenset(n for n, _, _ in b.lines))
            naive = greedy(items, prices, live.fee_fn, scales)
        for line in live.log:
            print(f"  {line}")
        for p, why in live.broken.items():
            print(f"  ! {p}: {why}")
        for p, run in runs.items():
            await _verify_cart(p, run)
        if best is None:
            print("\nNo split meets every app's minimum order.")
            return

    # Each basket of the plan was quoted live (see livefees); show those bills.
    bills = {p: q for p, b in best.baskets.items()
             if (q := live.quotes.get((p, frozenset(n for n, _, _ in b.lines)))) is not None}
    scenarios = []
    for p in runs:
        sold = frozenset(n for n, _ in items if p in prices.get(n, {}))
        missing = tuple(n for n, _ in items if prices.get(n) and p not in prices[n])
        q = live.quotes.get((p, sold))
        total = None if missing or q is None else (q.to_pay if q.to_pay is not None else q.item_total + q.fee_total)
        scenarios.append(Scenario(p, total, missing))
    print(render(best, sorted(runs), bills, decisions, scenarios, naive.total if naive else None, quoted_at))


async def cmd_quote_test(platform: str, cfg: Config) -> None:
    """Quote a one-item basket (first item on your list) and print the live bill."""
    item = cfg.items[0]
    async with AsyncExitStack() as stack:
        run = await _open_platform(platform, cfg, stack)
        cands = candidates_for(item, run.offers[item.name], platform[0])
        # No Claude here (this only checks fees), so prefer the names sharing most words with the item.
        words = set(item.name.lower().split())
        best = max((len(words & set(c.offer.name.lower().split())) for c in cands), default=0)
        pack = choose_pack([c for c in cands if len(words & set(c.offer.name.lower().split())) == best])
        if pack is None:
            raise SystemExit(f"{item.name} {item.size} not found on {platform}")
        print(f"quoting {pack.packs} x {pack.offer.name} | {pack.offer.size_text} | ₹{pack.offer.price:g} on {platform} ...")
        q = await quote(run.cart, [(pack, pack.packs)])
    print(f"items ₹{q.item_total:g}; fees: {', '.join(f'{l} ₹{a:g}' for l, a in q.fees) or 'none'}; to pay: {q.to_pay}")
    print(f"raw bill saved: {_dump(f'{platform}-quote-test.json', q.raw)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="grocer", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("login", "tools"):
        sub.add_parser(name).add_argument("platform", choices=SERVERS)
    probe = sub.add_parser("probe")
    probe.add_argument("platform", choices=SERVERS)
    probe.add_argument("query")
    sub.add_parser("addresses")
    sub.add_parser("compare")
    sub.add_parser("quote-test").add_argument("platform", choices=SERVERS)
    args = ap.parse_args(argv)

    if args.cmd == "login":
        anyio.run(cmd_login, args.platform)
    elif args.cmd == "tools":
        anyio.run(cmd_tools, args.platform)
    elif args.cmd == "addresses":
        anyio.run(cmd_addresses)
    elif args.cmd == "probe":
        cfg = load_config(args.config)
        anyio.run(cmd_probe, args.platform, args.query, cfg.platforms.get(args.platform) or {})
    elif args.cmd == "quote-test":
        anyio.run(cmd_quote_test, args.platform, load_config(args.config))
    elif args.cmd == "compare":
        anyio.run(cmd_compare, load_config(args.config))


if __name__ == "__main__":
    main()
