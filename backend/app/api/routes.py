from fastapi import APIRouter, HTTPException

from backend.app.models.schemas import RerouteRequest, RouteOptimizeResponse, RouteRequest
from backend.app.services.battery_simulator import plan_full_journey, reroute_from_stop
from backend.app.services.routing_service import GeocodingError, RoutingError, geocode, get_route
from backend.app.services.traffic_service import TrafficServiceError, get_traffic_eta

router = APIRouter(
    prefix="/route",
    tags=["Route Planning"]
)

ROUTE_LABELS = ["Route A", "Route B", "Route C"]
MAX_ROUTE_ALTERNATIVES = 3


def _score_route(route: dict, label: str, data: RouteRequest) -> dict:
    journey = plan_full_journey(
        route_legs=route["legs"],
        battery_pct=data.battery_percentage,
        vehicle_profile=data.vehicle_profile,
        load_state=data.load_state,
        payload_kg=data.payload_kg,
    )

    return {
        "label": label,
        "distance_km": round(route["distance_m"] / 1000.0, 2),
        "duration_min": round(route["duration_s"] / 60.0, 1),
        "predicted_energy_kwh": journey["total_predicted_energy_kwh"],
        "final_battery_pct": journey["final_battery_pct"],
        "status": journey["status"],
        "charging_plan": journey["stops"],
        "gap": journey["gap"],
        "legs": journey["legs"],
    }


@router.post("/optimize", response_model=RouteOptimizeResponse)
def optimize_route(data: RouteRequest):

    try:
        origin_lat, origin_lng = geocode(data.current_location)
        dest_lat, dest_lng = geocode(data.destination)
    except GeocodingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        route_result = get_route(origin_lat, origin_lng, dest_lat, dest_lng, alternatives=True)
    except RoutingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    alternatives = route_result["routes"][:MAX_ROUTE_ALTERNATIVES]
    routes_out = [
        _score_route(route, label, data)
        for label, route in zip(ROUTE_LABELS, alternatives)
    ]

    try:
        eta = get_traffic_eta(origin_lat, origin_lng, dest_lat, dest_lng)
    except TrafficServiceError:
        # Neither traffic provider is configured/reachable -- fall back to
        # OSRM's own (non-traffic-aware) duration for the best route rather
        # than failing the whole request.
        eta = {
            "duration_seconds": alternatives[0]["duration_s"],
            "distance_meters": alternatives[0]["distance_m"],
            "traffic_aware": False,
            "provider": "osrm_route_duration",
        }

    return {
        "origin": {"lat": origin_lat, "lng": origin_lng},
        "destination": {"lat": dest_lat, "lng": dest_lng},
        "estimated_time": f"{round(eta['duration_seconds'] / 60)} mins",
        "eta_provider": eta["provider"],
        "traffic_aware": eta["traffic_aware"],
        "routes": routes_out,
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
