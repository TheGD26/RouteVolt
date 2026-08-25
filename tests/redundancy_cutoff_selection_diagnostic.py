"""
Redundancy-charging cutoff selection: full neighbor-distribution capture
(measurement only, no production code changed).

This is a re-run of tests/redundancy_charging_impact_diagnostic.py's exact
same 246-edge refinement (identical stops, identical top-8 windows, same
graph_planner functions, same real OSRM/traffic calls) with ONE addition:
persists the FULL sorted per-neighbor energy list for every stop, not just
the single worst-case value the original diagnostic kept. The original
script's edge_energy_kwh dict was built in-memory and discarded on exit --
only the max survived to disk. This script exists solely to recover the
full distribution so cutoff options (top-3, top-5, 75th percentile) can be
evaluated without guessing.

Re-run approved by user 2026-08-25 (see tests/redundancy_cutoff_selection_2026-08-24.md).

    uv run python tests/redundancy_cutoff_selection_diagnostic.py
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import graph_planner as gp
from backend.app.services.battery_simulator import (
    SIMULATION_MARGIN_FLOOR_KWH,
    SIMULATION_MARGIN_RATE,
)
from backend.app.services.route_optimizer import (
    VEHICLE_PROFILES,
    estimate_leg_energy_kwh_batch,
    estimate_total_mass_kg,
    haversine_km as gp_haversine_km,
)
from tests.fanout_local_reconstruction import build_node_list
from tests.redundancy_charging_impact_diagnostic import (
    candidate_window_indices,
    real_distances_for_window,
    HEALTH_CHECK_PATH,
    VEHICLE_PROFILE,
    LOAD_STATE,
)

OUT_PATH = Path(__file__).resolve().parent / "redundancy_cutoff_selection_data_2026-08-24.json"


def real_top8_with_distance(nodes: list, i: int, range_km: float) -> list[tuple[int, float]]:
    """
    Same candidate set as real_top8_for_node (identical window, snap-guard,
    MAX_EDGES_PER_NODE cap -- same edges end up in the graph either way), but
    always returns (j, distance_km) sorted ascending by real distance, even
    when there are <=8 survivors. The original function only sorts when
    truncating (>8 candidates); here we need a genuine distance-rank for
    every stop to answer the drop-off-shape question, not just an index-order
    proxy.
    """
    window = candidate_window_indices(nodes, i, range_km)
    if not window:
        return []
    origin_coord = (nodes[i]["lat"], nodes[i]["lon"])
    window_coords = [(nodes[j]["lat"], nodes[j]["lon"]) for j in window]
    distances_km = real_distances_for_window(origin_coord, window_coords)

    candidates_for_i = []
    for j, distance_km in zip(window, distances_km):
        if distance_km is None:
            continue
        straight_line_km = gp_haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
        if (distance_km > gp.MIN_ABSOLUTE_KM_FOR_DETOUR_CHECK
                and distance_km > straight_line_km * gp.MAX_PLAUSIBLE_ROAD_DETOUR_RATIO):
            continue
        candidates_for_i.append((j, distance_km))

    candidates_for_i.sort(key=lambda c: c[1])
    return candidates_for_i[:gp.MAX_EDGES_PER_NODE]


def main():
    health_check = json.loads(HEALTH_CHECK_PATH.read_text())
    stops = health_check["result"]["stops"]
    print(f"Loaded {len(stops)} stops from {HEALTH_CHECK_PATH.name}")

    nodes = build_node_list()
    range_km = gp._vehicle_range_km(VEHICLE_PROFILE)
    print(f"vehicle_range_km({VEHICLE_PROFILE}) = {range_km:.1f}, total nodes = {len(nodes)}")

    def find_node_index(station_id, lat, lon, tol=1e-4):
        for idx, n in enumerate(nodes):
            if n.get("id") == station_id:
                return idx
            if abs(n["lat"] - lat) < tol and abs(n["lon"] - lon) < tol:
                return idx
        return None

    # Step 1: real top-8 by real driving distance, sorted (unlike the original
    # diagnostic, which only sorted when truncating >8 candidates).
    stop_node_info = []
    for stop_idx, s in enumerate(stops):
        loc = s["location"]
        node_idx = find_node_index(s.get("station_id"), loc["lat"], loc["lng"])
        if node_idx is None:
            stop_node_info.append({"stop_idx": stop_idx, "station_id": s.get("station_id"), "node_idx": None})
            continue
        top8_with_dist = real_top8_with_distance(nodes, node_idx, range_km)
        top8 = [j for j, _d in top8_with_dist]
        dist_by_j = {j: d for j, d in top8_with_dist}
        stop_node_info.append({
            "stop_idx": stop_idx, "station_id": s.get("station_id"),
            "station_name": s.get("station_name"), "node_idx": node_idx,
            "current_charge_to_percent": s.get("charge_to_percent"),
            "top8_node_indices": top8,
            "top8_distance_km_by_j": dist_by_j,
        })
        print(f"[stop {stop_idx}] node_idx={node_idx} station_id={s.get('station_id')} top8_count={len(top8)}")

    total_edges = sum(len(x["top8_node_indices"]) for x in stop_node_info if x.get("top8_node_indices"))
    print(f"\nTotal candidate edges needing real per-edge refinement: {total_edges}")

    # Step 2: real per-edge refinement (route + traffic), same as original.
    all_pairs = []
    for x in stop_node_info:
        if not x.get("top8_node_indices"):
            continue
        for j in x["top8_node_indices"]:
            all_pairs.append((x["node_idx"], j))
    all_pairs = list(dict.fromkeys(all_pairs))
    print(f"Unique edges to refine: {len(all_pairs)}")

    route_cache: dict = {}
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=gp.GRAPH_EDGE_REFINEMENT_CONCURRENCY) as pool:
        sub_legs_results = list(
            pool.map(lambda pair: gp._fetch_edge_sub_legs(pair[0], pair[1], nodes, route_cache), all_pairs)
        )
    elapsed = time.monotonic() - t0
    dropped = sum(1 for r in sub_legs_results if r is None)
    print(f"Refinement took {elapsed:.1f}s for {len(all_pairs)} edges ({dropped} dropped)")

    sub_legs_by_pair = dict(zip(all_pairs, sub_legs_results))

    # Step 3: real energy per edge, same as original.
    total_mass_kg = estimate_total_mass_kg(VEHICLE_PROFILE, LOAD_STATE, None)
    pair_ij = []
    pair_sub_leg_counts = []
    feature_rows = []
    for i, j in all_pairs:
        sub_legs = sub_legs_by_pair[(i, j)]
        if not sub_legs:
            continue
        coarse_elevation = gp.elevation_service.compute_leg_elevation(
            [(nodes[i]["lat"], nodes[i]["lon"]), (nodes[j]["lat"], nodes[j]["lon"])]
        )
        total_distance_km = sum(leg["distance_km"] for leg, _traffic in sub_legs) or 1e-9
        pair_ij.append((i, j))
        pair_sub_leg_counts.append(len(sub_legs))
        for leg, traffic in sub_legs:
            share = leg["distance_km"] / total_distance_km
            feature_rows.append(gp._sub_leg_feature_kwargs(
                leg, traffic, VEHICLE_PROFILE, LOAD_STATE, total_mass_kg,
                elevation_gain_m=coarse_elevation["elevation_gain_m"] * share,
                elevation_loss_m=coarse_elevation["elevation_loss_m"] * share,
            ))

    sub_leg_energies_kwh = estimate_leg_energy_kwh_batch(feature_rows) if feature_rows else []
    edge_energy_kwh = {}
    cursor = 0
    for (i, j), count in zip(pair_ij, pair_sub_leg_counts):
        energy_kwh = sum(sub_leg_energies_kwh[cursor:cursor + count])
        cursor += count
        edge_energy_kwh[(i, j)] = energy_kwh

    # Step 4: FULL per-stop distribution (the addition vs. the original diagnostic) --
    # every neighbor's energy, ranked by real distance (rank 1 = nearest ... rank 8 = farthest),
    # not just the worst.
    capacity_kwh = VEHICLE_PROFILES[VEHICLE_PROFILE]["battery_capacity_kwh"]

    def target_pct_with_margin(energy_kwh: float) -> float:
        margin_kwh = max(SIMULATION_MARGIN_FLOOR_KWH, SIMULATION_MARGIN_RATE * energy_kwh)
        return ((energy_kwh + margin_kwh) / capacity_kwh) * 100.0

    per_stop = []
    for x in stop_node_info:
        if x.get("node_idx") is None or not x.get("top8_node_indices"):
            per_stop.append({**x, "neighbors": [], "edges_computed": 0, "edges_dropped": 0})
            continue
        i = x["node_idx"]
        neighbors = []
        dropped_n = 0
        dist_by_j = x.get("top8_distance_km_by_j", {})
        # top8_node_indices is already sorted ascending by real driving distance
        # (real_top8_with_distance), so enumerate order == genuine distance rank.
        for rank, j in enumerate(x["top8_node_indices"], start=1):
            e = edge_energy_kwh.get((i, j))
            if e is None:
                dropped_n += 1
                continue
            neighbors.append({
                "distance_rank": rank,
                "neighbor_node_idx": j,
                "neighbor_station_id": nodes[j].get("id"),
                "neighbor_station_name": nodes[j].get("station_name"),
                "distance_km": round(dist_by_j.get(j, 0.0), 2),
                "energy_kwh": round(e, 3),
                "target_pct": round(target_pct_with_margin(e), 1),
            })
        per_stop.append({
            **x,
            "neighbors": neighbors,
            "edges_computed": len(neighbors),
            "edges_dropped": dropped_n,
        })

    out = {
        "meta": {
            "vehicle_profile": VEHICLE_PROFILE, "load_state": LOAD_STATE,
            "capacity_kwh": capacity_kwh, "range_km": range_km,
            "total_unique_edges_refined": len(all_pairs), "edges_dropped": dropped,
            "refinement_wall_clock_s": round(elapsed, 1),
        },
        "per_stop": per_stop,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()
