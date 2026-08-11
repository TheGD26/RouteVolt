from typing import Optional

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
