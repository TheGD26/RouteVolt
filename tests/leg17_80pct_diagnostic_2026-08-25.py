"""
Diagnostic for the "80% starting battery fails at leg 17" report.

Obtains the raw plan_journey_via_graph output for Chennai -> Nagercoil
(mid_ev, half_load) at battery_pct=80.0 -- the graph_planner function that
backend/app/api/routes.py's POST /route/optimize directly wraps (route_out's
"charging_plan" = journey["stops"], "gap" = journey["gap"], unmodified --
see routes.py:95-107), so this call's output is a faithful stand-in for the
live 80% API response body. No saved copy of that response exists anywhere
in the repo (checked tests/*.json for battery_pct in [75,85]: zero hits) and
there are no persisted backend request logs (main.py only logs to stdout),
so this live call is the only way to obtain it.

Mirrors tests/run_health_check_2026-08-25.py's pattern exactly, at
battery_pct=80.0 instead of 8.0.

    uv run python tests/leg17_80pct_diagnostic_2026-08-25.py
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
BATTERY_PCT = 80.0

OUT_PATH = Path(__file__).resolve().parent / f"leg17_80pct_diagnostic_output_{date.today().isoformat()}.json"


def main():
    log_records = []
    handler = logging.Handler()
    handler.emit = lambda record: log_records.append(record.getMessage())
    graph_planner.logger.addHandler(handler)
    prev_level = graph_planner.logger.level
    graph_planner.logger.setLevel(logging.INFO)

    print("=== leg17/80% diagnostic: plan_journey_via_graph, Chennai -> Nagercoil "
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

    graph_planner.logger.removeHandler(handler)
    graph_planner.logger.setLevel(prev_level)

    print(f"\nwall_clock_s={wall_clock_s:.1f}")
    if crashed:
        print("CRASHED -- unhandled exception:")
        print(crash_trace)
    else:
        print(f"feasible={result.get('feasible')} status={result.get('status')} reason={result.get('reason')}")
        print(f"gap={json.dumps(result.get('gap'))}")
        print(f"stops={len(result.get('stops', []))} legs={len(result.get('legs', []))}")

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
