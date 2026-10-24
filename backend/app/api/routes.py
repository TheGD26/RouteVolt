import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.app.config import DEMO_MODE
from backend.app.models.schemas import RerouteRequest, RouteOptimizeResponse, RouteRequest
from backend.app.services import graph_planner
from backend.app.services.battery_simulator import reroute_from_stop
from backend.app.services.routing_service import GeocodingError, GeocodingNotFoundError, RoutingError, geocode
from backend.app.services.traffic_service import TrafficServiceError, get_traffic_eta

router = APIRouter(
    prefix="/route",
    tags=["Route Planning"]
)

# --- Demo mode -------------------------------------------------------------
# Hackathon demo-day risk mitigation ONLY -- see README's "Demo mode" section
# for the full writeup and tests/demo_mode_implementation_2026-08-24.md for
# how/why. Not a production feature; safe to delete this whole block plus
# backend/app/fixtures/ post-hackathon.
#
# When DEMO_MODE=true (backend/app/config.py) and an incoming request's
# origin/destination/profile/load_state match an entry in
# demo_routes_manifest.json exactly, /route/optimize returns that entry's
# saved real response instead of running the live geocode -> graph_planner
# -> traffic pipeline, so a live demo can't be derailed by third-party rate
# limiting (see tests/baseline_rerun_and_health_check_2026-08-24.md's Part 2
# risk note). Any request that doesn't match -- wrong city names, wrong
# vehicle_profile/load_state, or DEMO_MODE unset -- falls through to the
# real pipeline untouched. New routes are added via
# backend/app/scripts/capture_demo_fixture.py, not by hand.
#
# Matching deliberately ignores battery_percentage (the frontend's default form
# sends 80 while e.g. the Nagercoil fixture was captured at 8), but a request
# that sets an exact payload_kg never matches. Every served fixture is labelled
# `precomputed: true` plus `precomputed_battery_percentage` (the battery the
# saved plan was actually computed for, from the manifest's
# `captured_battery_percentage`) so it can't pass as a live result.
_DEMO_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
_DEMO_MANIFEST_PATH = _DEMO_FIXTURES_DIR / "demo_routes_manifest.json"


@lru_cache(maxsize=1)
def _demo_manifest() -> list[dict]:
    if not _DEMO_MANIFEST_PATH.exists():
        return []
    return json.loads(_DEMO_MANIFEST_PATH.read_text())


def _demo_manifest_entry(data: RouteRequest) -> dict | None:
    if not DEMO_MODE:
        return None
    # Fixtures were captured from load_state's estimated payload. A request
    # with an exact payload_kg is a different physical question (mass changes
    # energy use), so it must run live rather than get a plan for another load.
    if data.payload_kg is not None:
        return None
    origin = data.current_location.strip().lower()
    destination = data.destination.strip().lower()
    for entry in _demo_manifest():
        if (
            entry["origin"] == origin
            and entry["destination"] == destination
            and entry["vehicle_profile"] == data.vehicle_profile
            and entry["load_state"] == data.load_state
        ):
            return entry
    return None


@lru_cache(maxsize=None)
def _demo_fixture(fixture_name: str) -> dict:
    return json.loads((_DEMO_FIXTURES_DIR / fixture_name).read_text())
# --- End demo mode -----------------------------------------------------

# Pre-graph-planner, this used to fetch up to 3 geometrically distinct OSRM
# route alternatives and independently run the (then-greedy) stop search
# over each one -- 3 genuinely different scored options. graph_planner's
# station graph is built directly from origin/destination coordinates, not
# from a specific route geometry, so it finds the single globally cheapest
# stop sequence once; re-running it against OSRM's other geometric
# alternatives would just solve the *same* graph again and return the same
# answer. That "3 alternatives" structure doesn't carry over -- flagging
# this rather than quietly keeping a 3-wide loop that repeats identical
# work. /route/optimize now returns a single planned route; `routes` stays
# a list (frontend already indexes into it) for response-shape stability
# and in case a genuinely route-geometry-aware alternative (e.g. "avoid
# highways") gets added later.
ROUTE_LABEL = "Route A"


@router.post("/optimize", response_model=RouteOptimizeResponse)
def optimize_route(data: RouteRequest):

    demo_entry = _demo_manifest_entry(data)
    if demo_entry is not None:
        # Copy (the fixture dict is lru_cached and shared) and label it: this
        # was NOT optimized for this request's battery_percentage.
        return {
            **_demo_fixture(demo_entry["fixture"]),
            "precomputed": True,
            "precomputed_battery_percentage": demo_entry.get("captured_battery_percentage"),
        }

    try:
        origin_lat, origin_lng = geocode(data.current_location)
        dest_lat, dest_lng = geocode(data.destination)
    except GeocodingNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GeocodingError as exc:
        # Provider unconfigured/down/rate-limited: not the caller's mistake, and retryable.
        raise HTTPException(status_code=503, detail=f"Geocoding service unavailable: {exc}") from exc

    journey = graph_planner.plan_journey_via_graph(
        origin_lat=origin_lat,
        origin_lon=origin_lng,
        dest_lat=dest_lat,
        dest_lon=dest_lng,
        battery_pct=data.battery_percentage,
        vehicle_profile=data.vehicle_profile,
        load_state=data.load_state,
        payload_kg=data.payload_kg,
    )

    route_out = {
        "label": ROUTE_LABEL,
        "distance_km": journey["distance_km"],
        "duration_min": journey["duration_min"],
        "predicted_energy_kwh": journey["total_predicted_energy_kwh"],
        "final_battery_pct": journey["final_battery_pct"],
        "status": journey["status"],
        "feasible": journey["feasible"],
        "reason": journey["reason"],
        "charging_plan": journey["stops"],
        "exceeded_ceiling_stops": journey["exceeded_ceiling_stops"],
        "gap": journey["gap"],
        "legs": journey["legs"],
    }

    try:
        eta = get_traffic_eta(origin_lat, origin_lng, dest_lat, dest_lng)
    except TrafficServiceError:
        # Neither traffic provider is configured/reachable -- fall back to
        # the graph-planned route's own (non-traffic-aware) duration rather
        # than failing the whole request. If the route wasn't feasible at
        # all, journey["duration_min"] is None too -- nothing to fall back
        # to, so estimated_time reports that plainly instead of crashing.
        eta = {
            "duration_seconds": (journey["duration_min"] or 0) * 60,
            "distance_meters": (journey["distance_km"] or 0) * 1000,
            "traffic_aware": False,
            "provider": "osrm_route_duration",
        }

    estimated_time = (
        f"{round(eta['duration_seconds'] / 60)} mins" if journey["feasible"] else "unreachable"
    )

    return {
        "origin": {"lat": origin_lat, "lng": origin_lng},
        "destination": {"lat": dest_lat, "lng": dest_lng},
        "estimated_time": estimated_time,
        "eta_provider": eta["provider"],
        "traffic_aware": eta["traffic_aware"],
        "routes": [route_out],
    }


@router.post("/reroute-stop")
def reroute_stop(data: RerouteRequest):
    """
    A planned charging stop's station turned out to be unavailable --
    replan the rest of the journey from the next-best alternative at that
    same stop (see battery_simulator.reroute_from_stop).
    """
    return reroute_from_stop(
        current_stop_alternatives=[alt.model_dump() for alt in data.current_stop_alternatives],
        failed_station_id=data.failed_station_id,
        remaining_route_legs=[leg.model_dump() for leg in data.remaining_route_legs],
        battery_pct=data.battery_pct,
        vehicle_profile=data.vehicle_profile,
        load_state=data.load_state,
        payload_kg=data.payload_kg,
    )
