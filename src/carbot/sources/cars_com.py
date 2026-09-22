"""cars.com - server-rendered search results.

Notes from verifying the real page (2026-09):
  * The `CarsWeb.SearchController` JSON island is a null hydration placeholder
    on the server response - do not bother with it.
  * Every result card is a <fuse-card> containing an <a data-vin> whose data-*
    attributes carry the structured record. That is the reliable payload.
  * Real query params, read out of the page's own filter schema:
      stock_type, makes[], models[], year_min, year_max, list_price_max,
      mileage_max, maximum_distance, zip, page, sort,
      vehicle_history_group[] in {NO_ACCIDENTS, CLEAN_TITLE, ONE_OWNER,
                                  PERSONAL_USE}
    The history group lets us push the accident/salvage/fleet exclusions
    server-side instead of guessing from badge text.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Iterable

from bs4 import BeautifulSoup, Tag

from ..config import CONFIG, Target
from ..http import BlockedError, Fetcher
from ..models import (
    FLAG_CERTIFIED,
    FLAG_CLEAN_TITLE,
    FLAG_NO_ACCIDENTS,
    FLAG_ONE_OWNER,
    FLAG_PERSONAL_USE,
    Listing,
)
from .base import clean_int, matches_target

log = logging.getLogger(__name__)

BASE = "https://www.cars.com/shopping/results/"

# "Davenport, IA (360 mi)" / "Fishers, IN (1,092 mi.)"
LOCATION_RE = re.compile(r"^(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s*\((?P<dist>[\d,]+)\s*mi\.?\)$")


class CarsComSource:
    name = "cars.com"

    def fetch(self, targets: list[Target], fetcher: Fetcher) -> list[Listing]:
        out: list[Listing] = []
        for target in targets:
            if not target.get("cars_com_slug"):
                continue
            try:
                out.extend(self._fetch_target(target, fetcher))
            except BlockedError as exc:
                log.warning("[%s] %s blocked: %s", self.name, target["label"], exc)
            except Exception as exc:  # layout change, bad markup, etc.
                log.warning("[%s] %s failed to parse: %s", self.name, target["label"], exc)
        return out

    # -- per target --------------------------------------------------------

    def _fetch_target(self, target: Target, fetcher: Fetcher) -> list[Listing]:
        found: list[Listing] = []
        for page in range(1, CONFIG["max_pages_per_target"] + 1):
            url = self._build_url(target, page)
            resp = fetcher.get(url, headers={"Referer": "https://www.cars.com/"})
            cards = _result_cards(resp.text)
            if not cards:
                break
            for card in cards:
                listing = self._parse_card(card, target)
                if listing:
                    found.append(listing)
            if len(cards) < 20:  # last page
                break
        log.info("[%s] %s: %d listings", self.name, target["label"], len(found))
        return found

    def _build_url(self, target: Target, page: int) -> str:
        params: list[tuple[str, str]] = [
            ("stock_type", "used"),
            ("makes[]", "audi"),
            ("models[]", str(target["cars_com_slug"])),
            ("year_min", str(target["year_min"])),
            ("list_price_max", str(CONFIG["max_price"])),
            ("mileage_max", str(CONFIG["max_mileage"])),
            ("maximum_distance", "all" if CONFIG["nationwide"] else "100"),
            ("zip", CONFIG["zip"]),
            ("sort", "list_price"),
            ("page", str(page)),
        ]
        if CONFIG.get("strict_history_filter", True):
            for value in ("NO_ACCIDENTS", "CLEAN_TITLE", "PERSONAL_USE"):
                params.append(("vehicle_history_group[]", value))
        return f"{BASE}?{urllib.parse.urlencode(params)}"

    # -- per card ----------------------------------------------------------

    def _parse_card(self, card: Tag, target: Target) -> Listing | None:
        anchor = card.select_one("a[data-vin]")
        if anchor is None:
            return None
        attrs = anchor.attrs
        vin = str(attrs.get("data-vin", "")).strip()
        if len(vin) != 17:
            return None

        model = str(attrs.get("data-model", ""))
        trim = str(attrs.get("data-trim", ""))
        body = str(attrs.get("data-bodystyle", ""))
        if not matches_target(target, model, trim, body):
            return None

        lines = [ln for ln in card.get_text("\n", strip=True).split("\n") if ln]
        city, state, distance = _parse_location(lines)

        return Listing(
            vin=vin,
            source=self.name,
            year=clean_int(attrs.get("data-year")),
            make=str(attrs.get("data-make", "Audi")),
            model=model,
            trim=trim,
            price=clean_int(attrs.get("data-price")),
            mileage=clean_int(attrs.get("data-mileage")),
            dealer=_parse_dealer(card, lines),
            city=city,
            state=state,
            url=_absolute(str(attrs.get("href", ""))),
            flags=_parse_flags(card, lines),
            status="available",
            distance_mi=distance,
            body_style=body,
        )


# -- module-level helpers (kept out of the class so tests can hit them) ------


def _result_cards(html: str) -> list[Tag]:
    soup = BeautifulSoup(html, "lxml")
    return [c for c in soup.select("fuse-card") if c.select_one("a[data-vin]")]


def _parse_location(lines: Iterable[str]) -> tuple[str, str, float | None]:
    for line in lines:
        m = LOCATION_RE.match(line)
        if m:
            return (
                m.group("city").strip(),
                m.group("state"),
                float(m.group("dist").replace(",", "")),
            )
    return "", "", None


def _parse_dealer(card: Tag, lines: list[str]) -> str:
    """Dealer name sits just above the location line (a star rating may intervene)."""
    for i, line in enumerate(lines):
        if LOCATION_RE.match(line):
            for back in (1, 2):
                if i - back >= 0:
                    candidate = lines[i - back]
                    if not re.fullmatch(r"\d(\.\d)?", candidate):
                        return candidate
            break
    el = card.select_one(".fuse-body-small")
    return el.get_text(" ", strip=True) if el else ""


def _parse_flags(card: Tag, lines: list[str]) -> list[str]:
    """Flags we can see on the results page.

    With strict_history_filter on, cars.com has already guaranteed no
    accidents / clean title / personal use, so we record those positively.
    """
    flags: list[str] = []
    if CONFIG.get("strict_history_filter", True):
        flags += [FLAG_NO_ACCIDENTS, FLAG_CLEAN_TITLE, FLAG_PERSONAL_USE]
    text = " ".join(lines).lower()
    if "certified" in text:
        flags.append(FLAG_CERTIFIED)
    if "one owner" in text or "1 owner" in text:
        flags.append(FLAG_ONE_OWNER)
    return sorted(set(flags))


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href.split("?")[0]
    return urllib.parse.urljoin("https://www.cars.com", href).split("?")[0]


SOURCE = CarsComSource()
