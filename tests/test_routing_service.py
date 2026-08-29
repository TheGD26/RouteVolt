"""
Regression tests for routing_service.py's OSRM -> Google Maps fallback
(get_route / get_distance_matrix), the circuit breaker that skips a
recently-failed provider (_provider_available/_mark_provider_failed), and
the hand-rolled polyline decoder Google Directions' step geometry needs.

Standalone script, not pytest (same reasoning as tests/test_graph_planner.py
/tests/test_route_optimizer.py):

    uv run python tests/test_routing_service.py

No live network calls: requests.get is patched at the module level
(routing_service.requests.get) with a fake dispatcher keyed by URL, mirroring
the shape of real OSRM/Google Directions/Google Distance Matrix responses --
same patch.object(...) style test_graph_planner.py already uses for
routing_service.get_route, just one layer lower (HTTP, not the service
function itself) since these tests exercise the HTTP-calling/parsing logic
those higher-level patches bypass entirely.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import routing_service

FAILURES = []


def _check(label: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def _reset_provider_state():
    """Clear the OSRM circuit breaker between tests so one test's failure doesn't leak into the next's cooldown."""
    routing_service._provider_failed_until.clear()


class _FakeResponse:
    def __init__(self, json_data: dict, status_code: int = 200, ok: bool = True):
        self._json_data = json_data
        self.status_code = status_code
        self.ok = ok
        self.text = str(json_data)[:2000]

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def _osrm_route_payload(distance_m: float, duration_s: float) -> dict:
    """A real-shaped single-step OSRM /route response (geometries=geojson, steps=true)."""
    return {
        "code": "Ok",
        "routes": [{
            "distance": distance_m,
            "duration": duration_s,
            "legs": [{
                "steps": [{
                    "distance": distance_m,
                    "duration": duration_s,
                    "maneuver": {"location": [78.0, 10.0]},
                    "geometry": {"coordinates": [[78.0, 10.0], [78.05, 10.05]]},
                }],
            }],
        }],
    }


def _osrm_table_payload(n: int) -> dict:
    distances = [[0.0 if i == j else 1000.0 * abs(i - j) for j in range(n)] for i in range(n)]
    durations = [[0.0 if i == j else 60.0 * abs(i - j) for j in range(n)] for i in range(n)]
    return {"code": "Ok", "distances": distances, "durations": durations}


def _google_directions_payload(distance_m: float, duration_s: float, encoded_polyline: str, n_routes: int = 1) -> dict:
    """A real-shaped single-leg Google Directions response."""
    route = {
        "legs": [{
            "distance": {"value": distance_m},
            "duration": {"value": duration_s},
            "steps": [{
                "distance": {"value": distance_m},
                "duration": {"value": duration_s},
                "start_location": {"lat": 10.0, "lng": 78.0},
                "end_location": {"lat": 10.05, "lng": 78.05},
                "polyline": {"points": encoded_polyline},
            }],
        }],
    }
    return {"status": "OK", "routes": [route] * n_routes}


def _make_fake_get(handlers: dict, calls: list):
    """
    Builds a requests.get replacement dispatching on URL prefix -- `handlers`
    maps a URL prefix to a callable(url, params) -> _FakeResponse | Exception
    instance (raised if it's an Exception). `calls` records every (url,
    params) pair actually requested, so tests can assert on call counts/args
    (e.g. "Google was never called" for the pure-OSRM-success case).
    """
    def fake_get(url, params=None, timeout=None, headers=None, **kwargs):
        calls.append((url, params))
        for prefix, handler in handlers.items():
            if url.startswith(prefix):
                result = handler(url, params)
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"Unexpected URL requested in test: {url}")
    return fake_get


# ---- 1. OSRM success: no fallback triggered ----

def test_get_route_osrm_success_no_fallback():
    print("\n=== get_route: OSRM succeeds, Google never touched ===")
    _reset_provider_state()
    calls = []
    handlers = {
        f"{routing_service.OSRM_BASE_URL}/route/v1/driving/": lambda url, params: _FakeResponse(_osrm_route_payload(5000.0, 400.0)),
    }
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)):
        route = routing_service.get_route(10.0, 78.0, 10.05, 78.05)

    _check("distance_m matches OSRM's raw distance", route["distance_m"] == 5000.0, f"got {route['distance_m']}")
    _check("duration_s matches OSRM's raw duration", route["duration_s"] == 400.0, f"got {route['duration_s']}")
    _check("exactly one HTTP call was made (OSRM only)", len(calls) == 1, f"got {len(calls)} calls: {calls}")
    _check("legs is a non-empty list", len(route["legs"]) >= 1, f"got {route['legs']}")


