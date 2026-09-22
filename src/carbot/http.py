"""Shared HTTP client.

Why curl_cffi instead of requests: cars.com (Cloudflare) and carmax.com
(Akamai) fingerprint the TLS handshake, not just the User-Agent. Plain
`requests` gets a 403 on every single request no matter what headers you set.
curl_cffi replays a real Chrome TLS/JA3 fingerprint and is a pure pip install -
no browser binary, unlike Selenium/Playwright.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from curl_cffi import requests as curl_requests

log = logging.getLogger(__name__)

# Do NOT set User-Agent by hand. curl_cffi sends a UA that matches the TLS
# fingerprint of the profile it is impersonating; overriding it desyncs the
# two and is itself a detection signal. Verified: spoofing a Chrome 131 UA on
# top of any profile gets Autotrader to serve a challenge page, while the same
# request with curl_cffi's own UA returns JSON.
#
# Rotating the *profile* rotates the User-Agent coherently, which is what we
# actually want. These four were each verified to get a real response from
# cars.com and autotrader.com; chrome131/133a/142/146, edge and safari were
# all challenged and are deliberately excluded.
IMPERSONATE_PROFILES = ["chrome", "chrome136", "chrome145", "chrome150"]


class BlockedError(RuntimeError):
    """The source responded 403/429, or served a challenge page."""


class Fetcher:
    """A polite, fingerprint-aware HTTP client with retry and backoff."""

    def __init__(
        self,
        delay_range: tuple[float, float] = (1.5, 4.0),
        timeout: int = 40,
        max_retries: int = 3,
    ) -> None:
        self.delay_range = delay_range
        self.timeout = timeout
        self.max_retries = max_retries
        self.impersonate = random.choice(IMPERSONATE_PROFILES)
        self._session = curl_requests.Session(impersonate=self.impersonate)
        self._last_request_at: float = 0.0
        self._warmed: set[str] = set()

    # -- internals ---------------------------------------------------------

    def _sleep_between(self) -> None:
        """Keep at least a random gap between consecutive requests."""
        elapsed = time.monotonic() - self._last_request_at
        wait = random.uniform(*self.delay_range) - elapsed
        if wait > 0:
            time.sleep(wait)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Only the headers a real navigation adds on top of the profile's own.

        Everything fingerprint-bearing (User-Agent, sec-ch-ua, TLS/H2 settings)
        is left to curl_cffi so it stays internally consistent.
        """
        h = {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
        }
        if extra:
            h.update(extra)
        return h

    # -- public ------------------------------------------------------------

    def warm_up(self, url: str) -> None:
        """Land on the site root once before hitting a deep URL.

        Cloudflare and Akamai score a bare request to a search-results URL far
        more harshly than the same request arriving after a normal-looking
        navigation, especially from a datacenter IP like a GitHub Actions
        runner. This picks up whatever clearance cookies the root sets and
        makes the follow-up look like a second page view rather than a
        cold-start scrape. Best effort - a failure here is not fatal.
        """
        host = _host(url)
        if host in self._warmed:
            return
        self._warmed.add(host)
        try:
            self._sleep_between()
            self._session.get(
                f"https://{host}/", headers=self._headers(), timeout=self.timeout
            )
            log.debug("  warmed up %s", host)
        except Exception as exc:
            log.debug("  warm-up of %s failed (continuing): %s", host, exc)
        finally:
            self._last_request_at = time.monotonic()

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        """GET with backoff. Raises BlockedError on a hard block."""
        self.warm_up(url)
        last_status: int | None = None
        for attempt in range(1, self.max_retries + 1):
            self._sleep_between()
            try:
                resp = self._session.get(
                    url, headers=self._headers(headers), timeout=self.timeout
                )
            except Exception as exc:  # network-level failure
                log.warning("  request error (%s/%s): %s", attempt, self.max_retries, exc)
                time.sleep(2 * attempt)
                continue
            finally:
                self._last_request_at = time.monotonic()

            last_status = resp.status_code
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 429, 503):
                backoff = 3 * attempt + random.uniform(0, 2)
                log.warning(
                    "  HTTP %s from %s - backing off %.1fs (%s/%s)",
                    resp.status_code, _host(url), backoff, attempt, self.max_retries,
                )
                time.sleep(backoff)
                # A fresh fingerprint sometimes clears a soft block. Rebuild the
                # session so the new profile applies to the TLS handshake too,
                # and re-warm: the new session has no cookies.
                self.rotate_fingerprint()
                self._warmed.discard(_host(url))
                self.warm_up(url)
                continue
            log.warning("  HTTP %s from %s", resp.status_code, _host(url))
            break

        raise BlockedError(f"{_host(url)} returned {last_status} after {self.max_retries} tries")

    def rotate_fingerprint(self) -> None:
        """Pick a new impersonation profile and rebuild the session with it."""
        self.impersonate = random.choice(IMPERSONATE_PROFILES)
        self._session = curl_requests.Session(impersonate=self.impersonate)
        self._warmed.clear()

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        """GET expecting JSON. Raises BlockedError if a challenge page comes back."""
        h = {"Accept": "application/json"}
        h.update(headers or {})
        resp = self.get(url, headers=h)
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            raise BlockedError(
                f"{_host(url)} returned {ctype or 'unknown content-type'} instead of JSON "
                "(usually a bot-challenge page)"
            )
        return resp.json()


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url
