from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend.app.models.schemas import OccupancyReportRequest
from backend.app.services.charger_service import (
    add_occupancy_report,
    estimate_wait_time,
    get_blended_occupancy,
    get_recent_reports,
    get_stations,
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
