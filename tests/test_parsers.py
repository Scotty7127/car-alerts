"""Parsing against saved fixtures, so layout checks never hit the network."""

from __future__ import annotations

import json

from carbot.config import TARGETS
from carbot.sources.autotrader import AutotraderSource
from carbot.sources.cars_com import CarsComSource, _parse_location, _result_cards

S3 = next(t for t in TARGETS if t["key"] == "s3")
S5 = next(t for t in TARGETS if t["key"] == "s5_sportback")


class TestCarsComParsing:
    def _parse_all(self, fixtures):
        html = (fixtures / "cars_com_srp.html").read_text()
        source = CarsComSource()
        return [
            listing
            for card in _result_cards(html)
            if (listing := source._parse_card(card, S3))
        ]

    def test_finds_every_card(self, fixtures):
        assert len(_result_cards((fixtures / "cars_com_srp.html").read_text())) == 4

    def test_extracts_the_core_record(self, fixtures):
        first = self._parse_all(fixtures)[0]
        assert first.vin == "WAUH3DGY3PA030642"
        assert first.year == 2023
        assert first.make == "Audi"
        assert first.model == "S3"
        assert first.price == 28_295
        assert first.mileage == 63_538
        assert first.url.startswith("https://www.cars.com/vehicledetail/")

    def test_extracts_dealer_and_location_not_the_star_rating(self, fixtures):
        listings = self._parse_all(fixtures)
        second = listings[1]
        assert second.city == "Davenport"
        assert second.state == "IA"
        assert second.distance_mi == 360.0
        # The card shows "4.9" between dealer and location; must not be the dealer.
        assert second.dealer == "Dahl Ford Davenport"

    def test_vin_is_uppercased_and_17_chars(self, fixtures):
        for listing in self._parse_all(fixtures):
            assert len(listing.vin) == 17
            assert listing.vin == listing.vin.upper()

    def test_certified_badge_becomes_a_flag(self, fixtures):
        listings = self._parse_all(fixtures)
        assert any("certified" in l.flags for l in listings)

    def test_wrong_target_is_skipped(self, fixtures):
        """S3 cards must not be returned when we asked for the S5 Sportback."""
        html = (fixtures / "cars_com_srp.html").read_text()
        source = CarsComSource()
        got = [c for c in _result_cards(html) if source._parse_card(c, S5)]
        assert got == []


class TestLocationLineParsing:
    def test_plain(self):
        assert _parse_location(["Davenport, IA (360 mi)"]) == ("Davenport", "IA", 360.0)

    def test_thousands_separator_and_period(self):
        assert _parse_location(["Longmont, CO (1,092 mi.)"]) == ("Longmont", "CO", 1092.0)

    def test_two_word_city(self):
        assert _parse_location(["West Chester, PA (479 mi)"]) == ("West Chester", "PA", 479.0)

    def test_no_location_line(self):
        assert _parse_location(["$31,394", "Check Availability"]) == ("", "", None)


class TestAutotraderParsing:
    def _rows(self, fixtures):
        return json.loads((fixtures / "autotrader_listing.json").read_text())["listings"]

    def test_parses_a_clean_listing(self, fixtures):
        listing = AutotraderSource()._parse_row(self._rows(fixtures)[0], S3)
        assert listing is not None
        assert listing.vin == "WAUH3DGY3PA030642"
        assert listing.price == 36_713
        assert listing.mileage == 26_049  # from the "26,049" string
        assert listing.dealer == "Alderman Automotive"
        assert listing.city == "Fishers"
        assert listing.state == "IN"
        assert listing.distance_mi == 160.4
        assert listing.url == "https://www.autotrader.com/cars-for-sale/vehicle/791111001"

    def test_vhr_tokens_become_flags(self, fixtures):
        listing = AutotraderSource()._parse_row(self._rows(fixtures)[0], S3)
        assert "no_accidents" in listing.flags
        assert "clean_title" in listing.flags
        assert "one_owner" in listing.flags

    def test_accident_report_becomes_a_disqualifying_flag(self, fixtures):
        listing = AutotraderSource()._parse_row(self._rows(fixtures)[1], S3)
        assert "accident" in listing.flags

    def test_s5_coupe_rejected_but_sportback_accepted(self, fixtures):
        rows = self._rows(fixtures)
        source = AutotraderSource()
        assert source._parse_row(rows[2], S5) is None      # Coupe
        sportback = source._parse_row(rows[3], S5)
        assert sportback is not None
        assert sportback.vin == "WAUC4CF5XPA000002"
        assert "certified" in sportback.flags


class TestAutotraderBodyStyleQuirk:
    """Autotrader never writes 'Sportback'. The S5 Sportback is the Hatchback
    body; the S5 Coupe is the Coupe body. Getting this wrong silently drops
    every S5 Sportback on the site, so it is pinned down here."""

    def _rows(self, fixtures):
        return json.loads((fixtures / "autotrader_listing.json").read_text())["listings"]

    def test_body_styles_are_dicts_not_strings(self, fixtures):
        for row in self._rows(fixtures):
            assert all(isinstance(e, dict) for e in row["bodyStyles"])

    def test_hatchback_body_satisfies_the_sportback_target(self, fixtures):
        row = self._rows(fixtures)[3]
        assert "sportback" not in json.dumps(row).lower()  # the whole point
        listing = AutotraderSource()._parse_row(row, S5)
        assert listing is not None
        assert listing.body_style == "Hatchback"

    def test_coupe_body_does_not_satisfy_the_sportback_target(self, fixtures):
        assert AutotraderSource()._parse_row(self._rows(fixtures)[2], S5) is None
