"""
Elevation lookups for RouteVolt's energy model (elevation_gain_m / elevation_loss_m
inputs -- see dataset_2/dataset_schema.md).

Open-Elevation (backend/app/config.ELEVATION_API_URL, defaults to the public
api.open-elevation.com instance) takes a batch of lat/lng points and returns
one elevation per point in a single call -- a good fit since OSRM already
hands back the lat/lng points along each route leg. Like routing_service /
traffic_service, this is a plain HTTP call with a 5s timeout that never lets
requests' exceptions leak to the caller -- callers catch ElevationError and
decide their own fallback (route_optimizer / battery_simulator both fall back
to a 0.0/0.0 stub for just the affected leg/candidate, not the whole route,
and flag it via elevation_source="stub_fallback" so it's distinguishable from
genuinely flat terrain).
"""

from typing import Optional

import requests

from backend.app.config import ELEVATION_API_URL

REQUEST_TIMEOUT_S = 5
CACHE_COORD_PRECISION = 5  # ~1.1m -- matches GPS precision, catches exact repeats

# Open-Elevation's public instance is backed by SRTM, a *surface* model (~30m
# horizontal resolution) that includes buildings/canopy, not bare-earth
# terrain -- verified: Chennai T. Nagar -> Airport, a coastal route with no
# real road grade, still returns a 5-23m band of point elevations that jumps
# +-5-15m between adjacent points (nearby structures, not the road climbing).
# Two mitigations, both needed -- one alone left visible noise on that route:
#   1. Points are downsampled well below OSRM's native 10-50m step spacing,
#      since sampling finer than the DEM's own ~30m resolution can't add
#      real signal, only noise (see _downsample_points).
#   2. Deltas below this floor are treated as flat rather than accumulated
#      (see the hysteresis loop in compute_leg_elevation).
# A self-hosted instance on a bare-earth DTM would need neither -- see README.
ELEVATION_NOISE_FLOOR_M = 5.0
MAX_SAMPLE_POINTS_PER_LEG = 25


class ElevationError(Exception):
    """Raised when Open-Elevation can't be reached or returns a bad response."""


# Simple in-memory cache, keyed by rounded (lat, lon) -- good enough to avoid
# re-querying the same points when a route/leg set repeats within a session.
# No persistence needed for this scale.
_elevation_cache: dict[tuple[float, float], float] = {}


def _cache_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, CACHE_COORD_PRECISION), round(lon, CACHE_COORD_PRECISION))


