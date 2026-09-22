"""ntfy notifications.

Topic, server URL and token come from the environment only:
    NTFY_TOPIC  (required to actually send)
    NTFY_URL    (optional, default https://ntfy.sh)
    NTFY_TOKEN  (optional, for a protected/self-hosted topic)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from curl_cffi import requests as curl_requests

from .config import CONFIG
from .geo import format_distance
from .models import Listing

log = logging.getLogger(__name__)

DEFAULT_SERVER = "https://ntfy.sh"

# Pretty names for the flags worth surfacing in a notification body.
FLAG_LABELS: dict[str, str] = {
    "no_accidents": "No accidents",
    "one_owner": "1 owner",
    "clean_title": "Clean title",
    "certified": "Certified",
    "personal_use": "Personal use",
    # Not on the exclusion list, but you want to see these before you drive out.
    "prior_theft": "⚠ Prior theft",
    "prior_flood": "⚠ Flood history",
    "prior_fire": "⚠ Fire damage",
    "prior_hail": "⚠ Hail damage",
    "lemon_law": "⚠ Lemon-law buyback",
    "fleet_use": "⚠ Fleet/rental use",
}

SOURCE_LABELS: dict[str, str] = {
    "cars.com": "Cars.com",
    "carmax": "CarMax",
    "autotrader": "Autotrader",
    "cargurus": "CarGurus",
}


@dataclass
class Notification:
    title: str
    body: str
    click: str
    priority: str
    tags: str

    def describe(self) -> str:
        return f"[{self.priority}] {self.title}\n    {self.body}\n    -> {self.click}"


# -- building ----------------------------------------------------------------


def build_new_listing(listing: Listing) -> Notification:
    price = f"${listing.price:,}" if listing.price is not None else "price n/a"
    return Notification(
        title=f"New: {listing.title} - {price}",
        body=_body(listing),
        click=listing.url,
        priority=_priority(listing),
        tags="car",
    )


def build_price_drop(listing: Listing, old_price: int, new_price: int) -> Notification:
    drop = old_price - new_price
    return Notification(
        title=f"Price drop: {listing.title} - ${new_price:,} (-${drop:,})",
        body=_body(listing, prefix=f"was ${old_price:,}"),
        click=listing.url,
        priority=_priority(listing),
        tags="chart_with_downwards_trend",
    )


def _body(listing: Listing, prefix: str = "") -> str:
    """'26,049 mi · Fishers, IN (~160 mi) · Alderman Automotive · No accidents · Cars.com'"""
    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    if listing.mileage is not None:
        parts.append(f"{listing.mileage:,} mi")

    distance = format_distance(listing.distance_mi)
    location = listing.location
    parts.append(f"{location} ({distance})" if distance else location)

    if listing.dealer:
        parts.append(listing.dealer)
    if listing.status and listing.status != "available":
        parts.append(listing.status.title())

    labels = [FLAG_LABELS[f] for f in listing.flags if f in FLAG_LABELS]
    parts.extend(labels)

    sources = " + ".join(SOURCE_LABELS.get(s, s) for s in listing.sources)
    parts.append(sources)
    return " · ".join(p for p in parts if p)


def _priority(listing: Listing) -> str:
    if listing.price is not None and listing.price < CONFIG["high_priority_under"]:
        return "high"
    return "default"


# -- sending -----------------------------------------------------------------


class NtfyClient:
    """Posts to ntfy. `enabled` is False when NTFY_TOPIC is unset."""

    def __init__(self) -> None:
        self.topic = os.environ.get("NTFY_TOPIC", "").strip()
        self.server = os.environ.get("NTFY_URL", DEFAULT_SERVER).strip().rstrip("/")
        self.token = os.environ.get("NTFY_TOKEN", "").strip()
        self.enabled = bool(self.topic)
        if not self.enabled:
            log.warning("NTFY_TOPIC is not set - notifications will not be sent")

    @property
    def endpoint(self) -> str:
        return f"{self.server}/{self.topic}"

    def send(self, note: Notification) -> bool:
        """Post one notification. Never raises - a failed ping must not
        abort the run or lose the state write."""
        if not self.enabled:
            log.warning("Skipping send (no NTFY_TOPIC): %s", note.title)
            return False

        headers = {
            "Title": _ascii(note.title),
            "Priority": note.priority,
            "Tags": note.tags,
        }
        if note.click:
            headers["Click"] = note.click
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            resp = curl_requests.post(
                self.endpoint,
                data=note.body.encode("utf-8"),
                headers=headers,
                timeout=20,
            )
        except Exception as exc:
            log.error("ntfy POST failed for %r: %s", note.title, exc)
            return False

        if resp.status_code >= 300:
            log.error("ntfy returned %s for %r: %s",
                      resp.status_code, note.title, resp.text[:200])
            return False
        return True


def _ascii(text: str) -> str:
    """ntfy sends headers as latin-1; keep the Title header safe."""
    return text.encode("ascii", "replace").decode("ascii")
