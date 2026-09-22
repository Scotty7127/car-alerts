"""The interface every source implements.

A source is a callable object with a `name` and a `fetch()` that returns
normalized Listings. It must never raise for an expected failure (block,
layout change, empty result) - it logs and returns what it has. main.py
catches anything unexpected so one broken source cannot stop the others.
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..config import Target
from ..http import Fetcher
from ..models import Listing

log = logging.getLogger(__name__)


class Source(Protocol):
    name: str

    def fetch(self, targets: list[Target], fetcher: Fetcher) -> list[Listing]:
        ...


def clean_int(value: object) -> int | None:
    """'43,585 mi.' -> 43585 ; '$31,394' -> 31394 ; junk -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None


def matches_target(
    target: Target, model: str, trim: str, body: str = ""
) -> bool:
    """Does this listing belong to the target we asked for?

    Guards against a site returning adjacent models, and enforces the
    Sportback body requirement for the S5.
    """
    if target["model"].lower() != (model or "").strip().lower():
        return False
    return body_matches(target, model, trim, body)


def body_matches(target: Target, model: str, trim: str, body: str = "") -> bool:
    """Enforce a target's body-style requirement, if it has one."""
    wanted = target.get("body_any_of")
    if not wanted:
        return True
    haystack = f"{model} {trim} {body}".lower()
    return any(token.lower() in haystack for token in wanted)
