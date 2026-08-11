from fastapi import APIRouter

from backend.app.models.schemas import RouteRequest
from backend.app.services.route_optimizer import calculate_best_route


router = APIRouter(
    prefix="/route",
    tags=["Route Planning"]
)


@router.post("/optimize")
def optimize_route(data: RouteRequest):

    # Later:
    # 1. Check battery
    # 2. Find charging stations
    # 3. Predict waiting time
    # 4. Calculate best route (mass-aware, see route_optimizer.py)

    result = calculate_best_route(
        battery=data.battery_percentage,
        distance=data.vehicle_range,
        stations=[],
        vehicle_profile=data.vehicle_profile,
        load_state=data.load_state,
        payload_kg=data.payload_kg,
    )

    return {
        "recommended_station": result["station"],
        "estimated_time": "45 mins",
        "charging_required": True,
        "total_mass_kg": result["total_mass_kg"],
    }
