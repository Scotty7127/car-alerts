"""CarMax - the JSON endpoint its own SRP calls.

carmax.com HTML is a pure Angular shell behind Akamai, so there are no
listings in the server response and an HTML fallback is pointless. Everything
comes from:

    GET https://www.carmax.com/cars/api/search/run?uri=<route>&skip=&take=

The `uri` param is the whole trick. It carries the SRP route that the SPA
would be sitting on, URL-encoded, *including its own query string*:

    uri=%2Fcars%2Faudi%2Fs3%3Fyear%3D2022-2027%26price%3D0-38500

Facets are derived from that route server-side, which is why every attempt to
pass `make=`/`model=`/`makes[]=` as top-level params is silently ignored and
returns the full ~59k nationwide inventory. Found by tracing `e.uri` in
/cars/dist/uftSearchApp.*.js. Verified:
    uri=/cars/all       -> 59,256      uri=/cars/audi    -> 1,770
    uri=/cars/audi/s3   ->      6      + ?year=2022-2027 ->     1

Range facets are `min-max` strings: year, price, mileage. Nationwide is the
default (radius-nationwide, shipping=-1), so we do not need to ask for it.

Useful fields: stockNumber, vin, year/make/model/trim, body ("4D Hatchback"
is how an S5 Sportback presents here), basePrice, mileage, storeCity,
stateAbbreviation, distance, isSaleable/isComingSoon/isReserved, and
priorUseDescriptions - a list of {id, name, description} dicts, which is the
cleanest fleet/rental signal of any source.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from ..config import CONFIG, Target
from ..http import BlockedError, Fetcher
from ..models import FLAG_FLEET_USE, FLAG_ONE_OWNER, Listing
from .base import clean_int, matches_target

log = logging.getLogger(__name__)

API = "https://www.carmax.com/cars/api/search/run"
PAGE_SIZE = 100

# priorUseDescriptions text that disqualifies a car outright.
FLEET_WORDS = ("fleet", "rental", "commercial", "livery", "taxi")

# Prior-use text worth surfacing in the notification even though it is not on
# the exclusion list - you probably want to know before driving to see it.
NOTABLE_PRIOR_USE = {
    "theft": "prior_theft",
    "flood": "prior_flood",
    "fire": "prior_fire",
    "hail": "prior_hail",
    "lemon": "lemon_law",
    "manufacturer buyback": "lemon_law",
}

# Safety net: if the response is obviously unfiltered, the uri contract broke.
MIN_MATCH_RATIO = 0.30


class CarMaxSource:
    name = "carmax"

    def fetch(self, targets: list[Target], fetcher: Fetcher) -> list[Listing]:
        out: list[Listing] = []
        for target in targets:
            if not target.get("carmax_path"):
                continue
            try:
                out.extend(self._fetch_target(target, fetcher))
            except BlockedError as exc:
                log.warning("[%s] %s unavailable, skipping: %s", self.name, target["label"], exc)
            except Exception as exc:
                log.warning("[%s] %s failed: %s", self.name, target["label"], exc)
        return out

    def _fetch_target(self, target: Target, fetcher: Fetcher) -> list[Listing]:
        payload = fetcher.get_json(
            self._build_url(target),
            headers={"Referer": f"https://www.carmax.com/cars/{target['carmax_path']}"},
        )
        rows = payload.get("items") or []
        if not rows:
            log.info("[%s] %s: 0 listings", self.name, target["label"])
            return []

        if not _looks_filtered(rows):
            log.warning(
                "[%s] %s: response is unfiltered (%d/%d rows are Audi, total=%s). "
                "The `uri` facet contract has changed - skipping rather than "
                "returning junk. See the docstring in sources/carmax.py.",
                self.name, target["label"],
                sum(1 for r in rows if str(r.get("make", "")).lower() == "audi"),
                len(rows), payload.get("totalCount"),
            )
            return []

        found = [lst for r in rows if (lst := self._parse_row(r, target))]
        log.info("[%s] %s: %d listings", self.name, target["label"], len(found))
        return found

    def _build_url(self, target: Target) -> str:
        """Build the uri-encoded route, then the outer query."""
        override = (CONFIG.get("carmax_query_overrides") or {}).get(target["key"])
        if override:
            return f"{API}?{override}"

        route_filters = urllib.parse.urlencode({
            "year": f"{target['year_min']}-{CONFIG['carmax_year_max']}",
            "price": f"0-{CONFIG['max_price']}",
            "mileage": f"0-{CONFIG['max_mileage']}",
        })
        route = f"/cars/{target['carmax_path']}?{route_filters}"

        outer = urllib.parse.urlencode({
            "uri": route,          # urlencode handles the inner encoding
            "skip": 0,
            "take": PAGE_SIZE,
            "zip": CONFIG["zip"],
        })
        return f"{API}?{outer}"

    def _parse_row(self, row: dict[str, Any], target: Target) -> Listing | None:
        vin = str(row.get("vin") or "").strip()
        if len(vin) != 17:
            return None

        model = str(row.get("model") or "")
        trim = str(row.get("trim") or "")
        body = str(row.get("body") or "")
        if not matches_target(target, model, trim, body):
            return None

        stock = row.get("stockNumber")
        store = str(row.get("storeName") or "").strip()
        return Listing(
            vin=vin,
            source=self.name,
            year=clean_int(row.get("year")),
            make=str(row.get("make") or "Audi"),
            model=model,
            trim=trim,
            price=clean_int(row.get("basePrice")),
            mileage=clean_int(row.get("mileage")),
            dealer=f"CarMax {store}".strip(),
            city=str(row.get("storeCity") or ""),
            state=str(row.get("stateAbbreviation") or ""),
            url=f"https://www.carmax.com/car/{stock}" if stock else "",
            flags=_parse_flags(row),
            status=_parse_status(row),
            distance_mi=_as_float(row.get("distance")),
            body_style=body,
        )


def _looks_filtered(rows: list[dict[str, Any]]) -> bool:
    audi = sum(1 for r in rows if str(r.get("make", "")).lower() == "audi")
    return (audi / len(rows)) >= MIN_MATCH_RATIO


def _parse_status(row: dict[str, Any]) -> str:
    if row.get("isComingSoon"):
        return "coming soon"
    if row.get("isReserved"):
        return "on hold"
    if row.get("isSaleable") is False:
        return "unavailable"
    return "available"


def _prior_use_text(row: dict[str, Any]) -> str:
    """priorUseDescriptions is a list of {id, name, description} dicts."""
    parts: list[str] = []
    for entry in row.get("priorUseDescriptions") or []:
        if isinstance(entry, dict):
            parts.append(f"{entry.get('name', '')} {entry.get('description', '')}")
        else:
            parts.append(str(entry))
    return " ".join(parts).lower()


def _parse_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    prior_use = _prior_use_text(row)
    if any(word in prior_use for word in FLEET_WORDS):
        flags.append(FLAG_FLEET_USE)
    for needle, flag in NOTABLE_PRIOR_USE.items():
        if needle in prior_use:
            flags.append(flag)
    if "singleOwner" in (row.get("highlights") or []):
        flags.append(FLAG_ONE_OWNER)
    return sorted(set(flags))


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


SOURCE = CarMaxSource()
