"""
Regression tests for the phantom-stop battery-clamp fix in battery_simulator.py.

Standalone script, not pytest (same reasoning as tests/test_graph_planner.py):

    uv run python tests/test_battery_simulator.py

Background: plan_full_journey's main loop used to set
`remaining_pct = stop_entry["charge_to_percent"]` unconditionally after every
stop, including when the graph-chosen stop turned out not to need any real
charging once the detailed leg-by-leg simulation ran (charge_to_percent came
back below the battery already on hand on arrival -- a "phantom stop"). That
silently discarded real battery charge, understating every subsequent leg's
starting battery. The fix clamps the battery carried forward to never drop
below battery_pct_on_arrival, and _build_stop_entry now flags whether a stop
was a real charging event via "charging_required" -- consumed by
graph_planner.plan_journey_via_graph to filter phantom stops out of the
*returned* stop list only (see test_graph_planner.py for that half of the
fix); plan_full_journey's own stop_log stays unfiltered on purpose, since
graph_planner._log_exceeded_ceiling_drift depends on it staying positionally
aligned with the Dijkstra path.

No network/DB calls: _rank_reachable_candidates, plan_charge_to_percent, and
the per-leg elevation/traffic/energy helpers are all patched with synthetic,
deterministic values.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import battery_simulator
from backend.app.services.route_optimizer import VEHICLE_PROFILES

FAILURES = []


def _check(label: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def test_build_stop_entry_charging_required_flag():
    """
    _build_stop_entry's new "charging_required" field must reuse the exact
    energy_needed_kwh computation it already has (charge_to_percent -
    battery_pct_on_arrival, floored at 0), not re-derive it -- so this pins
    down the boolean's value at the two sides of that same boundary:
    charge_to_percent at/below battery_pct_on_arrival (no real charging
    needed) vs. above it (real charging needed).
    """
    print("\n=== _build_stop_entry: charging_required reflects energy_needed_kwh (synthetic) ===")

    station = {"lat": 10.0, "lon": 80.0, "id": None, "station_name": "Synthetic", "power_kw": 50.0}

    # Phantom case: the graph-chosen stop's charge target (34.2%) is below
    # the battery already on hand on arrival (69.48%) -- the exact shape
    # from the real Chennai->Madurai screenshot this fix targets.
    with patch.object(battery_simulator, "_rank_reachable_candidates", return_value=[]), \
         patch.object(battery_simulator, "plan_charge_to_percent",
                      return_value={"charge_to_percent": 34.2, "exceeded_ceiling": False}):
        phantom_entry = battery_simulator._build_stop_entry(
            station=station, leg_index=1, position_before_segment=(9.9, 79.9),
            battery_pct_before_segment=80.0, battery_pct_on_arrival=69.48,
            remaining_legs=[], vehicle_profile="mid_ev", load_state="half_load",
            payload_kg=None, safety_margin=0.12,
        )
    _check(
        "charging_required is False when charge_to_percent <= battery_pct_on_arrival",
        phantom_entry["charging_required"] is False,
        f"charge_to_percent={phantom_entry['charge_to_percent']} "
        f"battery_pct_on_arrival={phantom_entry['battery_pct_on_arrival']}",
    )

    # Normal case: charge target is genuinely above what's on the battery.
    with patch.object(battery_simulator, "_rank_reachable_candidates", return_value=[]), \
         patch.object(battery_simulator, "plan_charge_to_percent",
                      return_value={"charge_to_percent": 75.0, "exceeded_ceiling": False}):
        real_entry = battery_simulator._build_stop_entry(
            station=station, leg_index=1, position_before_segment=(9.9, 79.9),
            battery_pct_before_segment=80.0, battery_pct_on_arrival=69.48,
            remaining_legs=[], vehicle_profile="mid_ev", load_state="half_load",
            payload_kg=None, safety_margin=0.12,
        )
    _check(
        "charging_required is True when charge_to_percent > battery_pct_on_arrival",
        real_entry["charging_required"] is True,
        f"charge_to_percent={real_entry['charge_to_percent']} "
        f"battery_pct_on_arrival={real_entry['battery_pct_on_arrival']}",
    )


def test_plan_full_journey_phantom_stop_battery_clamp():
    """
    Drives plan_full_journey through one phantom stop (charge target below
    the real battery on arrival) followed by one normal stop, patching
    _build_stop_entry to return hand-crafted stop_entry dicts so this test
    is isolated to plan_full_journey's own loop (leg energy accounting +
    the battery clamp + the stop_log append), not _build_stop_entry's own
    logic (covered separately above) or any network/model call.

    Confirms in one pass:
    - stop_log length/order is UNCHANGED from before the fix (still
      includes the phantom stop -- filtering is graph_planner's job, not
      plan_full_journey's, per the positional-correlation constraint
      _log_exceeded_ceiling_drift depends on).
    - the battery carried forward after the phantom stop is clamped to
      battery_pct_on_arrival (70.0%), not dropped to the lower
      charge_to_percent (34.2%).
    - the normal stop right after it is completely unaffected (charge_to_
      percent used as-is, since it's already above arrival) -- the
      pre-existing behavior for a real charging stop is unchanged.
    - leg_index values on both stops are correct despite the phantom stop
      in between (leg accounting runs independently of stop_log).
    """
    print("\n=== plan_full_journey: phantom-stop battery clamp + unfiltered stop_log (synthetic) ===")

    capacity_kwh = VEHICLE_PROFILES["mid_ev"]["battery_capacity_kwh"]

    def make_leg(n):
        return {"start": (13.0 - n * 0.1, 80.0), "end": (13.0 - n * 0.1 - 0.05, 79.95),
                "distance_km": 10.0, "duration_s": 600.0}

    route_leg_groups = [[make_leg(0)], [make_leg(1)], [make_leg(2)]]
    stations = [
        {"lat": 12.9, "lon": 80.0, "id": 1, "station_name": "Phantom"},
        {"lat": 12.8, "lon": 80.0, "id": 2, "station_name": "Real"},
    ]

    # leg0: 10% of capacity -> 80% starting battery arrives at stop "Phantom" with 70%.
    # leg1: 5% of capacity  -> arrives at stop "Real" with 65%.
    # leg2: 5% of capacity  -> final leg after "Real" recharges.
    leg_energies_kwh = [0.10 * capacity_kwh, 0.05 * capacity_kwh, 0.05 * capacity_kwh]

    def fake_build_stop_entry(*, station, leg_index, position_before_segment,
                               battery_pct_before_segment, battery_pct_on_arrival,
                               remaining_legs, vehicle_profile, load_state, payload_kg, safety_margin):
        if station["station_name"] == "Phantom":
            charge_to_percent = 34.2  # deliberately below battery_pct_on_arrival (70.0)
        else:
            charge_to_percent = 75.0  # deliberately above battery_pct_on_arrival (65.0)
        return {
            "leg_index": leg_index,
            "location": {"lat": station["lat"], "lng": station["lon"]},
            "battery_pct_on_arrival": round(battery_pct_on_arrival, 2),
            "station_id": station.get("id"),
            "station_name": station.get("station_name"),
            "charge_to_percent": charge_to_percent,
            "exceeded_ceiling": False,
            "charging_required": charge_to_percent > battery_pct_on_arrival,
            "estimated_charge_duration_min": 0.0,
            "expected_wait_minutes": 0.0,
            "queue_length": 0,
            "alternatives": [],
            "backup_stations": [],
        }

    with patch.object(battery_simulator, "_leg_elevation",
                      return_value={"elevation_gain_m": 0.0, "elevation_loss_m": 0.0, "elevation_source": "synthetic"}), \
         patch.object(battery_simulator, "_leg_traffic",
                      return_value={"congestion_level": 0.0, "congestion_source": "synthetic"}), \
         patch.object(battery_simulator, "_leg_energy_kwh", side_effect=leg_energies_kwh), \
         patch.object(battery_simulator, "_build_stop_entry", side_effect=fake_build_stop_entry):
        result = battery_simulator.plan_full_journey(
            route_leg_groups=route_leg_groups, stops=stations, battery_pct=80.0,
            vehicle_profile="mid_ev", load_state="half_load",
        )

    print(f"  status={result['status']} final_battery_pct={result['final_battery_pct']}")
    for s in result["stops"]:
        print(f"    stop={s['station_name']} leg_index={s['leg_index']} "
              f"battery_pct_on_arrival={s['battery_pct_on_arrival']} charge_to_percent={s['charge_to_percent']} "
              f"charging_required={s['charging_required']}")

    _check("status is ok", result["status"] == "ok", f"got {result['status']}")
    _check(
        "stop_log is UNFILTERED -- both the phantom and the real stop are present",
        len(result["stops"]) == 2,
        f"got {len(result['stops'])} stops",
    )

    if len(result["stops"]) == 2:
        phantom, real = result["stops"]

        _check("phantom stop retains its (unfilterd) charge_to_percent=34.2", phantom["charge_to_percent"] == 34.2)
        _check("phantom stop's battery_pct_on_arrival is 70.0", abs(phantom["battery_pct_on_arrival"] - 70.0) < 1e-6,
               f"got {phantom['battery_pct_on_arrival']}")
        _check("phantom stop is flagged charging_required=False", phantom["charging_required"] is False)
        _check("phantom stop's leg_index is 1", phantom["leg_index"] == 1, f"got {phantom['leg_index']}")

        _check(
            "real stop's battery_pct_on_arrival is 65.0 -- the clamp carried forward the phantom "
            "stop's real 70% (not the discarded 34.2%) before leg1's 5% draw",
            abs(real["battery_pct_on_arrival"] - 65.0) < 1e-6,
            f"got {real['battery_pct_on_arrival']}",
        )
        _check("real stop's charge_to_percent (75.0) is unchanged from the normal, pre-fix behavior",
               real["charge_to_percent"] == 75.0)
        _check("real stop is flagged charging_required=True", real["charging_required"] is True)
        _check("real stop's leg_index is 2 (unaffected by the phantom stop ahead of it)",
               real["leg_index"] == 2, f"got {real['leg_index']}")

    _check(
        "final_battery_pct is 70.0 (75% charge - 5% leg2 draw) -- reflects the clamped "
        "trajectory throughout, not one corrupted by the discarded phantom charge target",
        abs(result["final_battery_pct"] - 70.0) < 1e-6,
        f"got {result['final_battery_pct']}",
    )


def test_plan_full_journey_normal_stop_unaffected_by_clamp():
    """
    A single normal stop (charge_to_percent comfortably above
    battery_pct_on_arrival) must behave exactly as before the fix: the
    clamp's max() is a no-op whenever charge_to_percent is already the
    larger value, so remaining_pct == charge_to_percent, same as the old
    unconditional assignment.
    """
    print("\n=== plan_full_journey: normal stop (charge_to_percent > arrival) fully unchanged (synthetic) ===")

    capacity_kwh = VEHICLE_PROFILES["mid_ev"]["battery_capacity_kwh"]

    def make_leg(n):
        return {"start": (13.0 - n * 0.1, 80.0), "end": (13.0 - n * 0.1 - 0.05, 79.95),
                "distance_km": 10.0, "duration_s": 600.0}

    route_leg_groups = [[make_leg(0)], [make_leg(1)]]
    stations = [{"lat": 12.9, "lon": 80.0, "id": 1, "station_name": "Normal"}]
    leg_energies_kwh = [0.20 * capacity_kwh, 0.10 * capacity_kwh]  # 80% -> 60% arrival, then charge to 80%

    def fake_build_stop_entry(*, station, leg_index, battery_pct_on_arrival, **_kwargs):
        return {
            "leg_index": leg_index,
            "location": {"lat": station["lat"], "lng": station["lon"]},
            "battery_pct_on_arrival": round(battery_pct_on_arrival, 2),
            "station_id": station.get("id"),
            "station_name": station.get("station_name"),
            "charge_to_percent": 80.0,
            "exceeded_ceiling": False,
            "charging_required": True,
            "estimated_charge_duration_min": 5.0,
            "expected_wait_minutes": 0.0,
            "queue_length": 0,
            "alternatives": [],
            "backup_stations": [],
        }

    with patch.object(battery_simulator, "_leg_elevation",
                      return_value={"elevation_gain_m": 0.0, "elevation_loss_m": 0.0, "elevation_source": "synthetic"}), \
         patch.object(battery_simulator, "_leg_traffic",
                      return_value={"congestion_level": 0.0, "congestion_source": "synthetic"}), \
         patch.object(battery_simulator, "_leg_energy_kwh", side_effect=leg_energies_kwh), \
         patch.object(battery_simulator, "_build_stop_entry", side_effect=fake_build_stop_entry):
        result = battery_simulator.plan_full_journey(
            route_leg_groups=route_leg_groups, stops=stations, battery_pct=80.0,
            vehicle_profile="mid_ev", load_state="half_load",
        )

    _check("status is ok", result["status"] == "ok", f"got {result['status']}")
    _check("stop is present", len(result["stops"]) == 1, f"got {len(result['stops'])}")
    if result["stops"]:
        _check(
            "remaining_pct after the stop equals charge_to_percent (80.0), the clamp is a no-op here",
            abs(result["final_battery_pct"] - 70.0) < 1e-6,  # 80.0 charge - 10% leg2 draw = 70.0
            f"got final_battery_pct={result['final_battery_pct']}",
        )


if __name__ == "__main__":
    test_build_stop_entry_charging_required_flag()
    test_plan_full_journey_phantom_stop_battery_clamp()
    test_plan_full_journey_normal_stop_unaffected_by_clamp()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s) did not pass: {FAILURES}")
        sys.exit(1)
    print("All checks passed.")
