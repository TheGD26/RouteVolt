"""
Stop 21 structural diagnostic, closing item: does node 144 (Melur Zeon X A.N
Energy, the destination C for stop 21's segment) survive Hotel Arun Cafe's
(node_idx 123, station_id 435) OWN real-driving-distance top-8 cap?

Mirrors fanout_local_reconstruction.py's node list / DAG-order window
construction (free, local) and fanout_osrm_ranking_check.py's OSRM /table
ranking check (1-2 real calls), but sourced from node 123 specifically --
neither prior diagnostic ever computed node 123's own window, only node 122's
(the flagged stop 21 itself).

    uv run python tests/stop21_hotel_arun_cafe_own_window_check.py
"""

import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import OSRM_BASE_URL
from tests.fanout_local_reconstruction import build_node_list, candidate_window, vehicle_range_km

TARGET_NODE_IDX = 123   # Hotel Arun Cafe, station_id 435
C_NODE_IDX = 144         # Melur Zeon X A.N Energy Solutions, station_id 195 (stop 21's direct C)
MAX_EDGES_PER_NODE = 8
MAX_COORDS_PER_REQUEST = 100
VEHICLE_PROFILE = "mid_ev"


def osrm_one_to_many(source, destinations):
    coords = [source] + destinations
    coords_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    n = len(destinations)
    dest_indices = ";".join(str(i) for i in range(1, n + 1))
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coords_str}"
    response = requests.get(
        url,
        params={"annotations": "distance", "sources": "0", "destinations": dest_indices},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM table returned {payload.get('code')}: {payload}")
    return [d / 1000.0 if d is not None else None for d in payload["distances"][0]]


def main():
    nodes = build_node_list()
    range_km = vehicle_range_km(VEHICLE_PROFILE)
    window = candidate_window(nodes, TARGET_NODE_IDX, range_km)

    origin_node = nodes[TARGET_NODE_IDX]
    print(f"node_idx={TARGET_NODE_IDX} station_id={origin_node.get('id')} name={origin_node.get('station_name')!r}")
    print(f"vehicle_range_km({VEHICLE_PROFILE}) = {range_km:.1f}")
    print(f"DAG-order window size (pre-top8) = {len(window)} -- range {window[0]}..{window[-1]}" if window else "EMPTY WINDOW")
    print(f"is C (node_idx={C_NODE_IDX}) inside the DAG-order window? {C_NODE_IDX in window}")

    origin_coord = (origin_node["lat"], origin_node["lon"])
    window_coords = [(nodes[j]["lat"], nodes[j]["lon"]) for j in window]

    chunk_size = MAX_COORDS_PER_REQUEST - 1
    distances = []
    n_requests = 0
    for i in range(0, len(window_coords), chunk_size):
        chunk = window_coords[i:i + chunk_size]
        distances.extend(osrm_one_to_many(origin_coord, chunk))
        n_requests += 1
    print(f"OSRM /table requests made: {n_requests}")

    ranked = sorted(zip(window, distances), key=lambda pair: (pair[1] is None, pair[1]))
    top8 = ranked[:MAX_EDGES_PER_NODE]

    print(f"\nHotel Arun Cafe's (node 123) REAL top-{MAX_EDGES_PER_NODE} by driving distance:")
    for rank, (j, d) in enumerate(top8, start=1):
        n = nodes[j]
        flag = "  <-- C (Melur)" if j == C_NODE_IDX else ""
        print(f"  #{rank} node_idx={j} id={n.get('id')} {n.get('station_name')!r} {d:.2f}km{flag}")

    c_rank = next((r for r, (j, _d) in enumerate(ranked, start=1) if j == C_NODE_IDX), None)
    c_dist = next((d for j, d in ranked if j == C_NODE_IDX), None)
    in_top8 = C_NODE_IDX in [j for j, _d in top8]

    print(f"\nC (node_idx={C_NODE_IDX}, Melur Zeon X A.N Energy) real_driving_km={c_dist}, real_rank={c_rank}, in_real_top8={in_top8}")

    out = {
        "target_node_idx": TARGET_NODE_IDX,
        "target_station_id": origin_node.get("id"),
        "target_station_name": origin_node.get("station_name"),
        "vehicle_range_km": range_km,
        "dag_order_window_size": len(window),
        "c_node_idx": C_NODE_IDX,
        "c_in_dag_order_window": C_NODE_IDX in window,
        "osrm_requests": n_requests,
        "real_top8": [
            {"node_idx": j, "station_id": nodes[j].get("id"), "station_name": nodes[j].get("station_name"), "real_driving_km": d}
            for j, d in top8
        ],
        "c_real_driving_km": c_dist,
        "c_real_rank": c_rank,
        "c_in_real_top8": in_top8,
    }
    out_path = Path(__file__).with_name("stop21_hotel_arun_cafe_own_window_result_2026-08-24.json")
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
