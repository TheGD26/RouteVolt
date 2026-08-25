"""
Part 2 of the post-margin-fix baseline rerun + health check: runs the real,
unmodified plan_journey_via_graph pipeline (graph_planner's production entry
point -- the same one backend/app/api/routes.py's /route/optimize endpoint
calls) end to end for Chennai -> Nagercoil (mid_ev, half_load), at
battery_pct=8.0 -- the minimum feasible starting battery this session's
Part 1 (find_minimum_starting_battery) rerun found, so the exceeded_ceiling
check below is meaningful (a high-cushion battery_pct wouldn't stress that
logic) and comparable to the already-known stop 15/21 pattern.

Confirms the pipeline completes without unhandled exceptions/timeouts, and
captures wall-clock + the exact shape returned to the API layer for a quick
structural spot-check against RouteResults.jsx / RouteSummaryBar.jsx /
RouteMap.jsx's expectations.

    uv run python tests/run_health_check_2026-08-25.py
"""

import json
import logging
import sys
import time
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import graph_planner

ORIGIN_LAT, ORIGIN_LON = 13.0827, 80.2707   # Chennai
DEST_LAT, DEST_LON = 8.1833, 77.4119        # Nagercoil
VEHICLE_PROFILE = "mid_ev"
LOAD_STATE = "half_load"
BATTERY_PCT = 8.0  # matches this session's Part 1 minimum-feasible finding

OUT_PATH = Path(__file__).resolve().parent / f"health_check_output_2026-08-25_rerun_attempt2.json"


def main():
    log_records = []
    handler = logging.Handler()
    handler.emit = lambda record: log_records.append(record.getMessage())
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    prev_level = root_logger.level
    root_logger.setLevel(logging.INFO)

    print("=== Part 2 health check: plan_journey_via_graph, Chennai -> Nagercoil "
          f"(mid_ev, half_load, battery_pct={BATTERY_PCT}) ===")

    t0 = time.monotonic()
    crashed = False
    crash_trace = None
    result = None
    try:
        result = graph_planner.plan_journey_via_graph(
            origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
            dest_lat=DEST_LAT, dest_lon=DEST_LON,
            battery_pct=BATTERY_PCT,
            vehicle_profile=VEHICLE_PROFILE,
            load_state=LOAD_STATE,
            payload_kg=None,
        )
    except Exception:
        crashed = True
        crash_trace = traceback.format_exc()
    wall_clock_s = time.monotonic() - t0

    root_logger.removeHandler(handler)
    root_logger.setLevel(prev_level)

    print(f"\nwall_clock_s={wall_clock_s:.1f}")
    if crashed:
        print("CRASHED -- unhandled exception:")
        print(crash_trace)
    else:
        print(f"feasible={result.get('feasible')} status={result.get('status')} reason={result.get('reason')}")
        print(f"total_predicted_energy_kwh={result.get('total_predicted_energy_kwh')}")
        print(f"distance_km={result.get('distance_km')} duration_min={result.get('duration_min')}")
        print(f"final_battery_pct={result.get('final_battery_pct')}")
        print(f"stops={len(result.get('stops', []))} legs={len(result.get('legs', []))}")
        print(f"exceeded_ceiling_stops={result.get('exceeded_ceiling_stops')}")

    degraded_429 = sum(1 for m in log_records if "429" in m)
    degraded_timeout = sum(1 for m in log_records if "timed out" in m.lower() or "timeout" in m.lower())
    degraded_fallback = sum(1 for m in log_records if "falling back to straight-line" in m)
    print(f"\n429s={degraded_429} timeouts={degraded_timeout} straight-line-fallbacks={degraded_fallback}")

    output = {
        "meta": {
            "origin": [ORIGIN_LAT, ORIGIN_LON],
            "destination": [DEST_LAT, DEST_LON],
            "vehicle_profile": VEHICLE_PROFILE,
            "load_state": LOAD_STATE,
            "payload_kg": None,
            "battery_pct": BATTERY_PCT,
            "wall_clock_s": round(wall_clock_s, 1),
            "crashed": crashed,
            "crash_trace": crash_trace,
            "degraded_429_count": degraded_429,
            "degraded_timeout_count": degraded_timeout,
            "degraded_straight_line_fallback_count": degraded_fallback,
        },
        "result": result,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved full raw output to {OUT_PATH}")


if __name__ == "__main__":
    main()
