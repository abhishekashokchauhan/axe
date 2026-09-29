"""grocer: price your weekly list on Zepto + Instamart and pick the cheapest split.

  grocer login <platform>          browser login (phone + OTP), lists the server's tools
  grocer tools <platform>          dump tool schemas to .grocer-debug/
  grocer probe <platform> <query>  dump a raw search response to .grocer-debug/
  grocer addresses                 list Instamart saved addresses
  grocer compare                   search every item, let Claude choose the products, quote live
                                   bills and print the cheapest split (carts are left as they
                                   were; nothing is ever ordered). --details shows everything,
                                   --save FILE writes it to a file, --list FILE uses another list
  grocer quote-test <platform>     live bill for the first list item only (checks quoting works)
"""

import argparse
import json
import time
from contextlib import AsyncExitStack
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import anyio
from rich.console import Console
from rich.text import Text

from grocer.advisor import Candidate, Decision, candidates_for, choose_pack, decide
from grocer.config import DEFAULT_CONFIG, Config, load_config, read_list_file
from grocer.connection import SERVERS, GuardedSession, connect
from grocer.fees import learn
from grocer.models import Item, Offer
from grocer.livefees import LiveFees, Quoter, live_optimize
from grocer.optimizer import FeeSchedule, greedy
from grocer.quote import CARTS, quote
from grocer.pretty import ACCENT, BAD, OK, print_result
from grocer.report import APP_NAMES, Scenario, render
from grocer.platforms import ADAPTERS, instamart
from grocer.units import parse_size

DEBUG_DIR = Path(".grocer-debug")


def _dump(name: str, data) -> Path:
    DEBUG_DIR.mkdir(exist_ok=True)
    path = DEBUG_DIR / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return path


async def cmd_login(platform: str, quiet: bool = False) -> None:
    async with connect(platform) as s:
        tools = await s.list_tools()
    if not quiet:
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


def _first_query(item: Item) -> str:
    return item.query or item.search_query  # "Amul butter 500g": best at surfacing the exact pack


def _sold_out(item: Item, offers: list[Offer]) -> bool:
    """Was the item listed in your size but out of stock? (so "try again later" is worth saying)"""
    words = {w for w in item.name.lower().split() if len(w) > 2}
    want = parse_size(item.size)
    for o in offers:
        got = parse_size(o.size_text) or parse_size(o.name)
        if not o.in_stock and got and got[0] == want[0] and len(words & set(o.name.lower().split())) >= min(2, len(words)):
            return True
    return False


def _needs_second_search(item: Item, offers: list[Offer]) -> bool:
    """Search by name alone only when the sized search found no exact-size pack: that is
    when other sizes (or several smaller packs) are worth seeing. Measured on real lists,
    a second search after an exact match only added unrelated products."""
    return not item.query and not any(c.exact for c in candidates_for(item, offers, "x"))


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


async def _prepare_platform(platform: str, s: GuardedSession, cfg: Config) -> PlatformRun:
    """Pick the address, note the cart, prepare fees, and search every item -- concurrently."""
    adapter = ADAPTERS[platform]
    fees: dict = {}

    async def fallback_fees() -> None:  # only used if a live bill fails, so it runs alongside the searches
        if platform in cfg.fees:
            fees.update(schedule=cfg.fees[platform], note="set by hand in grocery.yaml", live=False)
        else:
            learned = learn(await adapter.fee_history(s))
            fees.update(schedule=learned.schedule, note=f"live bill (fallback estimate: {learned.summary})", live=True)

    searcher = await adapter.Searcher.create(s, cfg.platforms.get(platform) or {})
    cart = CARTS[platform](s, searcher.address_id)
    cart_at_start = await cart.contents()

    offers: dict[str, list[Offer]] = {}

    async def search(item: Item) -> None:  # the session caps requests in flight per app
        found = adapter.parse_search(await searcher.search_raw(_first_query(item)))
        if _needs_second_search(item, found):
            found += adapter.parse_search(await searcher.search_raw(item.name))
        offers[item.name] = found

    async with anyio.create_task_group() as tg:
        tg.start_soon(fallback_fees)
        for item in cfg.items:
            tg.start_soon(search, item)
    return PlatformRun(s, searcher, offers, fees["schedule"], fees["note"], fees["live"], cart, cart_at_start)


