"""
RouteVolt synthetic trip dataset generator (schema v2 — mass-aware).

Generates 500 rows stratified across vehicle_profile x road_type x load_state,
implementing the physics-based energy formula described in dataset_schema.md.
"""

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)

# ---- Vehicle profiles (fixed set of 3) ----
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

LOAD_STATES = {
    "unladen": (0.00, 0.10),
    "half_load": (0.40, 0.60),
    "full_load": (0.85, 1.00),
}

ROAD_TYPES = ["highway", "arterial", "city"]
WEATHER_CONDITIONS = ["clear", "rain", "heavy_rain"]

N_ROWS = 10000


def sample_payload(profile_name: str, load_state: str) -> float:
    lo, hi = LOAD_STATES[load_state]
    max_payload = VEHICLE_PROFILES[profile_name]["max_payload_kg"]
    return float(RNG.uniform(lo, hi) * max_payload)


def sample_speed(road_type: str) -> float:
    # avg_speed_kmh correlated with road_type
    if road_type == "highway":
        return float(np.clip(RNG.normal(85, 10), 50, 120))
    if road_type == "arterial":
        return float(np.clip(RNG.normal(45, 8), 20, 70))
    return float(np.clip(RNG.normal(22, 6), 5, 40))  # city


def speed_factor(avg_speed_kmh: float) -> float:
    # Non-linear (~v^2) drag effect relative to a 50 km/h reference
    return (avg_speed_kmh / 50.0) ** 2


def city_weight(road_type: str) -> float:
    # Stop-and-go frequency proxy used to weight the traffic/accel term
    return {"highway": 0.3, "arterial": 0.7, "city": 1.3}[road_type]


def temp_multiplier(temp_c: float) -> float:
    # U-shaped, minimum near ~21C
    return 1.0 + 0.0006 * (temp_c - 21) ** 2


def weather_multiplier(weather: str) -> float:
    return {"clear": 1.00, "rain": 1.04, "heavy_rain": 1.09}[weather]


def sample_traffic_congestion(road_type: str) -> float:
    if road_type == "city":
        return float(np.clip(RNG.beta(2.5, 2.0), 0, 1))
    if road_type == "arterial":
        return float(np.clip(RNG.beta(2.0, 3.0), 0, 1))
    return float(np.clip(RNG.beta(1.2, 5.0), 0, 1))  # highway: usually low


def sample_elevation(road_type: str):
    # City trips: small elevation changes; highway/arterial: can vary more
    if road_type == "highway":
        gain = float(RNG.exponential(40))
        loss = float(RNG.exponential(35))
    elif road_type == "arterial":
        gain = float(RNG.exponential(20))
        loss = float(RNG.exponential(18))
    else:
        gain = float(RNG.exponential(8))
        loss = float(RNG.exponential(8))
    return round(gain, 1), round(loss, 1)


def sample_distance(road_type: str) -> float:
    if road_type == "highway":
        return float(np.clip(RNG.gamma(shape=3.0, scale=25), 10, 300))
    if road_type == "arterial":
        return float(np.clip(RNG.gamma(shape=2.5, scale=8), 2, 80))
    return float(np.clip(RNG.gamma(shape=2.0, scale=4), 1, 40))  # city


def sample_temperature() -> float:
    # Chennai-realistic range, roughly 22-40C, skewed warm
    return float(np.clip(RNG.normal(31, 5), 20, 42))


def sample_weather() -> str:
    return str(RNG.choice(WEATHER_CONDITIONS, p=[0.75, 0.18, 0.07]))


def build_stratified_index(n_rows: int):
    """Balanced-ish stratification across profile x road_type x load_state (18 cells)."""
    cells = [
        (p, r, l)
        for p in VEHICLE_PROFILES
        for r in ROAD_TYPES
        for l in LOAD_STATES
    ]
    n_cells = len(cells)
    base = n_rows // n_cells
    remainder = n_rows - base * n_cells
    counts = [base + (1 if i < remainder else 0) for i in range(n_cells)]
    RNG.shuffle(counts)  # spread the +1 remainder cells around
    rows = []
    for (profile, road, load), cnt in zip(cells, counts):
        rows.extend([(profile, road, load)] * cnt)
    RNG.shuffle(rows)
    return rows


def generate_dataset() -> pd.DataFrame:
    strata = build_stratified_index(N_ROWS)
    records = []

    for trip_id, (profile_name, road_type, load_state) in enumerate(strata, start=1):
        profile = VEHICLE_PROFILES[profile_name]

        payload_kg = sample_payload(profile_name, load_state)
        total_mass_kg = profile["curb_weight_kg"] + payload_kg

        distance_km = sample_distance(road_type)
        avg_speed_kmh = sample_speed(road_type)
        elevation_gain_m, elevation_loss_m = sample_elevation(road_type)
        traffic_congestion_level = sample_traffic_congestion(road_type)
        ambient_temperature_c = sample_temperature()
        weather_condition = sample_weather()

        # ---- Physics-based energy terms ----
        energy_aero_kwh = (
            profile["efficiency_baseline_wh_per_km"]
            * speed_factor(avg_speed_kmh)
            * distance_km
            / 1000.0
        )
        net_climb_m = max(elevation_gain_m - 0.6 * elevation_loss_m, 0.0)
        energy_elevation_kwh = total_mass_kg * 9.81 * net_climb_m / 3_600_000.0
        energy_rolling_kwh = (
            0.01 * total_mass_kg * 9.81 * (distance_km * 1000.0) / 3_600_000.0
        )
        energy_traffic_kwh = (
            traffic_congestion_level
            * total_mass_kg
            * 0.00025
            * distance_km
            * city_weight(road_type)
        )

        subtotal_kwh = (
            energy_aero_kwh
            + energy_elevation_kwh
            + energy_rolling_kwh
            + energy_traffic_kwh
        )

        multiplier = temp_multiplier(ambient_temperature_c) * weather_multiplier(
            weather_condition
        )

        noise = RNG.normal(0, 0.03 * subtotal_kwh * multiplier + 0.01)
        energy_consumed_kwh = max(subtotal_kwh * multiplier + noise, 0.05)

        wh_per_km = energy_consumed_kwh * 1000.0 / distance_km

        records.append(
            {
                "trip_id": trip_id,
                "distance_km": round(distance_km, 2),
                "road_type": road_type,
                "elevation_gain_m": elevation_gain_m,
                "elevation_loss_m": elevation_loss_m,
                "avg_speed_kmh": round(avg_speed_kmh, 1),
                "traffic_congestion_level": round(traffic_congestion_level, 3),
                "ambient_temperature_c": round(ambient_temperature_c, 1),
                "weather_condition": weather_condition,
                "vehicle_profile": profile_name,
                "vehicle_battery_capacity_kwh": profile["battery_capacity_kwh"],
                "vehicle_efficiency_baseline_wh_per_km": profile[
                    "efficiency_baseline_wh_per_km"
                ],
                "vehicle_curb_weight_kg": profile["curb_weight_kg"],
                "load_state": load_state,
                "payload_kg": round(payload_kg, 1),
                "total_mass_kg": round(total_mass_kg, 1),
                "energy_consumed_kwh": round(energy_consumed_kwh, 3),
                "wh_per_km": round(wh_per_km, 1),
            }
        )

    return pd.DataFrame.from_records(records)


if __name__ == "__main__":
    df = generate_dataset()
    out_path = "trip_energy_dataset.csv"
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")
    print(df.groupby(["vehicle_profile", "load_state"])["total_mass_kg"].mean())
    print()
    print(df.groupby("load_state")["energy_consumed_kwh"].mean())
