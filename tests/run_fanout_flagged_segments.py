"""
Regenerates a fresh, current plan_journey_via_graph run for Chennai -> Nagercoil
(mid_ev, half_load) at battery_pct=8.0 -- the minimum starting battery found by
find_minimum_starting_battery in tests/find_minimum_starting_battery_output_2026-08-24.json
-- so the exceeded_ceiling flagged-segment list used for the fan-out cap
diagnostic reflects the code as it exists right now, not a stale prior run.

plan_journey_via_graph (unlike find_minimum_starting_battery's lean feasibility
check) runs the full plan_full_journey simulator, which does track
exceeded_ceiling per stop -- that's the field this diagnostic needs.

    uv run python tests/run_fanout_flagged_segments.py
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import graph_planner

ORIGIN_LAT, ORIGIN_LON = 13.0827, 80.2707   # Chennai
DEST_LAT, DEST_LON = 8.1833, 77.4119        # Nagercoil
VEHICLE_PROFILE = "mid_ev"
LOAD_STATE = "half_load"
BATTERY_PCT = 8.0  # minimum found by find_minimum_starting_battery last run

OUT_PATH = Path(__file__).resolve().parent / f"fanout_flagged_segments_source_{date.today().isoformat()}.json"


def main():
    print("=== Fresh plan_journey_via_graph run: Chennai -> Nagercoil (mid_ev, half_load, battery_pct=8.0) ===")
    print("Regenerating to get a current exceeded_ceiling flagged-segment list -- expect ~250-275s+...")

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

    print(f"\nwall_clock_s={wall_clock_s:.1f}")
    print(f"feasible={result.get('feasible')} status={result.get('status')} reason={result.get('reason')}")
    print(f"stop_count={len(stops)}")
    print(f"exceeded_ceiling_stops (node indices)={result.get('exceeded_ceiling_stops')}")
    print(f"flagged stop-list indices (0-based)={flagged_idx} count={len(flagged_idx)}")
    for i in flagged_idx:
        s = stops[i]
        print(f"  [{i}] station_id={s.get('station_id')} name={s.get('station_name')!r} "
              f"loc={s.get('location')} battery_pct_on_arrival={s.get('battery_pct_on_arrival')} "
              f"charge_to_percent={s.get('charge_to_percent')}")

    output = {
        "meta": {
            "origin": [ORIGIN_LAT, ORIGIN_LON], "destination": [DEST_LAT, DEST_LON],
            "vehicle_profile": VEHICLE_PROFILE, "load_state": LOAD_STATE,
            "battery_pct": BATTERY_PCT, "wall_clock_s": round(wall_clock_s, 1),
            "flagged_stop_indices": flagged_idx,
        },
        "result": result,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved full output to {OUT_PATH}")


if __name__ == "__main__":
    main()
