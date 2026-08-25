"""
Regression tests for route_optimizer.py's physics-based energy-prediction
floor (_apply_energy_floor / MIN_ENERGY_FRACTION_OF_BASELINE).

Standalone script, not pytest (same reasoning as tests/test_graph_planner.py):
no network calls here -- this exercises the trained energy model directly
with fixed feature inputs, deterministic and fast.

    uv run python tests/test_route_optimizer.py

Background: graph_planner.build_station_graph evaluating a real
near-duplicate-coordinate station pair near Trichy (two chargers ~0.1km
apart -- "Kaveri Garage Piratiyur" -> "Trichy - Dindigul Main Road Kaveri
Garage") predicted -0.23 kWh -- a physically impossible negative energy cost
for a real, positive-distance road leg. Dijkstra then treated that as a
free/negative-cost edge, corrupting graph_planner's whole path search toward
routing through spurious near-duplicate stops instead of a sane corridor.
These tests reproduce that exact feature combination (captured from a live
run against the real station data, see graph_planner test suite) to confirm
it's now clipped and logged, not trusted.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import route_optimizer

FAILURES = []


def _check(label: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


# Real feature values captured from a live Chennai -> Nagercoil (mid_ev) run:
# routing_service.get_distance_matrix's real distance/duration for the
# Kaveri-Garage-Piratiyur -> Trichy-Dindigul-Main-Road-Kaveri-Garage pair
# (~0.1km apart, two chargers at effectively the same site), floored to
# routing_service.MIN_LEG_DISTANCE_KM=1.0km before being handed to the model
# -- see graph_planner.build_station_graph's edge-cost loop.
_DEGENERATE_LEG_KWARGS = dict(
    distance_km=1.0,
    road_type="arterial",
    vehicle_profile="mid_ev",
    load_state="half_load",
    total_mass_kg=route_optimizer.estimate_total_mass_kg("mid_ev", "half_load", None),
    avg_speed_kmh=34.17230769230769,
    elevation_gain_m=0.0,
    elevation_loss_m=5.0,
)

# A clearly non-degenerate, real-scale leg (50km highway hop) -- used to
# confirm the floor doesn't clip legitimate predictions.
_NORMAL_LEG_KWARGS = dict(
    distance_km=50.0,
    road_type="highway",
    vehicle_profile="mid_ev",
    load_state="half_load",
    total_mass_kg=route_optimizer.estimate_total_mass_kg("mid_ev", "half_load", None),
    avg_speed_kmh=85.0,
    elevation_gain_m=100.0,
    elevation_loss_m=80.0,
)


def _capture_warnings():
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    route_optimizer.logger.addHandler(handler)
    prev_level = route_optimizer.logger.level
    route_optimizer.logger.setLevel(logging.WARNING)
    return records, handler, prev_level


def _release_warnings(handler, prev_level):
    route_optimizer.logger.removeHandler(handler)
    route_optimizer.logger.setLevel(prev_level)


def test_degenerate_leg_prediction_is_never_negative_single():
    """predict_energy_kwh -- the single-leg path battery_simulator's real per-leg simulation uses."""
    print("\n=== predict_energy_kwh: degenerate near-duplicate-coordinate leg is clipped, not negative ===")
    trip_features = route_optimizer._build_trip_features(**_DEGENERATE_LEG_KWARGS)

    records, handler, prev_level = _capture_warnings()
    try:
        energy_kwh = route_optimizer.predict_energy_kwh(trip_features)
    finally:
        _release_warnings(handler, prev_level)

    print(f"  predicted energy_kwh={energy_kwh}")
    _check("energy_kwh is not negative", energy_kwh >= 0.0, f"got {energy_kwh}")
    _check("at least one warning was logged", len(records) >= 1, f"got {len(records)}")
    if records:
        message = records[0].getMessage()
        _check("warning mentions the physics-based floor", "floor" in message.lower(), message)


def test_degenerate_leg_prediction_is_never_negative_batch():
    """estimate_leg_energy_kwh_batch -- the path graph_planner.build_station_graph's edge loop uses."""
    print("\n=== estimate_leg_energy_kwh_batch: same degenerate leg, batched path ===")
    records, handler, prev_level = _capture_warnings()
    try:
        results = route_optimizer.estimate_leg_energy_kwh_batch([_DEGENERATE_LEG_KWARGS])
    finally:
        _release_warnings(handler, prev_level)

    print(f"  batch result={results}")
    _check("batch returned exactly one prediction", len(results) == 1, f"got {len(results)}")
    _check("energy_kwh is not negative", bool(results) and results[0] >= 0.0, f"got {results}")
    _check("at least one warning was logged", len(records) >= 1, f"got {len(records)}")


def test_normal_leg_prediction_is_unaffected_by_the_floor():
    """
    Regression guard: the floor must not clip legitimate predictions for a
    real-scale leg, only genuinely-impossible ones from degenerate inputs.
    """
    print("\n=== predict_energy_kwh: a normal 50km highway leg is not clipped ===")
    trip_features = route_optimizer._build_trip_features(**_NORMAL_LEG_KWARGS)

    records, handler, prev_level = _capture_warnings()
    try:
        energy_kwh = route_optimizer.predict_energy_kwh(trip_features)
    finally:
        _release_warnings(handler, prev_level)

    capacity_kwh = route_optimizer.VEHICLE_PROFILES["mid_ev"]["battery_capacity_kwh"]
    print(f"  predicted energy_kwh={energy_kwh} (capacity={capacity_kwh})")
    _check(
        "energy_kwh is a plausible positive value, well under one full battery",
        0.0 < energy_kwh < capacity_kwh,
        f"got {energy_kwh}",
    )
    _check("no floor warning was logged for a normal leg", len(records) == 0, f"got {len(records)} warnings")


if __name__ == "__main__":
    test_degenerate_leg_prediction_is_never_negative_single()
    test_degenerate_leg_prediction_is_never_negative_batch()
    test_normal_leg_prediction_is_unaffected_by_the_floor()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) did not pass: {FAILURES}")
        sys.exit(1)
    print("All checks passed.")
