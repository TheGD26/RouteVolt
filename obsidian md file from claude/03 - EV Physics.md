#RouteVolt #physics

Back to [[00 - RouteVolt Master Map]] · Previous: [[02 - Feature Relationships]]

> [!warning] Code status
> No physics/energy code exists in the repo yet. The "RouteVolt implementation" sections below show a **proposed formula** — Python-like snippets you could put in a new `energy_model.py` — clearly labeled **[PROPOSED — not yet in the repo]**. This is a design reference, not a description of existing code. Nothing here should be pasted verbatim without you deciding it's right.

Each concept follows: **Real-world intuition → Physical reasoning → Simplified RouteVolt model → Numerical example → Proposed implementation → Assumptions → Limitations.**

---

## 1. Wh/km — the core efficiency unit

**Real-world intuition:** Wh/km ("watt-hours per kilometer") is like miles-per-gallon in reverse — instead of "distance per unit of fuel," it's "energy per unit of distance." Lower Wh/km = more efficient.

**Physical reasoning:** Energy (Wh) is power × time. A car moving at constant speed for a fixed distance uses energy to overcome friction, drag, and hills. Dividing total energy by distance gives a distance-normalized efficiency number that's comparable across trips of different length.

**Simplified model:** `energy_consumed_kwh = distance_km × wh_per_km / 1000`

**Numerical example:** A mid_ev with baseline 160 Wh/km driving 50 km uses `50 × 160 / 1000 = 8 kWh` at baseline conditions (no adjustments).

**Proposed implementation [PROPOSED]:**
```python
def energy_from_efficiency(distance_km: float, wh_per_km: float) -> float:
    return distance_km * wh_per_km / 1000
```

**Assumptions:** Wh/km is treated as if it's constant across the whole trip, when in reality it varies second-to-second with speed, grade, and traffic.

**Limitations:** A single scalar Wh/km per trip hides intra-trip variation (e.g., a highway cruise segment vs. a stop-and-go city segment within the same trip).

---

## 2. Baseline vehicle efficiency

**Real-world intuition:** Every EV has a "sticker" efficiency rating measured under standard test conditions — RouteVolt's three profiles (140/160/190 Wh/km) stand in for that.

**Physical reasoning:** Baseline efficiency bundles motor efficiency, rolling resistance, mass, and drag coefficient into one number, measured at a reference speed/temperature.

**Simplified model:** A fixed lookup per `vehicle_profile` (see [[01 - Feature Map]] table B).

**Numerical example:** `large_ev` baseline = 190 Wh/km — about 36% higher than `small_ev`'s 140 Wh/km, reflecting its larger mass and frontal area.

**Proposed implementation [PROPOSED]:**
```python
BASELINE_WH_PER_KM = {"small_ev": 140, "mid_ev": 160, "large_ev": 190}
```

**Assumptions:** One number per profile, ignoring individual vehicle variation within a class (tire wear, cargo load, etc.).

**Limitations:** Real vehicles within a "class" vary; this is a simplification for a synthetic dataset, not a claim about any specific real car.

---

## 3. Distance

**Real-world intuition:** More distance, more energy — the most obvious driver.

**Physical reasoning:** At constant efficiency, energy scales linearly with distance (work = force × distance, and averaged force over a trip is roughly constant for similar conditions).

**Simplified model:** Distance is a direct multiplier, not an "adjustment factor" — see the multiplicative-vs-additive section below.

**Numerical example:** Doubling distance from 25 km to 50 km at fixed efficiency exactly doubles energy use.

**Assumptions:** Efficiency is treated as independent of *how* the distance accumulates (e.g., one long highway trip vs. many short errands) — in reality short trips are less efficient (cold-start effects), which this model ignores.

**Limitations:** No representation of trip *segmentation* — RouteVolt models one road_type per trip, not a mixed route.

---

## 4. Speed and speed² (aerodynamic drag)

**Real-world intuition:** Doubling your driving speed doesn't just double wind resistance — it roughly quadruples the *power* needed to overcome it, because drag force itself grows with speed, and power is force × speed.

**Physical reasoning:** Aerodynamic drag force `F_drag = 0.5 × ρ × Cd × A × v²`, where ρ = air density, Cd = drag coefficient, A = frontal area, v = speed. Power to overcome it is `F_drag × v`, i.e., proportional to **v³** for power, but RouteVolt (like most simplified EV efficiency models) works in **energy per unit distance**, where the relevant term becomes proportional to **v²** — because energy = power × time = power × (distance/v), and the v³ and the 1/v combine to v².

