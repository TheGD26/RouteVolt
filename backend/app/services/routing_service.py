"""
Routing + geocoding for RouteVolt.

OSRM (backend/app/config.OSRM_BASE_URL, defaults to the public demo server)
is the primary turn-by-turn/distance-matrix provider; Google Directions +
Distance Matrix (backend/app/config.GOOGLE_MAPS_API_KEY,
ENABLE_GOOGLE_ROUTING_FALLBACK) are the fallback if OSRM fails or is in
cooldown after a recent failure (see _provider_available/_PROVIDER_COOLDOWN_S)
-- get_route/get_distance_matrix normalize either provider's response into
the exact same shape, so callers (graph_planner, battery_simulator) can't
tell which one answered. Both Google endpoints are billed, unlike OSRM's free
public demo server -- ENABLE_GOOGLE_ROUTING_FALLBACK gates that spend
independently of GOOGLE_MAPS_API_KEY being set (see its config.py comment),
and every time the fallback actually fires it's logged at INFO for cost
visibility. Ola Maps' Geocoding API (backend/app/config.OLA_MAPS_API_KEY,
same credential traffic_service.py uses) resolves the free-text
current_location/destination strings on RouteRequest into coordinates --
swapped in for Nominatim, whose OSM coverage of Indian addresses is weak and
whose usage policy rules out anything beyond light, non-production use. All
of these are plain HTTP calls with a 5s timeout -- none raise anything
provider-shaped, callers just catch RoutingError / GeocodingError.
"""

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

