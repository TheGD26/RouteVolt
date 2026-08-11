"""
Route optimization core.

Vehicle mass constants mirror generate_dataset.py / dataset_schema.md (v2) so that
whatever model gets trained on trip_energy_dataset.csv sees the same feature
definitions at inference time as it did at training time.
"""

from typing import Optional

# ---- Vehicle profiles (must stay in sync with generate_dataset.py) ----
VEHICLE_PROFILES = {
    "small_ev": {
        "battery_capacity_kwh": 24,
        "efficiency_baseline_wh_per_km": 140,
        "curb_weight_kg": 1400,
        "max_payload_kg": 300,
    },
    "mid_ev": {
        "battery_capacity_kwh": 40,
        "efficiency_baseline_wh_per_km": 160,
        "curb_weight_kg": 1900,
        "max_payload_kg": 450,
    },
    "large_ev": {
        "battery_capacity_kwh": 75,
        "efficiency_baseline_wh_per_km": 190,
        "curb_weight_kg": 2400,
        "max_payload_kg": 600,
    },
}

# Midpoint payload fraction per load_state, used when the caller doesn't supply
# an exact payload_kg (mirrors the sampling ranges in dataset_schema.md).
LOAD_STATE_PAYLOAD_FRACTION = {
    "unladen": 0.05,     # midpoint of uniform(0, 0.10)
    "half_load": 0.50,   # midpoint of uniform(0.40, 0.60)
    "full_load": 0.925,  # midpoint of uniform(0.85, 1.00)
}


def estimate_total_mass_kg(
    vehicle_profile: str,
    load_state: str = "half_load",
    payload_kg: Optional[float] = None,
) -> float:
    """
    Resolve total_mass_kg the same way the training data defines it:
    curb_weight_kg + payload_kg.

    If payload_kg is not supplied, it's estimated from load_state as a fraction
    of the profile's max_payload_kg.
    """

    if vehicle_profile not in VEHICLE_PROFILES:
        raise ValueError(
            f"Unknown vehicle_profile '{vehicle_profile}'. "
            f"Expected one of {list(VEHICLE_PROFILES)}."
        )

    profile = VEHICLE_PROFILES[vehicle_profile]

    if payload_kg is None:
        if load_state not in LOAD_STATE_PAYLOAD_FRACTION:
            raise ValueError(
                f"Unknown load_state '{load_state}'. "
                f"Expected one of {list(LOAD_STATE_PAYLOAD_FRACTION)}."
            )
        payload_kg = LOAD_STATE_PAYLOAD_FRACTION[load_state] * profile["max_payload_kg"]

    return profile["curb_weight_kg"] + payload_kg


def calculate_best_route(
    battery,
    distance,
    stations,
    vehicle_profile: str = "mid_ev",
    load_state: str = "half_load",
    payload_kg: Optional[float] = None,
):
    """
    Future ML pipeline:

    Inputs:
    - battery %
    - distance
    - terrain
    - traffic
    - weather
    - charging cost
    - queue time
    - vehicle_profile / load_state / payload_kg -> total_mass_kg  (NEW)

    total_mass_kg now feeds the same elevation / rolling-resistance / traffic
    energy terms used to generate trip_energy_dataset.csv, so a trained model
    can be swapped in here without changing the request contract.

    Output:
    - optimal charging station
    - charging percentage
    """

    total_mass_kg = estimate_total_mass_kg(vehicle_profile, load_state, payload_kg)

    return {
        "station": "A",
        "charge_to": 80,
        "vehicle_profile": vehicle_profile,
        "load_state": load_state,
        "total_mass_kg": round(total_mass_kg, 1),
    }
