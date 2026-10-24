"""
Offline regression tests for the five small audit fixes (traffic-cache TTL,
demo-mode labelling/matching, stale demo fixtures, geocoding error split,
connector filtering in the station graph).

Standalone script, not pytest (same convention as the other tests/ scripts):

    uv run python tests/test_audit_fixes_1_to_5.py

No network: external calls are patched. Exits non-zero if any check fails.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from fastapi import HTTPException

from backend.app.api import routes
from backend.app.models.schemas import RouteOptimizeResponse, RouteRequest
from backend.app.services import graph_planner, routing_service, traffic_service

FAILURES = []


def _check(label: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def _request(**overrides) -> RouteRequest:
    base = dict(
        current_location="Chennai", destination="Madurai", battery_percentage=80,
        vehicle_range=300, preferred_charging_speed="fast",
        vehicle_profile="mid_ev", load_state="half_load",
    )
    base.update(overrides)
    return RouteRequest(**base)


# ---- 1. traffic cache TTL ----------------------------------------------------
def test_traffic_cache_ttl():
    print("\n=== 1. get_leg_traffic cache expires after LEG_TRAFFIC_CACHE_TTL_S ===")
    fake_result = {"duration_seconds": 100.0, "distance_meters": 1000.0, "traffic_aware": True, "provider": "x"}
    traffic_service._leg_traffic_cache.clear()
    with patch.object(traffic_service, "get_eta_matrix", return_value=[fake_result]) as mocked:
        traffic_service.get_leg_traffic(10.0, 78.0, 10.1, 78.1, typical_duration_s=90.0)
        traffic_service.get_leg_traffic(10.0, 78.0, 10.1, 78.1, typical_duration_s=90.0)
        _check("second call within TTL is a cache hit", mocked.call_count == 1, f"calls={mocked.call_count}")

        key = next(iter(traffic_service._leg_traffic_cache))
        stored_at, out = traffic_service._leg_traffic_cache[key]
        traffic_service._leg_traffic_cache[key] = (stored_at - traffic_service.LEG_TRAFFIC_CACHE_TTL_S - 1, out)
        traffic_service.get_leg_traffic(10.0, 78.0, 10.1, 78.1, typical_duration_s=90.0)
        _check("call after TTL re-queries the provider", mocked.call_count == 2, f"calls={mocked.call_count}")

        traffic_service.get_leg_traffic(10.0, 78.0, 10.1, 78.1, typical_duration_s=90.0)
        _check("refreshed entry is cached again", mocked.call_count == 2, f"calls={mocked.call_count}")
    traffic_service._leg_traffic_cache.clear()


# ---- 2. demo mode matching + labelling -----------------------------------------
def test_demo_mode():
    print("\n=== 2. demo mode: matching rules and precomputed labelling ===")
    routes._demo_manifest.cache_clear()
    with patch.object(routes, "DEMO_MODE", True):
        entry = routes._demo_manifest_entry(_request())
        _check("Chennai->Madurai mid_ev/half_load matches", entry is not None)
        _check("match is case/whitespace-insensitive",
               routes._demo_manifest_entry(_request(current_location="  CHENNAI ", destination="madurai")) is not None)
        _check("different vehicle_profile does not match",
               routes._demo_manifest_entry(_request(vehicle_profile="large_ev")) is None)
        _check("different load_state does not match",
               routes._demo_manifest_entry(_request(load_state="full_load")) is None)
        _check("exact payload_kg never matches (runs live)",
               routes._demo_manifest_entry(_request(payload_kg=300.0)) is None)
        _check("battery_percentage is intentionally not part of the match",
               routes._demo_manifest_entry(_request(battery_percentage=33)) is not None)

        response = routes.optimize_route(_request())
        _check("served response is labelled precomputed", response.get("precomputed") is True)
        _check("served response reports the captured battery", response.get("precomputed_battery_percentage") == 80.0,
               f"got {response.get('precomputed_battery_percentage')}")
        RouteOptimizeResponse(**response)  # must satisfy the response model
        _check("served response validates against RouteOptimizeResponse", True)
        _check("cached fixture dict was not mutated", "precomputed" not in routes._demo_fixture(entry["fixture"]))

        nagercoil = routes._demo_manifest_entry(_request(destination="Nagercoil"))
        _check("Nagercoil fixture records its 8% capture battery",
               nagercoil is not None and nagercoil.get("captured_battery_percentage") == 8.0)

    with patch.object(routes, "DEMO_MODE", False):
        _check("DEMO_MODE off never matches", routes._demo_manifest_entry(_request()) is None)


# ---- 3. manifest / fixtures sanity ----------------------------------------------
def test_manifest_fixtures():
    print("\n=== 3. demo manifest only references existing, feasible fixtures ===")
    manifest = json.loads((routes._DEMO_FIXTURES_DIR / "demo_routes_manifest.json").read_text())
    destinations = {e["destination"] for e in manifest}
    _check("stale Dindigul/Kumbakonam entries are gone", not ({"dindigul", "kumbakonam"} & destinations),
           f"destinations={sorted(destinations)}")
    for e in manifest:
        path = routes._DEMO_FIXTURES_DIR / e["fixture"]
        _check(f"{e['destination']}: fixture file exists", path.exists())
        if path.exists():
            route = json.loads(path.read_text())["routes"][0]
            _check(f"{e['destination']}: fixture is a feasible plan", route["feasible"] is True)
        _check(f"{e['destination']}: has captured_battery_percentage",
               isinstance(e.get("captured_battery_percentage"), (int, float)))


# ---- 4. geocoding error split -----------------------------------------------------
def test_geocoding_errors():
    print("\n=== 4. geocoding: not-found vs provider failure ===")
    ok = MagicMock(); ok.raise_for_status.return_value = None
    ok.json.return_value = {"geocodingResults": [{"geometry": {"location": {"lat": 10.5, "lng": 78.5}}}]}
    empty = MagicMock(); empty.raise_for_status.return_value = None
    empty.json.return_value = {"geocodingResults": []}
    bad_shape = MagicMock(); bad_shape.raise_for_status.return_value = None
    bad_shape.json.return_value = {"geocodingResults": [{"nope": 1}]}
    not_json = MagicMock(); not_json.raise_for_status.return_value = None
    not_json.json.side_effect = ValueError("no json")

    with patch.object(routing_service, "OLA_MAPS_API_KEY", "k"):
        with patch.object(routing_service.requests, "get", return_value=ok):
            _check("valid response returns coordinates", routing_service.geocode("x") == (10.5, 78.5))
        for label, resp, exc_type in [
            ("empty results -> NotFound", empty, routing_service.GeocodingNotFoundError),
            ("unexpected shape -> Provider", bad_shape, routing_service.GeocodingProviderError),
            ("non-JSON body -> Provider", not_json, routing_service.GeocodingProviderError),
        ]:
            with patch.object(routing_service.requests, "get", return_value=resp):
                try:
                    routing_service.geocode("x"); raised = None
                except routing_service.GeocodingError as exc:
                    raised = exc
                _check(label, type(raised) is exc_type, f"got {type(raised).__name__}")
        with patch.object(routing_service.requests, "get", side_effect=requests.ConnectionError("down")):
            try:
                routing_service.geocode("x"); raised = None
            except routing_service.GeocodingError as exc:
                raised = exc
            _check("network failure -> Provider", isinstance(raised, routing_service.GeocodingProviderError))
    with patch.object(routing_service, "OLA_MAPS_API_KEY", None):
        try:
            routing_service.geocode("x"); raised = None
        except routing_service.GeocodingError as exc:
            raised = exc
        _check("missing API key -> Provider", isinstance(raised, routing_service.GeocodingProviderError))

    print("  -- HTTP status mapping in /route/optimize --")
    with patch.object(routes, "DEMO_MODE", False):
        for exc, status in [
            (routing_service.GeocodingNotFoundError("no match"), 422),
            (routing_service.GeocodingProviderError("429 from Ola"), 503),
        ]:
            with patch.object(routes, "geocode", side_effect=exc):
                try:
                    routes.optimize_route(_request(destination="Atlantis")); got = None
                except HTTPException as http_exc:
                    got = http_exc.status_code
                _check(f"{type(exc).__name__} -> HTTP {status}", got == status, f"got {got}")


# ---- 5. connector filtering in the graph -------------------------------------------
def test_connector_filter():
    print("\n=== 5. filter_candidate_stations enforces connector compatibility ===")
    origin = (13.0, 80.0)
    dest = (11.0, 80.0)  # ~222km, a multi-hop trip
    stations = [
        {"id": 1, "station_name": "ccs2", "latitude": 12.5, "longitude": 80.0, "connector_types": "CCS (Type 2)"},
        {"id": 2, "station_name": "type2 ac only", "latitude": 12.3, "longitude": 80.0, "connector_types": "AC Type 2"},
        {"id": 3, "station_name": "unknown", "latitude": 12.1, "longitude": 80.0, "connector_types": "Unknown"},
        {"id": 4, "station_name": "missing", "latitude": 11.9, "longitude": 80.0, "connector_types": None},
        {"id": 5, "station_name": "chademo only", "latitude": 11.7, "longitude": 80.0, "connector_types": "CHAdeMO"},
        {"id": 6, "station_name": "prebucketed ccs2", "latitude": 11.5, "longitude": 80.0,
         "connector_buckets": ["CCS2"], "connector_types": "whatever"},
        {"id": 7, "station_name": "multi incl ccs2", "latitude": 11.3, "longitude": 80.0,
         "connector_types": "16A (AC), CCS2 (DC)"},
    ]
    for profile, expected in [("mid_ev", {1, 6, 7}), ("large_ev", {1, 6, 7}), ("small_ev", {1, 2, 6, 7})]:
        got = {c["id"] for c in graph_planner.filter_candidate_stations(*origin, *dest, stations, profile, 222.0)}
        _check(f"{profile} keeps exactly {sorted(expected)}", got == expected, f"got {sorted(got)}")

    many = [
        {"id": 1000 + i, "station_name": f"ac{i}", "latitude": 12.0, "longitude": 80.0 + i * 1e-4,
         "connector_types": "AC Type 2"}
        for i in range(graph_planner.MAX_CANDIDATE_STATIONS + 20)
    ] + [{"id": 1, "station_name": "ccs2", "latitude": 12.5, "longitude": 80.0, "connector_types": "CCS (Type 2)"}]
    got = {c["id"] for c in graph_planner.filter_candidate_stations(*origin, *dest, many, "mid_ev", 222.0)}
    _check("candidate cap is spent after the connector filter (the lone CCS2 station survives)", got == {1},
           f"got {sorted(got)[:5]}...")


if __name__ == "__main__":
    test_traffic_cache_ttl()
    test_demo_mode()
    test_manifest_fixtures()
    test_geocoding_errors()
    test_connector_filter()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