**Simplified model:** `speed_factor = 1 + k × (v / v_ref)²`, a multiplicative adjustment to baseline efficiency, where k is a tunable sensitivity constant and v_ref is a reference "efficient" speed (commonly ~50-60 km/h for EVs, which are typically *most* efficient at moderate speed, not at their slowest).

**Numerical example:** If `k = 0.15` and `v_ref = 60`, at v = 120 km/h: `speed_factor = 1 + 0.15 × (120/60)² = 1 + 0.15×4 = 1.6` — a 60% efficiency penalty at double the reference speed.

**Proposed implementation [PROPOSED]:**
```python
def speed_factor(avg_speed_kmh: float, v_ref: float = 60.0, k: float = 0.15) -> float:
    return 1 + k * (avg_speed_kmh / v_ref) ** 2
```

**Assumptions:** Treats "average speed" as if the trip were driven at one constant speed — ignores acceleration/braking cycles, which themselves cost energy independent of average speed.

**Limitations:** Real EVs also lose efficiency at *very low* average speeds (stop-and-go city driving) for a different reason (motor/inverter losses, HVAC running while stationary) — a pure v² term doesn't capture the low-speed penalty. A more realistic model would be U-shaped in speed too, not monotonic.

---

## 5. Temperature and HVAC

**Real-world intuition:** EVs lose range in both very cold and very hot weather — cold saps battery chemistry and needs cabin heating (often resistive, expensive); heat needs air conditioning and can slightly stress the battery.

**Physical reasoning:** Two separate mechanisms bundled together: (1) battery internal resistance rises at low temperature, reducing usable energy per charge; (2) HVAC is a parasitic load unrelated to propulsion, drawn from the same battery.

**Simplified model:** A U-shaped multiplicative factor with a minimum near a comfortable reference temperature (e.g., ~20-22°C), rising toward both extremes.

**Numerical example:** A simple piecewise or quadratic model: `temp_factor = 1 + c × (T - T_ref)²`. With `c = 0.0008`, `T_ref = 21°C`, at T = 38°C (hot Chennai day): `1 + 0.0008×(17)² ≈ 1.23`, a 23% penalty.

**Proposed implementation [PROPOSED]:**
```python
def temp_factor(temp_c: float, t_ref: float = 21.0, c: float = 0.0008) -> float:
    return 1 + c * (temp_c - t_ref) ** 2
```

**Assumptions:** Symmetric U-shape — in reality cold-weather penalties are usually steeper than heat penalties for EVs.

**Limitations:** Doesn't distinguish "HVAC running" from "battery chemistry effect" — bundling them loses interpretability and can't represent, e.g., a driver who doesn't use AC.

---

## 6. Traffic / congestion

**Real-world intuition:** Congestion means more stop-start driving — braking wastes kinetic energy (even with regen recovering some), and idling in traffic burns energy for zero distance.

**Physical reasoning:** Frequent accel/decel cycles are less efficient than steady-state cruising even at the same average speed, because acceleration draws high instantaneous power and braking energy is only partially recovered.

**Simplified model:** `traffic_factor = 1 + m × traffic_congestion_level`, a linear multiplicative penalty on top of the speed-derived factor.

**Numerical example:** With `m = 0.25` and congestion = 0.6: `traffic_factor = 1 + 0.25×0.6 = 1.15`, a 15% penalty independent of what the congestion already did to average speed.

**Proposed implementation [PROPOSED]:**
```python
def traffic_factor(congestion_level: float, m: float = 0.25) -> float:
    return 1 + m * congestion_level
```

**Assumptions:** Treats congestion's effect on *stop-start inefficiency* as separate from its effect on *average speed* — this is intentional (per `dataset_schema.md`'s note that congestion should be distinguishable from "eco driving"), but it means congestion effectively gets "counted twice" (once via lowering avg_speed, once via this direct penalty) — see [[02 - Feature Relationships]] and [[10 - Correlation vs Causation]] for why this needs care.

**Limitations:** Purely synthetic tuning constant `m` — not derived from any measured dataset.

---

## 7. Road type

**Real-world intuition:** "City," "arterial," and "highway" driving have systematically different speed profiles and stop frequency even at the same average speed label.

