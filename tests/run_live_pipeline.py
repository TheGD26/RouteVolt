"""
Runs the real, unmodified plan_journey_via_graph pipeline end to end for
Chennai -> Nagercoil (same trip parameters as tests/inspect_path_shape.py's
diagnostic stand-in) and saves the raw real output to a JSON file.

Not a diagnostic stand-in like inspect_path_shape.py -- this calls the actual
production entry point (graph_planner.plan_journey_via_graph), which does
real get_route + real per-sub-leg traffic + real elevation for every capped
candidate edge (~1,980 of them at MAX_EDGES_PER_NODE=8), then real
plan_full_journey simulation over the chosen path. Expect several minutes of
wall-clock time against the public OSRM demo server -- this is the accepted,
signed-off-on cost of getting a real number, not a bug.

    uv run python tests/run_live_pipeline.py
"""

import json
import logging
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
BATTERY_PCT = 90.0  # matches inspect_path_shape.py's find_shortest_path call

OUT_PATH = Path(__file__).resolve().parent / f"live_pipeline_output_{date.today().isoformat()}_bounded.json"


def main():
    log_records = []
    handler = logging.Handler()
    handler.emit = lambda record: log_records.append(record.getMessage())
    graph_planner.logger.addHandler(handler)
    prev_level = graph_planner.logger.level
    graph_planner.logger.setLevel(logging.INFO)

    print("=== Live pipeline run: Chennai -> Nagercoil (mid_ev, half_load, MAX_EDGES_PER_NODE=8) ===")
    print(f"origin=({ORIGIN_LAT}, {ORIGIN_LON}) dest=({DEST_LAT}, {DEST_LON}) battery_pct={BATTERY_PCT}")
    print("Starting real plan_journey_via_graph -- expect several minutes (~1,980 real get_route calls)...")

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

    graph_planner.logger.removeHandler(handler)
    graph_planner.logger.setLevel(prev_level)

    refinement_log_lines = [msg for msg in log_records if "real per-edge refinement" in msg]
    drop_log_lines = [msg for msg in log_records if "dropping edge" in msg]

    print(f"\nwall_clock_s={wall_clock_s:.1f}")
    print(f"feasible={result.get('feasible')} status={result.get('status')} reason={result.get('reason')}")
    print(f"total_predicted_energy_kwh={result.get('total_predicted_energy_kwh')}")
    print(f"distance_km={result.get('distance_km')} duration_min={result.get('duration_min')}")
    print(f"final_battery_pct={result.get('final_battery_pct')}")
    print(f"stops={len(result.get('stops', []))} legs={len(result.get('legs', []))}")
    print(f"exceeded_ceiling_stops={result.get('exceeded_ceiling_stops')}")
    for line in refinement_log_lines:
        print(f"[graph_planner log] {line}")
    print(f"per-edge get_route drops observed: {len(drop_log_lines)}")
    for line in drop_log_lines:
        print(f"  [dropped] {line}")

    output = {
        "meta": {
            "origin": [ORIGIN_LAT, ORIGIN_LON],
            "destination": [DEST_LAT, DEST_LON],
            "vehicle_profile": VEHICLE_PROFILE,
            "load_state": LOAD_STATE,
            "payload_kg": None,
            "battery_pct": BATTERY_PCT,
            "max_edges_per_node": graph_planner.MAX_EDGES_PER_NODE,
            "wall_clock_s": round(wall_clock_s, 1),
            "refinement_log_lines": refinement_log_lines,
            "edge_drop_log_lines": drop_log_lines,
            "edge_drop_count": len(drop_log_lines),
        },
        "result": result,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved full raw output to {OUT_PATH}")


if __name__ == "__main__":
    main()
