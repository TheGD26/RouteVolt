"""
Traffic-aware ETA + congestion for RouteVolt.

Ola Maps (Krutrim) Distance Matrix is the primary provider; Google Distance
Matrix is the fallback if Ola fails or isn't configured. If both fail (or
neither API key is set), callers get a straight-line-distance estimate
instead of an exception -- /route/optimize should still return *something*
rather than 500 just because a third-party traffic API is down.

Both OLA_MAPS_API_KEY and GOOGLE_MAPS_API_KEY are read lazily (only when a
function here actually runs), not at import time, so the app still boots
fine with neither configured -- it just falls back straight to the
straight-line estimate.

get_leg_traffic()/get_leg_traffic_batch() are the shared core: one Ola/Google
matrix call produces both the traffic-aware duration (what get_traffic_eta
displays) and the congestion_level the energy model consumes
(route_optimizer / battery_simulator), so a leg's traffic never gets queried
twice for two different purposes.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

from backend.app.config import GOOGLE_MAPS_API_KEY, OLA_MAPS_ALLOWED_ORIGIN, OLA_MAPS_API_KEY

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 5
EARTH_RADIUS_KM = 6371.0
STRAIGHT_LINE_AVG_SPEED_KMH = 40.0  # rough urban/mixed fallback speed

OLA_DISTANCE_MATRIX_URL = "https://api.olamaps.io/routing/v1/distanceMatrix"
GOOGLE_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

# Congestion stub used when no real traffic data is available for a leg --
# same value route_optimizer/battery_simulator hardcoded everywhere before
# this module could supply a real number.
DEFAULT_CONGESTION_LEVEL = 0.3

CACHE_COORD_PRECISION = 4  # ~11m -- catches exact-repeat legs within a session

# Ola's distanceMatrix endpoint isn't documented with an origin-count limit,
# but empirically (single destination) it 200s at 50 origins and 400s
# ("Invalid Request. Please check the request parameters") at 51+ -- found by
# a direct binary-search sweep against the live API, not a guess. Batches of
# hundreds of candidate stations (route_optimizer's per-station scoring) need
# chunking below this before the request ever reaches Ola.
OLA_MAX_ORIGINS_PER_REQUEST = 50

# Concurrency for firing chunked batches: high enough that a several-hundred-
# origin call doesn't serialize into a dozen-plus sequential round trips, low
# enough to stay clear of Ola's per-*second* burst limit -- 4 concurrent
# batches tripped 429s even though the plan has thousands of monthly requests
# free, so this is a burst-limit workaround, not a quota-conservation measure.
OLA_BATCH_CONCURRENCY = 2


class TrafficServiceError(Exception):
    """Raised when no provider (including the straight-line fallback) can answer."""


def _require_a_provider_key():
    if not OLA_MAPS_API_KEY and not GOOGLE_MAPS_API_KEY:
        raise TrafficServiceError(
            "No traffic provider configured: set OLA_MAPS_API_KEY and/or "
            "GOOGLE_MAPS_API_KEY in the environment."
        )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _straight_line_eta(origin: tuple[float, float], dest: tuple[float, float]) -> dict:
    distance_km = _haversine_km(origin[0], origin[1], dest[0], dest[1])
    duration_hours = distance_km / STRAIGHT_LINE_AVG_SPEED_KMH
    return {
        "duration_seconds": round(duration_hours * 3600),
        "distance_meters": round(distance_km * 1000),
        "traffic_aware": False,
        "provider": "straight_line_estimate",
    }


def _format_latlng(point: tuple[float, float]) -> str:
    return f"{point[0]},{point[1]}"


def _ola_matrix(origins: list[tuple[float, float]], destination: tuple[float, float]) -> list[dict]:
    """
    Ola distance matrix for any number of origins against one destination --
    chunks into OLA_MAX_ORIGINS_PER_REQUEST-sized batches and fires them with
    bounded concurrency (see OLA_BATCH_CONCURRENCY) when there are more
    origins than fit in one request, then reassembles the results in the
    original origins order so callers see the same flat list either way.

    A single batch's failure propagates as-is (same as before chunking
    existed) rather than partially degrading -- get_eta_matrix's caller
    already falls back to Google/straight-line for the whole call on any
    TrafficServiceError/RequestException, and a mix of live + fallback
    congestion values within one route/scoring pass would be more confusing
    than useful.
    """
    if not OLA_MAPS_API_KEY:
        raise TrafficServiceError("OLA_MAPS_API_KEY not set")

    if len(origins) <= OLA_MAX_ORIGINS_PER_REQUEST:
        return _ola_matrix_batch(origins, destination)

    batches = [
        origins[i : i + OLA_MAX_ORIGINS_PER_REQUEST]
        for i in range(0, len(origins), OLA_MAX_ORIGINS_PER_REQUEST)
    ]
    logger.info(
        "Ola Maps distance matrix: chunking %d origins into %d batches (sizes=%s, max %d/request)",
        len(origins), len(batches), [len(b) for b in batches], OLA_MAX_ORIGINS_PER_REQUEST,
    )

    with ThreadPoolExecutor(max_workers=OLA_BATCH_CONCURRENCY) as pool:
        futures = [pool.submit(_ola_matrix_batch, batch, destination) for batch in batches]
        batch_results = [future.result() for future in futures]

    return [result for batch in batch_results for result in batch]


def _ola_matrix_batch(origins: list[tuple[float, float]], destination: tuple[float, float]) -> list[dict]:
    """One Ola distanceMatrix request for <= OLA_MAX_ORIGINS_PER_REQUEST origins."""
    response = requests.get(
        OLA_DISTANCE_MATRIX_URL,
        params={
            "origins": "|".join(_format_latlng(o) for o in origins),
            "destinations": _format_latlng(destination),
            "api_key": OLA_MAPS_API_KEY,
        },
        # Ola's domain whitelist check applies to this server-to-server call
        # too, not just browser requests -- without a matching Origin/Referer
        # it 401s, and with a mismatched one it 403s "Domain is not allowed".
        headers={
            "Origin": OLA_MAPS_ALLOWED_ORIGIN,
            "Referer": OLA_MAPS_ALLOWED_ORIGIN,
        },
        timeout=REQUEST_TIMEOUT_S,
    )
    if not response.ok:
        logger.warning(
            "Ola Maps distance matrix HTTP %s for origins=%r destinations=%r -- response body: %s",
            response.status_code, origins, destination, response.text[:2000],
        )
        response.raise_for_status()
    payload = response.json()

    # Ola's Distance Matrix response uses "SUCCESS" at the top level (unlike
    # Google's "OK") -- each individual element carries its own "OK"/error
    # status below, which is what's actually checked per-result.
    if str(payload.get("status", "")).upper() not in ("OK", "SUCCESS"):
        logger.warning("Ola Maps distance matrix returned status=%r -- full payload: %s", payload.get("status"), payload)
        raise TrafficServiceError(f"Ola Maps returned status {payload.get('status')!r}")

    results = []
    for row in payload["rows"]:
        element = row["elements"][0]
        if str(element.get("status", "OK")).upper() != "OK":
            logger.warning("Ola Maps element status=%r for row: %s", element.get("status"), row)
            raise TrafficServiceError(f"Ola Maps element status {element.get('status')!r}")
        results.append({
            "duration_seconds": int(element["duration"]),
            "distance_meters": int(element["distance"]),
            "traffic_aware": True,
            "provider": "ola_maps",
            # Ola's public Distance Matrix product returns a single
            # traffic-aware duration only -- no documented no-traffic
            # baseline in the same response (that's a separate "Distance
            # Matrix Basic" product). Querying it too would double Ola's API
            # usage per leg just to get a baseline, so callers instead supply
            # their own (e.g. OSRM's traffic-free leg duration) via
            # get_leg_traffic's typical_duration_s -- see _congestion_from_result.
            "duration_typical_seconds": None,
        })
    return results


def _google_matrix(origins: list[tuple[float, float]], destination: tuple[float, float]) -> list[dict]:
    if not GOOGLE_MAPS_API_KEY:
        raise TrafficServiceError("GOOGLE_MAPS_API_KEY not set")

    response = requests.get(
        GOOGLE_DISTANCE_MATRIX_URL,
        params={
            "origins": "|".join(_format_latlng(o) for o in origins),
            "destinations": _format_latlng(destination),
            "departure_time": "now",  # required for duration_in_traffic
            "key": GOOGLE_MAPS_API_KEY,
        },
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != "OK":
        raise TrafficServiceError(f"Google Distance Matrix returned status {payload.get('status')!r}")

    results = []
    for row in payload["rows"]:
        element = row["elements"][0]
        if element.get("status") != "OK":
            raise TrafficServiceError(f"Google Distance Matrix element status {element.get('status')!r}")
        duration = element.get("duration_in_traffic") or element["duration"]
        results.append({
            "duration_seconds": int(duration["value"]),
            "distance_meters": int(element["distance"]["value"]),
            "traffic_aware": "duration_in_traffic" in element,
            "provider": "google_maps",
            # Google's element always carries the plain (no-traffic) "duration"
            # alongside "duration_in_traffic" -- a real no-traffic baseline
            # from the same response, not a second call.
            "duration_typical_seconds": int(element["duration"]["value"]),
        })
    return results


def get_eta_matrix(origins: list[tuple[float, float]], destination: tuple[float, float]) -> list[dict]:
    """
    Batch ETA lookup: one origin-per-station, one shared destination, in a
    single HTTP call per provider (not N sequential calls). Falls back
    provider-wide: Ola -> Google -> straight-line per origin.
    """
    if not origins:
        return []

    _require_a_provider_key()

    try:
        return _ola_matrix(origins, destination)
    except (requests.RequestException, TrafficServiceError, KeyError, ValueError) as exc:
        logger.warning("Ola Maps provider failed (%s: %s), falling back to Google", type(exc).__name__, exc)

    try:
        return _google_matrix(origins, destination)
    except (requests.RequestException, TrafficServiceError, KeyError, ValueError) as exc:
        logger.warning("Google Distance Matrix provider failed (%s: %s), falling back to straight-line estimate", type(exc).__name__, exc)

    return [_straight_line_eta(origin, destination) for origin in origins]


def get_traffic_eta(origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float) -> dict:
    """
    Single-pair ETA: {"duration_seconds", "distance_meters", "traffic_aware", "provider"}.
    Ola Maps primary, Google Distance Matrix fallback, straight-line estimate
    if both fail -- this never raises for provider failure, only when neither
    key is configured at all.
    """
    _require_a_provider_key()
    results = get_eta_matrix([(origin_lat, origin_lng)], (dest_lat, dest_lng))
    return results[0]


# ---- Congestion (energy model input) -- shares get_eta_matrix's provider chain ----

_leg_traffic_cache: dict[tuple, dict] = {}


def _round_point(point: tuple[float, float]) -> tuple[float, float]:
    return (round(point[0], CACHE_COORD_PRECISION), round(point[1], CACHE_COORD_PRECISION))


def _congestion_from_result(result: dict, typical_duration_s: Optional[float]) -> tuple[float, str]:
    """
    0-1 congestion level: (duration_with_traffic - typical) / typical, clamped to [0, 1]
    -- no delay is 0, double the typical time (or worse) is 1, capped there rather than
    exceeding it.

    Falls back to (DEFAULT_CONGESTION_LEVEL, "stub_fallback") if this leg has no real
    traffic data at all (`result["traffic_aware"]` false -- both providers failed or
    neither is configured, `result` is the straight-line estimate) or no typical-duration
    baseline to compare against: the provider's own (Google's `duration_typical_seconds`)
    if present, else the caller-supplied one (e.g. OSRM's traffic-free leg duration).
    """
    if not result.get("traffic_aware"):
        return DEFAULT_CONGESTION_LEVEL, "stub_fallback"

    typical = result.get("duration_typical_seconds") or typical_duration_s
    if not typical or typical <= 0:
        return DEFAULT_CONGESTION_LEVEL, "stub_fallback"

    level = (result["duration_seconds"] - typical) / typical
    return max(0.0, min(level, 1.0)), "live"


def get_leg_traffic(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    typical_duration_s: Optional[float] = None,
) -> dict:
    """
    One Ola -> Google -> straight-line call for a single leg (see get_eta_matrix),
    producing everything both the display ETA and the energy model's congestion
    feature need from that one response -- not two separate calls for two purposes.
    Cached per rounded origin/destination pair so a leg queried more than once in the
    same session (e.g. plan_charge_to_percent re-summing remaining_legs) doesn't
    re-hit the network.

    typical_duration_s: a no-traffic baseline to compare against when the chosen
    provider doesn't supply its own (Ola doesn't -- see _ola_matrix). Pass the leg's
    OSRM duration_s when you have a real route leg.

    Returns {"duration_seconds", "distance_meters", "traffic_aware", "provider",
    "congestion_level", "congestion_source"}. Never raises -- a leg with no traffic
    data at all just gets congestion_source="stub_fallback", same spirit as
    elevation_service's per-leg fallback.
    """
    cache_key = (_round_point((origin_lat, origin_lng)), _round_point((dest_lat, dest_lng)))
    if cache_key in _leg_traffic_cache:
        return _leg_traffic_cache[cache_key]

    try:
        result = get_eta_matrix([(origin_lat, origin_lng)], (dest_lat, dest_lng))[0]
    except TrafficServiceError:
        result = _straight_line_eta((origin_lat, origin_lng), (dest_lat, dest_lng))

    congestion_level, congestion_source = _congestion_from_result(result, typical_duration_s)
    out = {**result, "congestion_level": congestion_level, "congestion_source": congestion_source}
    _leg_traffic_cache[cache_key] = out
    return out


def get_leg_traffic_batch(
    destination: tuple[float, float],
    origins: list[tuple[float, float]],
    typical_durations_s: list[Optional[float]],
) -> list[dict]:
    """
    Batched get_leg_traffic for callers scoring many points against one shared
    location in a single request (e.g. route_optimizer.calculate_best_route's
    per-candidate-station scoring) -- one Ola/Google matrix call for every origin
    against `destination`, not one call per candidate. `typical_durations_s` is
    index-aligned with `origins`.

    Direction note: origins here are the candidates, destination is the vehicle's
    current position (reverse of the actual travel direction) -- congestion level
    is treated as direction-symmetric for this approximation, same spirit as
    calculate_best_route's haversine detour_km already standing in for a real route.
    """
    if not origins:
        return []

    try:
        results = get_eta_matrix(origins, destination)
    except TrafficServiceError:
        results = [_straight_line_eta(origin, destination) for origin in origins]

    out = []
    for result, typical_duration_s in zip(results, typical_durations_s):
        congestion_level, congestion_source = _congestion_from_result(result, typical_duration_s)
        out.append({**result, "congestion_level": congestion_level, "congestion_source": congestion_source})
    return out


def get_traffic_congestion_level(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    typical_duration_s: Optional[float] = None,
) -> float:
    """
    0-1 traffic congestion level for a single origin/destination pair -- see
    get_leg_traffic for the source-flagged version (callers that need to know
    whether this came from live traffic data or the stub_fallback should prefer
    that one; this is a thin convenience wrapper around it).
    """
    return get_leg_traffic(origin_lat, origin_lng, dest_lat, dest_lng, typical_duration_s)["congestion_level"]
