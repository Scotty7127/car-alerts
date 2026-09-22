"""Diff + notification logic - the part that decides what actually pings you."""

from __future__ import annotations

import json

import pytest

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


class TestCircuitBreaker:
    """A hard-blocked host must not be hit again for the rest of the run."""

    def test_blocked_host_short_circuits_without_a_second_request(self, monkeypatch):
        from carbot.http import BlockedError, Fetcher

        fetcher = Fetcher(delay_range=(0, 0), max_retries=1)
        calls: list[str] = []

        class FakeResp:
            status_code = 403
            headers: dict[str, str] = {}

        def fake_get(url, **kwargs):
            calls.append(url)
            return FakeResp()

        monkeypatch.setattr(fetcher._session, "get", fake_get)
        monkeypatch.setattr("time.sleep", lambda *_: None)

        with pytest.raises(BlockedError):
            fetcher.get("https://www.example.com/a")
        first_round = len(calls)
        assert first_round > 0

        with pytest.raises(BlockedError, match="already hard-blocked"):
            fetcher.get("https://www.example.com/b")
        assert len(calls) == first_round, "must not touch the network again"

    def test_a_different_host_is_unaffected(self, monkeypatch):
        from carbot.http import BlockedError, Fetcher

        fetcher = Fetcher(delay_range=(0, 0), max_retries=1)
        fetcher._blocked_hosts.add("www.blocked.com")

        class OkResp:
            status_code = 200
            headers = {"content-type": "text/html"}

        monkeypatch.setattr(fetcher._session, "get", lambda url, **kw: OkResp())
        monkeypatch.setattr("time.sleep", lambda *_: None)
        assert fetcher.get("https://www.other.com/x").status_code == 200


class TestJsonChallengeRecovery:
    """A bot-challenge arrives as 200 text/html, not an error status. An
    unlucky impersonation profile must not silently zero a source."""

    def _fetcher(self, monkeypatch):
        from carbot.http import Fetcher
        f = Fetcher(delay_range=(0, 0), max_retries=3)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        return f

    def test_rotates_fingerprint_and_recovers_from_an_html_challenge(self, monkeypatch):
        f = self._fetcher(monkeypatch)
        seen_profiles: list[str] = []

        class Resp:
            def __init__(self, ctype, payload=None):
                self.status_code = 200
                self.headers = {"content-type": ctype}
                self._payload = payload

            def json(self):
                return self._payload

        def fake_get(url, **kwargs):
            seen_profiles.append(f.impersonate)
            # First profile is a dud; anything after it works.
            if len(seen_profiles) == 1:
                return Resp("text/html; charset=utf-8")
            return Resp("application/json", {"listings": [1, 2]})

        monkeypatch.setattr(f, "_session", type("S", (), {"get": staticmethod(fake_get)})())
        monkeypatch.setattr(f, "rotate_fingerprint",
                            lambda: setattr(f, "impersonate", "chrome150"))

        assert f.get_json("https://www.example.com/api") == {"listings": [1, 2]}
        assert len(seen_profiles) == 2, "should have retried under a new profile"

    def test_gives_up_with_a_clear_error_if_every_profile_is_challenged(self, monkeypatch):
        from carbot.http import BlockedError

        f = self._fetcher(monkeypatch)

        class Resp:
            status_code = 200
            headers = {"content-type": "text/html"}

        monkeypatch.setattr(
            f, "_session",
            type("S", (), {"get": staticmethod(lambda url, **kw: Resp())})())
        monkeypatch.setattr(f, "rotate_fingerprint", lambda: None)

        with pytest.raises(BlockedError, match="after 3 fingerprints"):
            f.get_json("https://www.example.com/api")

    def test_every_pinned_profile_is_one_of_the_verified_good_ones(self):
        from carbot.http import IMPERSONATE_PROFILES
        # chrome136/133a/146/119 measured 0/3 against the JSON APIs.
        assert set(IMPERSONATE_PROFILES).isdisjoint(
            {"chrome136", "chrome133a", "chrome146", "chrome119", "chrome120"})


