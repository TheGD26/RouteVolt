# RouteVolt — Trip Dataset Schema (v2)

Status: **Approved for generation.** Row unit = one synthetic vehicle trip along a road segment.

Change from v1: vehicle **mass** (curb weight + payload) is now a first-class input.
Previously, load was implicitly folded into a single fixed `vehicle_efficiency_baseline_wh_per_km`
constant per profile, which meant the model could never distinguish an empty car from a fully
loaded one. v2 makes total mass explicit and routes it through the physics terms where it
actually matters (elevation climb, rolling resistance, stop-and-go acceleration), while keeping
it out of the terms where it physically doesn't belong (aerodynamic drag).

## Vehicle profiles (fixed set of 3)

| Profile | battery_capacity_kwh | efficiency_baseline_wh_per_km | curb_weight_kg | max_payload_kg | Segment analogy |
|---|---|---|---|---|---|
| small_ev | 24 | 140 | 1400 | 300 | Hatchback-class |
| mid_ev   | 40 | 160 | 1900 | 450 | Compact SUV-class |
| large_ev | 75 | 190 | 2400 | 600 | Larger SUV-class |

`efficiency_baseline_wh_per_km` is now interpreted as the **aerodynamic + drivetrain baseline**
only (mass-independent), not an all-in figure. `curb_weight_kg` is the unladen (empty) vehicle
weight. `max_payload_kg` is the maximum rated payload (passengers + cargo) for that class and
scales with vehicle size, per class.

Each trip row samples one profile. Target dataset size: **500 rows**, stratified across
vehicle profile, road_type, and load_state so no category is underrepresented.

## Load state (laden / unladen) — NEW in v2

| load_state | payload_kg sampling | Notes |
|---|---|---|
| unladen | uniform(0, 0.10 × max_payload_kg) | Driver only, no meaningful cargo |
| half_load | uniform(0.40, 0.60) × max_payload_kg | Typical daily-use load |
| full_load | uniform(0.85, 1.00) × max_payload_kg | Fully loaded (passengers + cargo) |

`load_state` is sampled categorically (uniform across the 3 states) per trip, then `payload_kg`
is drawn from the corresponding range above — this keeps the dataset easy to group/report on
by load state while still adding continuous within-state noise so rows aren't identical.

```
total_mass_kg = curb_weight_kg + payload_kg
```

## Input features (X)

| Column | Type | Source | Notes |
|---|---|---|---|
| distance_km | float | synthetic (sampled) | Primary driver of energy_consumed_kwh |
| road_type | categorical: highway / arterial / city | synthetic | Proxy for stop-and-go frequency |
| elevation_gain_m | float, >= 0 | synthetic | Climb cost — now scales with total_mass_kg |
| elevation_loss_m | float, >= 0 | synthetic | Partial regen credit, not 1:1 with gain |
| avg_speed_kmh | float | synthetic, correlated to road_type | Non-linear (~v^2) drag effect, mass-independent |
| traffic_congestion_level | float [0,1] | synthetic | Distinguishes "slow from traffic" vs "slow from eco driving"; mass-dependent (accel/decel) |
| ambient_temperature_c | float | synthetic (Chennai-realistic range) | U-shaped efficiency effect |
| weather_condition | categorical: clear / rain / heavy_rain | synthetic | Minor rolling-resistance / speed effect |
| vehicle_profile | categorical: small_ev / mid_ev / large_ev | synthetic (fixed set) | Selects capacity + baseline efficiency + curb weight + max payload |
| vehicle_battery_capacity_kwh | float | synthetic (from profile) | |
| vehicle_efficiency_baseline_wh_per_km | float | synthetic (from profile) | Aero/drivetrain baseline only (mass-independent) |
| vehicle_curb_weight_kg | float | synthetic (from profile) | **NEW** — unladen vehicle mass |
| load_state | categorical: unladen / half_load / full_load | synthetic | **NEW** — sampled per trip |
| payload_kg | float | synthetic (from load_state range) | **NEW** — passengers + cargo mass |
| total_mass_kg | float | derived = curb_weight_kg + payload_kg | **NEW** — mass used in physics terms below |

## Target formula (v2 — mass-aware)

Energy consumption is now built from four physical terms rather than one flat baseline:

```
energy_aero_kwh      = efficiency_baseline_wh_per_km * speed_factor(avg_speed_kmh) * distance_km / 1000
energy_elevation_kwh = total_mass_kg * 9.81 * max(elevation_gain_m - 0.6 * elevation_loss_m, 0) / 3_600_000
energy_rolling_kwh   = 0.01 * total_mass_kg * 9.81 * (distance_km * 1000) / 3_600_000
energy_traffic_kwh   = traffic_congestion_level * total_mass_kg * 0.00025 * distance_km * city_weight(road_type)

energy_consumed_kwh  = (energy_aero_kwh + energy_elevation_kwh + energy_rolling_kwh + energy_traffic_kwh)
                        * temp_multiplier(ambient_temperature_c)
                        * weather_multiplier(weather_condition)
                        + noise
```

- `energy_aero_kwh` — aerodynamic drag + drivetrain loss. Deliberately **not** a function of mass
  (drag depends on frontal area/speed, not weight).
- `energy_elevation_kwh` — direct physics: `m·g·Δh`, converted J → kWh. This is the term that was
  previously invisible in v1 because mass wasn't a variable.
- `energy_rolling_kwh` — rolling resistance `Crr·m·g·d`, `Crr ≈ 0.01`. Scales linearly with mass.
- `energy_traffic_kwh` — proxy for repeated accel/decel energy loss in stop-and-go conditions;
  scales with mass and is weighted up for `road_type = city`.
- `temp_multiplier` — U-shaped, minimum near ~21°C, rising at both hot and cold extremes
  (HVAC/battery-chemistry losses).
- `weather_multiplier` — small increase for rain/heavy_rain (rolling resistance, wipers, reduced
  regen).

| Column | Type | Definition |
|---|---|---|
| energy_consumed_kwh | float | Primary target, formula above |
| wh_per_km | float | Derived/secondary = energy_consumed_kwh * 1000 / distance_km |

## Identifier (excluded from training)

| Column | Type | Notes |
|---|---|---|
| trip_id | int | Row id only |

## Explicitly excluded from this table

available_ports, occupied_ports, queue_time_minutes, cost_per_kwh — these belong to the
separate stations_clean.csv (recommendation-system data), not the ML training table.
No causal relationship to energy consumption during a drive.