def _alternatives(items, prices, runs) -> list[tuple[str, dict[str, frozenset[str]]]]:
    """For each app: buy everything it sells there, and each item it lacks on the cheapest
    other app that has it. Returns [(app, {app: basket, other app: basket, ...})]."""
    out = []
    for p in runs:
        baskets: dict[str, set[str]] = {}
        for n, _ in items:
            where = prices.get(n) or {}
            if not where:
                continue  # sold nowhere
            q = p if p in where else min(where, key=lambda a: (where[a], a))
            baskets.setdefault(q, set()).add(n)
        if p in baskets:
            out.append((p, {q: frozenset(ns) for q, ns in baskets.items()}))
    return out


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


def _decision_lines(decisions: list[Decision], platforms: list[str]) -> list[str]:
    out = []
    for d in decisions:
        out.append(f"\n{d.item.name} {d.item.size}  ->  {d.product or '(nothing suitable)'}")
        for p in platforms:
            c = d.picks.get(p)
            if c:
                many = f"{c.packs} x " if c.packs > 1 else ""
                out.append(f"  {p:<10} {many}{c.offer.name} | {c.offer.size_text} | ₹{c.price:g}"
                           f"  (₹{c.unit_price:.2f}/unit; {d.size_note(p)})")
            else:
                out.append(f"  {p:<10} not available")
        out.append(f"  why: {d.reason}")
    return out


class Progress:
    """One line per step: a live spinner while it runs, then "✓ done  12.3s"."""

    def __init__(self, console: Console):
        self.console = console  # records what's printed, for --save
        self._spinner_console = Console(highlight=False)  # not recorded: spinner frames never reach the file
        self._status = None
        self._t0 = time.monotonic()

    def start(self, text: str) -> None:
        self._t0 = time.monotonic()
        if self._spinner_console.is_terminal:
            self._status = self._spinner_console.status(Text(text, style="dim"), spinner="dots",
                                                        spinner_style=ACCENT)
            self._status.start()

    def _stop(self) -> None:
        if self._status:
            self._status.stop()
            self._status = None

    def done(self, text: str) -> None:
        self._stop()
        took = time.monotonic() - self._t0
        self.console.print(Text.assemble(("✓ ", OK), text, (f"  {took:.1f}s", "dim")))

    def warn(self, text: str) -> None:
        self._stop()
        self.console.print(Text.assemble(("! ", BAD), text))


