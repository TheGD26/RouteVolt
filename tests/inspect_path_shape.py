"""
Backtracking / duplicate-coordinate-stop diagnostic for the Chennai ->
Nagercoil stop-count anomaly, after ranking-criterion and cap-size were both
ruled out (see tests/sweep_max_edges_per_node.py).

Deliberately minimal network use, to stay inside "arithmetic over already-
available data": one bulk OSRM /table call (matrix["distances_km"] -- the
same cheap first pass build_station_graph/the cap-size sweep both already
treat as free to use, and explicitly what Part 1 of this task names as an
acceptable source: "already available from the /table matrix"), no
elevation call at all (elevation is set to 0 for every edge -- it's a
confirmed ~5% contributor to energy, not to *distance*, and distance/shape
is what this script is checking), and no get_route/traffic calls
whatsoever, so the produced path may differ slightly in its exact stop
selection from a fully-refined real run -- noted in the report, not hidden.

    uv run python tests/inspect_path_shape.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import charger_service, graph_planner, routing_service
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
CAP = 8  # the actual shipped MAX_EDGES_PER_NODE value -- the path production would pick


def build_nodes(origin_lat, origin_lon, dest_lat, dest_lon, candidates):
    origin_node = {"lat": origin_lat, "lon": origin_lon, "dist_from_origin_km": 0.0, "kind": "origin", "station_name": "ORIGIN (Chennai)"}
    dest_dist = graph_planner.haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    dest_node = {"lat": dest_lat, "lon": dest_lon, "dist_from_origin_km": dest_dist, "kind": "destination", "station_name": "DESTINATION (Nagercoil)"}
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


def gap_prune_and_cap(nodes, matrix, vehicle_range_km, cap):
    n = len(nodes)
    per_node = [[] for _ in range(n)]
    for i in range(n):
        row = []
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
        if len(row) > cap:
            row = sorted(row, key=lambda c: c[1])[:cap]
        per_node[i] = row
    return per_node


def main():
    print("=== Backtracking + duplicate-coordinate-stop diagnostic, Chennai -> Nagercoil (mid_ev, cap=8) ===")
    origin_lat, origin_lon = ORIGIN
    dest_lat, dest_lon = DEST
    stations = charger_service.get_stations()
    trip_distance_km = graph_planner.haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    candidates = graph_planner.filter_candidate_stations(
        origin_lat, origin_lon, dest_lat, dest_lon, stations, VEHICLE_PROFILE, trip_distance_km
    )
    nodes = build_nodes(origin_lat, origin_lon, dest_lat, dest_lon, candidates)
    coordinates = [(n["lat"], n["lon"]) for n in nodes]

    matrix = routing_service.get_distance_matrix(coordinates)  # one bulk /table call, no elevation/get_route/traffic

    vehicle_range_km = graph_planner._vehicle_range_km(VEHICLE_PROFILE)
    per_node = gap_prune_and_cap(nodes, matrix, vehicle_range_km, CAP)
    all_pairs = [(i, j, matrix["distances_km"][i][j], matrix["durations_min"][i][j]) for i, row in enumerate(per_node) for j, _d, _t in row]

    total_mass_kg = estimate_total_mass_kg(VEHICLE_PROFILE, LOAD_STATE, None)
    feature_rows = []
    for i, j, distance_km, duration_min in all_pairs:
        avg_speed_kmh = (distance_km / (duration_min / 60.0)) if duration_min > 0 else 0.0
        feature_rows.append({
            "distance_km": distance_km,
            "road_type": infer_road_type_from_speed(avg_speed_kmh),
            "vehicle_profile": VEHICLE_PROFILE,
            "load_state": LOAD_STATE,
            "total_mass_kg": total_mass_kg,
            "avg_speed_kmh": avg_speed_kmh,
            "elevation_gain_m": 0.0,  # deliberately skipped -- no elevation API call in this script
            "elevation_loss_m": 0.0,
            "traffic_congestion_level": 0.3,
        })
    energies = estimate_leg_energy_kwh_batch(feature_rows)
    coarse_energy = {(i, j): e for (i, j, _d, _t), e in zip(all_pairs, energies)}

    capacity_kwh = VEHICLE_PROFILES[VEHICLE_PROFILE]["battery_capacity_kwh"]
    safety_margin = graph_planner.GRAPH_EDGE_SAFETY_MARGIN
    usable_kwh_at_ceiling = capacity_kwh * (BATTERY_CEILING_PCT / 100.0) * (1 - safety_margin)
    usable_kwh_at_full_charge = capacity_kwh * (1 - safety_margin)
    edges = {}
    for i, j, _d, _t in all_pairs:
        e = coarse_energy[(i, j)]
        if e <= usable_kwh_at_ceiling:
            edges.setdefault(i, []).append((j, e, e))
        elif e <= usable_kwh_at_full_charge:
            edges.setdefault(i, []).append((j, e + graph_planner.EXTENDED_CHARGE_PENALTY_KWH, e))

    reachable = graph_planner.compute_reachable_to_destination(nodes, edges)
    onward_degree = graph_planner.compute_onward_degree(nodes, edges, reachable)
    result = graph_planner.find_shortest_path(nodes, edges, reachable, onward_degree, 90.0, VEHICLE_PROFILE)

    if not result["feasible"]:
        print(f"infeasible: {result['reason']}")
        return

    path = result["path"]
    print(f"path length: {len(path)} nodes ({len(path) - 2} stops), total_coarse_kwh={result['total_energy_kwh']}")

    # ---- Part 1: backtracking check ----
    print("\n--- Part 1: backtracking check ---")
    direct_haversine_km = graph_planner.haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    total_real_path_distance_km = 0.0
    hop_rows = []
    for a_idx, b_idx in zip(path, path[1:]):
        d = matrix["distances_km"][a_idx][b_idx]
        total_real_path_distance_km += d
        hop_rows.append((a_idx, b_idx, d))

    print(f"haversine origin->destination: {direct_haversine_km:.1f} km")
    print(f"sum of real driving distance along chosen path (all {len(hop_rows)} hops): {total_real_path_distance_km:.1f} km")
    print(f"ratio (path / haversine): {total_real_path_distance_km / direct_haversine_km:.2f}x")
    print("note: no real single-shot direct get_route distance available without a fresh call (out of scope) -- haversine is the only baseline used here.")

    # ---- Part 2: duplicate-coordinate stop check ----
    print("\n--- Part 2: duplicate-coordinate stop check ---")
    print(f"threshold: routing_service.MIN_LEG_DISTANCE_KM = {routing_service.MIN_LEG_DISTANCE_KM} km (reused, not invented)")
    near_zero_hops = [(a, b, d) for a, b, d in hop_rows if d < routing_service.MIN_LEG_DISTANCE_KM]
    print(f"{len(near_zero_hops)} of {len(hop_rows)} consecutive hops are under threshold")

    involved_stop_indices = set()
    for a, b, d in near_zero_hops:
        involved_stop_indices.add(a)
        involved_stop_indices.add(b)
    # origin/destination can't be "duplicate stops" -- exclude them from the count even if a near-zero hop touches them
    involved_stop_indices -= {0, len(nodes) - 1}
    print(f"{len(involved_stop_indices)} distinct path stops (of {len(path) - 2} total) are involved in a near-zero hop")

    print("\nfull path, hop distance to next node, and flag:")
    for k, (a_idx, b_idx, d) in enumerate(hop_rows):
        node = nodes[a_idx]
        name = node.get("station_name", node.get("kind"))
        city = node.get("city", "")
        district = node.get("district_area", "")
        corridor = node.get("corridor", "")
        flag = " <== NEAR-ZERO HOP TO NEXT" if d < routing_service.MIN_LEG_DISTANCE_KM else ""
        print(f"  [{k}] idx={a_idx} \"{name}\" ({city}/{district}, corridor={corridor}) "
              f"lat={node['lat']:.5f} lon={node['lon']:.5f} -> next hop {d:.3f}km{flag}")
    last = nodes[path[-1]]
    print(f"  [{len(hop_rows)}] idx={path[-1]} \"{last.get('station_name', last.get('kind'))}\" "
          f"lat={last['lat']:.5f} lon={last['lon']:.5f} (destination)")

    if near_zero_hops:
        print("\nnear-zero hops in detail (station identity on both ends):")
        for a, b, d in near_zero_hops:
            na, nb = nodes[a], nodes[b]
            print(f"  {d*1000:.0f}m: \"{na.get('station_name')}\" ({na.get('city')}, {na['lat']:.5f},{na['lon']:.5f}) "
                  f"-> \"{nb.get('station_name')}\" ({nb.get('city')}, {nb['lat']:.5f},{nb['lon']:.5f})")


if __name__ == "__main__":
    main()
