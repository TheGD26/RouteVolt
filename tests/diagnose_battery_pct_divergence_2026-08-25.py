"""
Diagnose: does the Chennai->Nagercoil (mid_ev, half_load) 16%/80%-fail vs
28%/91%-succeed pattern near Thiruvennainallur come from Dijkstra choosing a
DIFFERENT station path at different origin battery levels, or from something
else (detailed-simulation divergence on the SAME path / per-run live-data
noise)?

Builds the station graph ONCE (mirrors find_minimum_starting_battery's
reuse pattern) and, against that single graph object, calls find_shortest_path
at each of the user's 4 observed battery_percentage values (16, 28, 80, 91),
then feeds each resulting path's cached routes into plan_full_journey. This
removes the "4 separate live /route/optimize calls" confound -- each call
independently rebuilds the graph via live OSRM/Ola Maps requests, and this
corridor already has one confirmed case of two independent live builds
disagreeing on real edge energy (Stop 21).

Reports, per battery level: the full set of origin-admissible edges (station,
real_energy_kwh, tier), which of those admissions FLIP between 16/28 and
80/91, the chosen path/station sequence, and the detailed-simulation
feasibility outcome (gap location/leg/battery_pct if infeasible) -- enough to
mechanically verify one of three verdicts:
  - identical path across all 4 -> divergence is in simulation/live noise,
    not graph selection (RULED OUT)
  - path differs, failing pair (16/80) vs succeeding pair (28/91) -> CONFIRMED,
    report the flipped origin edge that caused it
  - any other pattern -> INCONCLUSIVE, report the raw table

    uv run python tests/diagnose_battery_pct_divergence_2026-08-25.py
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import charger_service, graph_planner, routing_service
from backend.app.services.battery_simulator import plan_full_journey
from backend.app.services.route_optimizer import DEFAULT_SAFETY_MARGIN, VEHICLE_PROFILES, haversine_km

ORIGIN_LAT, ORIGIN_LON = 13.0827, 80.2707   # Chennai
DEST_LAT, DEST_LON = 8.1833, 77.4119        # Nagercoil
VEHICLE_PROFILE = "mid_ev"
LOAD_STATE = "half_load"
BATTERY_LEVELS = [16.0, 28.0, 80.0, 91.0]   # the user's exact 4 live-UI values

OUT_PATH = Path(__file__).resolve().parent / f"diagnose_battery_pct_divergence_output_{date.today().isoformat()}.json"


def node_label(nodes, idx):
    n = nodes[idx]
    if n["kind"] == "origin":
        return "ORIGIN"
    if n["kind"] == "destination":
        return "DEST"
    return f"node{idx}:{n.get('id')}:{n.get('station_name')}"


def main():
    print("=== Part 1: build the station graph ONCE for Chennai -> Nagercoil "
          f"(mid_ev, half_load) ===")
    stations = charger_service.get_stations()
    trip_distance_km = haversine_km(ORIGIN_LAT, ORIGIN_LON, DEST_LAT, DEST_LON)
    candidates = graph_planner.filter_candidate_stations(
        ORIGIN_LAT, ORIGIN_LON, DEST_LAT, DEST_LON, stations, VEHICLE_PROFILE, trip_distance_km
    )
    print(f"trip_distance_km={trip_distance_km:.1f} candidates={len(candidates)}")

    t0 = time.monotonic()
    try:
        graph = graph_planner.build_station_graph(
            ORIGIN_LAT, ORIGIN_LON, DEST_LAT, DEST_LON, candidates, VEHICLE_PROFILE,
            load_state=LOAD_STATE, payload_kg=None,
        )
    except routing_service.RoutingError as exc:
        print(f"STOP: could not build the station graph at all -- {exc}")
        print("Cannot proceed with a single shared graph; refusing to silently "
              "fall back to 4 separate live builds (that would reintroduce the "
              "confound this task exists to remove).")
        return
    build_elapsed_s = time.monotonic() - t0
    print(f"graph build took {build_elapsed_s:.1f}s -- nodes={len(graph['nodes'])} "
          f"edges_from_origin={len(graph['edges'].get(0, []))}")

    nodes, edges = graph["nodes"], graph["edges"]
    route_cache = graph["route_cache"]
    reachable = graph_planner.compute_reachable_to_destination(nodes, edges)
    onward_degree = graph_planner.compute_onward_degree(nodes, edges, reachable)

    capacity_kwh = VEHICLE_PROFILES[VEHICLE_PROFILE]["battery_capacity_kwh"]
    usable_kwh_at_ceiling = (
        capacity_kwh * (graph_planner.BATTERY_CEILING_PCT / 100.0) * (1 - graph_planner.GRAPH_EDGE_SAFETY_MARGIN)
    )
    origin_edges = edges.get(0, [])  # (j, dijkstra_cost, real_energy_kwh)

    print(f"\nusable_kwh_at_ceiling (80% charge, {graph_planner.GRAPH_EDGE_SAFETY_MARGIN} margin) = "
          f"{usable_kwh_at_ceiling:.3f} kWh, capacity_kwh={capacity_kwh}")

    print("\n=== Part 2: all origin (node 0) edges the graph built, with tier ===")
    origin_edge_rows = []
    for j, cost, real_energy in sorted(origin_edges, key=lambda e: e[1]):
        tier = "normal" if real_energy <= usable_kwh_at_ceiling + 1e-9 else "bridging"
        origin_edge_rows.append({"j": j, "label": node_label(nodes, j), "real_energy_kwh": round(real_energy, 3),
                                  "dijkstra_cost": round(cost, 3), "tier": tier})
        print(f"  -> {node_label(nodes, j):45s} real_energy_kwh={real_energy:7.3f} tier={tier:9s} dijkstra_cost={cost:7.3f}")

    admissible_by_pct = {}
    path_result_by_pct = {}
    for pct in BATTERY_LEVELS:
        usable_kwh_from_origin = capacity_kwh * (pct / 100.0) * (1 - graph_planner.GRAPH_EDGE_SAFETY_MARGIN)
        admissible = [
            (j, real_energy, "normal" if real_energy <= usable_kwh_at_ceiling + 1e-9 else "bridging")
            for j, cost, real_energy in origin_edges
            if real_energy <= usable_kwh_from_origin
        ]
        admissible_by_pct[pct] = admissible
        path_result_by_pct[pct] = graph_planner.find_shortest_path(
            nodes, edges, reachable, onward_degree, pct, VEHICLE_PROFILE
        )
        print(f"\nbattery_pct={pct}: usable_kwh_from_origin={usable_kwh_from_origin:.3f}, "
              f"origin-admissible={len(admissible)}/{len(origin_edges)}")

    print("\n=== Part 2b: origin-admissible edges per battery level, flip check ===")
    print(f"{'station':45s} real_energy  16%  28%  80%  91%")
    flips = []
    for j, cost, real_energy in sorted(origin_edges, key=lambda e: e[1]):
        flags = []
        for pct in BATTERY_LEVELS:
            admitted = any(jj == j for jj, e, t in admissible_by_pct[pct])
            flags.append(admitted)
        flag_str = "  ".join(f"{pct:>4.0f}%:{'Y' if f else 'N'}" for pct, f in zip(BATTERY_LEVELS, flags))
        print(f"  {node_label(nodes, j):45s} {real_energy:7.3f}   {flag_str}")
        # flip between 16/28 pair, or between 80/91 pair
        if flags[0] != flags[1]:
            flips.append((j, "16->28", flags[0], flags[1]))
        if flags[2] != flags[3]:
            flips.append((j, "80->91", flags[2], flags[3]))
    if flips:
        print("\nFLIPS DETECTED (admission status changes within a pair):")
        for j, transition, before, after in flips:
            print(f"  {node_label(nodes, j)}: {transition} admitted {before} -> {after}")
    else:
        print("\nNo origin-edge admission flips within either pair (16->28, 80->91).")

    print("\n=== Part 3: feed each battery level's chosen path into plan_full_journey ===")
    table_rows = []
    for pct in BATTERY_LEVELS:
        pr = path_result_by_pct[pct]
        row = {"battery_pct": pct}
        if not pr["feasible"]:
            row.update({"dijkstra_feasible": False, "reason": pr["reason"], "path_indices": None,
                        "path_labels": None, "sim_feasible": None, "gap": None})
            table_rows.append(row)
            print(f"\nbattery_pct={pct}: Dijkstra itself found NO path -- {pr['reason']}")
            continue

        path_indices = pr["path"]
        path_nodes = [nodes[i] for i in path_indices]
        stop_nodes = path_nodes[1:-1]
        route_leg_groups = []
        try:
            for a_idx, b_idx, a, b in zip(path_indices, path_indices[1:], path_nodes, path_nodes[1:]):
                route = route_cache.get((a_idx, b_idx))
                if route is None:
                    route = routing_service.get_route(a["lat"], a["lon"], b["lat"], b["lon"])
                route_leg_groups.append(route["legs"])
        except routing_service.RoutingError as exc:
            row.update({"dijkstra_feasible": True, "path_indices": path_indices,
                        "path_labels": [node_label(nodes, i) for i in path_indices],
                        "sim_feasible": None, "gap": None,
                        "reason": f"route fetch failed during simulation feed: {exc}"})
            table_rows.append(row)
            continue

        stop_nodes_with_redundancy = [
            {**s, "_redundancy_top3_neighbors": graph_planner.get_top_n_neighbors(graph, idx)}
            for idx, s in zip(path_indices[1:-1], stop_nodes)
        ]
        journey = plan_full_journey(
            route_leg_groups=route_leg_groups,
            stops=stop_nodes_with_redundancy,
            battery_pct=pct,
            vehicle_profile=VEHICLE_PROFILE,
            load_state=LOAD_STATE,
            payload_kg=None,
            safety_margin=DEFAULT_SAFETY_MARGIN,
        )
        row.update({
            "dijkstra_feasible": True,
            "path_indices": path_indices,
            "path_labels": [node_label(nodes, i) for i in path_indices],
            "total_energy_kwh": pr["total_energy_kwh"],
            "sim_feasible": journey["status"] == "ok",
            "gap": journey.get("gap"),
            "reason": None if journey["status"] == "ok" else journey["gap"]["reason"],
        })
        table_rows.append(row)

        print(f"\nbattery_pct={pct}: path={row['path_labels']}")
        print(f"  sim_feasible={row['sim_feasible']}")
        if not row["sim_feasible"]:
            gap = row["gap"]
            print(f"  gap: leg_index={gap['leg_index']} location={gap['location']} "
                  f"battery_pct_at_gap={gap['battery_pct_at_gap']} reason={gap['reason']}")

    print("\n=== Part 4: summary table ===")
    header = f"{'battery_pct':>11s} | {'first_stop':30s} | {'feasible':8s} | {'gap_leg':7s} | {'gap_loc':30s} | {'gap_batt%':9s}"
    print(header)
    print("-" * len(header))
    for row in table_rows:
        first_stop = row["path_labels"][1] if row.get("path_labels") and len(row["path_labels"]) > 1 else "N/A"
        feasible = row.get("sim_feasible")
        feasible_str = "N/A" if feasible is None else str(feasible)
        gap = row.get("gap")
        gap_leg = str(gap["leg_index"]) if gap else "-"
        gap_loc = str(gap["location"]) if gap else "-"
        gap_batt = str(gap["battery_pct_at_gap"]) if gap else "-"
        print(f"{row['battery_pct']:>11.0f} | {first_stop:30s} | {feasible_str:8s} | {gap_leg:7s} | {gap_loc:30s} | {gap_batt:9s}")

    print("\n=== Verdict ===")
    paths_by_pct = {row["battery_pct"]: tuple(row["path_indices"]) if row.get("path_indices") else None for row in table_rows}
    all_paths = list(paths_by_pct.values())
    distinct_paths = set(all_paths)

    if None in distinct_paths:
        print("INCONCLUSIVE: at least one battery level had no Dijkstra path at all "
              "(not merely a simulation-level gap). Raw table above is authoritative.")
    elif len(distinct_paths) == 1:
        print("RULED OUT: the chosen path/station sequence is IDENTICAL across all 4 "
              "battery levels (16, 28, 80, 91). The graph-selection hypothesis is false "
              "for this corridor -- whatever makes 16%/80% fail and 28%/91% succeed lives "
              "in the detailed leg-by-leg simulation (plan_full_journey) or in per-run live "
              "data variance across separate /route/optimize calls, not in which path "
              "Dijkstra picked.")
        shared_path = next(iter(distinct_paths))
        print(f"Shared path: {[node_label(nodes, i) for i in shared_path]}")
        fail_pcts = [pct for pct in BATTERY_LEVELS if paths_by_pct[pct] is not None and
                     next(r for r in table_rows if r['battery_pct'] == pct)['sim_feasible'] is False]
        succeed_pcts = [pct for pct in BATTERY_LEVELS if paths_by_pct[pct] is not None and
                        next(r for r in table_rows if r['battery_pct'] == pct)['sim_feasible'] is True]
        print(f"Failing battery levels on this shared path: {fail_pcts}")
        print(f"Succeeding battery levels on this shared path: {succeed_pcts}")
        fail_gaps = [next(r for r in table_rows if r['battery_pct'] == pct)['gap'] for pct in fail_pcts]
        for pct, gap in zip(fail_pcts, fail_gaps):
            print(f"  battery_pct={pct} gap: leg_index={gap['leg_index']} location={gap['location']} "
                  f"battery_pct_at_gap={gap['battery_pct_at_gap']}")
    else:
        failing = {16.0, 80.0}
        succeeding = {28.0, 91.0}
        fail_paths = {paths_by_pct[p] for p in failing if paths_by_pct.get(p) is not None}
        succeed_paths = {paths_by_pct[p] for p in succeeding if paths_by_pct.get(p) is not None}
        if len(fail_paths) == 1 and len(succeed_paths) == 1 and fail_paths != succeed_paths:
            print("CONFIRMED: the failing pair (16%, 80%) chose a DIFFERENT path than the "
                  "succeeding pair (28%, 91%).")
            fail_path = next(iter(fail_paths))
            succeed_path = next(iter(succeed_paths))
            print(f"  16%/80% path:  {[node_label(nodes, i) for i in fail_path]}")
            print(f"  28%/91% path:  {[node_label(nodes, i) for i in succeed_path]}")
            # find the first divergence point (common prefix)
            common_prefix_len = 0
            for a, b in zip(fail_path, succeed_path):
                if a != b:
                    break
                common_prefix_len += 1
            print(f"  Paths share a common prefix of {common_prefix_len} node(s); diverge at "
                  f"fail_path[{common_prefix_len}]={node_label(nodes, fail_path[common_prefix_len]) if common_prefix_len < len(fail_path) else 'END'} "
                  f"vs succeed_path[{common_prefix_len}]={node_label(nodes, succeed_path[common_prefix_len]) if common_prefix_len < len(succeed_path) else 'END'}")
            if common_prefix_len == 1:
                diverging_node = fail_path[0]
                fail_next = fail_path[1]
                succeed_next = succeed_path[1]
                fail_edge = next((e for j, c, e in origin_edges if j == fail_next), None)
                succeed_edge = next((e for j, c, e in origin_edges if j == succeed_next), None)
                print(f"  Divergence is at the ORIGIN hop: 16%/80% take {node_label(nodes, fail_next)} "
                      f"(real_energy={fail_edge}), 28%/91% take {node_label(nodes, succeed_next)} "
                      f"(real_energy={succeed_edge}). This is exactly the origin-hop admission check "
                      f"(usable_kwh_from_origin) picking a different first edge at different battery levels.")
        else:
            print("INCONCLUSIVE: the path pattern doesn't cleanly split into "
                  "{16,80}-different-from-{28,91}. Raw table above is authoritative -- "
                  f"paths_by_pct={ {p: [node_label(nodes, i) for i in path] if path else None for p, path in paths_by_pct.items()} }")

    output = {
        "meta": {
            "origin": [ORIGIN_LAT, ORIGIN_LON], "destination": [DEST_LAT, DEST_LON],
            "vehicle_profile": VEHICLE_PROFILE, "load_state": LOAD_STATE,
            "battery_levels": BATTERY_LEVELS, "build_elapsed_s": round(build_elapsed_s, 1),
        },
        "origin_edges": origin_edge_rows,
        "admissible_by_pct": {
            str(pct): [{"j": j, "label": node_label(nodes, j), "real_energy_kwh": round(e, 3), "tier": t}
                       for j, e, t in adm]
            for pct, adm in admissible_by_pct.items()
        },
        "flips": [{"j": j, "label": node_label(nodes, j), "transition": t, "before": b, "after": a}
                  for j, t, b, a in flips],
        "table_rows": table_rows,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved full raw output to {OUT_PATH}")


if __name__ == "__main__":
    main()
