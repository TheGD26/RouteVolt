"""
Redundancy-charging impact diagnostic (measurement only, no production code
changed). Answers: if every one of the 31 stops on the most recent baseline
path (tests/health_check_output_2026-08-25.json) needed enough charge to
reach the WORST-CASE (highest-energy) of its own real top-8 graph neighbors,
how would that compare to the current post-Option-C charge_to_percent?

Targeted partial build, NOT a full graph rebuild: only computes real
per-edge energy for the (up to 8 x 31 = 248) candidate edges outbound from
the 31 path stops themselves, reusing graph_planner's own node
reconstruction, snap-guard, and per-edge refinement functions directly
(no reimplementation, no drift risk) rather than rebuilding edges for
every one of the ~69 graph nodes the way a real build_station_graph call
would.

    uv run python tests/redundancy_charging_impact_diagnostic.py
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import OSRM_BASE_URL
from backend.app.services import graph_planner as gp
from backend.app.services.battery_simulator import (
    BATTERY_CEILING_PCT,
    BATTERY_FLOOR_PCT,
    SIMULATION_MARGIN_FLOOR_KWH,
    SIMULATION_MARGIN_RATE,
)
from backend.app.services.route_optimizer import (
    VEHICLE_PROFILES,
    estimate_leg_energy_kwh_batch,
    estimate_total_mass_kg,
    haversine_km,
)
from tests.fanout_local_reconstruction import build_node_list

HEALTH_CHECK_PATH = Path(__file__).resolve().parent / "health_check_output_2026-08-25.json"
VEHICLE_PROFILE = "mid_ev"
LOAD_STATE = "half_load"
MAX_COORDS_PER_TABLE_REQUEST = 100
REQUEST_TIMEOUT_S = 5


def osrm_one_to_many(source: tuple, destinations: list[tuple]) -> list[float]:
    """One-to-many OSRM /table call (source index 0), real driving km, index-aligned with destinations."""
    if not destinations:
        return []
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


def real_distances_for_window(origin_coord: tuple, window_coords: list[tuple]) -> list:
    chunk_size = MAX_COORDS_PER_TABLE_REQUEST - 1
    distances = []
    for i in range(0, len(window_coords), chunk_size):
        chunk = window_coords[i:i + chunk_size]
        distances.extend(osrm_one_to_many(origin_coord, chunk))
    return distances


def candidate_window_indices(nodes: list, i: int, range_km: float) -> list[int]:
    """Mirrors build_station_graph's stage-1 DAG-order gap break (graph_planner.py:436-439)."""
    window = []
    for j in range(i + 1, len(nodes)):
        approx_gap_km = nodes[j]["dist_from_origin_km"] - nodes[i]["dist_from_origin_km"]
        if approx_gap_km > range_km:
            break
        window.append(j)
    return window


