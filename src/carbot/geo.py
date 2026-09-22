"""Straight-line distance from the home ZIP.

Most sources hand us a distance directly because we pass our ZIP in the
search. This is the fallback for the ones that only give coordinates.
"""

from __future__ import annotations

import math

# Centroid of ZIP 45217 (Cincinnati / Elmwood Place, OH).
HOME_LAT = 39.1656
HOME_LON = -84.4903

EARTH_RADIUS_MI = 3958.8


def haversine_miles(lat: float, lon: float, *, from_lat: float = HOME_LAT,
                    from_lon: float = HOME_LON) -> float:
    """Great-circle distance in statute miles."""
    d_lat = math.radians(lat - from_lat)
    d_lon = math.radians(lon - from_lon)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(from_lat)) * math.cos(math.radians(lat))
        * math.sin(d_lon / 2) ** 2
    )
    return EARTH_RADIUS_MI * 2 * math.asin(math.sqrt(a))


def format_distance(miles: float | None) -> str:
    """'~160 mi' - or empty string when we genuinely do not know."""
    if miles is None:
        return ""
    return f"~{round(miles):,} mi"