from backend.app.config import (
    ENABLE_GOOGLE_ROUTING_FALLBACK,
    GOOGLE_MAPS_API_KEY,
    OLA_MAPS_ALLOWED_ORIGIN,
    OLA_MAPS_API_KEY,
    OSRM_BASE_URL,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 5
OLA_GEOCODE_URL = "https://api.olamaps.io/places/v1/geocode"
GOOGLE_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
GOOGLE_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

# Circuit breaker for OSRM itself, once it's shown it's down within this
# request -- same idea as traffic_service._provider_failed_until, kept as
# its own copy here rather than imported/shared (routing and traffic are
# independent concerns/failure domains, even against the same public OSRM
# instance in OSRM_BASE_URL's case). Without this, build_station_graph's
# ThreadPoolExecutor firing hundreds of get_route calls in one request (see
# graph_planner.GRAPH_EDGE_REFINEMENT_CONCURRENCY) would pay a full OSRM
# timeout on every single one before falling back, once OSRM has already
# shown it's down. 30s mirrors traffic_service._PROVIDER_COOLDOWN_S's own
# reasoning: short enough a transient blip still gets retried within a long
# request, long enough not to keep re-discovering the same outage.
_PROVIDER_COOLDOWN_S = 30.0
_provider_failed_until: dict[str, float] = {}


def _provider_available(name: str) -> bool:
    return time.monotonic() >= _provider_failed_until.get(name, 0.0)


def _mark_provider_failed(name: str) -> None:
    _provider_failed_until[name] = time.monotonic() + _PROVIDER_COOLDOWN_S


def _google_fallback_enabled() -> bool:
    return ENABLE_GOOGLE_ROUTING_FALLBACK and bool(GOOGLE_MAPS_API_KEY)


class RoutingError(Exception):
    """Raised when neither OSRM nor the Google fallback (if enabled) can produce a route."""


class GeocodingError(Exception):
    """Raised when an address can't be resolved to coordinates."""


class GeocodingNotFoundError(GeocodingError):
    """The provider answered, but nothing matched this address -- a problem with the user's input."""


class GeocodingProviderError(GeocodingError):
    """The provider is unconfigured, unreachable, rate-limited or returned something unusable -- not the user's fault."""


def geocode(address: str) -> tuple[float, float]:
    """Resolve a free-text address to (latitude, longitude) via Ola Maps' Geocoding API."""
    if not OLA_MAPS_API_KEY:
        raise GeocodingProviderError("OLA_MAPS_API_KEY not set")

    try:
        response = requests.get(
            OLA_GEOCODE_URL,
            params={"address": address, "api_key": OLA_MAPS_API_KEY},
            # Same domain-whitelist check traffic_service.py's Ola calls hit --
            # applies to this server-to-server call too, see that module's
            # _ola_matrix_batch for the 401/403 details.
            headers={
                "Origin": OLA_MAPS_ALLOWED_ORIGIN,
                "Referer": OLA_MAPS_ALLOWED_ORIGIN,
            },
            timeout=REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GeocodingProviderError(f"Geocoding request failed for '{address}': {exc}") from exc

    # Ola's forward-geocode response: {"geocodingResults": [{"geometry":
    # {"location": {"lat", "lng"}}, ...}, ...], "status": "ok"} -- confirmed
    # against the live API, not assumed from docs (Ola's public docs pages
    # don't include a worked example response).
    try:
        results = response.json().get("geocodingResults") or []
    except ValueError as exc:
        raise GeocodingProviderError(f"Geocoding response for '{address}' was not valid JSON") from exc
    if not results:
        raise GeocodingNotFoundError(f"No geocoding match found for '{address}'")

    try:
        location = results[0]["geometry"]["location"]
        return float(location["lat"]), float(location["lng"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeocodingProviderError(f"Geocoding response for '{address}' had an unexpected shape") from exc


# The public OSRM demo server (router.project-osrm.org) caps its Table
# Service at 100 total coordinates per request -- found by a direct
# binary-search sweep against the live instance (same spirit as
# traffic_service.OLA_MAX_ORIGINS_PER_REQUEST's precedent): 100 succeeds,
# 101+ either 400s outright or times out under load. A full-state,
# north-to-south trip's candidate station list can run into the hundreds,
# so a single request can't cover it -- see _get_distance_matrix_chunked.
OSRM_TABLE_MAX_COORDINATES = 100

# Block size for chunked requests: any two blocks combined (2 * this) must
# stay under OSRM_TABLE_MAX_COORDINATES, since a cross-block request lists
# both blocks' coordinates together to get real bidirectional distances in
# one call (see _get_distance_matrix_chunked).
_TABLE_BLOCK_SIZE = OSRM_TABLE_MAX_COORDINATES // 2

# Concurrency for firing chunked block-pair requests -- same burst-limit
# reasoning as traffic_service.OLA_BATCH_CONCURRENCY: a handful of
# concurrent requests keeps a many-block matrix from serializing into a slow
# chain of round trips without hammering the shared public demo server.
_TABLE_BATCH_CONCURRENCY = 3


def _table_request(coordinates: list[tuple[float, float]]) -> dict:
    """One raw OSRM Table Service call for <= OSRM_TABLE_MAX_COORDINATES coordinates."""
    coords = ";".join(f"{lon},{lat}" for lat, lon in coordinates)
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coords}"
    try:
        response = requests.get(url, params={"annotations": "distance,duration"}, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f"OSRM table request failed: {exc}") from exc

    payload = response.json()
    if payload.get("code") != "Ok":
        raise RoutingError(f"OSRM table returned no matrix: {payload.get('code', 'unknown error')}")
    return payload


def _get_distance_matrix_chunked(coordinates: list[tuple[float, float]]) -> dict:
    """
    Full N x N distance/duration matrix built from block-pair OSRM Table
    requests, for N beyond OSRM_TABLE_MAX_COORDINATES. Coordinates are split
    into blocks of <= _TABLE_BLOCK_SIZE; for every pair of blocks (including
    a block against itself), one request lists that pair's combined
    coordinates (<= OSRM_TABLE_MAX_COORDINATES total) and OSRM returns the
    full bidirectional submatrix between them in one call -- cheaper than
    querying each direction separately. Fired with bounded concurrency (see
    _TABLE_BATCH_CONCURRENCY), then stitched into the full matrix.
    """
    n = len(coordinates)
    blocks = [
        list(range(i, min(i + _TABLE_BLOCK_SIZE, n)))
        for i in range(0, n, _TABLE_BLOCK_SIZE)
    ]

    def _fetch_block_pair(block_a: list[int], block_b: list[int]) -> tuple[list[int], list[int], dict]:
        combined = block_a if block_a is block_b else block_a + block_b
        return block_a, block_b, _table_request([coordinates[idx] for idx in combined])

    pairs = [
        (blocks[a], blocks[b])
        for a in range(len(blocks))
        for b in range(a, len(blocks))
    ]
    logger.info(
        "OSRM table: chunking %d coordinates into %d blocks (%d block-pair requests, max %d/request)",
        n, len(blocks), len(pairs), OSRM_TABLE_MAX_COORDINATES,
    )

    distances_m = [[None] * n for _ in range(n)]
    durations_s = [[None] * n for _ in range(n)]

    with ThreadPoolExecutor(max_workers=_TABLE_BATCH_CONCURRENCY) as pool:
        futures = [pool.submit(_fetch_block_pair, block_a, block_b) for block_a, block_b in pairs]
        for future in futures:
            block_a, block_b, payload = future.result()
            if block_b is block_a:
                # Self-pair: payload is a plain size_a x size_a submatrix
                # over block_a alone.
                for local_i, global_i in enumerate(block_a):
                    for local_j, global_j in enumerate(block_a):
                        distances_m[global_i][global_j] = payload["distances"][local_i][local_j]
                        durations_s[global_i][global_j] = payload["durations"][local_i][local_j]
            else:
                # Cross-pair: payload is over the concatenated
                # [block_a, block_b] coordinate list -- block_a occupies
                # positions 0..size_a-1, block_b occupies
                # size_a..size_a+size_b-1 in both dimensions. Only the two
                # off-diagonal quadrants (a->b, b->a) are filled here; the
                # diagonal quadrants are each block's own self-pair's job.
                # Deliberately NOT also re-filling the diagonal quadrants
                # from this payload too: real station data has duplicate/
                # near-duplicate coordinates (multiple chargers at the same
                # site), and OSRM's road-snapping for those isn't perfectly
                # stable across differently-composed requests -- writing the
                # same cell from two different requests could silently pick
                # up two different values depending on which future finishes
                # last. Single source of truth per cell avoids that.
                size_a = len(block_a)
                for local_i, global_i in enumerate(block_a):
                    for local_j, global_j in enumerate(block_b):
                        distances_m[global_i][global_j] = payload["distances"][local_i][size_a + local_j]
                        durations_s[global_i][global_j] = payload["durations"][local_i][size_a + local_j]
                        distances_m[global_j][global_i] = payload["distances"][size_a + local_j][local_i]
                        durations_s[global_j][global_i] = payload["durations"][size_a + local_j][local_i]

    return {"distances": distances_m, "durations": durations_s}


# Google Distance Matrix's real per-request caps: 25 origins, 25
# destinations, AND 100 elements (origins x destinations) per request,
# whichever binds first -- confirmed current against Google's own
# usage-and-billing docs (developers.google.com/maps/documentation/
# distance-matrix/usage-and-billing) rather than assumed, and notably
# stricter than OSRM's own single OSRM_TABLE_MAX_COORDINATES cap. Square
# blocks of this size use exactly 100 elements per request (10*10) while
# staying comfortably under the 25/25 per-dimension caps too -- close to the
# theoretical minimum request count for a full n x n matrix (ceil(n/10)^2
# requests, ~n^2/100 once n is a multiple of 10).
GOOGLE_MATRIX_BLOCK_SIZE = 10

# Concurrency for firing chunked Google matrix block-pair requests -- same
# spirit as _TABLE_BATCH_CONCURRENCY/traffic_service.OLA_BATCH_CONCURRENCY: a
# handful of concurrent requests keeps a many-block matrix from serializing
# into a slow chain of round trips without bursting past Google's own
# per-project QPS limits.
GOOGLE_MATRIX_BATCH_CONCURRENCY = 3


def _google_matrix_block(
    origins: list[tuple[float, float]], destinations: list[tuple[float, float]]
) -> tuple[list[list[float]], list[list[float]]]:
    """
    One Google Distance Matrix request for <= GOOGLE_MATRIX_BLOCK_SIZE
    origins x <= GOOGLE_MATRIX_BLOCK_SIZE destinations. Any element that
    isn't status="OK" (e.g. ZERO_RESULTS between a genuinely unreachable
    pair) fails the whole block rather than silently leaving a None/zero in
    the matrix -- same reasoning as _google_matrix's per-element handling in
    traffic_service.py: a corrupted matrix entry would crash arithmetic
    downstream in a much uglier way than a clear RoutingError does here.
    """
    if not GOOGLE_MAPS_API_KEY:
        raise RoutingError("GOOGLE_MAPS_API_KEY not set")

    try:
        response = requests.get(
            GOOGLE_DISTANCE_MATRIX_URL,
            params={
                "origins": "|".join(_format_latlng(o) for o in origins),
                "destinations": "|".join(_format_latlng(d) for d in destinations),
                "key": GOOGLE_MAPS_API_KEY,
            },
            timeout=REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f"Google Distance Matrix request failed: {exc}") from exc

    payload = response.json()
    if payload.get("status") != "OK":
        raise RoutingError(f"Google Distance Matrix returned status {payload.get('status', 'unknown error')!r}")

    distances_m: list[list[float]] = []
    durations_s: list[list[float]] = []
    for row in payload.get("rows", []):
        d_row, t_row = [], []
        for element in row.get("elements", []):
            if element.get("status") != "OK":
                raise RoutingError(f"Google Distance Matrix element status {element.get('status')!r}")
            d_row.append(float(element["distance"]["value"]))
            t_row.append(float(element["duration"]["value"]))
        distances_m.append(d_row)
        durations_s.append(t_row)
    return distances_m, durations_s


def _format_latlng(point: tuple[float, float]) -> str:
    return f"{point[0]},{point[1]}"


def _get_distance_matrix_google(coordinates: list[tuple[float, float]]) -> dict:
    """
    Full N x N distance/duration matrix from Google's Distance Matrix API --
    only reached as OSRM's fallback (see get_distance_matrix). Unlike OSRM's
    Table Service, which computes a full bidirectional matrix from one shared
    coordinate list in a single call, Google's origins/destinations are
    directional by design -- coordinates is split into GOOGLE_MATRIX_BLOCK_SIZE
    row/column blocks, and every (row_block, col_block) combination
    (including a block against itself) gets its own request, each within
    Google's real per-request caps. Fired with bounded concurrency
    (GOOGLE_MATRIX_BATCH_CONCURRENCY), then stitched into the full matrix.
    """
    n = len(coordinates)
    blocks = [
        list(range(i, min(i + GOOGLE_MATRIX_BLOCK_SIZE, n)))
        for i in range(0, n, GOOGLE_MATRIX_BLOCK_SIZE)
    ]

    def _fetch_block_pair(row_block: list[int], col_block: list[int]):
        origins = [coordinates[idx] for idx in row_block]
        destinations = [coordinates[idx] for idx in col_block]
        distances_m, durations_s = _google_matrix_block(origins, destinations)
        return row_block, col_block, distances_m, durations_s

    pairs = [(row, col) for row in blocks for col in blocks]
    logger.info(
        "routing_service: Google Distance Matrix fallback chunking %d coordinates into %d blocks "
        "(%d block-pair requests, max %dx%d elements/request)",
        n, len(blocks), len(pairs), GOOGLE_MATRIX_BLOCK_SIZE, GOOGLE_MATRIX_BLOCK_SIZE,
    )

    distances_m_grid: list[list[Optional[float]]] = [[None] * n for _ in range(n)]
    durations_s_grid: list[list[Optional[float]]] = [[None] * n for _ in range(n)]

    with ThreadPoolExecutor(max_workers=GOOGLE_MATRIX_BATCH_CONCURRENCY) as pool:
        futures = [pool.submit(_fetch_block_pair, row, col) for row, col in pairs]
        for future in futures:
            row_block, col_block, distances_m, durations_s = future.result()
            for local_i, global_i in enumerate(row_block):
                for local_j, global_j in enumerate(col_block):
                    distances_m_grid[global_i][global_j] = distances_m[local_i][local_j]
                    durations_s_grid[global_i][global_j] = durations_s[local_i][local_j]

    return {
        "distances_km": [[d / 1000.0 for d in row] for row in distances_m_grid],
        "durations_min": [[d / 60.0 for d in row] for row in durations_s_grid],
    }


def _get_distance_matrix_osrm(coordinates: list[tuple[float, float]]) -> dict:
    """OSRM's own Table Service path -- one call for <= OSRM_TABLE_MAX_COORDINATES points, chunked beyond that."""
    if len(coordinates) <= OSRM_TABLE_MAX_COORDINATES:
        payload = _table_request(coordinates)
        distances_m = payload["distances"]
        durations_s = payload["durations"]
    else:
        chunked = _get_distance_matrix_chunked(coordinates)
        distances_m = chunked["distances"]
        durations_s = chunked["durations"]

    return {
        "distances_km": [[d / 1000.0 for d in row] for row in distances_m],
        "durations_min": [[d / 60.0 for d in row] for row in durations_s],
    }


def get_distance_matrix(coordinates: list[tuple[float, float]]) -> dict:
    """
    Bulk driving distance/duration matrix -- OSRM's Table Service primary,
    Google Distance Matrix fallback (see module docstring /
    ENABLE_GOOGLE_ROUTING_FALLBACK) if OSRM fails or is in cooldown after a
    recent failure in this process (see _provider_available). coordinates is
    (lat, lon) in the order you want the matrix indexed by. No route
    geometry comes back from either provider (no real elevation) -- this is
    the cheap first pass; per-edge elevation is a separate, sparser cost paid
    only on survivors (see graph_planner.build_station_graph).

    Raises RoutingError only if OSRM fails AND the Google fallback is
    disabled/unconfigured or also fails.
    """
    osrm_error: Optional[RoutingError] = None
    if _provider_available("osrm"):
        try:
            return _get_distance_matrix_osrm(coordinates)
        except RoutingError as exc:
            osrm_error = exc
            _mark_provider_failed("osrm")
    else:
        osrm_error = RoutingError("OSRM skipped for this call -- recent failure, still in cooldown")

    if not _google_fallback_enabled():
        raise osrm_error

    logger.info(
        "routing_service.get_distance_matrix: OSRM unavailable (%s) -- falling back to Google "
        "Distance Matrix (billed API) for %d coordinates",
        osrm_error, len(coordinates),
    )
    try:
        return _get_distance_matrix_google(coordinates)
    except RoutingError as exc:
        raise RoutingError(
            f"Both distance-matrix providers failed for {len(coordinates)} coordinates: "
            f"OSRM={osrm_error}; Google={exc}"
        ) from exc


def _leg_from_step(step: dict) -> dict:
    start_lon, start_lat = step["maneuver"]["location"]
    # geometries=geojson (see get_route) gives each step its own [lon, lat]
    # coordinate list -- these become the sampled points elevation_service
    # walks to compute gain/loss along the leg, not just its start/end.
    coords = step.get("geometry", {}).get("coordinates") or [[start_lon, start_lat]]
    points = [(lat, lon) for lon, lat in coords]
    return {
        "start": (start_lat, start_lon),
        "end": (start_lat, start_lon),  # OSRM steps don't expose an explicit end coord
        "distance_km": step["distance"] / 1000.0,
        "duration_s": step["duration"],
        "points": points,
    }


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """
    Standard Google encoded-polyline-algorithm decoder -- the format Google
    Directions' step["polyline"]["points"] (see _leg_from_google_step) uses.
    Hand-rolled (~25 lines) instead of adding the `polyline` PyPI package, to
    match this codebase's existing pattern of raw `requests` calls with no
    maps SDK/helper dependencies anywhere (see this module's docstring).
    Ported directly from Google's published algorithm description:
    developers.google.com/maps/documentation/utilities/polylinealgorithm.
    """
    points: list[tuple[float, float]] = []
    index, lat, lng = 0, 0, 0
    length = len(encoded)

    while index < length:
        for is_lat in (True, False):
            shift, result = 0, 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lng += delta
        points.append((lat / 1e5, lng / 1e5))

    return points


def _leg_from_google_step(step: dict) -> dict:
    """
    Google Directions equivalent of _leg_from_step -- unlike OSRM's steps,
    Google's carry real, distinct start_location/end_location per step (no
    "OSRM steps don't expose an explicit end coord" workaround needed here),
    and geometry as an encoded polyline (_decode_polyline) rather than a raw
    coordinate list.
    """
    start = step["start_location"]
    end = step["end_location"]
    encoded_polyline = step.get("polyline", {}).get("points") or ""
    points = _decode_polyline(encoded_polyline) if encoded_polyline else [
        (start["lat"], start["lng"]), (end["lat"], end["lng"]),
    ]
    return {
        "start": (start["lat"], start["lng"]),
        "end": (end["lat"], end["lng"]),
        "distance_km": step["distance"]["value"] / 1000.0,
        "duration_s": step["duration"]["value"],
        "points": points,
    }


# dataset_2/trip_energy_dataset.csv's shortest training trip is 1km -- the
# energy model plateaus/extrapolates poorly below that (verified: it returns
# a near-constant ~0.07 kWh for anything under ~1km regardless of the actual
# distance). OSRM's individual maneuver steps are frequently a few tens of
# meters, so predicting per-step and summing hundreds of them massively
# overestimates a route's real energy use. Steps are merged into legs of at
# least this length before ever being handed to the model.
MIN_LEG_DISTANCE_KM = 1.0

# Fallback speed for a degenerate (~0km/~0s) leg that reaches
# _legs_from_osrm_route's no-steps branch (see there) with essentially
# nothing to derive a real speed from. Matches _ROAD_TYPE_AVG_SPEED_KMH's
# "city" bucket mean in route_optimizer.py, since a same-site hop is exactly
# that: a short, slow urban leg, not a highway cruise. Used to backfill a
# plausible duration_s once distance_km has been floored, rather than
# leaving duration_s at 0 and feeding the energy model an avg_speed_kmh=0.0
# row it never saw in training (see MIN_LEG_DISTANCE_KM flooring below and
# route_optimizer.MIN_ENERGY_FRACTION_OF_BASELINE for the failure this
# causes downstream).
NOMINAL_SPEED_KMH_FOR_DEGENERATE_LEG = 22.0

# A long, straight highway stretch with no turns is a single OSRM step --
# sometimes 50-100+km. Left uncapped, battery_simulator's chaining loop can
# only place a charging stop at the very *start* of such a mega-leg, missing
# any real station partway through it. Legs longer than this are split into
# equal-length chunks (endpoints linearly interpolated between the leg's real
# start/end -- a straight-line approximation, fine for "should we stop here"
# checks, not for turn-by-turn navigation).
MAX_LEG_DISTANCE_KM = 20.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _cumulative_km(points: list[tuple[float, float]]) -> list[float]:
    cumulative = [0.0]
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + _haversine_km(lat1, lon1, lat2, lon2))
    return cumulative


def _split_long_leg(leg: dict, max_km: float = MAX_LEG_DISTANCE_KM) -> list[dict]:
    distance_km = leg["distance_km"]
    if distance_km <= max_km:
        return [leg]

    n_chunks = math.ceil(distance_km / max_km)
    points = leg.get("points") or [leg["start"], leg["end"]]
    n_points = len(points)

    if n_points < 2:
        # No real geometry to measure/split along -- can't do better than an
        # equal share.
        chunks = []
        prev_end = leg["start"]
        for i in range(n_chunks):
            is_last = i == n_chunks - 1
            chunk_end = leg["end"] if is_last else prev_end
            chunks.append({
                "start": prev_end, "end": chunk_end,
                "distance_km": distance_km / n_chunks, "duration_s": leg["duration_s"] / n_chunks,
                "points": [prev_end, chunk_end],
            })
            prev_end = chunk_end
        return chunks

    # Chunk boundaries are chosen as point *indices* along the leg's real
    # sampled geometry (nearest point to each 1/n_chunks share of the
    # sampled path length), not a straight-line lat/lon interpolation
    # between the leg's overall start/end -- on a winding road (e.g. the
    # Coimbatore->Ooty ghat section) that straight line diverges wildly from
    # the actual route, which previously made a chunk's declared start/end
    # disagree with its own points[0]/points[-1]. Consecutive chunks share
    # their boundary point, so chunk[i]["end"] always equals
    # chunk[i + 1]["start"] exactly. distance_km/duration_s are allocated by
    # each chunk's share of the real sampled path length (not divided
    # equally), so two chunks only end up identical if the road genuinely
    # covers equal ground either side of the split.
    cumulative = _cumulative_km(points)
    total_path_km = cumulative[-1] or 1e-9

    bounds = [0]
    j = 0
    for k in range(1, n_chunks):
        target = total_path_km * k / n_chunks
        while j < n_points - 1 and cumulative[j] < target:
            j += 1
        bounds.append(max(j, bounds[-1] + 1))
    bounds.append(n_points - 1)
    bounds = [min(b, n_points - 1) for b in bounds]

    chunks = []
    prev_end = leg["start"]
    prev_cum = 0.0
    for i in range(n_chunks):
        idx_start, idx_end = bounds[i], max(bounds[i + 1], bounds[i] + 1 if bounds[i] < n_points - 1 else bounds[i])
        idx_end = min(idx_end, n_points - 1)
        chunk_points = list(points[idx_start:idx_end + 1]) or [points[idx_start]]
        if len(chunk_points) < 2:
            chunk_points.append(points[min(idx_start + 1, n_points - 1)])
        chunk_points[0] = prev_end
        is_last = i == n_chunks - 1
        chunk_end = leg["end"] if is_last else chunk_points[-1]
        chunk_points[-1] = chunk_end

        chunk_path_km = max(cumulative[idx_end] - prev_cum, 0.0)
        share = chunk_path_km / total_path_km
        chunks.append({
            "start": prev_end,
            "end": chunk_end,
            "distance_km": distance_km * share,
            "duration_s": leg["duration_s"] * share,
            "points": chunk_points,
        })
        prev_end = chunk_end
        prev_cum = cumulative[idx_end]
    return chunks


def _merge_steps_into_legs(
    fine_legs: list[dict], dest: tuple[float, float], min_leg_km: float = MIN_LEG_DISTANCE_KM
) -> list[dict]:
    """
    Accumulate consecutive fine-grained per-maneuver-step legs (already
    normalized to RouteVolt's leg shape by _leg_from_step/_leg_from_google_step
    -- provider-agnostic on purpose, so OSRM and Google routes go through
    identical merging/splitting) into legs of at least min_leg_km each (see
    MIN_LEG_DISTANCE_KM), so every leg handed to the energy model falls
    inside its training distance range instead of being a raw few-meter
    maneuver step. Callers are expected to have already dropped any
    zero-distance steps.
    """
    if not fine_legs:
        return []

    for i in range(len(fine_legs) - 1):
        fine_legs[i]["end"] = fine_legs[i + 1]["start"]
    fine_legs[-1]["end"] = dest

    merged: list[dict] = []
    buffer: Optional[dict] = None
    for leg in fine_legs:
        if buffer is None:
            buffer = dict(leg)
            buffer["points"] = list(leg["points"])
        else:
            buffer["end"] = leg["end"]
            buffer["distance_km"] += leg["distance_km"]
            buffer["duration_s"] += leg["duration_s"]
            buffer["points"].extend(leg["points"])
        if buffer["distance_km"] >= min_leg_km:
            merged.append(buffer)
            buffer = None

    if buffer is not None:
        if merged:
            # Leftover shorter than min_leg_km -- fold into the previous leg
            # rather than leaving an under-sized one at the end.
            merged[-1]["end"] = buffer["end"]
            merged[-1]["distance_km"] += buffer["distance_km"]
            merged[-1]["duration_s"] += buffer["duration_s"]
            merged[-1]["points"].extend(buffer["points"])
        else:
            merged.append(buffer)  # whole route is shorter than min_leg_km

    return [chunk for leg in merged for chunk in _split_long_leg(leg)]


def _fallback_single_leg(
    origin: tuple[float, float], dest: tuple[float, float], distance_m: float, duration_s: float
) -> dict:
    """
    Builds a single whole-route leg when no step-level detail survived (see
    _legs_from_osrm_route/_google_route_dict) -- shared between both routing
    providers, since the degenerate-leg problem below (and its fix) is a
    property of "no steps came back", not of which provider answered it.
    Two different situations land here:

    1. A genuinely trivial route: graph_planner.build_station_graph scoring
       an edge between two near-duplicate-coordinate stations (e.g. two
       chargers at the same site) makes the provider snap both endpoints to
       the same road node, returning distance=0, duration=0, no steps.
       Passed through raw, that reads as a stationary (avg_speed_kmh=0.0)
       leg -- wildly outside the energy model's training distribution, which
       predicts a small negative kWh for it (see
       route_optimizer.MIN_ENERGY_FRACTION_OF_BASELINE for the physics-floor
       clipping that catches it, and its docstring for a worked example of
       the corrupted-Dijkstra fallout). distance_km is floored to
       MIN_LEG_DISTANCE_KM like any other undersized leg, and since there's
       no real duration to trust either, duration_s is backfilled from
       NOMINAL_SPEED_KMH_FOR_DEGENERATE_LEG so the leg reads as a short,
       real urban hop instead of a stationary one.

    2. A real steps=false-shaped query: the route's own distance/duration
       are genuine and nonzero, just without maneuver-level detail. Only
       distance_km needs flooring here; duration_s is scaled up by the same
       factor so the route's own implied avg speed is preserved rather than
       inflated by shrinking distance under a fixed duration.
    """
    raw_distance_km = distance_m / 1000.0
    raw_duration_s = duration_s
    distance_km = max(raw_distance_km, MIN_LEG_DISTANCE_KM)
    if raw_distance_km <= 0 or raw_duration_s <= 0:
        computed_duration_s = distance_km / NOMINAL_SPEED_KMH_FOR_DEGENERATE_LEG * 3600.0
    else:
        computed_duration_s = raw_duration_s * (distance_km / raw_distance_km)
    return {
        "start": origin,
        "end": dest,
        "distance_km": distance_km,
        "duration_s": computed_duration_s,
        "points": [origin, dest],
    }


def _legs_from_osrm_route(route: dict, origin: tuple[float, float], dest: tuple[float, float]) -> list[dict]:
    """
    Flatten OSRM's legs[].steps[] (one entry per maneuver) and merge them
    into RouteVolt's flatter leg list (see _merge_steps_into_legs), so
    battery_simulator walks the route at a granularity finer than "one giant
    end-to-end leg" but coarse enough to stay within the energy model's
    valid input range. Falls back to _fallback_single_leg when no step-level
    detail comes back at all (e.g. steps=false, or a genuinely trivial
    0km/0s route -- see its docstring).
    """
    steps = [step for osrm_leg in route.get("legs", []) for step in osrm_leg.get("steps", [])]
    fine_legs = [_leg_from_step(step) for step in steps if step["distance"] > 0]
    legs = _merge_steps_into_legs(fine_legs, dest)

    if not legs:
        legs = [_fallback_single_leg(origin, dest, route["distance"], route["duration"])]

    return legs


def _route_dict(route: dict, origin: tuple[float, float], dest: tuple[float, float]) -> dict:
    return {
        "distance_m": route["distance"],
        "duration_s": route["duration"],
        "legs": _legs_from_osrm_route(route, origin, dest),
    }


def _osrm_route_request(
    origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float, alternatives: bool
) -> dict:
    coords = f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coords}"

    try:
        response = requests.get(
            url,
            params={
                "overview": "false",
                "steps": "true",
                # geojson step geometry gives each step's intermediate lat/lng
                # points (not just its start), which elevation_service needs
                # to sample gain/loss along a leg rather than netting it out
                # from start/end alone.
                "geometries": "geojson",
                "alternatives": "true" if alternatives else "false",
            },
            timeout=REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f"OSRM request failed: {exc}") from exc

    payload = response.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(f"OSRM returned no route: {payload.get('code', 'unknown error')}")
    return payload


def _get_route_osrm(
    origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float, alternatives: bool
) -> dict:
    payload = _osrm_route_request(origin_lat, origin_lng, dest_lat, dest_lng, alternatives)
    origin = (origin_lat, origin_lng)
    dest = (dest_lat, dest_lng)

    if alternatives:
        return {"routes": [_route_dict(r, origin, dest) for r in payload["routes"]]}
    return _route_dict(payload["routes"][0], origin, dest)


def _google_directions_request(
    origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float, alternatives: bool
) -> dict:
    if not GOOGLE_MAPS_API_KEY:
        raise RoutingError("GOOGLE_MAPS_API_KEY not set")

    try:
        response = requests.get(
            GOOGLE_DIRECTIONS_URL,
            params={
                "origin": _format_latlng((origin_lat, origin_lng)),
                "destination": _format_latlng((dest_lat, dest_lng)),
                "alternatives": "true" if alternatives else "false",
                "key": GOOGLE_MAPS_API_KEY,
            },
            timeout=REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f"Google Directions request failed: {exc}") from exc

    payload = response.json()
    if payload.get("status") != "OK" or not payload.get("routes"):
        raise RoutingError(f"Google Directions returned no route: {payload.get('status', 'unknown error')!r}")
    return payload


def _google_route_dict(route: dict, origin: tuple[float, float], dest: tuple[float, float]) -> dict:
    """
    Google Directions equivalent of _route_dict/_legs_from_osrm_route --
    flattens every waypoint-leg's steps (Directions calls this trip's own
    "legs", but with no intermediate waypoints supplied here it's always
    exactly one -- distinct from RouteVolt's own flatter "leg" concept
    _merge_steps_into_legs produces) into fine-grained per-maneuver legs via
    _leg_from_google_step, then reuses the exact same _merge_steps_into_legs
    (MIN_LEG_DISTANCE_KM merging, MAX_LEG_DISTANCE_KM splitting) and
    _fallback_single_leg (degenerate/no-step route flooring) that OSRM's own
    path uses, so callers genuinely cannot tell which provider answered.
    """
    total_distance_m = sum(leg["distance"]["value"] for leg in route.get("legs", []))
    total_duration_s = sum(leg["duration"]["value"] for leg in route.get("legs", []))

    steps = [step for leg in route.get("legs", []) for step in leg.get("steps", [])]
    fine_legs = [_leg_from_google_step(step) for step in steps if step["distance"]["value"] > 0]
    legs = _merge_steps_into_legs(fine_legs, dest)

    if not legs:
        legs = [_fallback_single_leg(origin, dest, total_distance_m, total_duration_s)]

    return {"distance_m": total_distance_m, "duration_s": total_duration_s, "legs": legs}


def _get_route_google(
    origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float, alternatives: bool
) -> dict:
    payload = _google_directions_request(origin_lat, origin_lng, dest_lat, dest_lng, alternatives)
    origin = (origin_lat, origin_lng)
    dest = (dest_lat, dest_lng)

    if alternatives:
        return {"routes": [_google_route_dict(r, origin, dest) for r in payload["routes"]]}
    return _google_route_dict(payload["routes"][0], origin, dest)


def get_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    alternatives: bool = False,
) -> dict:
    """
    Fetch a driving route -- OSRM primary, Google Directions fallback (see
    module docstring / ENABLE_GOOGLE_ROUTING_FALLBACK) if OSRM fails or is in
    cooldown after a recent failure in this process (see _provider_available).

    Returns {"distance_m", "duration_s", "legs": [...]} for the primary route
    when alternatives=False. When alternatives=True, returns
    {"routes": [that same shape, ...]} with up to the answering provider's
    available alternates (usually 1-3) instead, primary route first. Either
    provider's response is normalized into this exact same shape (see
    _route_dict/_google_route_dict) -- callers can't tell which one answered.

    Raises RoutingError only if OSRM fails AND the Google fallback is
    disabled/unconfigured or also fails.
    """
    origin = (origin_lat, origin_lng)
    dest = (dest_lat, dest_lng)

    osrm_error: Optional[RoutingError] = None
    if _provider_available("osrm"):
        try:
            return _get_route_osrm(origin_lat, origin_lng, dest_lat, dest_lng, alternatives)
        except RoutingError as exc:
            osrm_error = exc
            _mark_provider_failed("osrm")
    else:
        osrm_error = RoutingError("OSRM skipped for this call -- recent failure, still in cooldown")

    if not _google_fallback_enabled():
        raise osrm_error

    logger.info(
        "routing_service.get_route: OSRM unavailable (%s) -- falling back to Google Directions "
        "(billed API) for origin=%s dest=%s",
        osrm_error, origin, dest,
    )
    try:
        return _get_route_google(origin_lat, origin_lng, dest_lat, dest_lng, alternatives)
    except RoutingError as exc:
        raise RoutingError(
            f"Both routing providers failed for {origin}->{dest}: OSRM={osrm_error}; Google={exc}"
        ) from exc
