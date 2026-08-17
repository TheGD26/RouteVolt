"""
Routing + geocoding for RouteVolt.

OSRM (backend/app/config.OSRM_BASE_URL, defaults to the public demo server)
provides turn-by-turn routes; Ola Maps' Geocoding API (backend/app/config.
OLA_MAPS_API_KEY, same credential traffic_service.py uses) resolves the
free-text current_location/destination strings on RouteRequest into
coordinates -- swapped in for Nominatim, whose OSM coverage of Indian
addresses is weak and whose usage policy rules out anything beyond light,
non-production use. Both are plain HTTP calls with a 5s timeout -- neither
raises anything OSRM/Ola-shaped, callers just catch RoutingError /
GeocodingError.
"""

import math
from typing import Optional

import requests

from backend.app.config import OLA_MAPS_ALLOWED_ORIGIN, OLA_MAPS_API_KEY, OSRM_BASE_URL

REQUEST_TIMEOUT_S = 5
OLA_GEOCODE_URL = "https://api.olamaps.io/places/v1/geocode"


class RoutingError(Exception):
    """Raised when OSRM can't produce a route (timeout, no route found, bad response)."""


class GeocodingError(Exception):
    """Raised when an address can't be resolved to coordinates."""


def geocode(address: str) -> tuple[float, float]:
    """Resolve a free-text address to (latitude, longitude) via Ola Maps' Geocoding API."""
    if not OLA_MAPS_API_KEY:
        raise GeocodingError("OLA_MAPS_API_KEY not set")

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
        raise GeocodingError(f"Geocoding request failed for '{address}': {exc}") from exc

    # Ola's forward-geocode response: {"geocodingResults": [{"geometry":
    # {"location": {"lat", "lng"}}, ...}, ...], "status": "ok"} -- confirmed
    # against the live API, not assumed from docs (Ola's public docs pages
    # don't include a worked example response).
    results = response.json().get("geocodingResults") or []
    if not results:
        raise GeocodingError(f"No geocoding match found for '{address}'")

    location = results[0]["geometry"]["location"]
    return float(location["lat"]), float(location["lng"])


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


# dataset_2/trip_energy_dataset.csv's shortest training trip is 1km -- the
# energy model plateaus/extrapolates poorly below that (verified: it returns
# a near-constant ~0.07 kWh for anything under ~1km regardless of the actual
# distance). OSRM's individual maneuver steps are frequently a few tens of
# meters, so predicting per-step and summing hundreds of them massively
# overestimates a route's real energy use. Steps are merged into legs of at
# least this length before ever being handed to the model.
MIN_LEG_DISTANCE_KM = 1.0

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
    steps: list[dict], dest: tuple[float, float], min_leg_km: float = MIN_LEG_DISTANCE_KM
) -> list[dict]:
    """
    Accumulate consecutive OSRM steps into legs of at least min_leg_km each
    (see MIN_LEG_DISTANCE_KM), so every leg handed to the energy model falls
    inside its training distance range instead of being a raw few-meter
    maneuver step.
    """
    fine_legs = [_leg_from_step(step) for step in steps if step["distance"] > 0]
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


def _legs_from_osrm_route(route: dict, origin: tuple[float, float], dest: tuple[float, float]) -> list[dict]:
    """
    Flatten OSRM's legs[].steps[] (one entry per maneuver) and merge them
    into RouteVolt's flatter leg list (see _merge_steps_into_legs), so
    battery_simulator walks the route at a granularity finer than "one giant
    end-to-end leg" but coarse enough to stay within the energy model's
    valid input range.
    """
    steps = [step for osrm_leg in route.get("legs", []) for step in osrm_leg.get("steps", [])]
    legs = _merge_steps_into_legs(steps, dest)

    if not legs:
        # No step-level detail available (e.g. steps=false) -- fall back to a
        # single leg spanning the whole route.
        legs = [{
            "start": origin,
            "end": dest,
            "distance_km": route["distance"] / 1000.0,
            "duration_s": route["duration"],
            "points": [origin, dest],
        }]

    return legs


def _route_dict(route: dict, origin: tuple[float, float], dest: tuple[float, float]) -> dict:
    return {
        "distance_m": route["distance"],
        "duration_s": route["duration"],
        "legs": _legs_from_osrm_route(route, origin, dest),
    }


def get_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    alternatives: bool = False,
) -> dict:
    """
    Fetch a driving route from OSRM.

    Returns {"distance_m", "duration_s", "legs": [...]} for the primary route
    when alternatives=False. When alternatives=True, returns
    {"routes": [that same shape, ...]} with up to OSRM's available alternates
    (usually 1-3) instead, primary route first.

    Raises RoutingError on timeout, network failure, or no route found --
    never lets requests' exceptions leak to the caller.
    """
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

    origin = (origin_lat, origin_lng)
    dest = (dest_lat, dest_lng)

    if alternatives:
        return {"routes": [_route_dict(r, origin, dest) for r in payload["routes"]]}

    return _route_dict(payload["routes"][0], origin, dest)
