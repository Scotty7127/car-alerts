"""Hard-filter and dedupe logic. Pure functions - no I/O, easy to test."""

from __future__ import annotations

import logging

from .config import CONFIG, Target
from .models import Listing
from .sources.base import body_matches

log = logging.getLogger(__name__)


def target_for(listing: Listing, targets: list[Target]) -> Target | None:
    """Which configured target (if any) this listing satisfies."""
    for target in targets:
        if target["model"].lower() != listing.model.lower():
            continue
        if not body_matches(target, listing.model, listing.trim, listing.body_style):
            continue
        return target
    return None


def rejection_reason(listing: Listing, targets: list[Target]) -> str | None:
    """None if the listing passes every hard filter, else a short reason."""
    target = target_for(listing, targets)
    if target is None:
        return f"not a target model ({listing.model})"

    if listing.year is None:
        return "missing year"
    if listing.year < target["year_min"]:
        return f"year {listing.year} < {target['year_min']}"

    if listing.price is None:
        return "missing price"
    if listing.price > CONFIG["max_price"]:
        return f"price ${listing.price:,} > ${CONFIG['max_price']:,}"

    if listing.mileage is None:
        return "missing mileage"
    if listing.mileage > CONFIG["max_mileage"]:
        return f"mileage {listing.mileage:,} > {CONFIG['max_mileage']:,}"

    bad = sorted(set(listing.flags) & set(CONFIG["exclude_flags"]))
    if bad:
        return f"excluded flag: {', '.join(bad)}"

    return None


def passes(listing: Listing, targets: list[Target]) -> bool:
    return rejection_reason(listing, targets) is None


def apply_filters(
    listings: list[Listing], targets: list[Target]
) -> tuple[list[Listing], list[tuple[Listing, str]]]:
    """Split listings into (kept, [(rejected, reason), ...])."""
    kept: list[Listing] = []
    rejected: list[tuple[Listing, str]] = []
    for listing in listings:
        reason = rejection_reason(listing, targets)
        if reason is None:
            kept.append(listing)
        else:
            rejected.append((listing, reason))
    return kept, rejected


def dedupe_by_vin(listings: list[Listing]) -> list[Listing]:
    """Collapse the same VIN seen on multiple sites into one record.

    The first occurrence wins as the base record; later ones are merged in,
    contributing their URL, flags and any detail the base was missing.
    """
    merged: dict[str, Listing] = {}
    for listing in listings:
        existing = merged.get(listing.vin)
        if existing is None:
            merged[listing.vin] = listing
        else:
            existing.merge_from(listing)
    return list(merged.values())
