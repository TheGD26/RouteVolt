"""
Runs the new find_minimum_starting_battery against Chennai -> Nagercoil
(mid_ev, half_load) end to end, real APIs, one real graph build reused across
the whole binary search. Reports total wall-clock, iteration count, the found
minimum, and the monotonicity sanity check find_minimum_starting_battery
performs internally (just below/above the found minimum, on the same reused
graph).

    uv run python tests/run_find_minimum_starting_battery.py
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
TOLERANCE_PCT = 1.0

OUT_PATH = Path(__file__).resolve().parent / f"find_minimum_starting_battery_output_{date.today().isoformat()}.json"


def main():
    print("=== find_minimum_starting_battery: Chennai -> Nagercoil (mid_ev, half_load) ===")
    print("Building the station graph once, then binary-searching starting battery -- "
          "expect the graph build (~250-275s) to dominate, iterations should be fast...")

    t0 = time.monotonic()
    result = graph_planner.find_minimum_starting_battery(
        origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
        dest_lat=DEST_LAT, dest_lon=DEST_LON,
        vehicle_profile=VEHICLE_PROFILE, load_state=LOAD_STATE,
        tolerance_pct=TOLERANCE_PCT,
    )
    wall_clock_s = time.monotonic() - t0

    print(f"\nwall_clock_s={wall_clock_s:.1f}")
    print(f"feasible={result.get('feasible')} reason={result.get('reason')}")
    print(f"iterations={result.get('iterations')}")
    print(f"minimum_starting_battery_pct={result.get('minimum_starting_battery_pct')}")
    print(f"stop_count={result.get('stop_count')} final_battery_pct={result.get('final_battery_pct')}")
    print(f"total_energy_kwh={result.get('total_energy_kwh')}")
    mc = result.get("monotonicity_check")
    if mc:
        print(f"\nmonotonicity_check: below={mc['below_pct']}% feasible={mc['below_feasible']}  "
              f"above={mc['above_pct']}% feasible={mc['above_feasible']}  holds={mc['holds']}")

    output = {
        "meta": {
            "origin": [ORIGIN_LAT, ORIGIN_LON], "destination": [DEST_LAT, DEST_LON],
            "vehicle_profile": VEHICLE_PROFILE, "load_state": LOAD_STATE,
            "tolerance_pct": TOLERANCE_PCT, "wall_clock_s": round(wall_clock_s, 1),
        },
        "result": result,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved full output to {OUT_PATH}")


if __name__ == "__main__":
    main()