def test_get_distance_matrix_osrm_success_no_fallback():
    print("\n=== get_distance_matrix: OSRM succeeds, Google never touched ===")
    _reset_provider_state()
    calls = []
    handlers = {
        f"{routing_service.OSRM_BASE_URL}/table/v1/driving/": lambda url, params: _FakeResponse(_osrm_table_payload(3)),
    }
    coords = [(10.0, 78.0), (10.1, 78.1), (10.2, 78.2)]
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)):
        matrix = routing_service.get_distance_matrix(coords)

    _check("exactly one HTTP call was made (OSRM only)", len(calls) == 1, f"got {len(calls)} calls")
    _check(
        "distances_km[0][2] matches OSRM's raw distance/1000",
        matrix["distances_km"][0][2] == 2.0, f"got {matrix['distances_km'][0][2]}",
    )
    _check(
        "durations_min[0][2] matches OSRM's raw duration/60",
        matrix["durations_min"][0][2] == 2.0, f"got {matrix['durations_min'][0][2]}",
    )


# ---- 2. OSRM RoutingError -> Google success ----

def test_get_route_falls_back_to_google_on_osrm_failure():
    print("\n=== get_route: OSRM fails, Google Directions fallback succeeds ===")
    _reset_provider_state()
    calls = []
    encoded = "_p~iF~ps|U_ulLnnqC"  # decodes to (38.5,-120.2), (40.7,-120.95) -- a real Google-published pair
    handlers = {
        f"{routing_service.OSRM_BASE_URL}/route/v1/driving/": lambda url, params: requests.RequestException("simulated timeout"),
        routing_service.GOOGLE_DIRECTIONS_URL: lambda url, params: _FakeResponse(
            _google_directions_payload(6000.0, 500.0, encoded)
        ),
    }
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)), \
         patch.object(routing_service, "GOOGLE_MAPS_API_KEY", "fake-google-key"), \
         patch.object(routing_service, "ENABLE_GOOGLE_ROUTING_FALLBACK", True):
        route = routing_service.get_route(10.0, 78.0, 10.05, 78.05)

    _check("both OSRM and Google were called", len(calls) == 2, f"got {len(calls)} calls: {[c[0] for c in calls]}")
    _check("distance_m matches Google's total", route["distance_m"] == 6000.0, f"got {route['distance_m']}")
    _check("duration_s matches Google's total", route["duration_s"] == 500.0, f"got {route['duration_s']}")
    leg = route["legs"][0]
    _check(
        "leg points came from the decoded polyline, not a straight start/end line",
        len(leg["points"]) >= 2 and leg["points"][0] == (38.5, -120.2),
        f"got {leg['points']}",
    )
    _check("OSRM is now in cooldown after the failure", not routing_service._provider_available("osrm"))


def test_get_route_google_fallback_maps_alternatives():
    print("\n=== get_route: alternatives=True maps every Google route, not just the first ===")
    _reset_provider_state()
    calls = []
    encoded = "_p~iF~ps|U_ulLnnqC"
    handlers = {
        f"{routing_service.OSRM_BASE_URL}/route/v1/driving/": lambda url, params: requests.RequestException("simulated timeout"),
        routing_service.GOOGLE_DIRECTIONS_URL: lambda url, params: _FakeResponse(
            _google_directions_payload(6000.0, 500.0, encoded, n_routes=2)
        ),
    }
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)), \
         patch.object(routing_service, "GOOGLE_MAPS_API_KEY", "fake-google-key"), \
         patch.object(routing_service, "ENABLE_GOOGLE_ROUTING_FALLBACK", True):
        result = routing_service.get_route(10.0, 78.0, 10.05, 78.05, alternatives=True)

    _check("2 alternative routes were returned", len(result["routes"]) == 2, f"got {len(result['routes'])}")


