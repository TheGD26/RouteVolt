"""
Fan-out cap diagnostic, step 2: real OSRM driving-distance ranking for each of
the 13 flagged nodes' DAG-order candidate windows (from
tests/fanout_local_reconstruction.py's output), to check whether the best
unused/on-corridor candidate identified locally would actually survive
graph_planner.MAX_EDGES_PER_NODE's real-distance top-8 cap
(graph_planner.py:476: `sorted(candidates_for_i, key=lambda c: c[1])[:MAX_EDGES_PER_NODE]`,
c[1] = distances_km from the OSRM table matrix).

Diagnostic-only script -- does NOT call routing_service.get_distance_matrix
(which always builds a full symmetric N x N matrix among every point passed
in). Instead calls OSRM's /table endpoint directly with sources=0 (just the
flagged node) and a destinations= subset (its window), the same endpoint
routing_service._table_request uses, just asymmetric -- one row per node
instead of a full matrix, since only that row is needed here. Splits a node's
destinations across OSRM_TABLE_MAX_COORDINATES-1 wherever a window exceeds it
(same public-demo-server 100-coordinate cap routing_service.py documents).

Does not modify any production file. No production code path is exercised by
this script; it exists only to answer the fan-out cap diagnostic's step 2/3.

    uv run python tests/fanout_osrm_ranking_check.py <local_reconstruction_file.json>
"""

import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import OSRM_BASE_URL
from backend.app.services.route_optimizer import haversine_km
from tests.fanout_local_reconstruction import build_node_list

REQUEST_TIMEOUT_S = 5
MAX_COORDS_PER_REQUEST = 100
MAX_EDGES_PER_NODE = 8  # graph_planner.MAX_EDGES_PER_NODE, mirrored here for the top-8 check


def osrm_one_to_many(source: tuple, destinations: list[tuple]) -> list[float]:
    """
    One OSRM /table/v1/driving/ call: source is index 0, destinations follow.
    sources=0 & destinations=1..N so OSRM only computes/returns the one row
    we need, not a full matrix. Returns real driving distance in km,
    index-aligned with `destinations`. Raises on any request failure -- this
    diagnostic wants to know about drops, not silently paper over them.
    """
    coords = [source] + destinations
    coords_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    n = len(destinations)
    dest_indices = ";".join(str(i) for i in range(1, n + 1))
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coords_str}"
    response = requests.get(
        url,
        params={"annotations": "distance", "sources": "0", "destinations": dest_indices},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM table returned {payload.get('code')}: {payload}")
    row_m = payload["distances"][0]
    return [d / 1000.0 if d is not None else None for d in row_m]


def real_distances_for_window(origin_coord: tuple, window_coords: list[tuple]) -> tuple[list, int]:
    """Chunks a window across MAX_COORDS_PER_REQUEST-1 destinations per request. Returns (distances_km, request_count)."""
    chunk_size = MAX_COORDS_PER_REQUEST - 1
    distances = []
    request_count = 0
    for i in range(0, len(window_coords), chunk_size):
        chunk = window_coords[i:i + chunk_size]
        distances.extend(osrm_one_to_many(origin_coord, chunk))
        request_count += 1
    return distances, request_count


def main():
    if len(sys.argv) < 2:
        print("usage: fanout_osrm_ranking_check.py <local_reconstruction_file.json>")
        sys.exit(1)

    report = json.loads(Path(sys.argv[1]).read_text())
    nodes = build_node_list()

    total_requests = 0
    t0 = time.monotonic()
    out = []

    for entry in report:
        if entry.get("node_idx") is None:
            out.append({**entry, "status": "unresolved_node"})
            continue

        node_idx = entry["node_idx"]
        window_indices = entry["window_node_indices"]
        origin_node = nodes[node_idx]
        origin_coord = (origin_node["lat"], origin_node["lon"])
        window_coords = [(nodes[j]["lat"], nodes[j]["lon"]) for j in window_indices]

        try:
            distances_km, n_requests = real_distances_for_window(origin_coord, window_coords)
        except (requests.RequestException, RuntimeError) as exc:
            print(f"[node_idx={node_idx}] OSRM request failed: {exc}")
            out.append({**entry, "status": "osrm_failure", "error": str(exc)})
            continue

        total_requests += n_requests

        ranked = sorted(
            zip(window_indices, distances_km),
            key=lambda pair: (pair[1] is None, pair[1]),
        )
        real_top8_indices = [j for j, _d in ranked[:MAX_EDGES_PER_NODE]]

        best_candidates = entry.get("best_unused_candidates", [])
        best_candidate_check = []
        for cand in best_candidates:
            cand_node_idx = cand["node_idx"]
            cand_real_km = next((d for j, d in zip(window_indices, distances_km) if j == cand_node_idx), None)
            cand_rank = next((rank for rank, (j, _d) in enumerate(ranked, start=1) if j == cand_node_idx), None)
            best_candidate_check.append({
                **cand,
                "real_driving_km": cand_real_km,
                "real_rank": cand_rank,
                "in_real_top8": cand_node_idx in real_top8_indices,
            })

        top8_detail = [
            {"node_idx": j, "station_id": nodes[j].get("id"), "station_name": nodes[j].get("station_name"),
             "real_driving_km": d}
            for j, d in ranked[:MAX_EDGES_PER_NODE]
        ]

        print(f"\n[node_idx={node_idx}] station_id={entry['station_id']} window_size={len(window_indices)} "
              f"-- {n_requests} OSRM request(s)")
        print(f"  real top-8 by driving distance:")
        for t in top8_detail:
            print(f"    #{top8_detail.index(t)+1} node_idx={t['node_idx']} id={t['station_id']} "
                  f"{t['station_name']!r} {t['real_driving_km']:.1f}km")
        print(f"  best-local-candidate check:")
        for c in best_candidate_check:
            print(f"    node_idx={c['node_idx']} id={c['station_id']} {c['station_name']!r} "
                  f"local_detour_km={c['detour_km']} real_driving_km={c['real_driving_km']} "
                  f"real_rank={c['real_rank']} in_real_top8={c['in_real_top8']}")

        out.append({
            **entry, "status": "ok",
            "real_top8": top8_detail,
            "best_candidate_check": best_candidate_check,
        })

    wall_clock_s = time.monotonic() - t0
    print(f"\n=== total OSRM /table requests: {total_requests}  wall_clock_s: {wall_clock_s:.1f} ===")

    out_path = Path(sys.argv[1]).with_name("fanout_osrm_ranking_result.json")
    out_path.write_text(json.dumps({
        "meta": {"total_osrm_requests": total_requests, "wall_clock_s": round(wall_clock_s, 1)},
        "nodes": out,
    }, indent=2, default=str))
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