async def cmd_compare(cfg: Config, details: bool = False, save: Path | None = None) -> None:
    list_name = cfg.list_file.name if cfg.list_file else "grocery.yaml"
    console = Console(record=True, highlight=False)
    console.print(Text.assemble(("🛒 axe", "bold"), (f"  ·  {len(cfg.items)} items from {list_name}", "dim")))
    progress = Progress(console)
    log: list[str] = []  # the full story, for --details / --save
    platforms = [p for p in cfg.platforms if p in SERVERS]
    async with AsyncExitStack() as stack:  # sessions stay open: quotes come after Claude decides
        runs: dict[str, PlatformRun] = {}
        clock = time.monotonic()
        progress.start("Searching " + " and ".join(APP_NAMES.get(p, p) for p in platforms))
        sessions = {}
        for p in platforms:  # one at a time: a login may need the (single) browser callback
            try:
                sessions[p] = await stack.enter_async_context(connect(p))
            except Exception as e:  # one platform down shouldn't sink the comparison
                progress.warn(f"{APP_NAMES.get(p, p)} skipped: {type(e).__name__}: {e}")

        async def prepare(p: str) -> None:
            try:
                runs[p] = await _prepare_platform(p, sessions[p], cfg)
            except Exception as e:  # noqa: BLE001
                progress.warn(f"{APP_NAMES.get(p, p)} skipped: {type(e).__name__}: {e}")

        async with anyio.create_task_group() as tg:  # both apps at once
            for p in sessions:
                tg.start_soon(prepare, p)
        runs = {p: runs[p] for p in platforms if p in runs}  # stable order
        if not runs:
            return
        log += [f"{p}: fees: {r.fee_note}" for p, r in runs.items()]
        t_search = time.monotonic() - clock
        progress.done(f"Searched {len(cfg.items)} items on " + " and ".join(APP_NAMES.get(p, p) for p in runs))
        offers = {i.name: {p: r.offers[i.name] for p, r in runs.items()} for i in cfg.items}

        progress.start("Claude is choosing the products")
        clock = time.monotonic()
        decisions = await decide(cfg.items, offers, **cfg.advisor)
        t_claude = time.monotonic() - clock
        progress.done("Claude chose the product for each line")
        log += _decision_lines(decisions, sorted(runs))

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
        progress.start("Reading live bills")
        clock = time.monotonic()
        quoted_at = datetime.now().strftime("%d %b %Y, %I:%M %p")
        best = await live_optimize(items, prices, scales, live)
        naive = greedy(items, prices, live.fee_fn, scales)
        # Alternatives to compare against, quoted live too: the cheapest-per-item split, and
        # for each app "all you can there, the rest on the other app".
        alts = _alternatives(items, prices, runs)
        extra = [(p, frozenset(n for n, _, _ in b.lines)) for p, b in naive.baskets.items()] if naive else []
        await live.quote_many(extra + [pair for _, baskets in alts for pair in baskets.items()])
        if naive:
            naive = greedy(items, prices, live.fee_fn, scales)
        async with anyio.create_task_group() as tg:
            for p, run in runs.items():
                tg.start_soon(_verify_cart, p, run)
        t_bills = time.monotonic() - clock
        log += ["", "live bills (each quote fills the cart, reads the bill, puts your cart back):"]
        log += [f"  {line}" for line in live.log]
        for p, why in live.broken.items():
            progress.warn(f"{APP_NAMES.get(p, p)}: {why}")
        progress.done("Live bills read · fees included" if not live.broken else "Bills read")
        retries = {p: s.retries for p, s in sessions.items() if s.retries}
        log.append(f"timings: searching {t_search:.1f}s · Claude {t_claude:.1f}s · live bills {t_bills:.1f}s"
                   + (f" · rate-limit retries {retries}" if retries else ""))
        if best is None:
            print("\nNo split meets every app's minimum order.")
            return

    # Each basket of the plan was quoted live (see livefees); show those bills.
    bills = {p: q for p, b in best.baskets.items()
             if (q := live.quotes.get((p, frozenset(n for n, _, _ in b.lines)))) is not None}
    scenarios = []
    for p, baskets in alts:
        missing = tuple(n for n, _ in items if prices.get(n) and p not in prices[n])
        quotes = [live.quotes.get(pair) for pair in baskets.items()]
        total = None if not quotes or any(q is None for q in quotes) else \
            sum(q.to_pay if q.to_pay is not None else q.item_total + q.fee_total for q in quotes)
        scenarios.append(Scenario(p, total, missing, tuple(q for q in baskets if q != p)))
    naive_total = naive.total if naive else None
    short_time = datetime.now().strftime("%d %b, %I:%M %p")
    sold_out = {n for n in best.unavailable if _sold_out(next(i for i in cfg.items if i.name == n),
                                                         [o for r in runs.values() for o in r.offers[n]])}
    print_result(console, best, sorted(runs), bills, decisions, scenarios, short_time, sold_out,
                 save.name if save else None)
    full = render(best, sorted(runs), bills, decisions, scenarios, naive_total, quoted_at)
    if details:
        console.print(Text("\n".join(log) + "\n" + full))
    if save:  # what was on screen (as plain text) first, then the full details
        on_screen = console.export_text(styles=False, clear=False)
        rule = "\n" + "=" * 30 + " details " + "=" * 30
        save.write_text(on_screen + rule + "\n" + "\n".join(log) + "\n" + full + "\n")


async def cmd_quote_test(platform: str, cfg: Config) -> None:
    """Quote a one-item basket (first item on your list) and print the live bill."""
    item = cfg.items[0]
    async with AsyncExitStack() as stack:
        run = await _prepare_platform(platform, await stack.enter_async_context(connect(platform)), cfg)
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
    login = sub.add_parser("login")
    login.add_argument("platform", choices=SERVERS)
    login.add_argument("--quiet", action="store_true", help="print nothing on success")
    sub.add_parser("tools").add_argument("platform", choices=SERVERS)
    probe = sub.add_parser("probe")
    probe.add_argument("platform", choices=SERVERS)
    probe.add_argument("query")
    sub.add_parser("addresses")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--details", action="store_true", help="also show Claude's choices, the quotes and the full report")
    cmp_.add_argument("--save", type=Path, help="write the full details to this file")
    cmp_.add_argument("--list", type=Path, help="use this grocery list instead of list_file in grocery.yaml")
    sub.add_parser("quote-test").add_argument("platform", choices=SERVERS)
    args = ap.parse_args(argv)

    if args.cmd == "login":
        anyio.run(cmd_login, args.platform, args.quiet)
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
        try:
            cfg = load_config(args.config)
            if args.list:
                cfg.list_file = args.list.expanduser().resolve()
                cfg.items = read_list_file(cfg.list_file)
        except (ValueError, FileNotFoundError) as e:  # a problem with the list: say so plainly
            Console(stderr=True, highlight=False).print(Text.assemble(("✗ ", BAD), str(e)))
            raise SystemExit(1)
        anyio.run(cmd_compare, cfg, args.details, args.save)


if __name__ == "__main__":
    main()
