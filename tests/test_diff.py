"""Diff + notification logic - the part that decides what actually pings you."""

from __future__ import annotations

import json

from carbot import state as state_mod
from carbot.main import _notifications_for, diff_listings
from carbot.notify import build_new_listing, build_price_drop
from conftest import make_listing

THRESHOLD = 500


def state_of(*listings):
    return {l.vin: l for l in listings}


class TestNewListings:
    def test_unseen_vin_is_new(self):
        current = [make_listing(vin="A" * 17)]
        diff, _ = diff_listings({}, current, THRESHOLD)
        assert [l.vin for l in diff.new] == ["A" * 17]

    def test_known_vin_is_not_new(self):
        known = make_listing(vin="A" * 17)
        diff, _ = diff_listings(state_of(known), [make_listing(vin="A" * 17)], THRESHOLD)
        assert diff.new == []
        assert diff.unchanged == 1

    def test_first_seen_is_preserved_across_runs(self):
        known = make_listing(vin="A" * 17)
        known.first_seen = "2020-01-01T00:00:00+00:00"
        _, next_state = diff_listings(state_of(known), [make_listing(vin="A" * 17)], THRESHOLD)
        assert next_state["A" * 17].first_seen == "2020-01-01T00:00:00+00:00"

    def test_last_seen_advances_for_a_listing_still_present(self):
        known = make_listing(vin="A" * 17)
        known.last_seen = "2020-01-01T00:00:00+00:00"
        _, next_state = diff_listings(state_of(known), [make_listing(vin="A" * 17)], THRESHOLD)
        assert next_state["A" * 17].last_seen != "2020-01-01T00:00:00+00:00"


class TestPriceDrops:
    def _drop(self, old_price, new_price):
        known = make_listing(vin="A" * 17, price=old_price)
        current = [make_listing(vin="A" * 17, price=new_price)]
        diff, _ = diff_listings(state_of(known), current, THRESHOLD)
        return diff

    def test_drop_at_the_threshold_notifies(self):
        assert len(self._drop(37_000, 36_500).price_drops) == 1

    def test_drop_below_the_threshold_is_silent(self):
        diff = self._drop(37_000, 36_501)
        assert diff.price_drops == []
        assert diff.unchanged == 1

    def test_price_increase_is_silent(self):
        assert self._drop(36_000, 37_000).price_drops == []

    def test_drop_reports_both_prices(self):
        listing, old, new = self._drop(38_000, 36_000).price_drops[0]
        assert (old, new) == (38_000, 36_000)

    def test_a_price_drop_is_not_also_counted_as_new(self):
        assert self._drop(38_000, 36_000).new == []


class TestDisappeared:
    def test_missing_vin_is_marked_gone_and_kept(self):
        known = make_listing(vin="A" * 17)
        diff, next_state = diff_listings(state_of(known), [], THRESHOLD)
        assert [l.vin for l in diff.disappeared] == ["A" * 17]
        assert next_state["A" * 17].status == "gone"

    def test_last_seen_is_frozen_for_a_gone_listing(self):
        known = make_listing(vin="A" * 17)
        known.last_seen = "2020-01-01T00:00:00+00:00"
        _, next_state = diff_listings(state_of(known), [], THRESHOLD)
        assert next_state["A" * 17].last_seen == "2020-01-01T00:00:00+00:00"

    def test_disappearing_never_generates_a_notification(self):
        known = make_listing(vin="A" * 17)
        diff, _ = diff_listings(state_of(known), [], THRESHOLD)
        assert _notifications_for(diff) == []

    def test_a_returning_listing_is_not_re_notified_as_new(self):
        gone = make_listing(vin="A" * 17)
        gone.status = "gone"
        diff, _ = diff_listings(state_of(gone), [make_listing(vin="A" * 17)], THRESHOLD)
        assert diff.new == []


class TestNotificationContent:
    def test_new_listing_title_matches_the_agreed_format(self):
        note = build_new_listing(make_listing(year=2023, model="S3",
                                              trim="Premium Plus", price=36_713))
        assert note.title == "New: 2023 Audi S3 Premium Plus - $36,713"

    def test_body_matches_the_agreed_format(self):
        note = build_new_listing(make_listing(flags=["no_accidents"]))
        assert note.body == (
            "26,049 mi · Fishers, IN (~160 mi) · Alderman Automotive · "
            "No accidents · Cars.com"
        )

    def test_click_action_is_the_listing_url(self):
        listing = make_listing(url="https://example.com/car/1")
        assert build_new_listing(listing).click == "https://example.com/car/1"

    def test_priority_is_high_under_36k(self):
        assert build_new_listing(make_listing(price=35_999)).priority == "high"

    def test_priority_is_default_at_36k_and_above(self):
        assert build_new_listing(make_listing(price=36_000)).priority == "default"

    def test_price_drop_title_shows_the_delta(self):
        note = build_price_drop(make_listing(price=36_000), 38_000, 36_000)
        assert "Price drop" in note.title
        assert "$36,000" in note.title and "-$2,000" in note.title

    def test_price_drop_body_mentions_the_old_price(self):
        note = build_price_drop(make_listing(price=36_000), 38_000, 36_000)
        assert note.body.startswith("was $38,000 · ")

    def test_multi_source_listing_credits_both_sites(self):
        listing = make_listing()
        listing.sources = ["cars.com", "carmax"]
        assert build_new_listing(listing).body.endswith("Cars.com + CarMax")

    def test_non_available_status_is_surfaced(self):
        listing = make_listing(status="coming soon")
        assert "Coming Soon" in build_new_listing(listing).body


class TestSeedAndStatePersistence:
    def test_seed_run_produces_state_but_the_caller_suppresses_pings(self):
        current = [make_listing(vin="A" * 17), make_listing(vin="B" * 17)]
        diff, next_state = diff_listings({}, current, THRESHOLD)
        assert len(next_state) == 2
        assert len(_notifications_for(diff)) == 2  # main.py drops these under --seed

    def test_state_roundtrips_through_json(self, tmp_path):
        path = tmp_path / "listings.json"
        original = {l.vin: l for l in [make_listing(vin="A" * 17)]}
        state_mod.save(original, path)
        loaded = state_mod.load(path)
        assert loaded["A" * 17].to_dict() == original["A" * 17].to_dict()

    def test_save_reports_no_change_on_identical_data(self, tmp_path):
        path = tmp_path / "listings.json"
        data = {l.vin: l for l in [make_listing(vin="A" * 17)]}
        assert state_mod.save(data, path) is True
        assert state_mod.save(data, path) is False

    def test_save_reports_change_when_price_moves(self, tmp_path):
        path = tmp_path / "listings.json"
        state_mod.save({"A" * 17: make_listing(vin="A" * 17, price=37_000)}, path)
        assert state_mod.save({"A" * 17: make_listing(vin="A" * 17, price=36_000)}, path) is True

    def test_corrupt_state_file_starts_empty_rather_than_crashing(self, tmp_path):
        path = tmp_path / "listings.json"
        path.write_text("{not json")
        assert state_mod.load(path) == {}

    def test_missing_state_file_starts_empty(self, tmp_path):
        assert state_mod.load(tmp_path / "nope.json") == {}
