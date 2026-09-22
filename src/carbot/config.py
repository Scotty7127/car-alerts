"""User-editable configuration.

Everything you are likely to want to change lives in this file.
Secrets do NOT live here - they come from environment variables (see notify.py).
"""

from __future__ import annotations

from typing import Final, TypedDict


class Target(TypedDict):
    """One car you are shopping for, plus the per-site identifiers to find it."""

    key: str
    label: str
    make: str
    model: str
    year_min: int
    # Per-source identifiers. A source skips a target whose id is None.
    cars_com_slug: str | None
    autotrader_code: str | None
    carmax_path: str | None
    # If set, the listing's model/trim/body text must contain at least one of
    # these (case-insensitive). Used for the S5 Sportback, which no site
    # exposes as a distinct model - and which each site names differently:
    # cars.com writes "Sportback" in the trim, Autotrader only ever calls it a
    # "Hatchback" body style. Matching either is what makes both work.
    body_any_of: list[str] | None


TARGETS: Final[list[Target]] = [
    {
        "key": "s3",
        "label": "Audi S3",
        "make": "Audi",
        "model": "S3",
        "year_min": 2022,
        "cars_com_slug": "audi-s3",
        "autotrader_code": "AUDS3",
        "carmax_path": "audi/s3",
        "body_any_of": None,
    },
    {
        "key": "s4",
        "label": "Audi S4",
        "make": "Audi",
        "model": "S4",
        "year_min": 2020,
        "cars_com_slug": "audi-s4",
        "autotrader_code": "S4",
        "carmax_path": "audi/s4",
        "body_any_of": None,
    },
    {
        "key": "s5_sportback",
        "label": "Audi S5 Sportback",
        "make": "Audi",
        "model": "S5",
        "year_min": 2020,
        "cars_com_slug": "audi-s5",
        "autotrader_code": "S5",
        "carmax_path": "audi/s5",
        "body_any_of": ["sportback", "hatchback"],
    },
]

CONFIG: Final[dict] = {
    # --- Hard filters -----------------------------------------------------
    "max_price": 38_500,
    "max_mileage": 45_000,
    # Reject a listing if any of these normalized flags is present.
    "exclude_flags": [
        "accident",
        "salvage",
        "frame_damage",
        "fleet_use",
    ],
    # Push the accident/salvage/fleet exclusions to the sites' own filters
    # where they support it (cars.com does). This is stricter: it also drops
    # listings with NO history data at all. Set False to keep unknowns and
    # rely only on explicitly-bad flags.
    "strict_history_filter": True,
    # --- Search origin ----------------------------------------------------
    "zip": "45217",  # Cincinnati, OH. Used for distance, not to limit results.
    "nationwide": True,
    # --- Notification tuning ---------------------------------------------
    "price_drop_threshold": 500,   # notify when price falls by >= this
    "high_priority_under": 36_000,  # ntfy priority=high below this price
    # --- Politeness / robustness -----------------------------------------
    "request_delay_seconds": (1.5, 4.0),  # random sleep between requests
    "request_timeout": 40,
    "max_retries": 3,
    "max_pages_per_target": 3,  # SRP pagination cap, per target per source
    # Upper bound for CarMax's year range facet (it needs min-max, not min).
    "carmax_year_max": 2027,
    # Escape hatch: raw query string for CarMax, keyed by target["key"].
    # Only needed if their `uri` facet contract changes - normally leave empty.
    "carmax_query_overrides": {},
    # --- Source toggles ---------------------------------------------------
    # Flip any of these to False to stop calling a source entirely.
    "sources": {
        "cars.com": True,
        "carmax": True,
        "autotrader": True,
        "cargurus": True,
    },
}
