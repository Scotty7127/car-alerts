"""The single normalized shape every source must produce."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Flags are free-form strings, but these are the ones the filter logic knows
# about. Sources should emit these spellings so exclusions actually fire.
FLAG_ACCIDENT = "accident"
FLAG_SALVAGE = "salvage"
FLAG_FRAME_DAMAGE = "frame_damage"
FLAG_FLEET_USE = "fleet_use"
FLAG_NO_ACCIDENTS = "no_accidents"
FLAG_ONE_OWNER = "one_owner"
FLAG_CLEAN_TITLE = "clean_title"
FLAG_CERTIFIED = "certified"
FLAG_PERSONAL_USE = "personal_use"


def utc_now() -> str:
    """ISO-8601 UTC timestamp, second resolution."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Listing:
    """One vehicle, normalized across sources.

    `urls` is a dict of source -> url so a VIN seen on two sites keeps both
    links in a single record.
    """

    vin: str
    source: str
    year: int | None
    make: str
    model: str
    trim: str
    price: int | None
    mileage: int | None
    dealer: str
    city: str
    state: str
    url: str
    flags: list[str] = field(default_factory=list)
    status: str = "available"
    distance_mi: float | None = None
    # Raw body-style text from the source, e.g. "Sedan" / "Hatchback".
    # Needed to tell an S5 Sportback from an S5 Coupe after parsing.
    body_style: str = ""
    first_seen: str = field(default_factory=utc_now)
    last_seen: str = field(default_factory=utc_now)
    urls: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.urls:
            self.urls = {self.source: self.url}
        if not self.sources:
            self.sources = [self.source]
        self.vin = self.vin.strip().upper()

    # -- naming ------------------------------------------------------------

    @property
    def title(self) -> str:
        """e.g. '2023 Audi S3 Premium Plus'."""
        parts = [str(self.year or ""), self.make, self.model, self.trim]
        return " ".join(p for p in parts if p).strip()

    @property
    def location(self) -> str:
        if self.city and self.state:
            return f"{self.city}, {self.state}"
        return self.city or self.state or "Unknown location"

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Listing":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def merge_from(self, other: "Listing") -> None:
        """Fold a duplicate of this VIN (from another source) into this record."""
        self.urls.update(other.urls)
        for s in other.sources:
            if s not in self.sources:
                self.sources.append(s)
        for f in other.flags:
            if f not in self.flags:
                self.flags.append(f)
        # Prefer the lower advertised price and any non-null detail we lack.
        if other.price is not None and (self.price is None or other.price < self.price):
            self.price = other.price
        for attr in ("mileage", "year", "trim", "dealer", "city", "state",
                     "distance_mi", "body_style"):
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))
