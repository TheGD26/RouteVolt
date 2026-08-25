"""
Local-only (no OSRM/API calls) reconstruction of build_station_graph's node
ordering and per-node candidate windows for the fan-out cap diagnostic --
mirrors graph_planner.py's own logic (haversine dist_from_origin_km sort,
DAG-order gap break at vehicle_range_km) exactly, using candidates_top250.json
(the already-saved 250-candidate pool) so this step costs nothing new.

Loads the fresh flagged-segment source (tests/fanout_flagged_segments_source_*.json,
produced by run_fanout_flagged_segments.py) to find each flagged stop's real
node identity, then reports:
  - each flagged origin's index in the node list
  - its DAG-order candidate window (which js survive the range-gap break,
    BEFORE the real-distance top-8 cap -- this is what step 2's OSRM /table
    call needs to be run against)
  - which of those window candidates are "unused" (not already a stop on the
    chosen 31-stop path) and geographically near the segment's midpoint --
    the local proxy for "the better energy-fit candidate" this diagnostic
    needs to check against the real top-8.

    uv run python tests/fanout_local_reconstruction.py <fanout_flagged_segments_source_file.json>
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.route_optimizer import VEHICLE_PROFILES, haversine_km

ORIGIN_LAT, ORIGIN_LON = 13.0827, 80.2707   # Chennai
DEST_LAT, DEST_LON = 8.1833, 77.4119        # Nagercoil
VEHICLE_PROFILE = "mid_ev"

CANDIDATES_PATH = Path(__file__).resolve().parents[1] / "candidates_top250.json"


def vehicle_range_km(profile: str) -> float:
    p = VEHICLE_PROFILES[profile]
    return (p["battery_capacity_kwh"] * 1000) / p["efficiency_baseline_wh_per_km"]


def build_node_list():
    """Mirrors build_station_graph's node construction exactly (graph_planner.py:374-395)."""
    candidates = json.loads(CANDIDATES_PATH.read_text())
    origin_node = {"lat": ORIGIN_LAT, "lon": ORIGIN_LON, "dist_from_origin_km": 0.0, "kind": "origin"}
    dest_dist = haversine_km(ORIGIN_LAT, ORIGIN_LON, DEST_LAT, DEST_LON)
    dest_node = {"lat": DEST_LAT, "lon": DEST_LON, "dist_from_origin_km": dest_dist, "kind": "destination"}

    sorted_candidates = sorted(
        (
            {
                **c, "lat": c["latitude"], "lon": c["longitude"],
                "dist_from_origin_km": haversine_km(ORIGIN_LAT, ORIGIN_LON, c["latitude"], c["longitude"]),
                "kind": "station",
            }
            for c in candidates
        ),
        key=lambda c: c["dist_from_origin_km"],
    )
    return [origin_node, *sorted_candidates, dest_node]


def find_node_index(nodes, station_id=None, lat=None, lon=None, tol=1e-4):
    for idx, n in enumerate(nodes):
        if station_id is not None and n.get("id") == station_id:
            return idx
        if lat is not None and lon is not None and abs(n["lat"] - lat) < tol and abs(n["lon"] - lon) < tol:
            return idx
    return None


def candidate_window(nodes, i, range_km):
    """Mirrors build_station_graph's stage-1 DAG-order gap break (graph_planner.py:426-459),
    WITHOUT the real-distance snap-guard (needs OSRM) and WITHOUT the top-8 cap."""
    window = []
    for j in range(i + 1, len(nodes)):
        approx_gap_km = nodes[j]["dist_from_origin_km"] - nodes[i]["dist_from_origin_km"]
        if approx_gap_km > range_km:
            break
        window.append(j)
    return window


