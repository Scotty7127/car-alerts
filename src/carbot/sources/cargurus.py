"""CarGurus - attempted, expected to skip.

Verified 2026-09: cargurus.com returns 200 to a Chrome-impersonating client,
but the SRP is a Remix app (window.__remixContext) whose listings are fetched
client-side. There is not a single VIN in the server response, and the JSON
route endpoints require a session token minted by the page's own JS.

Per the brief this is nice-to-have, so it warns and returns [] rather than
pretending. If you want it badly enough to run a browser, see README
"Optional: browser-rendered sources".
"""

from __future__ import annotations

import logging
import re

from ..config import Target
from ..http import BlockedError, Fetcher
from ..models import Listing

log = logging.getLogger(__name__)

SRP = "https://www.cargurus.com/Cars/l-Used-Audi-{model}-d{code}"
VIN_RE = re.compile(r"\b(WAU[A-HJ-NPR-Z0-9]{14})\b")


class CarGurusSource:
    name = "cargurus"

    def fetch(self, targets: list[Target], fetcher: Fetcher) -> list[Listing]:
        """Probe once; if the HTML is client-rendered (it is), warn and skip."""
        url = "https://www.cargurus.com/Cars/l-Used-Audi-S3-d2426"
        try:
            resp = fetcher.get(url)
        except BlockedError as exc:
            log.warning("[%s] blocked, skipping: %s", self.name, exc)
            return []
        except Exception as exc:
            log.warning("[%s] request failed, skipping: %s", self.name, exc)
            return []

        if not VIN_RE.search(resp.text):
            log.warning(
                "[%s] page is client-rendered (no VINs in server HTML) - skipping. "
                "This is expected; it is not an error.", self.name,
            )
            return []

        # If CarGurus ever starts server-rendering again, we want to know.
        log.warning(
            "[%s] VINs are now present in server HTML - a parser could be written. "
            "Currently unimplemented.", self.name,
        )
        return []


SOURCE = CarGurusSource()
