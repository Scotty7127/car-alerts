"""Persisted listing state - a single JSON file committed back to the repo."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .models import Listing, utc_now

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("state/listings.json")
SCHEMA_VERSION = 1


def load(path: Path = DEFAULT_PATH) -> dict[str, Listing]:
    """VIN -> Listing. Missing or corrupt file yields an empty state."""
    if not path.exists():
        log.info("No state file at %s - starting empty", path)
        return {}
    try:
        raw: dict[str, Any] = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        log.error("State file %s is corrupt (%s) - starting empty", path, exc)
        return {}
    return {
        vin: Listing.from_dict(record)
        for vin, record in (raw.get("listings") or {}).items()
    }


def serialize(listings: dict[str, Listing]) -> str:
    """Deterministic JSON so an unchanged run produces a byte-identical file."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": utc_now(),
        "listings": {
            vin: listings[vin].to_dict() for vin in sorted(listings)
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def changed(listings: dict[str, Listing], path: Path = DEFAULT_PATH) -> bool:
    """True if saving would alter the file, ignoring the updated_at stamp."""
    if not path.exists():
        return True
    try:
        old = json.loads(path.read_text()).get("listings")
    except json.JSONDecodeError:
        return True
    new = json.loads(serialize(listings))["listings"]
    return old != new


def save(listings: dict[str, Listing], path: Path = DEFAULT_PATH) -> bool:
    """Write state. Returns True if the file's listings actually changed."""
    did_change = changed(listings, path)
    if not did_change:
        log.info("State unchanged (%d listings) - not rewriting %s", len(listings), path)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize(listings))
    log.info("Wrote %s (%d listings)", path, len(listings))
    return True