def test_get_distance_matrix_falls_back_to_google_on_osrm_failure():
    print("\n=== get_distance_matrix: OSRM fails, Google Distance Matrix fallback succeeds ===")
    _reset_provider_state()
    calls = []

    def google_matrix_handler(url, params):
        origins = params["origins"].split("|")
        destinations = params["destinations"].split("|")
        rows = []
        for o in origins:
            o_idx = round(float(o.split(",")[0]))
            elements = []
            for d in destinations:
                d_idx = round(float(d.split(",")[0]))
                elements.append({
                    "status": "OK",
                    "distance": {"value": 1000.0 * abs(o_idx - d_idx)},
                    "duration": {"value": 60.0 * abs(o_idx - d_idx)},
                })
            rows.append({"elements": elements})
        return _FakeResponse({"status": "OK", "rows": rows})

    handlers = {
        f"{routing_service.OSRM_BASE_URL}/table/v1/driving/": lambda url, params: requests.RequestException("simulated timeout"),
        routing_service.GOOGLE_DISTANCE_MATRIX_URL: google_matrix_handler,
    }
    coords = [(float(i), float(i)) for i in range(3)]
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)), \
         patch.object(routing_service, "GOOGLE_MAPS_API_KEY", "fake-google-key"), \
         patch.object(routing_service, "ENABLE_GOOGLE_ROUTING_FALLBACK", True):
        matrix = routing_service.get_distance_matrix(coords)

    _check("both OSRM and Google were called", len(calls) == 2, f"got {len(calls)} calls: {[c[0] for c in calls]}")
    _check("distances_km[0][2] matches Google's element", matrix["distances_km"][0][2] == 2.0, f"got {matrix['distances_km'][0][2]}")
    _check("durations_min[0][2] matches Google's element", matrix["durations_min"][0][2] == 2.0, f"got {matrix['durations_min'][0][2]}")


def test_osrm_cooldown_skips_straight_to_google():
    print("\n=== get_route: OSRM already in cooldown -- skipped entirely, straight to Google ===")
    _reset_provider_state()
    routing_service._mark_provider_failed("osrm")
    calls = []
    encoded = "_p~iF~ps|U_ulLnnqC"
    handlers = {
        routing_service.GOOGLE_DIRECTIONS_URL: lambda url, params: _FakeResponse(
            _google_directions_payload(6000.0, 500.0, encoded)
        ),
    }
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)), \
         patch.object(routing_service, "GOOGLE_MAPS_API_KEY", "fake-google-key"), \
         patch.object(routing_service, "ENABLE_GOOGLE_ROUTING_FALLBACK", True):
        route = routing_service.get_route(10.0, 78.0, 10.05, 78.05)

    _check("only Google was called -- OSRM skipped during cooldown", len(calls) == 1, f"got {len(calls)} calls: {[c[0] for c in calls]}")
    _check("Google's result was returned", route["distance_m"] == 6000.0, f"got {route['distance_m']}")
    _reset_provider_state()


# ---- 3. Both providers failing -> RoutingError propagates ----

def test_get_route_raises_when_both_providers_fail():
    print("\n=== get_route: OSRM and Google both fail -- RoutingError propagates ===")
    _reset_provider_state()
    calls = []
    handlers = {
        f"{routing_service.OSRM_BASE_URL}/route/v1/driving/": lambda url, params: requests.RequestException("simulated OSRM timeout"),
        routing_service.GOOGLE_DIRECTIONS_URL: lambda url, params: _FakeResponse({"status": "ZERO_RESULTS", "routes": []}),
    }
    raised = None
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)), \
         patch.object(routing_service, "GOOGLE_MAPS_API_KEY", "fake-google-key"), \
         patch.object(routing_service, "ENABLE_GOOGLE_ROUTING_FALLBACK", True):
        try:
            routing_service.get_route(10.0, 78.0, 10.05, 78.05)
        except routing_service.RoutingError as exc:
            raised = exc

    _check("RoutingError was raised", raised is not None, f"got {raised}")
    _check("both providers were actually tried", len(calls) == 2, f"got {len(calls)} calls")
    if raised:
        _check("error message references both providers", "OSRM" in str(raised) and "Google" in str(raised), str(raised))


def test_get_route_raises_immediately_when_google_fallback_disabled():
    print("\n=== get_route: OSRM fails, Google fallback disabled -- RoutingError propagates without touching Google ===")
    _reset_provider_state()
    calls = []
    handlers = {
        f"{routing_service.OSRM_BASE_URL}/route/v1/driving/": lambda url, params: requests.RequestException("simulated OSRM timeout"),
    }
    raised = None
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)), \
         patch.object(routing_service, "ENABLE_GOOGLE_ROUTING_FALLBACK", False):
        try:
            routing_service.get_route(10.0, 78.0, 10.05, 78.05)
        except routing_service.RoutingError as exc:
            raised = exc

    _check("RoutingError was raised", raised is not None, f"got {raised}")
    _check("Google was never called (fallback disabled)", len(calls) == 1, f"got {len(calls)} calls: {[c[0] for c in calls]}")


# ---- 4. Polyline decoder ----

