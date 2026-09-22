"""Entry point: scrape -> filter -> dedupe -> diff -> notify -> persist."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG, TARGETS, Target
from .filters import apply_filters, dedupe_by_vin
from .http import Fetcher
from .models import Listing, utc_now
from .notify import Notification, NtfyClient, build_new_listing, build_price_drop
from . import state as state_mod
from .sources.autotrader import SOURCE as AUTOTRADER
from .sources.cargurus import SOURCE as CARGURUS
from .sources.carmax import SOURCE as CARMAX
from .sources.cars_com import SOURCE as CARS_COM

log = logging.getLogger("carbot")

ALL_SOURCES = [CARS_COM, CARMAX, AUTOTRADER, CARGURUS]


@dataclass
class Diff:
    new: list[Listing] = field(default_factory=list)
    price_drops: list[tuple[Listing, int, int]] = field(default_factory=list)
    disappeared: list[Listing] = field(default_factory=list)
    unchanged: int = 0


# -- pure logic (unit-tested) -------------------------------------------------


def diff_listings(
    previous: dict[str, Listing],
    current: list[Listing],
    drop_threshold: int,
) -> tuple[Diff, dict[str, Listing]]:
    """Compare this run against the last, and build the state to persist.

    Returns (diff, next_state). Listings that vanished are kept in state with
    their original last_seen and a 'gone' status - they are never notified.
    """
    result = Diff()
    next_state: dict[str, Listing] = {}
    seen_now: set[str] = set()

    for listing in current:
        seen_now.add(listing.vin)
        old = previous.get(listing.vin)
        if old is None:
            listing.first_seen = listing.last_seen = utc_now()
            result.new.append(listing)
            next_state[listing.vin] = listing
            continue

        # Known VIN: carry provenance forward, refresh the rest.
        listing.first_seen = old.first_seen
        listing.last_seen = utc_now()
        listing.urls = {**old.urls, **listing.urls}

        if (
            old.price is not None
            and listing.price is not None
            and old.price - listing.price >= drop_threshold
        ):
            result.price_drops.append((listing, old.price, listing.price))
        else:
            result.unchanged += 1
        next_state[listing.vin] = listing

    for vin, old in previous.items():
        if vin in seen_now:
            continue
        old.status = "gone"  # last_seen deliberately left at its old value
        result.disappeared.append(old)
        next_state[vin] = old

    return result, next_state


def collect(sources, targets: list[Target], fetcher: Fetcher) -> list[Listing]:
    """Run every enabled source. One source failing never stops the rest."""
    collected: list[Listing] = []
    for source in sources:
        if not CONFIG["sources"].get(source.name, True):
            log.info("[%s] disabled in config - skipping", source.name)
            continue
        log.info("--- %s ---", source.name)
        try:
            found = source.fetch(targets, fetcher)
            collected.extend(found)
            log.info("[%s] returned %d listings", source.name, len(found))
        except Exception as exc:  # a source must never take the run down
            log.exception("[%s] crashed, continuing without it: %s", source.name, exc)
    return collected


# -- reporting ---------------------------------------------------------------


def _print_listings(listings: list[Listing]) -> None:
    if not listings:
        print("  (none)")
        return
    for lst in sorted(listings, key=lambda x: (x.price or 0)):
        price = f"${lst.price:,}" if lst.price is not None else "n/a"
        miles = f"{lst.mileage:,} mi" if lst.mileage is not None else "n/a"
        dist = f"~{round(lst.distance_mi):,} mi" if lst.distance_mi is not None else "?"
        print(f"  {price:>9}  {miles:>10}  {lst.title[:46]:46s}")
        print(f"             {lst.location} ({dist}) · {lst.dealer[:34]}")
        print(f"             {', '.join(lst.flags) or 'no flags'} · "
              f"{'+'.join(lst.sources)} · {lst.status}")
        print(f"             {lst.vin} · {lst.url}")


def _notifications_for(diff: Diff) -> list[Notification]:
    notes = [build_new_listing(lst) for lst in diff.new]
    notes += [build_price_drop(lst, old, new) for lst, old, new in diff.price_drops]
    return notes


# -- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m carbot.main",
        description="Scan used-car sites and push ntfy alerts for new matches.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be sent; send nothing, write nothing")
    p.add_argument("--seed", action="store_true",
                   help="populate state from this run without notifying (first run)")
    p.add_argument("--state", type=Path, default=state_mod.DEFAULT_PATH,
                   help=f"state file path (default: {state_mod.DEFAULT_PATH})")
    p.add_argument("--source", action="append", dest="only_sources", metavar="NAME",
                   help="limit to one source (repeatable): cars.com, carmax, "
                        "autotrader, cargurus")
    p.add_argument("--show-rejected", action="store_true",
                   help="also print listings that failed the filters, and why")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    sources = ALL_SOURCES
    if args.only_sources:
        wanted = set(args.only_sources)
        sources = [s for s in ALL_SOURCES if s.name in wanted]
        if not sources:
            log.error("No source matched %s", sorted(wanted))
            return 2

    mode = "DRY RUN" if args.dry_run else ("SEED" if args.seed else "LIVE")
    log.info("carbot starting (%s) - %d targets, %d sources",
             mode, len(TARGETS), len(sources))

    fetcher = Fetcher(
        delay_range=CONFIG["request_delay_seconds"],
        timeout=CONFIG["request_timeout"],
        max_retries=CONFIG["max_retries"],
    )

    raw = collect(sources, TARGETS, fetcher)
    kept, rejected = apply_filters(raw, TARGETS)
    listings = dedupe_by_vin(kept)
    log.info("scraped %d · passed filters %d · unique VINs %d",
             len(raw), len(kept), len(listings))

    previous = state_mod.load(args.state)
    diff, next_state = diff_listings(previous, listings, CONFIG["price_drop_threshold"])

    print()
    print("=" * 78)
    print(f"MATCHING LISTINGS ({len(listings)})")
    print("=" * 78)
    _print_listings(listings)

    if args.show_rejected and rejected:
        print()
        print(f"REJECTED ({len(rejected)})")
        for lst, reason in sorted(rejected, key=lambda r: r[1]):
            print(f"  {lst.title[:44]:44s}  {reason}")

    print()
    print("=" * 78)
    print(f"DIFF vs previous run: {len(diff.new)} new · "
          f"{len(diff.price_drops)} price drops · "
          f"{len(diff.disappeared)} gone · {diff.unchanged} unchanged")
    print("=" * 78)

    notes = _notifications_for(diff)

    if args.seed:
        log.info("--seed: suppressing %d notification(s)", len(notes))
        notes = []

    if args.dry_run:
        print(f"\nWOULD SEND {len(notes)} notification(s):\n")
        for note in notes:
            print(note.describe())
            print()
        log.info("--dry-run: no notifications sent, no state written")
        return 0

    client = NtfyClient()
    sent = sum(1 for note in notes if client.send(note))

    # Persist regardless: a delivery failure must not cause the same cars to be
    # re-detected and re-sent on the next run.
    state_mod.save(next_state, args.state)

    if notes:
        log.info("sent %d/%d notification(s)", sent, len(notes))
        if sent == 0:
            log.error(
                "every notification failed to send - check NTFY_TOPIC/NTFY_URL. "
                "State was still written, so these %d will not be resent.", len(notes)
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
