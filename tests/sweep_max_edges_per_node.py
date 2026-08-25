"""
Part 1 of the MAX_EDGES_PER_NODE cap-size investigation (Chennai -> Nagercoil
stop-count anomaly). Two prior sessions ruled out the top-8 ranking
*criterion* (distance vs. coarse energy -- nearly identical results either
way). This tests a different hypothesis: that the cap SIZE (8) itself is too
small to let rare long skip-edges compete with clusters of short local hops,
regardless of how those 8 are chosen.

Standalone script, not pytest (same spirit as test_graph_planner.py):

    uv run python tests/sweep_max_edges_per_node.py

Coarse-only: one real OSRM Table Service call for the distance/duration
matrix (needed for gap-pruning -- this is the same cheap first pass
build_station_graph itself uses), then ONE batched coarse-energy estimate
for every gap-pruned survivor (flat 0.3 traffic, single 2-point elevation
per edge, avg speed from the matrix -- the same coarse approximation
build_station_graph used before the real per-edge get_route/traffic
refinement was added). No real per-edge get_route calls here, so this stays
fast regardless of how large the cap gets -- that real-refinement cost is
exactly what Part 2 exists to test, only if this sweep shows it's worth
paying for.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import charger_service, elevation_service, graph_planner, routing_service
from backend.app.services.battery_simulator import BATTERY_CEILING_PCT
from backend.app.services.route_optimizer import (
    VEHICLE_PROFILES,
    estimate_leg_energy_kwh_batch,
    estimate_total_mass_kg,
    infer_road_type_from_speed,
)

ORIGIN = (13.0827, 80.2707)   # Chennai
DEST = (8.1833, 77.4119)      # Nagercoil
VEHICLE_PROFILE = "mid_ev"
LOAD_STATE = "half_load"
CAP_VALUES = [8, 15, 20, 30, None]  # None = uncapped reference point


def build_nodes(origin_lat, origin_lon, dest_lat, dest_lon, candidates):
    origin_node = {"lat": origin_lat, "lon": origin_lon, "dist_from_origin_km": 0.0, "kind": "origin"}
    dest_dist = graph_planner.haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    dest_node = {"lat": dest_lat, "lon": dest_lon, "dist_from_origin_km": dest_dist, "kind": "destination"}
    sorted_candidates = sorted(
        (
            {
                **c,
                "lat": c["latitude"],
                "lon": c["longitude"],
                "dist_from_origin_km": graph_planner.haversine_km(origin_lat, origin_lon, c["latitude"], c["longitude"]),
                "kind": "station",
            }
            for c in candidates
        ),
        key=lambda c: c["dist_from_origin_km"],
    )
    return [origin_node, *sorted_candidates, dest_node]


def gap_prune_all_survivors(nodes, matrix, vehicle_range_km):
    """
    Mirrors build_station_graph's Stage 1 loop exactly (range-window break +
    OSRM-snap sanity guard), but returns every survivor per node, uncapped --
    the cap is applied afterward, once, per sweep value, over this same
    shared survivor set.
    """
    n = len(nodes)
    survivors_per_node: list[list[tuple[int, float, float]]] = [[] for _ in range(n)]
    for i in range(n):
        row: list[tuple[int, float, float]] = []
        for j in range(i + 1, n):
            approx_gap_km = nodes[j]["dist_from_origin_km"] - nodes[i]["dist_from_origin_km"]
            if approx_gap_km > vehicle_range_km:
                break

            distance_km = matrix["distances_km"][i][j]
            duration_min = matrix["durations_min"][i][j]

            straight_line_km = graph_planner.haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
            if (
                distance_km > graph_planner.MIN_ABSOLUTE_KM_FOR_DETOUR_CHECK
                and distance_km > straight_line_km * graph_planner.MAX_PLAUSIBLE_ROAD_DETOUR_RATIO
            ):
                continue

            row.append((j, distance_km, duration_min))
        survivors_per_node[i] = row
    return survivors_per_node


def cap_survivors(survivors_per_node, cap):
    """Same fan-out cap as build_station_graph: nearest-by-real-driving-distance, ranking untouched."""
    if cap is None:
        return survivors_per_node
    capped = []
    for row in survivors_per_node:
        if len(row) > cap:
            row = sorted(row, key=lambda c: c[1])[:cap]
        capped.append(row)
    return capped


def coarse_energy_for_pairs(nodes, all_pairs, vehicle_profile, load_state, total_mass_kg):
    """
    One batched coarse-energy estimate for every (i, j) pair: avg speed from
    the already-fetched matrix duration, flat 0.3 traffic, single 2-point
    elevation per edge (elevation_service's cache is prewarmed with every
    node up front, so this is a local cache hit, not a network call) -- the
    same coarse approximation build_station_graph used before real per-edge
    get_route/traffic refinement was added. Returns {(i, j): energy_kwh}.
    """
    feature_rows = []
    for i, j, distance_km, duration_min in all_pairs:
        avg_speed_kmh = (distance_km / (duration_min / 60.0)) if duration_min > 0 else 0.0
        road_type = infer_road_type_from_speed(avg_speed_kmh)
        elevation = elevation_service.compute_leg_elevation(
            [(nodes[i]["lat"], nodes[i]["lon"]), (nodes[j]["lat"], nodes[j]["lon"])]
        )
        feature_rows.append({
            "distance_km": distance_km,
            "road_type": road_type,
            "vehicle_profile": vehicle_profile,
            "load_state": load_state,
            "total_mass_kg": total_mass_kg,
            "avg_speed_kmh": avg_speed_kmh,
            "elevation_gain_m": elevation["elevation_gain_m"],
            "elevation_loss_m": elevation["elevation_loss_m"],
            "traffic_congestion_level": 0.3,
        })

    energies = estimate_leg_energy_kwh_batch(feature_rows)
    return {(i, j): e for (i, j, _d, _t), e in zip(all_pairs, energies)}


def build_edges_from_coarse(all_pairs, coarse_energy, capacity_kwh, safety_margin):
    usable_kwh_at_ceiling = capacity_kwh * (BATTERY_CEILING_PCT / 100.0) * (1 - safety_margin)
    usable_kwh_at_full_charge = capacity_kwh * (1 - safety_margin)
    edges: dict[int, list[tuple[int, float, float]]] = {}
    for i, j, _d, _t in all_pairs:
        energy_kwh = coarse_energy[(i, j)]
        if energy_kwh <= usable_kwh_at_ceiling:
            edges.setdefault(i, []).append((j, energy_kwh, energy_kwh))
        elif energy_kwh <= usable_kwh_at_full_charge:
            edges.setdefault(i, []).append((j, energy_kwh + graph_planner.EXTENDED_CHARGE_PENALTY_KWH, energy_kwh))
    return edges


def main():
    print("=== Part 1: coarse-only MAX_EDGES_PER_NODE cap-size sweep, Chennai -> Nagercoil (mid_ev) ===")

    origin_lat, origin_lon = ORIGIN
    dest_lat, dest_lon = DEST
    stations = charger_service.get_stations()
    trip_distance_km = graph_planner.haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    candidates = graph_planner.filter_candidate_stations(
        origin_lat, origin_lon, dest_lat, dest_lon, stations, VEHICLE_PROFILE, trip_distance_km
    )
    print(f"trip_distance_km={trip_distance_km:.1f}  candidates={len(candidates)}")

    nodes = build_nodes(origin_lat, origin_lon, dest_lat, dest_lon, candidates)
    coordinates = [(n["lat"], n["lon"]) for n in nodes]

    t0 = time.monotonic()
    matrix = routing_service.get_distance_matrix(coordinates)
    print(f"distance matrix fetched in {time.monotonic() - t0:.1f}s for {len(nodes)} nodes")

    try:
        elevation_service.get_elevation_profile(coordinates)
    except elevation_service.ElevationError:
        pass

    vehicle_range_km = graph_planner._vehicle_range_km(VEHICLE_PROFILE)
    survivors_per_node = gap_prune_all_survivors(nodes, matrix, vehicle_range_km)
    total_survivor_pairs = sum(len(row) for row in survivors_per_node)
    print(f"gap-pruned survivor pairs (uncapped): {total_survivor_pairs}")

    total_mass_kg = estimate_total_mass_kg(VEHICLE_PROFILE, LOAD_STATE, None)
    capacity_kwh = VEHICLE_PROFILES[VEHICLE_PROFILE]["battery_capacity_kwh"]
    safety_margin = graph_planner.GRAPH_EDGE_SAFETY_MARGIN

    # Coarse energy computed ONCE for the full uncapped survivor set (a
    # superset of every capped value below), so every cap in CAP_VALUES
    # reuses the same batch instead of paying for it again.
    all_survivor_pairs = [
        (i, j, d, t) for i, row in enumerate(survivors_per_node) for j, d, t in row
    ]
    t0 = time.monotonic()
    coarse_energy = coarse_energy_for_pairs(nodes, all_survivor_pairs, VEHICLE_PROFILE, LOAD_STATE, total_mass_kg)
    print(f"coarse energy computed for all {len(all_survivor_pairs)} survivor pairs in {time.monotonic() - t0:.2f}s")

    print(f"\n{'cap':>8} {'stops':>6} {'total_coarse_kwh':>18} {'candidate_pairs':>16} {'graph_edges':>12} {'feasible':>9}")
    for cap in CAP_VALUES:
        capped_per_node = cap_survivors(survivors_per_node, cap)
        all_pairs = [(i, j, d, t) for i, row in enumerate(capped_per_node) for j, d, t in row]
        edges = build_edges_from_coarse(all_pairs, coarse_energy, capacity_kwh, safety_margin)
        total_graph_edges = sum(len(v) for v in edges.values())

        reachable = graph_planner.compute_reachable_to_destination(nodes, edges)
        onward_degree = graph_planner.compute_onward_degree(nodes, edges, reachable)
        result = graph_planner.find_shortest_path(nodes, edges, reachable, onward_degree, 90.0, VEHICLE_PROFILE)

        cap_label = "uncapped" if cap is None else str(cap)
        if result["feasible"]:
            stops = len(result["path"]) - 2
            energy = result["total_energy_kwh"]
            print(f"{cap_label:>8} {stops:>6} {energy:>18.2f} {len(all_pairs):>16} {total_graph_edges:>12} {'True':>9}")
        else:
            print(f"{cap_label:>8} {'--':>6} {'--':>18} {len(all_pairs):>16} {total_graph_edges:>12} {'False':>9} -- {result['reason']}")


if __name__ == "__main__":
    main()