def main():
    if len(sys.argv) < 2:
        print("usage: fanout_local_reconstruction.py <fanout_flagged_segments_source_file.json>")
        sys.exit(1)

    source = json.loads(Path(sys.argv[1]).read_text())
    result = source["result"]
    stops = result.get("stops", [])
    flagged_idx = source["meta"]["flagged_stop_indices"]

    nodes = build_node_list()
    range_km = vehicle_range_km(VEHICLE_PROFILE)
    print(f"vehicle_range_km({VEHICLE_PROFILE}) = {range_km:.1f}")
    print(f"total nodes = {len(nodes)} (1 origin + {len(nodes) - 2} candidates + 1 destination)")

    used_station_ids = {s.get("station_id") for s in stops}

    report = []
    for stop_list_idx in flagged_idx:
        s = stops[stop_list_idx]
        loc = s.get("location", {})
        node_idx = find_node_index(nodes, station_id=s.get("station_id"), lat=loc.get("lat"), lon=loc.get("lng"))
        if node_idx is None:
            print(f"[stop {stop_list_idx}] station_id={s.get('station_id')} name={s.get('station_name')!r} "
                  f"-- NOT FOUND in reconstructed node list (may not be in the 250-candidate pool, e.g. "
                  f"if it's actually a duplicate/edge case) -- flagging as unresolved for this node.")
            report.append({"stop_list_idx": stop_list_idx, "station_id": s.get("station_id"),
                            "node_idx": None, "window": None})
            continue

        window = candidate_window(nodes, node_idx, range_km)
        # Next stop (or destination) -- the segment's arrival endpoint.
        next_stop = stops[stop_list_idx + 1] if stop_list_idx + 1 < len(stops) else None
        arrival_lat, arrival_lon = (
            (next_stop["location"]["lat"], next_stop["location"]["lng"]) if next_stop
            else (DEST_LAT, DEST_LON)
        )
        dep_lat, dep_lon = loc.get("lat"), loc.get("lng")
        direct_km = haversine_km(dep_lat, dep_lon, arrival_lat, arrival_lon)

        # Unused, on-corridor candidates in this node's window, ranked by
        # detour_km added to the direct dep->arrival hop (local proxy for
        # "would bisect this segment well") -- same detour formula
        # filter_candidate_stations already uses.
        window_detail = []
        for j in window:
            n = nodes[j]
            if n.get("id") in used_station_ids:
                continue
            detour_km = haversine_km(dep_lat, dep_lon, n["lat"], n["lon"]) + haversine_km(n["lat"], n["lon"], arrival_lat, arrival_lon) - direct_km
            window_detail.append({
                "node_idx": j, "station_id": n.get("id"), "station_name": n.get("station_name"),
                "lat": n["lat"], "lon": n["lon"], "detour_km": round(detour_km, 2),
            })
        window_detail.sort(key=lambda w: w["detour_km"])

        print(f"\n[stop {stop_list_idx}] node_idx={node_idx} station_id={s.get('station_id')} "
              f"name={s.get('station_name')!r} battery_pct_on_arrival={s.get('battery_pct_on_arrival')} "
              f"charge_to_percent={s.get('charge_to_percent')}")
        print(f"  segment: dep=({dep_lat},{dep_lon}) -> arrival=({arrival_lat},{arrival_lon}) direct_km={direct_km:.1f}")
        print(f"  DAG-order window size (pre-snap-guard, pre-top8) = {len(window)}")
        print(f"  best unused on-corridor candidates (lowest detour_km first, top 5 shown):")
        for w in window_detail[:5]:
            print(f"    node_idx={w['node_idx']} id={w['station_id']} {w['station_name']!r} detour_km={w['detour_km']}")

        report.append({
            "stop_list_idx": stop_list_idx, "station_id": s.get("station_id"), "node_idx": node_idx,
            "window_size": len(window), "window_node_indices": window,
            "best_unused_candidates": window_detail[:5],
        })

    out_path = Path(sys.argv[1]).with_name(Path(sys.argv[1]).stem + "_local_reconstruction.json")
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved local reconstruction to {out_path}")

    total_window_coords = sum(r["window_size"] + 1 for r in report if r.get("window_size") is not None)
    print(f"\nTotal coordinates across all flagged-node windows (for step-2 OSRM /table sizing) = {total_window_coords}")


if __name__ == "__main__":
    main()