def get_elevation_profile(points: list[tuple[float, float]]) -> list[float]:
    """
    Batch-query Open-Elevation for a list of (lat, lon) points, returning
    elevation in meters in the same order (cache hits included).

    Raises ElevationError on timeout, network failure, or a malformed
    response -- callers decide their own per-leg/per-candidate fallback
    rather than this module silently returning zeros.
    """
    if not points:
        return []

    to_fetch = [(lat, lon) for lat, lon in points if _cache_key(lat, lon) not in _elevation_cache]

    if to_fetch:
        locations = [{"latitude": lat, "longitude": lon} for lat, lon in to_fetch]
        try:
            response = requests.post(
                ELEVATION_API_URL,
                json={"locations": locations},
                timeout=REQUEST_TIMEOUT_S,
            )
            response.raise_for_status()
            results = response.json()["results"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise ElevationError(f"Open-Elevation request failed: {exc}") from exc

        if len(results) != len(to_fetch):
            raise ElevationError(
                f"Open-Elevation returned {len(results)} results for {len(to_fetch)} points"
            )

        for (lat, lon), result in zip(to_fetch, results):
            try:
                _elevation_cache[_cache_key(lat, lon)] = float(result["elevation"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ElevationError(f"Open-Elevation returned a malformed result: {result}") from exc

    return [_elevation_cache[_cache_key(lat, lon)] for lat, lon in points]


def _downsample_points(points: list[tuple[float, float]], max_points: int = MAX_SAMPLE_POINTS_PER_LEG) -> list[tuple[float, float]]:
    """Evenly-spaced index subset of `points`, always keeping the first and last. See ELEVATION_NOISE_FLOOR_M."""
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    indices = sorted({round(i * step) for i in range(max_points)})
    return [points[i] for i in indices]


def compute_leg_elevation(points: list[tuple[float, float]]) -> dict:
    """
    Elevation gain/loss along an ordered sequence of points (a route leg's
    sampled coordinates), matching dataset_schema.md's convention: gain and
    loss are separate non-negative sums of consecutive positive/negative
    deltas, not a single net delta (climbing 100m then descending 100m within
    one leg is elevation_gain_m=100, elevation_loss_m=100, not a wash).

    Falls back to {"elevation_gain_m": 0.0, "elevation_loss_m": 0.0,
    "elevation_source": "stub_fallback"} if Open-Elevation is unreachable or
    there are fewer than 2 points to compare, so one flaky/degenerate leg
    doesn't take down the whole route plan.
    """
    points = _downsample_points(points)
    if len(points) < 2:
        return {"elevation_gain_m": 0.0, "elevation_loss_m": 0.0, "elevation_source": "stub_fallback"}

    try:
        elevations = get_elevation_profile(points)
    except ElevationError:
        return {"elevation_gain_m": 0.0, "elevation_loss_m": 0.0, "elevation_source": "stub_fallback"}

    # Hysteresis against ELEVATION_NOISE_FLOOR_M rather than a plain
    # consecutive-point diff: the reference point only moves once a delta
    # actually crosses the floor, so a real gradual climb (many small
    # same-direction steps) still accumulates correctly instead of every
    # individual sub-floor step being discarded, while DEM noise oscillating
    # around a flat baseline stays below the floor and never triggers.
    gain = 0.0
    loss = 0.0
    reference_elev = elevations[0]
    for elev in elevations[1:]:
        delta = elev - reference_elev
        if abs(delta) < ELEVATION_NOISE_FLOOR_M:
            continue
        if delta > 0:
            gain += delta
        else:
            loss += -delta
        reference_elev = elev

    return {"elevation_gain_m": gain, "elevation_loss_m": loss, "elevation_source": "open_elevation"}


def compute_point_to_point_elevation(origin: Optional[tuple[float, float]], points: list[tuple[float, float]]) -> dict:
    """
    Elevation gain/loss from a single origin to each of `points`, batched into
    one Open-Elevation call (origin + every point) rather than one call per
    point. For callers with only a straight-line distance to a candidate
    (no real route geometry) -- e.g. route_optimizer.calculate_best_route's
    per-station scoring -- this is a two-point approximation of the terrain
    change to reach that candidate, same spirit as its haversine detour_km
    already being a straight-line approximation rather than a real route.

    Returns {"elevation_source": ..., "by_point": [{"elevation_gain_m", "elevation_loss_m"}, ...]}
    index-aligned with `points`. Falls back to all-zero stub entries (source
    "stub_fallback") if Open-Elevation is unreachable, if `points` is empty,
    or if `origin` is None.
    """
    if not points or origin is None:
        return {
            "elevation_source": "stub_fallback",
            "by_point": [{"elevation_gain_m": 0.0, "elevation_loss_m": 0.0} for _ in points],
        }

    try:
        elevations = get_elevation_profile([origin] + list(points))
    except ElevationError:
        return {
            "elevation_source": "stub_fallback",
            "by_point": [{"elevation_gain_m": 0.0, "elevation_loss_m": 0.0} for _ in points],
        }

    origin_elev = elevations[0]
    by_point = []
    for elev in elevations[1:]:
        delta = elev - origin_elev
        if abs(delta) < ELEVATION_NOISE_FLOOR_M:
            by_point.append({"elevation_gain_m": 0.0, "elevation_loss_m": 0.0})
        elif delta > 0:
            by_point.append({"elevation_gain_m": delta, "elevation_loss_m": 0.0})
        else:
            by_point.append({"elevation_gain_m": 0.0, "elevation_loss_m": -delta})

    return {"elevation_source": "open_elevation", "by_point": by_point}