def test_decode_polyline_matches_googles_published_example():
    print("\n=== _decode_polyline: matches Google's own published worked example ===")
    # developers.google.com/maps/documentation/utilities/polylinealgorithm
    encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    expected = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    decoded = routing_service._decode_polyline(encoded)
    _check("decoded points match exactly", decoded == expected, f"got {decoded}")


def test_decode_polyline_empty_string():
    print("\n=== _decode_polyline: empty string decodes to no points ===")
    _check("empty encoding -> empty list", routing_service._decode_polyline("") == [])


def test_decode_polyline_single_point():
    print("\n=== _decode_polyline: single-point encoding ===")
    # Encoding of a single point (0, 0) is two zero-deltas -- "??" per the algorithm's own examples.
    decoded = routing_service._decode_polyline("??")
    _check("single (0,0) point decodes correctly", decoded == [(0.0, 0.0)], f"got {decoded}")


# ---- 5. get_distance_matrix Google chunking against real limits ----

def test_google_distance_matrix_chunks_across_block_boundary():
    print("\n=== get_distance_matrix: Google fallback chunks correctly beyond GOOGLE_MATRIX_BLOCK_SIZE ===")
    _reset_provider_state()
    n = 15  # > GOOGLE_MATRIX_BLOCK_SIZE (10) -- forces a 2x2 block grid (10+5 split)
    assert n > routing_service.GOOGLE_MATRIX_BLOCK_SIZE
    calls = []

    def google_matrix_handler(url, params):
        origins = params["origins"].split("|")
        destinations = params["destinations"].split("|")
        _check(
            f"block request stays within Google's real caps (<=25 origins, <=25 dest, <=100 elements)",
            len(origins) <= 25 and len(destinations) <= 25 and len(origins) * len(destinations) <= 100,
            f"origins={len(origins)} destinations={len(destinations)}",
        )
        rows = []
        for o in origins:
            o_idx = round(float(o.split(",")[0]))
            elements = []
            for d in destinations:
                d_idx = round(float(d.split(",")[0]))
                elements.append({
                    "status": "OK",
                    "distance": {"value": 1000.0 * abs(o_idx - d_idx)},
                    "duration": {"value": 60.0 * abs(o_idx - d_idx)},
                })
            rows.append({"elements": elements})
        return _FakeResponse({"status": "OK", "rows": rows})

    handlers = {
        f"{routing_service.OSRM_BASE_URL}/table/v1/driving/": lambda url, params: requests.RequestException("simulated timeout"),
        routing_service.GOOGLE_DISTANCE_MATRIX_URL: google_matrix_handler,
    }
    coords = [(float(i), float(i)) for i in range(n)]
    with patch.object(routing_service.requests, "get", side_effect=_make_fake_get(handlers, calls)), \
         patch.object(routing_service, "GOOGLE_MAPS_API_KEY", "fake-google-key"), \
         patch.object(routing_service, "ENABLE_GOOGLE_ROUTING_FALLBACK", True):
        matrix = routing_service.get_distance_matrix(coords)

    # 1 OSRM call (fails) + a 2x2 block grid = 4 Google calls (blocks [0:10],[10:15] against themselves)
    _check("1 OSRM call + 4 Google block-pair calls", len(calls) == 5, f"got {len(calls)} calls: {[c[0] for c in calls]}")
    _check("matrix is n x n", len(matrix["distances_km"]) == n and all(len(row) == n for row in matrix["distances_km"]), "shape mismatch")

    ok = True
    for i in range(n):
        for j in range(n):
            if matrix["distances_km"][i][j] != float(abs(i - j)) or matrix["durations_min"][i][j] != float(abs(i - j)):
                ok = False
    _check("every cell in the stitched full matrix matches the expected per-pair value", ok)


if __name__ == "__main__":
    test_get_route_osrm_success_no_fallback()
    test_get_distance_matrix_osrm_success_no_fallback()
    test_get_route_falls_back_to_google_on_osrm_failure()
    test_get_route_google_fallback_maps_alternatives()
    test_get_distance_matrix_falls_back_to_google_on_osrm_failure()
    test_osrm_cooldown_skips_straight_to_google()
    test_get_route_raises_when_both_providers_fail()
    test_get_route_raises_immediately_when_google_fallback_disabled()
    test_decode_polyline_matches_googles_published_example()
    test_decode_polyline_empty_string()
    test_decode_polyline_single_point()
    test_google_distance_matrix_chunks_across_block_boundary()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) did not pass: {FAILURES}")
        sys.exit(1)
    print("All checks passed.")