**Physical reasoning:** Road type is really a **proxy** for a bundle of things (traffic light frequency, speed limit, pedestrian interaction) that this simplified model doesn't represent individually.

**Simplified model:** Road type mainly acts *indirectly*, by influencing the sampled `avg_speed_kmh` and `traffic_congestion_level` distributions at generation time (see [[05 - Synthetic Dataset]]) rather than appearing as its own multiplicative term in the energy formula.

**Assumptions:** If road_type has *no direct* term in the energy formula (only influences the *sampling* of speed/traffic), then it's not really "causing" energy use in the formula sense — it's a **generator-level correlation structure**. Get this right, or you'll double count it.

**Limitations:** A single categorical label per trip can't represent a route that mixes highway and city driving.

---

## 8. Elevation gain

**Real-world intuition:** Climbing a hill stores energy as height — you feel this as needing to press the accelerator harder going uphill.

**Physical reasoning:** Gravitational potential energy: `PE = m × g × h`, where m = vehicle mass (kg), g = 9.81 m/s², h = elevation gained (m). This energy must come from the battery.

**Simplified model:** `elevation_energy_wh = (mass_kg × 9.81 × elevation_gain_m) / 3600` (dividing by 3600 converts joules to watt-hours).

**Numerical example:** A 1800 kg mid_ev climbing 200 m: `PE = 1800 × 9.81 × 200 = 3,531,600 J = 981 Wh ≈ 0.98 kWh` — a substantial addition for one trip.

**Proposed implementation [PROPOSED]:**
```python
def elevation_gain_energy_kwh(mass_kg: float, elevation_gain_m: float) -> float:
    return (mass_kg * 9.81 * elevation_gain_m) / 3600 / 1000
```

**Assumptions:** Ignores drivetrain efficiency losses converting battery energy to climbing force (real efficiency is maybe 85-90%, not 100%).

**Limitations:** Treats the whole climb as one lump regardless of grade steepness, which affects motor efficiency at different loads.

---

## 9. Elevation loss and regenerative braking

**Real-world intuition:** Going downhill, an EV can recover some energy by using the motor as a generator — but you don't get it all back; some is lost to heat, friction, and control limits.

**Physical reasoning:** Same PE formula as gain, but multiplied by a regen efficiency factor `η_regen` typically 60-70% for real EVs (varies by model and how aggressively regen is applied).

**Simplified model:** `regen_credit_kwh = (mass_kg × 9.81 × elevation_loss_m) / 3600 / 1000 × η_regen`, **subtracted** from total energy (this is why the schema explicitly says "not 1:1 with gain").

**Numerical example:** Same 200 m descent, η_regen = 0.65: `0.981 × 0.65 ≈ 0.64 kWh` recovered, not the full 0.98 kWh.

**Proposed implementation [PROPOSED]:**
```python
def elevation_loss_regen_kwh(mass_kg: float, elevation_loss_m: float, eta_regen: float = 0.65) -> float:
    return (mass_kg * 9.81 * elevation_loss_m) / 3600 / 1000 * eta_regen
```

**Assumptions:** Fixed regen efficiency regardless of descent steepness or speed — real regen efficiency varies (too steep/fast a descent and the friction brakes take over, capturing nothing).

**Limitations:** No cap ensuring regen credit can't exceed what the battery can physically accept (real batteries have charge-rate limits, especially when cold or nearly full).

---

## 10. Weather

**Real-world intuition:** Rain increases rolling resistance slightly (wet pavement, water displacement) and can indirectly reduce speeds.

**Physical reasoning:** A small, real, but genuinely minor physical effect compared to speed, elevation, or temperature — the schema explicitly labels this "minor."

**Simplified model:** `weather_factor = {clear: 1.0, rain: 1.03, heavy_rain: 1.07}` (illustrative, small multiplicative bump).

**Proposed implementation [PROPOSED]:**
```python
WEATHER_FACTOR = {"clear": 1.00, "rain": 1.03, "heavy_rain": 1.07}
```

**Assumptions:** Weather's effect on *average speed* (people drive slower in heavy rain) is handled separately via the sampling correlation in [[05 - Synthetic Dataset]], not here — again, watch for double-counting.

**Limitations:** Doesn't represent wind, which is often physically a bigger factor than rain for aerodynamic drag.

---

## 11. Random noise

**Real-world intuition:** No two real trips with identical inputs use *exactly* the same energy — driver behavior, exact traffic light timing, minor route variation, etc.

