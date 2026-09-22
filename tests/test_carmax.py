"""CarMax parsing, against a real captured API response.

The fixture is a live /cars/api/search/run payload trimmed to the fields the
parser reads. It deliberately includes a car with a real "Fleet"
priorUseDescriptions entry, and S5s in all three body styles.
"""

from __future__ import annotations

import json

from carbot.config import TARGETS
from carbot.filters import apply_filters
from carbot.models import FLAG_FLEET_USE
from carbot.sources.carmax import (
    CarMaxSource,
    _looks_filtered,
    _parse_flags,
    _parse_status,
    _prior_use_text,
)

S3 = next(t for t in TARGETS if t["key"] == "s3")
S4 = next(t for t in TARGETS if t["key"] == "s4")
S5 = next(t for t in TARGETS if t["key"] == "s5_sportback")


def rows(fixtures):
    return json.loads((fixtures / "carmax_search.json").read_text())["items"]


class TestUriContract:
    """The `uri` param is the whole reason this source works."""

    def test_route_and_filters_are_encoded_into_uri(self):
        url = CarMaxSource()._build_url(S3)
        assert "uri=%2Fcars%2Faudi%2Fs3%3F" in url
        assert "year%3D2022-2027" in url
        assert "price%3D0-38500" in url
        assert "mileage%3D0-45000" in url

    def test_year_floor_is_per_target(self):
        assert "year%3D2020-2027" in CarMaxSource()._build_url(S4)

    def test_paging_params_stay_outside_the_uri(self):
        url = CarMaxSource()._build_url(S3)
        assert "&skip=0" in url and "&take=100" in url

    def test_make_model_are_never_sent_as_top_level_params(self):
        """They are silently ignored by the API; sending them invites confusion."""
        url = CarMaxSource()._build_url(S3)
        query = url.split("?", 1)[1]
        top_level = {p.split("=")[0] for p in query.split("&")}
        assert top_level == {"uri", "skip", "take", "zip"}


class TestParsing:
    def test_parses_records_with_stock_number_urls(self, fixtures):
        parsed = [l for r in rows(fixtures) if (l := CarMaxSource()._parse_row(r, S4))]
        assert parsed
        for listing in parsed:
            assert listing.url.startswith("https://www.carmax.com/car/")
            assert listing.url.split("/")[-1].isdigit()
            assert len(listing.vin) == 17
            assert listing.dealer.startswith("CarMax ")

    def test_distance_and_location_are_populated(self, fixtures):
        parsed = [l for r in rows(fixtures) if (l := CarMaxSource()._parse_row(r, S4))]
        assert all(l.city and l.state for l in parsed)
        assert any(l.distance_mi is not None for l in parsed)


class TestPriorUseHandling:
    def test_prior_use_is_a_list_of_dicts_not_strings(self, fixtures):
        entries = [e for r in rows(fixtures) for e in (r.get("priorUseDescriptions") or [])]
        assert entries, "fixture should contain at least one prior-use car"
        assert all(isinstance(e, dict) for e in entries)

    def test_prior_use_text_flattens_name_and_description(self):
        row = {"priorUseDescriptions": [
            {"id": 10, "name": "Fleet", "description": "part of a fleet of vehicles"}]}
        assert "fleet" in _prior_use_text(row)

    def test_fleet_prior_use_becomes_the_exclusion_flag(self):
        row = {"priorUseDescriptions": [{"id": 10, "name": "Fleet", "description": ""}]}
        assert FLAG_FLEET_USE in _parse_flags(row)

    def test_the_real_fleet_car_in_the_fixture_gets_the_flag(self, fixtures):
        """The live fixture holds a 2022 S4 with a genuine 'Fleet' prior use."""
        fleet_rows = [r for r in rows(fixtures) if "fleet" in _prior_use_text(r)]
        assert fleet_rows, "fixture should contain a real fleet car"
        listing = CarMaxSource()._parse_row(fleet_rows[0], S4)
        assert listing is not None
        assert FLAG_FLEET_USE in listing.flags

    def test_that_fleet_car_is_rejected_on_the_flag_once_other_filters_pass(
        self, fixtures
    ):
        """It happens to be over the mileage cap too, and rejection_reason
        reports the first failure - so neutralise mileage to prove the flag
        itself is disqualifying, not just incidentally co-occurring."""
        fleet_rows = [r for r in rows(fixtures) if "fleet" in _prior_use_text(r)]
        listing = CarMaxSource()._parse_row(fleet_rows[0], S4)
        listing.mileage = 20_000
        listing.price = 30_000
        kept, rejected = apply_filters([listing], TARGETS)
        assert kept == []
        assert any("fleet_use" in reason for _, reason in rejected)

    def test_theft_history_is_surfaced_but_not_excluded(self):
        row = {"priorUseDescriptions": [
            {"id": 630, "name": "Prior Theft History",
             "description": "This vehicle has prior theft history reported."}]}
        flags = _parse_flags(row)
        assert "prior_theft" in flags
        assert FLAG_FLEET_USE not in flags

    def test_empty_prior_use_yields_no_flags(self):
        assert _parse_flags({"priorUseDescriptions": []}) == []


class TestS5BodyStyles:
    """CarMax writes '4D Hatchback' for a Sportback, '2D Coupe' for a coupe."""

    def test_hatchback_is_accepted_as_the_sportback(self, fixtures):
        hatch = [r for r in rows(fixtures) if "Hatchback" in str(r.get("body"))]
        assert hatch, "fixture should contain a 4D Hatchback S5"
        assert CarMaxSource()._parse_row(hatch[0], S5) is not None

    def test_coupe_is_rejected(self, fixtures):
        coupes = [r for r in rows(fixtures) if "2D Coupe" in str(r.get("body"))]
        assert coupes, "fixture should contain a 2D Coupe S5"
        assert all(CarMaxSource()._parse_row(r, S5) is None for r in coupes)


class TestStatus:
    def test_available(self):
        assert _parse_status({"isSaleable": True}) == "available"

    def test_coming_soon(self):
        assert _parse_status({"isComingSoon": True}) == "coming soon"

    def test_on_hold(self):
        assert _parse_status({"isReserved": True}) == "on hold"

    def test_unavailable(self):
        assert _parse_status({"isSaleable": False}) == "unavailable"


class TestUnfilteredGuard:
    def test_an_all_audi_response_passes(self):
        assert _looks_filtered([{"make": "Audi"}] * 10) is True

    def test_a_nationwide_junk_response_is_caught(self):
        assert _looks_filtered([{"make": "Dodge"}] * 99 + [{"make": "Audi"}]) is False