class TestClickThroughAndActions:
    """Tapping a notification must land on the actual listing page."""

    def test_click_is_the_listing_url_for_every_source(self):
        for url in ("https://www.cars.com/vehicledetail/abc/",
                    "https://www.autotrader.com/cars-for-sale/vehicle/791111001",
                    "https://www.carmax.com/car/28511769"):
            assert build_new_listing(make_listing(url=url)).click == url

    def test_price_drop_also_links_to_the_listing(self):
        listing = make_listing(url="https://www.carmax.com/car/123")
        note = build_price_drop(listing, 38_000, 36_000)
        assert note.click == "https://www.carmax.com/car/123"

    def test_single_source_listing_gets_no_redundant_buttons(self):
        assert build_new_listing(make_listing()).actions == []

    def test_two_source_listing_gets_a_button_per_site(self):
        listing = make_listing()
        listing.urls = {"cars.com": "https://cars.com/x",
                        "carmax": "https://www.carmax.com/car/1"}
        note = build_new_listing(listing)
        assert [label for label, _ in note.actions] == ["CarMax", "Cars.com"]

    def test_actions_header_is_valid_ntfy_syntax(self):
        listing = make_listing()
        listing.urls = {"cars.com": "https://cars.com/x",
                        "autotrader": "https://autotrader.com/y"}
        header = build_new_listing(listing).actions_header()
        assert header == (
            "view, Autotrader, https://autotrader.com/y, clear=true; "
            "view, Cars.com, https://cars.com/x, clear=true")

    def test_actions_are_capped_at_three(self):
        listing = make_listing()
        listing.urls = {f"src{i}": f"https://example.com/{i}" for i in range(5)}
        assert len(build_new_listing(listing).actions_header().split(";")) == 3

    def test_blank_urls_are_not_turned_into_buttons(self):
        listing = make_listing()
        listing.urls = {"cars.com": "https://cars.com/x", "carmax": ""}
        assert [label for label, _ in build_new_listing(listing).actions] == ["Cars.com"]


class TestNtfyClientEnvHandling:
    """GitHub Actions sets every env var declared in the workflow, so an unset
    secret arrives as an empty string. os.environ.get(k, default) does NOT fall
    back in that case - this silently broke every notification in production."""

    def test_empty_ntfy_url_falls_back_to_the_public_server(self, monkeypatch):
        from carbot.notify import NtfyClient
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.setenv("NTFY_URL", "")      # exactly what Actions supplies
        monkeypatch.setenv("NTFY_TOKEN", "")
        assert NtfyClient().endpoint == "https://ntfy.sh/t"

    def test_absent_ntfy_url_also_falls_back(self, monkeypatch):
        from carbot.notify import NtfyClient
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.delenv("NTFY_URL", raising=False)
        assert NtfyClient().endpoint == "https://ntfy.sh/t"

    def test_whitespace_only_url_falls_back(self, monkeypatch):
        from carbot.notify import NtfyClient
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.setenv("NTFY_URL", "   ")
        assert NtfyClient().endpoint == "https://ntfy.sh/t"

    def test_self_hosted_url_is_respected_and_trailing_slash_trimmed(self, monkeypatch):
        from carbot.notify import NtfyClient
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.setenv("NTFY_URL", "https://ntfy.example.com/")
        assert NtfyClient().endpoint == "https://ntfy.example.com/t"

    def test_a_relative_url_is_rejected_loudly_rather_than_failing_silently(
        self, monkeypatch
    ):
        from carbot.notify import NtfyClient
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.setenv("NTFY_URL", "ntfy.sh")   # no scheme
        with pytest.raises(ValueError, match="absolute URL"):
            NtfyClient()

    def test_empty_topic_disables_sending(self, monkeypatch):
        from carbot.notify import NtfyClient
        monkeypatch.setenv("NTFY_TOPIC", "")
        assert NtfyClient().enabled is False

    def test_empty_token_means_no_auth_header(self, monkeypatch):
        from carbot.notify import NtfyClient
        monkeypatch.setenv("NTFY_TOPIC", "t")
        monkeypatch.setenv("NTFY_TOKEN", "")
        assert NtfyClient().token == ""
