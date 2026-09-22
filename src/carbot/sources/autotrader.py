"""Autotrader - the JSON endpoint its own SRP calls.

Verified 2026-09: the page at /cars-for-sale/... is Next.js and its
__NEXT_DATA__ only carries the 3 sponsored "spotlight" cars, so parsing the
HTML is a dead end. The real inventory comes from:

    GET https://www.autotrader.com/rest/lsc/listing

Params that the server actually honours (confirmed by reading `requestParams`
back off the response - anything it ignores is silently dropped from there):
    makeCode, modelCode, zip, searchRadius (0 = nationwide), listingTypes,
    startYear, endYear, maxPrice, maxMileage, numRecords, firstRecord

`vhrPreview` carries the history flags: NO_ACCIDENTS_REPORTED,
NO_SALVAGE_TITLE, ONE_OWNER / NO_ONE_OWNER, NO_FRAME_DAMAGE, ...
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from ..config import CONFIG, Target
from ..geo import haversine_miles
from ..http import BlockedError, Fetcher
from ..models import (
    FLAG_ACCIDENT,
    FLAG_CLEAN_TITLE,
    FLAG_FLEET_USE,
    FLAG_FRAME_DAMAGE,
    FLAG_NO_ACCIDENTS,
    FLAG_ONE_OWNER,
    FLAG_SALVAGE,
    Listing,
)
from .base import clean_int, matches_target

log = logging.getLogger(__name__)

API = "https://www.autotrader.com/rest/lsc/listing"
REFERER = "https://www.autotrader.com/cars-for-sale/all-cars/audi"
PAGE_SIZE = 100

# vhrPreview token -> our normalized flag. Absence of a token means "unknown",
# never "bad", so we only translate the ones that are actually assertions.
VHR_MAP: dict[str, str] = {
    "NO_ACCIDENTS_REPORTED": FLAG_NO_ACCIDENTS,
    "ACCIDENTS_REPORTED": FLAG_ACCIDENT,
    "NO_SALVAGE_TITLE": FLAG_CLEAN_TITLE,
    "SALVAGE_TITLE": FLAG_SALVAGE,
    "FRAME_DAMAGE": FLAG_FRAME_DAMAGE,
    "ONE_OWNER": FLAG_ONE_OWNER,
    "FLEET_USE": FLAG_FLEET_USE,
    "RENTAL_USE": FLAG_FLEET_USE,
}


class AutotraderSource:
    name = "autotrader"

    def fetch(self, targets: list[Target], fetcher: Fetcher) -> list[Listing]:
        out: list[Listing] = []
        for target in targets:
            if not target.get("autotrader_code"):
                continue
            try:
                out.extend(self._fetch_target(target, fetcher))
            except BlockedError as exc:
                log.warning("[%s] %s unavailable, skipping: %s", self.name, target["label"], exc)
            except Exception as exc:
                log.warning("[%s] %s failed: %s", self.name, target["label"], exc)
        return out

    def _fetch_target(self, target: Target, fetcher: Fetcher) -> list[Listing]:
        found: list[Listing] = []
        for page in range(CONFIG["max_pages_per_target"]):
            url = self._build_url(target, first_record=page * PAGE_SIZE)
            payload = fetcher.get_json(url, headers={"Referer": REFERER})
            rows = payload.get("listings") or []
            if not rows:
                break
            for row in rows:
                listing = self._parse_row(row, target)
                if listing:
                    found.append(listing)
            if len(rows) < PAGE_SIZE:
                break
        log.info("[%s] %s: %d listings", self.name, target["label"], len(found))
        return found

    def _build_url(self, target: Target, first_record: int) -> str:
        params = {
            "makeCode": "AUDI",
            "modelCode": str(target["autotrader_code"]),
            "zip": CONFIG["zip"],
            "searchRadius": "0" if CONFIG["nationwide"] else "100",
            "listingTypes": "USED,CERTIFIED",
            "startYear": str(target["year_min"]),
            "maxPrice": str(CONFIG["max_price"]),
            "maxMileage": str(CONFIG["max_mileage"]),
            "numRecords": str(PAGE_SIZE),
            "firstRecord": str(first_record),
            "sortBy": "derivedpriceASC",
        }
        return f"{API}?{urllib.parse.urlencode(params)}"

    def _parse_row(self, row: dict[str, Any], target: Target) -> Listing | None:
        vin = str(row.get("vin") or "").strip()
        if len(vin) != 17:
            return None

        model = _nested(row, "model", "name")
        trim = _nested(row, "trim", "name")
        body = _body_text(row)
        if not matches_target(target, model, trim, body):
            return None

        owner = row.get("owner") or {}
        location = owner.get("location") or {}
        address = location.get("address") or {}

        return Listing(
            vin=vin,
            source=self.name,
            year=clean_int(row.get("year")),
            make=_nested(row, "make", "name") or "Audi",
            model=model,
            trim=trim,
            price=clean_int((row.get("pricingDetail") or {}).get("displayPrice")),
            mileage=clean_int(_nested(row, "mileage", "value")),
            dealer=str(owner.get("name") or ""),
            city=str(address.get("city") or ""),
            state=str(address.get("state") or ""),
            url=_listing_url(row),
            flags=_parse_flags(row),
            status="available",
            distance_mi=_distance(owner),
            body_style=" ".join(
                str(e.get("name") or "") for e in (row.get("bodyStyles") or [])
                if isinstance(e, dict)
            ),
        )


def _body_text(row: dict[str, Any]) -> str:
    """Flatten the body-style descriptors into one searchable string.

    bodyStyles is a list of {"code": "HATCH", "name": "Hatchback"} - not
    strings. Autotrader never writes "Sportback" anywhere, so the Hatchback
    body name is the only way to tell an S5 Sportback from an S5 Coupe.
    """
    names = [
        str(entry.get("name") or entry.get("code") or "")
        for entry in (row.get("bodyStyles") or [])
        if isinstance(entry, dict)
    ]
    return " ".join([*names, str(row.get("titleLong") or "")])


def _nested(row: dict[str, Any], key: str, sub: str) -> str:
    value = row.get(key)
    if isinstance(value, dict):
        return str(value.get(sub) or "")
    return str(value or "")


def _distance(owner: dict[str, Any]) -> float | None:
    """Autotrader gives distance directly; fall back to the dealer lat/lon."""
    raw = owner.get("distanceFromSearch")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    address = (owner.get("location") or {}).get("address") or {}
    lat, lon = address.get("latitude"), address.get("longitude")
    if lat is not None and lon is not None:
        try:
            return round(haversine_miles(float(lat), float(lon)), 1)
        except (TypeError, ValueError):
            return None
    return None


def _listing_url(row: dict[str, Any]) -> str:
    listing_id = row.get("id")
    return f"https://www.autotrader.com/cars-for-sale/vehicle/{listing_id}" if listing_id else ""


def _parse_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    for token in row.get("vhrPreview") or []:
        mapped = VHR_MAP.get(str(token).upper())
        if mapped:
            flags.append(mapped)
    if (row.get("listingType") or "").upper() == "CERTIFIED":
        flags.append("certified")
    return sorted(set(flags))


SOURCE = AutotraderSource()
