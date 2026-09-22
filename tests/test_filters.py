"""Filter logic: the part that decides whether a car is worth a ping."""

from __future__ import annotations

import pytest

from carbot.config import TARGETS
from carbot.filters import apply_filters, dedupe_by_vin, rejection_reason, target_for
from conftest import make_listing


class TestYearFloorIsPerModel:
    def test_s3_2022_passes(self):
        assert rejection_reason(make_listing(model="S3", year=2022), TARGETS) is None

    def test_s3_2021_rejected(self):
        reason = rejection_reason(make_listing(model="S3", year=2021), TARGETS)
        assert reason is not None and "2021" in reason

    def test_s4_2020_passes_even_though_s3_floor_is_2022(self):
        assert rejection_reason(make_listing(model="S4", year=2020), TARGETS) is None

    def test_s4_2019_rejected(self):
        reason = rejection_reason(make_listing(model="S4", year=2019), TARGETS)
        assert reason is not None and "2019" in reason


class TestPriceAndMileage:
    def test_at_the_price_cap_passes(self):
        assert rejection_reason(make_listing(price=38_500), TARGETS) is None

    def test_one_dollar_over_rejected(self):
        assert "price" in (rejection_reason(make_listing(price=38_501), TARGETS) or "")

    def test_at_the_mileage_cap_passes(self):
        assert rejection_reason(make_listing(mileage=45_000), TARGETS) is None

    def test_one_mile_over_rejected(self):
        assert "mileage" in (rejection_reason(make_listing(mileage=45_001), TARGETS) or "")

    @pytest.mark.parametrize("field", ["price", "mileage", "year"])
    def test_missing_data_is_rejected_not_assumed_good(self, field):
        assert rejection_reason(make_listing(**{field: None}), TARGETS) is not None


class TestExcludedFlags:
    @pytest.mark.parametrize(
        "flag", ["accident", "salvage", "frame_damage", "fleet_use"]
    )
    def test_each_disqualifying_flag_rejects(self, flag):
        reason = rejection_reason(make_listing(flags=[flag]), TARGETS)
        assert reason is not None and flag in reason

    def test_good_flags_do_not_reject(self):
        listing = make_listing(flags=["no_accidents", "one_owner", "certified"])
        assert rejection_reason(listing, TARGETS) is None

    def test_one_bad_flag_among_good_ones_still_rejects(self):
        listing = make_listing(flags=["no_accidents", "salvage", "certified"])
        assert rejection_reason(listing, TARGETS) is not None


class TestS5SportbackBodyRequirement:
    def test_sportback_matches(self):
        listing = make_listing(model="S5", trim="Sportback Premium Plus", year=2021)
        assert target_for(listing, TARGETS) is not None
        assert rejection_reason(listing, TARGETS) is None

    def test_non_sportback_s5_is_not_a_target(self):
        listing = make_listing(model="S5", trim="Premium Plus Coupe", year=2021)
        assert target_for(listing, TARGETS) is None

    def test_unrelated_model_is_not_a_target(self):
        assert target_for(make_listing(model="A4"), TARGETS) is None


class TestApplyFilters:
    def test_splits_kept_from_rejected_with_reasons(self):
        listings = [
            make_listing(vin="A" * 17),
            make_listing(vin="B" * 17, price=99_000),
            make_listing(vin="C" * 17, mileage=200_000),
        ]
        kept, rejected = apply_filters(listings, TARGETS)
        assert [l.vin for l in kept] == ["A" * 17]
        assert len(rejected) == 2
        assert all(reason for _, reason in rejected)


class TestDedupeByVin:
    def test_same_vin_on_two_sites_becomes_one_record_with_both_urls(self):
        a = make_listing(source="cars.com", url="https://cars.com/x")
        b = make_listing(source="carmax", url="https://carmax.com/car/123")
        merged = dedupe_by_vin([a, b])
        assert len(merged) == 1
        assert merged[0].urls == {
            "cars.com": "https://cars.com/x",
            "carmax": "https://carmax.com/car/123",
        }
        assert sorted(merged[0].sources) == ["carmax", "cars.com"]

    def test_merge_keeps_the_lower_price(self):
        a = make_listing(source="cars.com", price=37_000)
        b = make_listing(source="autotrader", price=36_200)
        assert dedupe_by_vin([a, b])[0].price == 36_200

    def test_merge_unions_flags(self):
        a = make_listing(source="cars.com", flags=["no_accidents"])
        b = make_listing(source="autotrader", flags=["one_owner"])
        assert sorted(dedupe_by_vin([a, b])[0].flags) == ["no_accidents", "one_owner"]

    def test_merge_fills_in_missing_detail(self):
        a = make_listing(source="cars.com", dealer="", distance_mi=None)
        b = make_listing(source="autotrader", dealer="Real Dealer", distance_mi=99.0)
        merged = dedupe_by_vin([a, b])[0]
        assert merged.dealer == "Real Dealer"
        assert merged.distance_mi == 99.0

    def test_different_vins_stay_separate(self):
        listings = [make_listing(vin="A" * 17), make_listing(vin="B" * 17)]
        assert len(dedupe_by_vin(listings)) == 2


class TestBodyStyleSynonyms:
    """The S5 Sportback is named differently per site: cars.com puts
    'Sportback' in the trim, Autotrader only reports a 'Hatchback' body."""

    def test_sportback_in_trim_matches(self):
        listing = make_listing(model="S5", trim="Sportback Premium Plus",
                               body_style="Hatchback", year=2021)
        assert target_for(listing, TARGETS) is not None

    def test_hatchback_body_alone_matches(self):
        listing = make_listing(model="S5", trim="Premium Plus",
                               body_style="Hatchback", year=2021)
        assert target_for(listing, TARGETS) is not None

    def test_coupe_body_does_not_match(self):
        listing = make_listing(model="S5", trim="Premium Plus",
                               body_style="Coupe", year=2021)
        assert target_for(listing, TARGETS) is None

    def test_convertible_body_does_not_match(self):
        listing = make_listing(model="S5", trim="Prestige",
                               body_style="Convertible", year=2021)
        assert target_for(listing, TARGETS) is None

    def test_s3_is_unaffected_by_body_style(self):
        assert target_for(make_listing(model="S3", body_style="Sedan"), TARGETS) is not None
