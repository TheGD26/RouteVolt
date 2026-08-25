"""
Live end-to-end verification for the margin recalibration (Option C):
re-runs plan_journey_via_graph at the EXACT same parameters as the original
13-flagged-segment baseline (Chennai -> Nagercoil, mid_ev, half_load,
battery_pct=8.0 -- tests/fanout_flagged_segments_source_2026-08-24_BASELINE_
before_margin_fix.json) so the exceeded_ceiling_stops list can be compared
directly, before vs. after the SIMULATION_MARGIN_RATE/SIMULATION_MARGIN_
FLOOR_KWH change in battery_simulator.plan_charge_to_percent.

    uv run python tests/run_margin_recalibration_verification.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import graph_planner

ORIGIN_LAT, ORIGIN_LON = 13.0827, 80.2707   # Chennai
DEST_LAT, DEST_LON = 8.1833, 77.4119        # Nagercoil
VEHICLE_PROFILE = "mid_ev"
LOAD_STATE = "half_load"
BATTERY_PCT = 8.0  # matches the original baseline run exactly

OUT_PATH = Path(__file__).resolve().parent / "margin_recalibration_live_verification_2026-08-24.json"
BASELINE_PATH = Path(__file__).resolve().parent / "fanout_flagged_segments_source_2026-08-24_BASELINE_before_margin_fix.json"


def main():
    baseline = json.loads(BASELINE_PATH.read_text())
    baseline_stops = baseline["result"]["stops"]
    baseline_flagged_idx = [i for i, s in enumerate(baseline_stops) if s.get("exceeded_ceiling")]
    baseline_station_ids = {baseline_stops[i]["station_id"] for i in baseline_flagged_idx}

    print("=== Margin recalibration live verification: Chennai -> Nagercoil (mid_ev, half_load, battery_pct=8.0) ===")
    print(f"Baseline (before fix): {len(baseline_flagged_idx)} flagged stops, station_ids={sorted(baseline_station_ids)}")
    print("Running plan_journey_via_graph with the new margin mechanism...")

    t0 = time.monotonic()
    result = graph_planner.plan_journey_via_graph(
        origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
        dest_lat=DEST_LAT, dest_lon=DEST_LON,
        battery_pct=BATTERY_PCT,
        vehicle_profile=VEHICLE_PROFILE,
        load_state=LOAD_STATE,
        payload_kg=None,
    )
    wall_clock_s = time.monotonic() - t0

    stops = result.get("stops", [])
    flagged_idx = [i for i, s in enumerate(stops) if s.get("exceeded_ceiling")]
    flagged_station_ids = {stops[i]["station_id"] for i in flagged_idx}

    print(f"\nwall_clock_s={wall_clock_s:.1f}")
    print(f"feasible={result.get('feasible')} status={result.get('status')} reason={result.get('reason')}")
    print(f"stop_count={len(stops)}")
    print(f"flagged stop-list indices (0-based)={flagged_idx} count={len(flagged_idx)}")
    print(f"flagged station_ids={sorted(flagged_station_ids)}")
    for i in flagged_idx:
        s = stops[i]
        print(f"  [{i}] station_id={s.get('station_id')} name={s.get('station_name')!r} "
              f"battery_pct_on_arrival={s.get('battery_pct_on_arrival')} charge_to_percent={s.get('charge_to_percent')}")

    resolved = baseline_station_ids - flagged_station_ids
    still_firing = baseline_station_ids & flagged_station_ids
    new_unexpected = flagged_station_ids - baseline_station_ids

    print(f"\n--- comparison vs. baseline ---")
    print(f"resolved (flagged before, not flagged now): {sorted(resolved)} (count={len(resolved)})")
    print(f"still firing (flagged before AND now): {sorted(still_firing)} (count={len(still_firing)})")
    print(f"NEW/unexpected (flagged now, NOT flagged before): {sorted(new_unexpected)} (count={len(new_unexpected)})")
    if new_unexpected:
        print("!!! UNEXPECTED SIDE EFFECT: new station_ids flagged that weren't in the original 12/13 !!!")

    output = {
        "meta": {
            "origin": [ORIGIN_LAT, ORIGIN_LON], "destination": [DEST_LAT, DEST_LON],
            "vehicle_profile": VEHICLE_PROFILE, "load_state": LOAD_STATE,
            "battery_pct": BATTERY_PCT, "wall_clock_s": round(wall_clock_s, 1),
        },
        "baseline_flagged_station_ids": sorted(baseline_station_ids),
        "after_fix_flagged_station_ids": sorted(flagged_station_ids),
        "resolved_station_ids": sorted(resolved),
        "still_firing_station_ids": sorted(still_firing),
        "new_unexpected_station_ids": sorted(new_unexpected),
        "result": result,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved full output to {OUT_PATH}")


if __name__ == "__main__":
    main()