def real_top8_for_node(nodes: list, i: int, range_km: float) -> list[int]:
    """
    Reproduces build_station_graph's Stage 1 exactly for one node i: DAG-order
    window -> real OSRM distance -> snap-guard -> top MAX_EDGES_PER_NODE by
    real distance. Returns the surviving j indices (up to 8), same set
    build_station_graph itself would hand to Stage 2 refinement for this node.
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
        straight_line_km = haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
        if (distance_km > gp.MIN_ABSOLUTE_KM_FOR_DETOUR_CHECK
                and distance_km > straight_line_km * gp.MAX_PLAUSIBLE_ROAD_DETOUR_RATIO):
            continue
        candidates_for_i.append((j, distance_km))

    if len(candidates_for_i) > gp.MAX_EDGES_PER_NODE:
        candidates_for_i = sorted(candidates_for_i, key=lambda c: c[1])[:gp.MAX_EDGES_PER_NODE]
    return [j for j, _d in candidates_for_i]


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

    # Step 1: real top-8 (by real driving distance, mirroring build_station_graph
    # Stage 1) for every one of the 31 path stops.
    stop_node_info = []
    for stop_idx, s in enumerate(stops):
        loc = s["location"]
        node_idx = find_node_index(s.get("station_id"), loc["lat"], loc["lng"])
        if node_idx is None:
            print(f"[stop {stop_idx}] station_id={s.get('station_id')} NOT FOUND in reconstructed node list -- skipping")
            stop_node_info.append({"stop_idx": stop_idx, "station_id": s.get("station_id"), "node_idx": None})
            continue
        top8 = real_top8_for_node(nodes, node_idx, range_km)
        stop_node_info.append({
            "stop_idx": stop_idx, "station_id": s.get("station_id"),
            "station_name": s.get("station_name"), "node_idx": node_idx,
            "current_charge_to_percent": s.get("charge_to_percent"),
            "top8_node_indices": top8,
        })
        print(f"[stop {stop_idx}] node_idx={node_idx} station_id={s.get('station_id')} top8_count={len(top8)}")

    total_edges = sum(len(x["top8_node_indices"]) for x in stop_node_info if x.get("top8_node_indices"))
    print(f"\nTotal candidate edges needing real per-edge refinement: {total_edges}")

    # Step 2: real per-edge refinement (route + traffic) for every (stop -> top8-neighbor)
    # pair, reusing graph_planner._fetch_edge_sub_legs directly.
    all_pairs = []
    for x in stop_node_info:
        if not x.get("top8_node_indices"):
            continue
        for j in x["top8_node_indices"]:
            all_pairs.append((x["node_idx"], j))
    all_pairs = list(dict.fromkeys(all_pairs))  # de-dup (two stops could share a neighbor)
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

    # Step 3: real energy per edge, exactly mirroring build_station_graph Stage 2.
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

    # Step 4: per-stop worst-case-neighbor requirement + combined target.
    capacity_kwh = VEHICLE_PROFILES[VEHICLE_PROFILE]["battery_capacity_kwh"]

    def target_pct_with_margin(energy_kwh: float) -> float:
        margin_kwh = max(SIMULATION_MARGIN_FLOOR_KWH, SIMULATION_MARGIN_RATE * energy_kwh)
        return ((energy_kwh + margin_kwh) / capacity_kwh) * 100.0

    per_stop = []
    for x in stop_node_info:
        if x.get("node_idx") is None or not x.get("top8_node_indices"):
            per_stop.append({**x, "worst_neighbor_energy_kwh": None, "worst_neighbor_target_pct": None,
                              "final_combined_target_pct": x.get("current_charge_to_percent"),
                              "delta_pct": 0.0, "edges_computed": 0, "edges_dropped": 0})
            continue
        i = x["node_idx"]
        edge_energies = []
        dropped_n = 0
        for j in x["top8_node_indices"]:
            e = edge_energy_kwh.get((i, j))
            if e is None:
                dropped_n += 1
                continue
            edge_energies.append((j, e))

        if not edge_energies:
            per_stop.append({**x, "worst_neighbor_energy_kwh": None, "worst_neighbor_target_pct": None,
                              "final_combined_target_pct": x.get("current_charge_to_percent"),
                              "delta_pct": 0.0, "edges_computed": 0, "edges_dropped": dropped_n})
            continue

        worst_j, worst_energy_kwh = max(edge_energies, key=lambda p: p[1])
        worst_target_pct = target_pct_with_margin(worst_energy_kwh)
        current_target = x["current_charge_to_percent"]
        final_target = max(current_target, worst_target_pct)
        final_target = min(final_target, 100.0)
        per_stop.append({
            **x,
            "worst_neighbor_node_idx": worst_j,
            "worst_neighbor_station_id": nodes[worst_j].get("id"),
            "worst_neighbor_station_name": nodes[worst_j].get("station_name"),
            "worst_neighbor_energy_kwh": round(worst_energy_kwh, 3),
            "worst_neighbor_target_pct": round(worst_target_pct, 1),
            "final_combined_target_pct": round(final_target, 1),
            "delta_pct": round(final_target - current_target, 1),
            "edges_computed": len(edge_energies),
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
    out_path = Path(__file__).resolve().parent / "redundancy_charging_impact_diagnostic_data_2026-08-24.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
