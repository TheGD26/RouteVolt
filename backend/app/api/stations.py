from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from backend.app.models.schemas import OccupancyReportRequest, QueueJoinRequest
from backend.app.services.charger_service import (
    add_occupancy_report,
    estimate_charge_duration_for_station,
    estimate_wait_time,
    get_blended_occupancy,
    get_queue_state,
    get_recent_reports,
    get_stations,
    join_queue,
    release_reservation,
    station_exists,
)

router = APIRouter(
    prefix="/stations",
    tags=["Charging Stations"]
)


@router.get("/")
def list_stations():
    return get_stations()


@router.get("/{station_id}/occupancy")
def get_station_occupancy(station_id: str):
    try:
        return get_blended_occupancy(station_id, datetime.now(timezone.utc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{station_id}/wait-estimate")
def get_station_wait_estimate(station_id: str):
    try:
        return estimate_wait_time(station_id, datetime.now(timezone.utc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{station_id}/report")
def report_station_occupancy(station_id: str, data: OccupancyReportRequest):
    if station_id != data.station_id:
        raise HTTPException(
            status_code=400,
            detail="station_id in the URL and request body must match",
        )
    if not station_exists(station_id):
        raise HTTPException(status_code=404, detail=f"Unknown station_id '{station_id}'")

    return add_occupancy_report(station_id, data.reported_status)


@router.get("/{station_id}/reports/recent")
def get_station_recent_reports(station_id: str, limit: int = 5):
    if not station_exists(station_id):
        raise HTTPException(status_code=404, detail=f"Unknown station_id '{station_id}'")

    return get_recent_reports(station_id, limit)


@router.post("/{station_id}/queue/join")
def join_station_queue(station_id: str, data: QueueJoinRequest):
    if not station_exists(station_id):
        raise HTTPException(status_code=404, detail=f"Unknown station_id '{station_id}'")

    charge_duration_min = estimate_charge_duration_for_station(
        station_id, data.current_battery_pct, data.target_battery_pct, data.vehicle_profile
    )
    eta = datetime.now(timezone.utc) + timedelta(minutes=data.eta_minutes_from_now)
    assignment = join_queue(
        station_id=station_id,
        user_id=data.user_id,
        charge_duration_min=charge_duration_min,
        user_name=data.user_name,
        eta=eta,
    )
    return {**assignment, "estimated_charge_duration_min": charge_duration_min}


@router.get("/{station_id}/queue")
def get_station_queue(station_id: str):
    try:
        return get_queue_state(station_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/queue/{reservation_id}")
def leave_station_queue(reservation_id: int):
    if not release_reservation(reservation_id):
        raise HTTPException(status_code=404, detail=f"Unknown or already-released reservation_id '{reservation_id}'")
    return {"status": "released", "reservation_id": reservation_id}
