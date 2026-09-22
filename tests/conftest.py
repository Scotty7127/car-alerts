from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carbot.models import Listing  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def make_listing(**overrides) -> Listing:
    """A listing that passes every filter unless you break it on purpose."""
    base = dict(
        vin="WAUH3DGY3PA030642",
        source="cars.com",
        year=2023,
        make="Audi",
        model="S3",
        trim="Premium Plus",
        price=36_713,
        mileage=26_049,
        dealer="Alderman Automotive",
        city="Fishers",
        state="IN",
        url="https://www.cars.com/vehicledetail/abc/",
        flags=["no_accidents", "one_owner"],
        status="available",
        distance_mi=160.0,
        body_style="Sedan",
    )
    base.update(overrides)
    return Listing(**base)
