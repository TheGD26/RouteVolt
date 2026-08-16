from typing import Literal, Optional

from pydantic import BaseModel, Field


class RouteRequest(BaseModel):

    current_location: str

    destination: str

    battery_percentage: float

    vehicle_range: float

    preferred_charging_speed: str

    # --- Mass-aware fields (mirrors trip_energy_dataset.csv schema v2) ---

    vehicle_profile: str = Field(
        default="mid_ev",
        description="Vehicle class used to look up curb weight and baseline "
        "efficiency: 'small_ev' | 'mid_ev' | 'large_ev'.",
    )

    load_state: str = Field(
        default="half_load",
        description="Approximate load level when an exact payload isn't known: "
        "'unladen' | 'half_load' | 'full_load'.",
    )

    payload_kg: Optional[float] = Field(
        default=None,
        description="Optional precise payload (passengers + cargo) in kg. "
        "Overrides the load_state estimate when provided.",
    )

    # --- Optional origin coordinates for real station-distance estimation ---
    # current_location is currently free-text (no geocoding step exists yet),
    # so these let a caller who already has coordinates (e.g. browser
    # geolocation) skip straight to accurate haversine distances.
    origin_lat: Optional[float] = Field(
        default=None, description="Origin latitude, if known, for distance-to-station estimation."
    )

    origin_lon: Optional[float] = Field(
        default=None, description="Origin longitude, if known, for distance-to-station estimation."
    )


class OccupancyReportRequest(BaseModel):

    station_id: str

    reported_status: Literal["busy", "free", "unknown"]


class LatLng(BaseModel):
    lat: float
    lng: float


class RouteLeg(BaseModel):
    """One leg as produced by routing_service.get_route (OSRM step-level granularity)."""

    start: tuple[float, float]
    end: tuple[float, float]
    distance_km: float
    duration_s: float
    points: list[tuple[float, float]] = Field(
        default_factory=list,
        description="Lat/lng points sampled along the leg's OSRM geometry -- "
        "what elevation_service walks to compute elevation_gain_m/loss_m. "
        "Echo this back unchanged in /route/reroute-stop's remaining_route_legs "
        "so elevation stays accurate for the rest of the journey.",
    )
    elevation_gain_m: Optional[float] = Field(
        default=None, description="Real climb over this leg (Open-Elevation), not a flat-terrain stub."
    )
    elevation_loss_m: Optional[float] = Field(
        default=None, description="Real descent over this leg (Open-Elevation), not a flat-terrain stub."
    )
    elevation_source: Optional[str] = Field(
        default=None,
        description="'open_elevation' if real elevation was used, 'stub_fallback' if Open-Elevation "
        "was unreachable and this leg fell back to 0.0 gain/loss -- lets a caller tell a genuinely "
        "flat leg apart from a silently-degraded one.",
    )
    traffic_congestion_level: Optional[float] = Field(
        default=None,
        description="Real 0-1 congestion level for this leg (Ola Maps / Google Distance Matrix), "
        "not the flat 0.3 stub it used to be.",
    )
    traffic_source: Optional[str] = Field(
        default=None,
        description="'live' if real traffic data was used, 'stub_fallback' if no traffic provider "
        "was reachable/configured and this leg fell back to 0.3 -- lets a caller tell genuinely "
        "light traffic apart from a silently-degraded guess.",
    )


class StopAlternative(BaseModel):
    """One of the top-N reachable stations at a planned charging stop (see battery_simulator)."""

    station_id: Optional[int] = None
    station_name: Optional[str] = None
    latitude: float
    longitude: float
    power_kw: float
    detour_km: float
    detour_time_min: float
    requires_detour: bool
    predicted_energy_kwh: float
    expected_wait_minutes: float = Field(
        default=0.0, description="Queueing-arithmetic wait estimate (not ML) if arriving now -- see charger_service.estimate_wait_time."
    )
    queue_length: int = Field(
        default=0, description="Predicted number of vehicles ahead in the queue at arrival."
    )


class ChargingStop(BaseModel):

    leg_index: int
    location: LatLng
    battery_pct_on_arrival: float
    station_id: Optional[int] = None
    station_name: Optional[str] = None
    charge_to_percent: float = Field(
        description="Target battery % for this stop -- capped at BATTERY_CEILING_PCT "
        "unless exceeded_ceiling is true."
    )
    exceeded_ceiling: bool = Field(
        description="True if the very next leg required charging above BATTERY_CEILING_PCT to be reachable at all."
    )
    estimated_charge_duration_min: float
    expected_wait_minutes: float = Field(
        default=0.0, description="Queueing-arithmetic wait estimate (not ML) at the chosen station -- see charger_service.estimate_wait_time."
    )
    queue_length: int = Field(
        default=0, description="Predicted number of vehicles ahead in the queue at arrival."
    )
    alternatives: list[StopAlternative]


class RouteGap(BaseModel):
    """A genuine dead zone: no reachable station, or a leg too long for the vehicle even at 100%."""

    leg_index: int
    location: LatLng
    battery_pct_at_gap: float
    reason: str


class RouteAlternative(BaseModel):

    label: str
    distance_km: float
    duration_min: float
    predicted_energy_kwh: float
    final_battery_pct: float
    status: Literal["ok", "unreachable_gap"]
    charging_plan: list[ChargingStop] = Field(
        default_factory=list,
        description="Every planned charging stop in order; empty for a route that needs none.",
    )
    gap: Optional[RouteGap] = None
    legs: list[RouteLeg] = Field(
        description="Every leg actually driven, in order, index-aligned with each "
        "charging_plan entry's leg_index -- slice legs[leg_index:] to get the "
        "remaining_route_legs a POST /route/reroute-stop call needs."
    )


class RouteOptimizeResponse(BaseModel):

    origin: LatLng
    destination: LatLng
    estimated_time: str
    eta_provider: str
    traffic_aware: bool
    routes: list[RouteAlternative]


class RerouteRequest(BaseModel):
    """
    Body for POST /route/reroute-stop: the frontend sends back exactly what
    /route/optimize gave it for the stop whose station failed (its
    alternatives + the remaining leg list) plus which station failed.
    """

    failed_station_id: str
    current_stop_alternatives: list[StopAlternative]
    remaining_route_legs: list[RouteLeg]
    battery_pct: float

    vehicle_profile: str = "mid_ev"
    load_state: str = "half_load"
    payload_kg: Optional[float] = None