**Physical reasoning:** Not physics per se — this is a **statistical modeling choice** to prevent the target from being a deterministic function of the inputs.

**Simplified model:** Multiply or add a small random term, e.g., `energy_final = energy_computed × (1 + N(0, σ))` with σ small (e.g., 0.03-0.05).

**Proposed implementation [PROPOSED]:**
```python
import numpy as np
def apply_noise(energy_kwh: float, sigma: float = 0.04, rng=None) -> float:
    rng = rng or np.random.default_rng()
    return max(energy_kwh * (1 + rng.normal(0, sigma)), 0.0)
```

**Assumptions:** Noise is independent of the inputs (homoscedastic) — real-world variance likely *grows* with trip distance/complexity (heteroscedastic), which this simple model won't capture.

**Limitations:** This is purely a synthetic-data design choice, not something "true" about EVs — but it's essential; see [[08 - Leakage]] for why skipping it is dangerous.

---

## Why some effects are multiplicative and others are additive

**1. Intuition:** Multiplicative factors answer "what % more/less efficient am I driving right now?" — they scale the *rate* of energy use per km. Additive terms answer "what extra lump of energy did this one-time event cost?" — elevation change is a one-time potential-energy transaction, not a change in your ongoing rate of consumption.

**2. Physics:** Speed, temperature, traffic, and weather all modify the **power draw per unit of motion** (a rate). Elevation change is a **one-time energy transfer** (mgh) unrelated to how fast or slow you drove — a car that climbs 200 m uses the same extra ~1 kWh whether it takes 5 minutes or 50 minutes (ignoring efficiency-at-different-loads effects).

**3. Mathematics:** Efficiency factors compose naturally as products because each represents a fractional adjustment to a rate: `wh_per_km_effective = baseline × speed_factor × temp_factor × traffic_factor × weather_factor`. Elevation composes as a sum because it's a separate energy *quantity*, not a *rate multiplier*: `total_energy = distance_km × wh_per_km_effective / 1000 + elevation_gain_energy − elevation_loss_regen`.

**4. Numerical example:**
- baseline = 160 Wh/km, speed_factor = 1.2, temp_factor = 1.1, traffic_factor = 1.1, weather_factor = 1.03
- `wh_per_km_effective = 160 × 1.2 × 1.1 × 1.1 × 1.03 ≈ 239.4 Wh/km`
- distance = 40 km → driving energy = `40 × 239.4 / 1000 ≈ 9.58 kWh`
- elevation gain = 150 m, mass = 1800 kg → `+0.736 kWh`
- elevation loss = 100 m, η_regen = 0.65 → `−0.319 kWh`
- **total ≈ 9.58 + 0.736 − 0.319 = 9.99 kWh**

**5. Proposed code [PROPOSED]:**
```python
def total_energy_kwh(distance_km, baseline_wh_km, speed_f, temp_f, traffic_f, weather_f,
                      mass_kg, elev_gain_m, elev_loss_m, eta_regen=0.65):
    wh_per_km_effective = baseline_wh_km * speed_f * temp_f * traffic_f * weather_f
    driving_energy = distance_km * wh_per_km_effective / 1000
    gain_energy = (mass_kg * 9.81 * elev_gain_m) / 3600 / 1000
    loss_credit = (mass_kg * 9.81 * elev_loss_m) / 3600 / 1000 * eta_regen
    return driving_energy + gain_energy - loss_credit
```

**What would go wrong with the wrong structure:**
- **If elevation were multiplicative:** a 1000 km highway trip and a 5 km trip with the *same* elevation gain would get wildly different absolute energy penalties for climbing the *same physical hill* — physically wrong, since climbing a specific hill costs a specific amount of energy regardless of how far you drove before or after it.
- **If all efficiency factors were additive instead of multiplicative:** combining several *simultaneous* penalties (e.g., hot day + heavy traffic + high speed) would either overstate or understate their combined effect depending on scale, and — critically — an additive model can't represent the intuitive fact that a 20% speed penalty and a 20% traffic penalty compound (relatively) rather than simply stacking as flat Wh/km amounts regardless of baseline. Multiplicative factors naturally scale with the vehicle's baseline efficiency; additive ones would apply the same flat penalty to a `small_ev` and a `large_ev`, which is physically implausible (a less efficient vehicle usually degrades proportionally more, not by an identical fixed amount).

Next: [[04 - Target Generation]]
