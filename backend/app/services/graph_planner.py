"""
Graph-based route feasibility/optimization layer (Option B).

Replaces battery_simulator's old leg-by-leg greedy stop search (walk the
route, and whenever the battery would cross BATTERY_FLOOR_PCT, search for
*a* reachable station right there, no lookahead) with an upfront global
plan: build a station graph once (filter_candidate_stations ->
build_station_graph), find the cheapest feasible stop sequence with
Dijkstra (find_shortest_path), then hand that *fixed* sequence to
battery_simulator.plan_full_journey to simulate the real turn-by-turn
energy draw and produce the charging plan the frontend renders.

plan_journey_via_graph (bottom of this file) is the single entry point that
wires the whole pipeline together; routes.py calls only that.
"""

import heapq
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from backend.app.services import charger_service, elevation_service, routing_service
from backend.app.services.battery_simulator import (
    BATTERY_CEILING_PCT,
    CHARGE_TARGET_LOOKAHEAD_SEGMENTS,
    _estimate_charge_duration_min,
    _leg_elevation,
    _leg_energy_kwh,
    _leg_traffic,
    plan_charge_to_percent,
    plan_full_journey,
)
from backend.app.services.route_optimizer import (
    DEFAULT_SAFETY_MARGIN,
    VEHICLE_PROFILES,
    clean_number,
    estimate_leg_energy_kwh_batch,
    estimate_total_mass_kg,
    haversine_km,
    infer_road_type_from_speed,
)

logger = logging.getLogger(__name__)

# How much extra distance (as a fraction of a full charge's range) a
# candidate is allowed to add before it's rejected as "too far off the
# corridor to bother with." Widened from the original 0.5 -- for a sparse,
# real corridor (see the Thiruttani->Kanyakumari investigation), a station
# a genuine detour away can be the only way to bridge a gap between two
# denser clusters, and the tighter threshold was excluding it from the
# candidate pool before build_station_graph ever got a chance to consider
# it. This is a real time/distance cost the frontend should show, not
# something to hide -- it's ACCEPT_CONSIDERABLE_DETOURS in spirit: the
# candidate pool is now generous, and build_station_graph's own Dijkstra
# cost is what actually decides whether a detour-y candidate is worth using
# for a given trip (see filter_candidate_stations' MAX_CANDIDATE_STATIONS
# cut for why that selection is a flat lowest-detour-first sort, not a
# fancier position-aware one -- a fancier version was tried and reverted
# after it broke a real working case).
MAX_DETOUR_FRACTION_OF_LEFTOVER_RANGE = 1.0

# routing_service.get_distance_matrix chunks around OSRM's 100-coordinate
# request cap, so this no longer has to stay under that limit -- it's now a
# request-count/latency budget instead: a full-state north-south trip's
# candidate list can run into the hundreds, and each additional 50 or so
# candidates costs roughly one more "row" of block-pair OSRM requests (see
# routing_service._get_distance_matrix_chunked). 250 keeps that bounded to a
# few dozen requests while giving real cross-state trips (candidate spacing
# well under any single vehicle profile's real per-hop range, see
# MAX_PLAUSIBLE_ROAD_DETOUR_RATIO below for why "real per-hop range" is much
# shorter than the nominal profile range) enough density to actually find a
# path, not just the nearest handful of stations to the corridor.
MAX_CANDIDATE_STATIONS = 250

# See the edge-cost loop in build_station_graph: a real, confirmed OSRM
# Table Service bug on the public demo server occasionally snaps a
# coordinate to the wrong, distant road network component when queried
# alongside many other points. A distance more than this many times the
# straight-line distance between two nodes is treated as an untrustworthy
# snap artifact rather than a real (if winding) road, and that edge is
# skipped. MIN_ABSOLUTE_KM_FOR_DETOUR_CHECK keeps this from misfiring on
# short, tightly-clustered urban hops, where a real one-way/no-direct-road
# detour can legitimately multiply a tiny straight-line distance many times
# over while staying small in absolute terms.
MAX_PLAUSIBLE_ROAD_DETOUR_RATIO = 4.0
MIN_ABSOLUTE_KM_FOR_DETOUR_CHECK = 30.0

# Fan-out cap: among a node's gap-pruning survivors (see the pass-1 loop in
# build_station_graph), only the this-many nearest by real driving distance
# (matrix["distances_km"][i][j] -- already fetched in bulk, free to rank by)
# get the expensive per-edge treatment: a real routing_service.get_route call
# plus real per-sub-leg traffic (see GRAPH_EDGE_REFINEMENT_CONCURRENCY),
# replacing the single-averaged-speed/flat-0.3-traffic coarse estimate this
# graph used to build edges from. Without this cap, per-edge work would run
# for every gap-pruning survivor -- measured on two real Chennai-origin
# routes with a full 250-candidate pool: 19,759 and 25,896 surviving pairs
# respectively (mean fan-out ~78-103 edges per node, some nodes past 150),
# not the low hundreds a candidate *count* alone would suggest -- tens of
# thousands of uncapped get_route calls against the public OSRM demo server
# per graph build.
#
# 8 is chosen from measuring actual outcomes on those same two routes, not
# picked arbitrarily: capping to 8 cuts per-edge work by ~90-92% (19,759 ->
# 1,980 and 25,896 -> 1,980) while feasible stayed True on both, and the
# chosen path's total_energy_kwh only rose ~4.7-4.9% versus uncapped
# (Nagercoil: 295.4 -> 310.1; Trichy: 142.9 -> 149.7) -- the ranking heuristic
# (nearest by real driving distance) is doing its job: the edges it drops are
# genuinely the worse ones, not an arbitrary cut that happens to remove a
# load-bearing edge. Cap=6 saves a further ~25% of calls for a similar energy
# cost; cap=15 nearly triples the call count for only ~1 percentage point
# less energy cost. 8 sits at the knee of that curve. If a future route shows
# find_shortest_path losing feasibility that existed uncapped, raise this
# rather than removing the cap -- that's the signal the cap became too
# aggressive for that route's station density, not that capping is wrong.
MAX_EDGES_PER_NODE = 8

# Consecutive real stops within this real driving distance of each other are
# collapsed into one displayed stop -- dense-cluster case (e.g. two Chennai-
# area stations both needing charge within a few km of each other), not a
# graph-construction fix.
MERGE_ADJACENT_STOPS_MAX_KM = 20.0

# Redundancy charge-sizing / backup-station recommendation: how many of a
# stop's nearest real graph neighbors (edges[i], already sorted ascending by
# real driving distance -- see the fan-out cap above) are used to size a
# stop's charge target and to surface "backup station" options. Confirmed 3
# via tests/redundancy_cutoff_selection_2026-08-24.md: of top-3/top-5/75th-
# percentile cutoffs tested against the full top-8 per-neighbor energy
# distribution, top-3 was the only one that fully cleared all 8 stops the
# top-8 rule made hard-infeasible (worst residual 82.6%, no new 100%-ceiling
# pushes), with a modest, bounded side effect (6/29 stops, +9.0pp avg
# increase). Top-5 and 75th-percentile both left real hard-infeasible
# residue (see that file's Part 2). This does NOT change MAX_EDGES_PER_NODE,
# graph topology, or which edges Dijkstra can choose -- it only picks how
# many of an already-built node's real edges feed
# battery_simulator.plan_charge_to_percent's redundancy target and the
# backup-station list (see get_top_n_neighbors below).
REDUNDANCY_NEIGHBOR_COUNT = 3

# Bounded concurrency for build_station_graph's per-edge real-route/traffic
# refinement phase (see _fetch_edge_sub_legs): how many concurrent
# routing_service.get_route calls (each followed by a handful of sequential
# per-sub-leg traffic_service calls, still within that same worker -- not a
# second, nested concurrency layer stacked on top of this one) are allowed
# in flight against the public OSRM demo server at once.
#
# OSRM_BASE_URL defaults to https://router.project-osrm.org, which does NOT
# publish a numerical rate or concurrency limit -- confirmed against its own
# usage policy (github.com/Project-OSRM/osrm-backend/wiki/Api-usage-policy),
# whose only guidance is "Excessive use is not allowed... if your requests
# are impacting the service stability, we will block you." No number to
# calibrate against. In the absence of one, 10 is a conservative starting
# point -- deliberately NOT copied from traffic_service.OLA_BATCH_CONCURRENCY
# (=2), which is calibrated to a *different* provider's *documented* limits
# and has nothing to do with OSRM's -- and NOT a measured-correct value for
# this server either. Tune down if a real run shows rate-limit-shaped
# failures (many edges dropping together with the same RoutingError
# pattern, see the refinement-phase warning log); only tune up against a
# self-hosted OSRM instance with real headroom to spend, never further
# against the shared public demo server.
GRAPH_EDGE_REFINEMENT_CONCURRENCY = 10

# Deliberately smaller than route_optimizer.DEFAULT_SAFETY_MARGIN (0.12),
# and a separate constant rather than reusing it: this margin's only job is
# giving build_station_graph's/find_shortest_path's edge-cost estimate some
# slack against the detailed leg-by-leg simulation that re-checks it later,
# not to model real-world charging risk the way DEFAULT_SAFETY_MARGIN does
# for charge targets elsewhere. As of the combined traffic + speed-
# granularity fix (_fetch_edge_sub_legs), speed and traffic are real
# per-sub-leg values, not coarse edge-level ones -- elevation is the one
# piece still a coarse 2-point read per edge (see MAX_EDGES_PER_NODE's
# docstring; confirmed a small, ~5% contributor to the coarse-vs-real drift
# this margin exists to buffer, not worth its own per-edge network call).
# Verified empirically on a real ~800km north-south Tamil Nadu
# trip: 0.12 here made every edge on a tight, sparsely-covered mid_ev
# corridor inadmissible -- no path found at all, even though a real,
# slightly-marginal path existed (0% margin found it, and it only narrowly
# failed the later detailed-simulation re-check). 0.12 is the right margin
# for "how much to charge," not for "is this edge worth considering at all."
GRAPH_EDGE_SAFETY_MARGIN = 0.05

# An edge that doesn't fit within a normal BATTERY_CEILING_PCT charge is
# still admitted if it fits within a full 100% charge instead -- previously
# a deferred TODO ("charging past BATTERY_CEILING_PCT for resilience"), now
# implemented because it's the actual missing piece for bridging the sparser
# real gaps in Tamil Nadu's charging network: battery_simulator.plan_full_journey
# and plan_charge_to_percent already fully support charging a stop above the
# ceiling when the next leg demands it (see plan_charge_to_percent's
# "exceeded_ceiling" escape hatch) -- the graph itself just never considered
# an edge that would require it. EXTENDED_CHARGE_PENALTY_KWH is added to
# such an edge's Dijkstra cost (not its real energy) purely so a normal,
# ceiling-only chain of stops is still preferred whenever one exists --
# charging from ~80% to 100% takes disproportionately longer than the same
# kWh lower in the curve (see _estimate_charge_duration_min's docstring),
# which a pure kWh comparison wouldn't otherwise account for. Reaching for
# an extended-charge edge should mean "this was genuinely necessary to
# bridge a gap," not "this happened to look marginally cheaper."
EXTENDED_CHARGE_PENALTY_KWH = 8.0


def _vehicle_range_km(vehicle_profile: str) -> float:
    """
    The vehicle's real maximum single-hop reach -- a full 100% charge, not
    BATTERY_CEILING_PCT -- used everywhere a caller needs "could this
    candidate/edge possibly be relevant at all" (filter_candidate_stations'
    detour budget, build_station_graph's DAG break threshold). This is
    deliberately the more generous of the two thresholds build_station_graph
    now considers per edge (see EXTENDED_CHARGE_PENALTY_KWH) -- normal-vs-
    extended-charge preference is enforced by Dijkstra's cost, not by
    excluding candidates before the graph is even built.
    """
    profile = VEHICLE_PROFILES[vehicle_profile]
    usable_kwh = profile["battery_capacity_kwh"]
    return (usable_kwh * 1000) / profile["efficiency_baseline_wh_per_km"]


def filter_candidate_stations(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    stations: list, vehicle_profile: str, trip_distance_km: float,
) -> list:
    """
    Cheap pre-filter before the expensive graph build: a station only makes
    the cut if routing through it doesn't add more detour than a full
    charge's range (MAX_DETOUR_FRACTION_OF_LEFTOVER_RANGE) -- e.g. a station
    a few km off a 400km trip on a 300km-range vehicle is fine, one that
    would add 400km of its own detour is not, regardless of how "close" it
    looks on a map. Uses haversine, not OSRM, on purpose -- this runs
    against every station in range (hundreds), and only the survivors go on
    to the real distance-matrix/routing calls below.

    "Leftover after driving it all directly" only means something when the
    trip fits in one charge (trip_distance_km <= vehicle_range_km) -- for
    any trip that genuinely needs a stop (trip_distance_km > vehicle_range_km,
    the normal case for a real road trip), that leftover is 0 by definition,
    which would zero out detour_threshold_km and reject every candidate
    outright, including the ones a multi-hop path actually needs. In that
    case the per-stop detour budget falls back to a full charge's own range
    instead -- the real per-hop constraint build_station_graph enforces is
    range between consecutive stops, not the whole trip's leftover.
    """
    direct_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    vehicle_range_km = _vehicle_range_km(vehicle_profile)
    if trip_distance_km > vehicle_range_km:
        detour_basis_km = vehicle_range_km
    else:
        detour_basis_km = vehicle_range_km - trip_distance_km
    detour_threshold_km = detour_basis_km * MAX_DETOUR_FRACTION_OF_LEFTOVER_RANGE

    candidates = []
    for station in stations:
        lat = clean_number(station.get("latitude"), default=None)
        lon = clean_number(station.get("longitude"), default=None)
        if lat is None or lon is None:
            continue
        origin_dist_km = haversine_km(origin_lat, origin_lon, lat, lon)
        detour_km = origin_dist_km + haversine_km(lat, lon, dest_lat, dest_lon) - direct_km
        if detour_km <= detour_threshold_km:
            candidates.append({**station, "detour_km": round(detour_km, 2), "_origin_dist_km": origin_dist_km})

    if len(candidates) > MAX_CANDIDATE_STATIONS:
        # Flat lowest-detour-first cut. A position-bucketed alternative (cap
        # candidates per equal-length stretch of the corridor, to guarantee
        # sparse stretches keep some representation) was tried here and
        # reverted: it measurably broke a real, previously-working case (a
        # full-state large_ev plan that used to finish on 12% battery ended
        # up 17% longer overall and failed) by capping candidates *out* of
        # dense clusters to force in worse, higher-detour ones elsewhere,
        # even on a corridor that didn't need the help. The bucketing was a
        # hypothesis about crowding that never had a confirmed real failure
        # behind it; the regression it caused was confirmed and reproducible.
        # If dense-cluster crowding on a genuinely sparse route turns out to
        # be a real problem later, worth another look -- just not this cut.
        candidates = sorted(candidates, key=lambda c: c["detour_km"])[:MAX_CANDIDATE_STATIONS]

    for c in candidates:
        del c["_origin_dist_km"]
    return candidates


def _sub_leg_feature_kwargs(
    leg: dict, traffic: dict, vehicle_profile: str, load_state: str, total_mass_kg: float,
    elevation_gain_m: float, elevation_loss_m: float,
) -> dict:
    """
    Feature kwargs for one real sub-leg, ready for route_optimizer.
    estimate_leg_energy_kwh_batch. Mirrors battery_simulator._leg_energy_kwh's
    own avg_speed_kmh/road_type derivation exactly (distance_km/duration_s ->
    speed -> infer_road_type_from_speed) -- not routed through
    _leg_energy_kwh itself so every real sub-leg across every surviving edge
    can go through ONE batched call rather than one predict_energy_kwh call
    each. With MAX_EDGES_PER_NODE-capped edges (still ~2,000 on a large real
    graph) x several real sub-legs apiece, that's thousands of individual
    model calls -- exactly the per-call-overhead trap (~15ms/call, see
    estimate_leg_energy_kwh_batch's docstring) this codebase batches around
    everywhere else. The feature VALUES are identical either way (same
    inputs, same model); this is a call-mechanism choice, not a computation
    that could drift from _leg_energy_kwh's own.

    `traffic` is real, fetched via battery_simulator._leg_traffic for this
    exact sub-leg (see _fetch_edge_sub_legs) -- not a stub. `elevation_gain_m`/
    `elevation_loss_m` are passed in already computed by the caller (still a
    coarse 2-point read per edge, distributed proportionally across real
    sub-legs by distance -- see build_station_graph's docstring for why
    elevation stays coarse).
    """
    distance_km = leg["distance_km"]
    duration_s = leg["duration_s"]
    avg_speed_kmh = (distance_km / (duration_s / 3600.0)) if duration_s > 0 else 0.0
    road_type = infer_road_type_from_speed(avg_speed_kmh)
    return {
        "distance_km": distance_km, "road_type": road_type, "vehicle_profile": vehicle_profile,
        "load_state": load_state, "total_mass_kg": total_mass_kg, "avg_speed_kmh": avg_speed_kmh,
        "elevation_gain_m": elevation_gain_m, "elevation_loss_m": elevation_loss_m,
        "traffic_congestion_level": traffic["congestion_level"],
    }


def _fetch_edge_sub_legs(
    i: int, j: int, nodes: list, route_cache: dict,
) -> Optional[list[tuple[dict, dict]]]:
    """
    Real per-sub-leg data for edge i->j -- the unit of work run inside
    build_station_graph's ThreadPoolExecutor (see
    GRAPH_EDGE_REFINEMENT_CONCURRENCY).

    Fetches the real route (routing_service.get_route) and, for every real
    turn-by-turn sub-leg in it, real traffic via battery_simulator.
    _leg_traffic -- the exact call the detailed leg-by-leg simulation
    already uses for the same purpose, not a separate implementation that
    could drift from it. The fetched route is stashed in `route_cache`
    (keyed by (i, j), mutated in place -- safe under threads here since each
    worker writes a distinct key, no shared key is ever read-modified) so
    plan_journey_via_graph can reuse it for whichever edges end up on the
    final chosen path, instead of calling get_route a second time for the
    same pair within one request.

    Returns None (never raises) if the route fetch fails --
    routing_service.RoutingError is caught here and logged as a warning
    naming the edge's node indices; the caller treats a None result as
    "drop this edge from the graph," same as if it had never survived
    gap-pruning/capping in the first place. A stub fallback here (like
    elevation_service's or traffic_service's own) would silently
    reintroduce the coarse-estimate drift this whole fix exists to close --
    dropping the option is the honest response to "we couldn't verify this
    edge is actually safe," not a fallback guess. _leg_traffic itself never
    raises (its own stub_fallback only degrades one sub-leg's congestion
    reading, not the whole edge's viability), so it's safe to call
    unguarded here.
    """
    try:
        route = routing_service.get_route(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
    except routing_service.RoutingError as exc:
        logger.warning("build_station_graph: dropping edge %d->%d -- real route fetch failed: %s", i, j, exc)
        return None

    route_cache[(i, j)] = route
    return [(leg, _leg_traffic(leg)) for leg in route["legs"]]


def build_station_graph(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    candidates: list, vehicle_profile: str,
    load_state: str = "half_load", payload_kg: Optional[float] = None,
    safety_margin: float = GRAPH_EDGE_SAFETY_MARGIN,
) -> dict:
    """
    Nodes: origin, every surviving candidate (filter_candidate_stations),
    destination -- ordered by straight-line distance from origin so edge
    search below can short-circuit (see the `break`).

    Edges: an (i, j) edge exists only if the predicted energy for i->j fits
    within a full charge to BATTERY_CEILING_PCT, minus safety_margin -- every
    edge assumes the vehicle departs i already charged to the ceiling;
    find_shortest_path below is what actually accounts for the *real*
    starting battery at the origin node specifically. The margin exists
    because even with real per-edge routing/traffic (see below), this is
    still one edge-construction-time estimate rather than the finer-grained
    real simulation that re-checks it later (battery_simulator.
    plan_full_journey); without this margin, an edge that's only marginally
    admissible here has no cushion left for that later check to disagree
    with (verified: a real ~800km, 6-stop north-to-south Tamil Nadu plan
    built from margin-less edges reached the last stretch on 2.5% battery
    and then failed detailed simulation -- the graph's own estimate had no
    slack for the real world to be even slightly worse than assumed). Same
    DEFAULT_SAFETY_MARGIN reservation route_optimizer/battery_simulator
    already use everywhere else for the same reason; this was the one place
    in the pipeline it had been left out.

    One bulk OSRM Table Service call (routing_service.get_distance_matrix)
    gets real driving distance/duration for every node pair up front, used
    only for the cheap gap-pruning + MAX_EDGES_PER_NODE fan-out cap below.
    Every edge that survives capping then gets a real route
    (routing_service.get_route) and real per-sub-leg traffic
    (battery_simulator._leg_traffic) -- see _fetch_edge_sub_legs and
    GRAPH_EDGE_REFINEMENT_CONCURRENCY -- replacing what used to be a single
    averaged speed across the whole edge and a flat 0.3 traffic stub.
    Elevation is the one piece still a coarse 2-point read per edge
    (confirmed a small, ~5% contributor to the coarse-vs-real drift this
    whole refinement targets -- not worth a second per-edge network call).
    """
    vehicle_range_km = _vehicle_range_km(vehicle_profile)
    total_mass_kg = estimate_total_mass_kg(vehicle_profile, load_state, payload_kg)

    origin_node = {"lat": origin_lat, "lon": origin_lon, "dist_from_origin_km": 0.0, "kind": "origin"}
    dest_dist = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    dest_node = {"lat": dest_lat, "lon": dest_lon, "dist_from_origin_km": dest_dist, "kind": "destination"}

    sorted_candidates = sorted(
        (
            {
                **c,
                # Station dicts carry "latitude"/"longitude" (see charger_service),
                # but every node needs a uniform "lat"/"lon" key -- origin_node/
                # dest_node above are built with "lat"/"lon" directly, and the
                # coordinates/edge-cost loop below indexes all nodes the same way.
                "lat": c["latitude"],
                "lon": c["longitude"],
                "dist_from_origin_km": haversine_km(origin_lat, origin_lon, c["latitude"], c["longitude"]),
                "kind": "station",
            }
            for c in candidates
        ),
        key=lambda c: c["dist_from_origin_km"],
    )
    nodes = [origin_node, *sorted_candidates, dest_node]

    coordinates = [(n["lat"], n["lon"]) for n in nodes]
    matrix = routing_service.get_distance_matrix(coordinates)

    # Pre-warm elevation_service's per-point cache with every node's
    # elevation in ONE batched call, instead of paying for it lazily inside
    # the edge loop below. compute_leg_elevation's own get_elevation_profile
    # call caches by rounded (lat, lon) -- since every edge's endpoints are
    # a subset of these same `nodes`, the loop's per-edge calls become pure
    # cache hits (no network) once this has run. There are only n distinct
    # points but up to ~n^2/2 edges to evaluate, so this turns an O(n^2)
    # network-bound cost into one O(n) call plus O(n^2) local arithmetic --
    # the difference between this scaling to a few hundred candidate
    # stations and not (verified: 250 points batch in under a second).
    # Best-effort: if Open-Elevation is unreachable, this silently no-ops
    # and every per-edge compute_leg_elevation call below falls back to its
    # own existing stub_fallback individually, same as if this call didn't
    # exist -- this is a speed optimization, not a correctness dependency.
    try:
        elevation_service.get_elevation_profile(coordinates)
    except elevation_service.ElevationError:
        pass

    n = len(nodes)

    # Stage 1: gather every (i, j) pair that survives the cheap checks --
    # range window, the OSRM-snap sanity guard below -- then apply the
    # MAX_EDGES_PER_NODE fan-out cap, all before any per-edge network work
    # (real route + real traffic) runs for it. No energy-model work here.
    candidates_per_node: list[list[tuple[int, float, float]]] = [[] for _ in range(n)]
    for i in range(n):
        candidates_for_i: list[tuple[int, float, float]] = []  # (j, distance_km, duration_min)
        for j in range(i + 1, n):
            approx_gap_km = nodes[j]["dist_from_origin_km"] - nodes[i]["dist_from_origin_km"]
            if approx_gap_km > vehicle_range_km:
                break  # every later j is farther still -- DAG order guarantees this

            distance_km = matrix["distances_km"][i][j]
            duration_min = matrix["durations_min"][i][j]

            # Sanity guard against a real, confirmed OSRM Table Service bug
            # on the public demo server: when many coordinates are queried
            # together, it occasionally snaps one to the wrong nearby road
            # network component instead of the correct one -- verified
            # directly (a station's raw coordinate correctly snaps within
            # ~13m via OSRM's own /nearest endpoint, but the exact same
            # point snapped ~119km away inside a 90-point /table request,
            # reproducibly across repeated identical calls). A real road
            # route is almost never more than a few times longer than the
            # straight-line distance between its endpoints (even winding
            # ghat roads or coastal detours don't reach this), so a distance
            # wildly out of proportion to haversine is treated as an
            # untrustworthy snap and this edge is skipped rather than
            # trusted -- Dijkstra can still route around it via other
            # nodes/edges if one exists. The absolute floor keeps this from
            # false-positiving on short, tightly-clustered urban hops, where
            # a real detour (one-ways, no direct road) can legitimately be
            # several times the straight-line distance despite being tiny
            # in absolute terms.
            straight_line_km = haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
            if distance_km > MIN_ABSOLUTE_KM_FOR_DETOUR_CHECK and distance_km > straight_line_km * MAX_PLAUSIBLE_ROAD_DETOUR_RATIO:
                continue

            candidates_for_i.append((j, distance_km, duration_min))

        # Fan-out cap (see MAX_EDGES_PER_NODE): keep only the nearest-by-real-
        # driving-distance survivors. Ranking by distance_km rather than a
        # real energy estimate is deliberate -- it's already fetched (the
        # bulk /table matrix above), so this costs nothing extra to rank by,
        # whereas energy would require the real per-edge refinement below to
        # already have run, defeating the point of capping before it happens.
        if len(candidates_for_i) > MAX_EDGES_PER_NODE:
            candidates_for_i = sorted(candidates_for_i, key=lambda c: c[1])[:MAX_EDGES_PER_NODE]
        candidates_per_node[i] = candidates_for_i

    # Stage 2: real per-edge refinement -- a real route (routing_service.
    # get_route) and real per-sub-leg traffic (battery_simulator._leg_traffic)
    # for every capped survivor, replacing the old single-averaged-speed/
    # flat-0.3-traffic coarse estimate. Run concurrently, bounded by
    # GRAPH_EDGE_REFINEMENT_CONCURRENCY (see its docstring for why get_route
    # can't be batched the way /table is, and the OSRM usage-policy finding
    # behind the concurrency value). A failed fetch drops that edge (see
    # _fetch_edge_sub_legs) rather than falling back to a coarse guess or
    # failing this whole call.
    all_pairs: list[tuple[int, int]] = [
        (i, j) for i, cands in enumerate(candidates_per_node) for j, _d, _t in cands
    ]

    route_cache: dict[tuple[int, int], dict] = {}
    refinement_started_at = time.monotonic()
    if all_pairs:
        with ThreadPoolExecutor(max_workers=GRAPH_EDGE_REFINEMENT_CONCURRENCY) as pool:
            sub_legs_results = list(
                pool.map(lambda pair: _fetch_edge_sub_legs(pair[0], pair[1], nodes, route_cache), all_pairs)
            )
    else:
        sub_legs_results = []
    refinement_elapsed_s = time.monotonic() - refinement_started_at

    sub_legs_by_pair = dict(zip(all_pairs, sub_legs_results))
    dropped_count = sum(1 for result in sub_legs_results if result is None)
    # This is the number that turns "we accepted the per-edge get_route cost"
    # into "we know whether that risk is actually materializing" -- watch
    # refinement_elapsed_s and dropped_count across real usage, not just once.
    logger.info(
        "build_station_graph: real per-edge refinement took %.1fs for %d candidate edges "
        "(%d dropped due to route-fetch failures) at concurrency=%d",
        refinement_elapsed_s, len(all_pairs), dropped_count, GRAPH_EDGE_REFINEMENT_CONCURRENCY,
    )

    # Gather every real sub-leg's feature row (across every surviving edge)
    # for one batched model call -- see _sub_leg_feature_kwargs -- tracking
    # how many sub-legs belong to each edge so the batch's flat prediction
    # list can be summed back into one total per edge afterward. Elevation
    # is still one coarse 2-point read per edge (see build_station_graph's
    # own docstring), distributed proportionally by real distance across
    # that edge's real sub-legs.
    pair_ij: list[tuple[int, int]] = []
    pair_sub_leg_counts: list[int] = []
    feature_rows: list[dict] = []
    for i, j in all_pairs:
        sub_legs = sub_legs_by_pair[(i, j)]
        if not sub_legs:
            continue  # dropped (route fetch failed) or an empty route

        coarse_elevation = elevation_service.compute_leg_elevation(
            [(nodes[i]["lat"], nodes[i]["lon"]), (nodes[j]["lat"], nodes[j]["lon"])]
        )
        total_distance_km = sum(leg["distance_km"] for leg, _traffic in sub_legs) or 1e-9

        pair_ij.append((i, j))
        pair_sub_leg_counts.append(len(sub_legs))
        for leg, traffic in sub_legs:
            share = leg["distance_km"] / total_distance_km
            feature_rows.append(_sub_leg_feature_kwargs(
                leg, traffic, vehicle_profile, load_state, total_mass_kg,
                elevation_gain_m=coarse_elevation["elevation_gain_m"] * share,
                elevation_loss_m=coarse_elevation["elevation_loss_m"] * share,
            ))

    # Pass 2: one batched prediction for every real sub-leg from every
    # surviving edge, then apply the feasibility filter -- two tiers, see
    # EXTENDED_CHARGE_PENALTY_KWH: a normal edge fits within a
    # BATTERY_CEILING_PCT charge; a "bridging" edge doesn't, but does fit
    # within a full 100% charge, and is admitted anyway with a cost penalty
    # so Dijkstra only reaches for it when nothing cheaper gets through.
    sub_leg_energies_kwh = estimate_leg_energy_kwh_batch(feature_rows)
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]
    usable_kwh_at_ceiling = capacity_kwh * (BATTERY_CEILING_PCT / 100.0) * (1 - safety_margin)
    usable_kwh_at_full_charge = capacity_kwh * (1 - safety_margin)

    # Each edge is (j, dijkstra_cost, real_energy_kwh) -- dijkstra_cost
    # includes EXTENDED_CHARGE_PENALTY_KWH for a bridging edge, real_energy_kwh
    # never does. find_shortest_path needs the *real* figure for its
    # origin-hop check (the origin isn't a station the vehicle chooses a
    # charge target at -- it's constrained by origin_battery_pct alone,
    # which has nothing to do with the ceiling/extended-charge distinction).
    edges: dict[int, list[tuple[int, float, float]]] = {}
    cursor = 0
    for (i, j), count in zip(pair_ij, pair_sub_leg_counts):
        energy_kwh = sum(sub_leg_energies_kwh[cursor:cursor + count])
        cursor += count
        if energy_kwh <= usable_kwh_at_ceiling:
            edges.setdefault(i, []).append((j, energy_kwh, energy_kwh))
        elif energy_kwh <= usable_kwh_at_full_charge:
            edges.setdefault(i, []).append((j, energy_kwh + EXTENDED_CHARGE_PENALTY_KWH, energy_kwh))

    # route_cache: every real route fetched during refinement, keyed by
    # (i, j) -- plan_journey_via_graph reuses these for whichever edges end
    # up on the final chosen path instead of calling get_route a second time.
    #
    # candidates_per_node: Stage 1's pre-refinement (j, distance_km,
    # duration_min) lists, kept around purely so get_top_n_neighbors can look
    # up a neighbor's real distance_km for display without a second matrix
    # call -- edges' own tuples only carry (j, dijkstra_cost, real_energy_kwh).
    return {"nodes": nodes, "edges": edges, "route_cache": route_cache, "candidates_per_node": candidates_per_node}


def get_top_n_neighbors(graph: dict, node_idx: int, n: int = REDUNDANCY_NEIGHBOR_COUNT) -> list[dict]:
    """
    A node's nearest `n` real graph neighbors by real driving distance, each
    with its real per-edge energy cost -- for redundancy charge-target
    sizing and the "recommended backup stations" list (see
    REDUNDANCY_NEIGHBOR_COUNT, tests/redundancy_cutoff_selection_2026-08-24.md).

    Reuses graph["edges"][node_idx] as-is rather than re-ranking: it's
    already sorted ascending by real driving distance (Stage 1 of
    build_station_graph sorts+caps candidates_per_node[i] by distance before
    Stage 2 appends to edges in that same order, skipping only edges a
    route-fetch failure dropped -- relative order is preserved), so its
    first `n` entries already ARE the nearest n real neighbors that survived
    per-edge refinement. No new routing/energy calls, no recomputation.
    graph["candidates_per_node"] (the same Stage-1 structure, pre-refinement)
    supplies each neighbor's real distance_km for display.
    """
    edges_for_node = graph["edges"].get(node_idx, [])
    if not edges_for_node:
        return []

    distance_by_j = {j: distance_km for j, distance_km, _duration_min in graph["candidates_per_node"][node_idx]}
    nodes = graph["nodes"]

    neighbors = []
    for j, _dijkstra_cost, real_energy_kwh in edges_for_node[:n]:
        neighbor_node = nodes[j]
        neighbors.append({
            "node_index": j,
            "station_id": neighbor_node.get("id"),
            "station_name": neighbor_node.get("station_name"),
            "lat": neighbor_node["lat"],
            "lon": neighbor_node["lon"],
            "distance_km": distance_by_j.get(j),
            "energy_kwh": round(real_energy_kwh, 3),
        })
    return neighbors


def compute_reachable_to_destination(nodes: list, edges: dict) -> list:
    """Backward reachability: can node i reach the destination at all, via any chain of edges?"""
    n = len(nodes)
    reachable = [False] * n
    reachable[n - 1] = True
    for i in range(n - 2, -1, -1):
        reachable[i] = any(reachable[j] for j, _cost, _energy in edges.get(i, []))
    return reachable


def compute_onward_degree(nodes: list, edges: dict, reachable: list) -> list:
    """
    How many of node i's outgoing edges actually lead somewhere useful (a
    node that can still reach the destination). Used only as Dijkstra's
    tie-breaker below -- among paths of equal energy cost, prefer routing
    through a node with more onward options, since that's the more
    resilient choice if a station later turns out unavailable.
    """
    return [
        sum(1 for j, _cost, _energy in edges.get(i, []) if reachable[j])
        for i in range(len(nodes))
    ]


def find_shortest_path(
    nodes: list, edges: dict, reachable: list, onward_degree: list,
    origin_battery_pct: float, vehicle_profile: str,
    safety_margin: float = GRAPH_EDGE_SAFETY_MARGIN,
) -> dict:
    """
    Dijkstra over the station graph, minimizing total cost (real kWh, plus
    EXTENDED_CHARGE_PENALTY_KWH on edges that need more than a
    BATTERY_CEILING_PCT charge to traverse -- see build_station_graph) across
    all edges/stops. Every edge already assumes whatever charge (ceiling or
    full) its own tier needs at its start node *except* the very first hop
    from the origin, which is constrained to the vehicle's real current
    charge (origin_battery_pct, same margin applied) via
    usable_kwh_from_origin below -- checked against the edge's real energy,
    not its (possibly penalized) Dijkstra cost, since the origin isn't a
    station the vehicle gets to choose a charge target at.
    """
    n = len(nodes)
    dest = n - 1
    if not reachable[0]:
        return {"feasible": False, "reason": "No station graph path reaches the destination at all."}

    usable_kwh_from_origin = (
        VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"] * (origin_battery_pct / 100.0) * (1 - safety_margin)
    )
    dist = [math.inf] * n
    prev = [None] * n
    dist[0] = 0.0
    heap = [(0.0, -onward_degree[0], 0)]
    visited = set()

    while heap:
        d, _neg_degree, i = heapq.heappop(heap)
        if i in visited:
            continue
        visited.add(i)
        if i == dest:
            break
        for j, cost, real_energy_kwh in edges.get(i, []):
            if not reachable[j]:
                continue
            if i == 0 and real_energy_kwh > usable_kwh_from_origin:
                continue
            new_dist = d + cost
            if new_dist < dist[j]:
                dist[j] = new_dist
                prev[j] = i
                heapq.heappush(heap, (new_dist, -onward_degree[j], j))

    if dist[dest] == math.inf:
        return {"feasible": False, "reason": "No path within range/battery constraints reaches the destination."}

    path = []
    node = dest
    while node is not None:
        path.append(node)
        node = prev[node]
    path.reverse()
    return {"feasible": True, "path": path, "total_energy_kwh": round(dist[dest], 2)}


def _gap_result(reason: str, origin_lat: float, origin_lon: float, battery_pct: float) -> dict:
    """Shared shape for plan_journey_via_graph's early-exit failure cases (no candidates, no path, routing failure)."""
    return {
        "legs": [],
        "stops": [],
        "final_battery_pct": round(battery_pct, 2),
        "total_predicted_energy_kwh": 0.0,
        "distance_km": None,
        "duration_min": None,
        "status": "unreachable_gap",
        "gap": {
            "leg_index": 0,
            "location": {"lat": origin_lat, "lng": origin_lon},
            "battery_pct_at_gap": round(battery_pct, 2),
            "reason": reason,
        },
        "feasible": False,
        "reason": reason,
        "exceeded_ceiling_stops": [],
    }


def _log_exceeded_ceiling_drift(path: list[int], edges: dict, journey: dict) -> list[int]:
    """
    Cross-checks plan_full_journey's per-stop "exceeded_ceiling" flags
    (set by battery_simulator.plan_charge_to_percent from real turn-by-turn
    legs) against this same edge's coarse per-edge estimate --
    build_station_graph's single estimate_leg_energy_kwh_batch call, which
    already admitted it into the graph as a normal (non-extended-charge)
    edge, i.e. one it believed fit within a BATTERY_CEILING_PCT charge.

    A mismatch is possible -- the two models sample elevation/distance/speed
    differently (see build_station_graph's docstring) -- but should be RARE.
    When it fires, that's estimate drift between the coarse and fine-grained
    energy models, not a bug: plan_charge_to_percent's own escape hatch
    already keeps the vehicle safe by raising the charge target. Logged as a
    warning so this drift is visible/measurable over real usage instead of
    silently absorbed, per plan_charge_to_percent's docstring contract.

    Returns the node indices (into `path`, the graph's node list) where this
    fired, for plan_journey_via_graph's `exceeded_ceiling_stops` response
    field -- never raises, never drops the flag from the response.
    """
    exceeded_ceiling_stops = []
    stops = journey.get("stops", [])
    legs = journey.get("legs", [])
    for k, stop_entry in enumerate(stops):
        if not stop_entry.get("exceeded_ceiling"):
            continue
        node_idx = path[k + 1]
        next_node_idx = path[k + 2]
        exceeded_ceiling_stops.append(node_idx)

        coarse_energy_kwh = next(
            (real_energy for j, _cost, real_energy in edges.get(node_idx, []) if j == next_node_idx),
            None,
        )
        segment_start = stop_entry.get("leg_index", 0)
        segment_end = stops[k + 1]["leg_index"] if k + 1 < len(stops) else len(legs)
        fine_energy_kwh = round(
            sum(leg.get("predicted_energy_kwh", 0.0) for leg in legs[segment_start:segment_end]), 3
        )
        logger.warning(
            "plan_journey_via_graph: exceeded_ceiling fired for graph-chosen stop "
            "node_index=%s (charge_to_percent=%s%%) -- coarse graph edge estimate=%s kWh vs "
            "fine-grained real-leg estimate=%s kWh for the next segment. Expected to be rare: "
            "means build_station_graph's coarse per-edge model and the real turn-by-turn energy "
            "model disagreed enough that the next leg alone needed more than BATTERY_CEILING_PCT "
            "provides. plan_charge_to_percent already raised the charge target to cover it -- "
            "this is visibility into estimate drift, not a failure.",
            node_idx, stop_entry.get("charge_to_percent"), coarse_energy_kwh, fine_energy_kwh,
        )
    return exceeded_ceiling_stops


def _merge_adjacent_stops(stops: list[dict], legs: list[dict], vehicle_profile: str) -> list[dict]:
    """
    Collapses consecutive display stops that sit within
    MERGE_ADJACENT_STOPS_MAX_KM real driving distance of each other into one
    -- the dense-cluster case where the graph-chosen path routes through two
    (or more) stations that are practically the same stop for the driver
    (e.g. two Chennai-area stations a few km apart). Runs after the
    phantom-stop filter, over that already-filtered list -- pure display
    post-processing, does not touch build_station_graph/find_shortest_path
    or how the path was chosen.

    Keeps the FIRST stop of each merged run (its location/station_id/
    station_name/leg_index/alternatives/backup_stations -- that's where the
    vehicle actually stops), raised to charge_to_percent = max across the
    run and exceeded_ceiling = True if any stop in the run had it, with
    estimated_charge_duration_min recomputed for that combined target via
    battery_simulator._estimate_charge_duration_min (not reimplemented
    here). Chains forward: a third stop within range of the (already-
    merged) kept stop's position also merges in, using the kept stop's
    leg_index for that next distance check, not the original second stop's.
    """
    if not stops:
        return stops

    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]
    merged = [dict(stops[0])]
    for stop in stops[1:]:
        kept = merged[-1]
        real_distance_km = sum(
            leg["distance_km"] for leg in legs[kept["leg_index"]:stop["leg_index"]]
        )
        if real_distance_km > MERGE_ADJACENT_STOPS_MAX_KM:
            merged.append(dict(stop))
            continue

        logger.info(
            "plan_journey_via_graph: merging adjacent stop %s into %s "
            "(%.2f km apart, within MERGE_ADJACENT_STOPS_MAX_KM=%.1f)",
            stop.get("station_name"), kept.get("station_name"),
            real_distance_km, MERGE_ADJACENT_STOPS_MAX_KM,
        )
        kept_station = charger_service.get_station(kept.get("station_id")) or {}
        power_kw = clean_number(kept_station.get("power_kw"), default=0.0)
        new_charge_to_percent = max(kept["charge_to_percent"], stop["charge_to_percent"])
        energy_needed_kwh = (
            max(new_charge_to_percent - kept["battery_pct_on_arrival"], 0.0) / 100.0 * capacity_kwh
        )
        kept["charge_to_percent"] = new_charge_to_percent
        kept["exceeded_ceiling"] = kept["exceeded_ceiling"] or stop["exceeded_ceiling"]
        kept["estimated_charge_duration_min"] = round(
            _estimate_charge_duration_min(energy_needed_kwh, power_kw), 1
        )

    return merged


def _simulate_feasibility_only(
    route_leg_groups: list[list[dict]], stops: list[dict], battery_pct: float,
    vehicle_profile: str, load_state: str, payload_kg: Optional[float], safety_margin: float,
) -> dict:
    """
    Feasibility-only mirror of battery_simulator.plan_full_journey's leg-by-leg
    energy-drain loop -- calls the exact same _leg_elevation/_leg_traffic/
    _leg_energy_kwh/plan_charge_to_percent functions in the same order/lookahead
    window, so its verdict agrees with plan_full_journey's exactly, but skips
    plan_full_journey's per-stop _build_stop_entry -> _rank_reachable_candidates
    -> route_optimizer.calculate_best_route "alternatives" ranking.

    That skip matters: calculate_best_route calls traffic_service.
    get_leg_traffic_batch, which calls get_eta_matrix directly -- bypassing
    _leg_traffic_cache entirely (unlike the singular get_leg_traffic/_leg_traffic
    the energy simulation below uses, which IS cached by rounded coordinates).
    Measured directly against this repo's real ~449-station set: ~12.6s of live
    Ola Maps traffic per stop, every call, uncached. find_minimum_starting_battery
    calls this once per binary-search iteration -- paying that per-stop cost on
    every iteration for a corridor with dozens of stops (Chennai->Nagercoil
    resolves to 31 at 90% battery, see tests/live_pipeline_output_*.json) would
    make the binary search cost MORE than the naive full-rebuild-per-iteration
    approach it exists to avoid, not less. Alternatives are irrelevant to a
    feasibility check (they only rank display/reroute candidates), so skipping
    them here is safe and doesn't change the real feasibility verdict.

    Only used by find_minimum_starting_battery -- plan_full_journey remains the
    unmodified, full-featured simulator (alternatives included) for every other
    caller.
    """
    total_mass_kg = estimate_total_mass_kg(vehicle_profile, load_state, payload_kg)
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]

    remaining_pct = battery_pct
    for segment_index, leg_group in enumerate(route_leg_groups):
        for leg in leg_group:
            elevation = _leg_elevation(leg)
            traffic = _leg_traffic(leg)
            energy_kwh = _leg_energy_kwh(
                leg, vehicle_profile, load_state, total_mass_kg, elevation=elevation, traffic=traffic
            )
            pct_after = remaining_pct - (energy_kwh / capacity_kwh) * 100.0
            if pct_after < 0:
                return {
                    "status": "unreachable_gap",
                    "final_battery_pct": round(remaining_pct, 2),
                    "reason": "The detailed leg-by-leg simulation needs more energy for this "
                              "stretch than the upfront graph plan assumed.",
                }
            remaining_pct = pct_after

        if segment_index < len(stops):
            lookahead_end = segment_index + 1 + CHARGE_TARGET_LOOKAHEAD_SEGMENTS
            remaining_route_legs = [
                leg for group in route_leg_groups[segment_index + 1:lookahead_end] for leg in group
            ]
            charge_plan = plan_charge_to_percent(
                remaining_legs=remaining_route_legs, vehicle_profile=vehicle_profile,
                safety_margin=safety_margin, load_state=load_state, payload_kg=payload_kg,
            )
            battery_pct_on_arrival = remaining_pct
            remaining_pct = max(charge_plan["charge_to_percent"], battery_pct_on_arrival)

    return {"status": "ok", "final_battery_pct": round(remaining_pct, 2), "reason": None}


def find_minimum_starting_battery(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    vehicle_profile: str, load_state: str = "half_load", payload_kg: Optional[float] = None,
    tolerance_pct: float = 1.0, safety_margin: float = DEFAULT_SAFETY_MARGIN,
    stations: Optional[list] = None,
) -> dict:
    """
    Binary search over starting_battery_pct in [0, 100] for the minimum value
    that makes origin->destination feasible, for a fixed (origin, destination,
    vehicle_profile, load_state). Monotonicity holds because build_station_graph
    (the expensive real-refinement pass) never depends on starting battery --
    only find_shortest_path's origin-hop check does (see usable_kwh_from_origin,
    confirmed the only use-site) -- so more starting battery only ever loosens
    that one check, never tightens any other edge in the graph.

    Builds the station graph ONCE (the ~250s real get_route/traffic/elevation
    refinement pass), then reuses that same graph object across every
    iteration: find_shortest_path is cheap pure Dijkstra with no API calls, and
    every edge it could possibly choose already has a real route cached in the
    graph's route_cache (every surviving edge got refined during the single
    build, not just the eventually-chosen path) -- so per-leg elevation/traffic
    lookups during simulation are cache hits too (see _simulate_feasibility_only
    for why that function, not plan_full_journey, is used for the per-iteration
    feasibility check).

    Returns:
      feasible: False only if even 100% starting battery can't reach the
        destination -- reported directly, no binary search performed in that
        case (searching a range with no feasible point would either loop
        pointlessly or report a misleading number).
      minimum_starting_battery_pct: the found minimum, rounded UP to
        tolerance_pct so the reported number is itself confirmed feasible
        (rounding down could report a value one tolerance-step short of
        actually working). None if infeasible.
      iterations: number of find_shortest_path/_simulate_feasibility_only
        calls the binary search performed (not counting the initial 100%
        feasibility check).
      stops / path / distance_km / duration_min / total_energy_kwh: the
        resolved path at the found minimum, for transparency -- same shape
        plan_journey_via_graph resolves stop_nodes into, computed once more
        at the end rather than carried through the search (the search only
        needs the boolean feasible flag per iteration).
    """
    if stations is None:
        stations = charger_service.get_stations()

    trip_distance_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    candidates = filter_candidate_stations(
        origin_lat, origin_lon, dest_lat, dest_lon, stations, vehicle_profile, trip_distance_km
    )

    try:
        graph = build_station_graph(
            origin_lat, origin_lon, dest_lat, dest_lon, candidates, vehicle_profile, load_state=load_state,
            payload_kg=payload_kg,
        )
    except routing_service.RoutingError as exc:
        return {
            "feasible": False, "minimum_starting_battery_pct": None, "iterations": 0,
            "reason": f"Could not build the station graph (routing provider failure): {exc}",
        }

    nodes, edges = graph["nodes"], graph["edges"]
    route_cache = graph.get("route_cache", {})
    reachable = compute_reachable_to_destination(nodes, edges)
    onward_degree = compute_onward_degree(nodes, edges, reachable)

    def check(battery_pct: float):
        path_result = find_shortest_path(nodes, edges, reachable, onward_degree, battery_pct, vehicle_profile)
        if not path_result["feasible"]:
            return False, path_result, None

        path_indices = path_result["path"]
        path_nodes = [nodes[i] for i in path_indices]
        stop_nodes = path_nodes[1:-1]
        route_leg_groups = []
        for a_idx, b_idx, a, b in zip(path_indices, path_indices[1:], path_nodes, path_nodes[1:]):
            route = route_cache.get((a_idx, b_idx))
            if route is None:
                # Defensive only -- every edge Dijkstra can choose survived
                # build_station_graph's refinement, so route_cache should
                # always have it already.
                route = routing_service.get_route(a["lat"], a["lon"], b["lat"], b["lon"])
            route_leg_groups.append(route["legs"])

        sim = _simulate_feasibility_only(
            route_leg_groups, stop_nodes, battery_pct, vehicle_profile, load_state, payload_kg, safety_margin
        )
        return sim["status"] == "ok", path_result, sim

    feasible_at_100, path_at_100, sim_at_100 = check(100.0)
    if not feasible_at_100:
        reason = sim_at_100["reason"] if sim_at_100 else path_at_100["reason"]
        return {
            "feasible": False, "minimum_starting_battery_pct": None, "iterations": 0,
            "reason": f"Infeasible even at 100% starting battery: {reason}",
        }

    lo, hi = 0.0, 100.0
    best_path_result, best_sim = path_at_100, sim_at_100
    iterations = 0
    while hi - lo > tolerance_pct:
        mid = (lo + hi) / 2.0
        iterations += 1
        feasible, path_result, sim = check(mid)
        if feasible:
            hi = mid
            best_path_result, best_sim = path_result, sim
        else:
            lo = mid

    minimum_pct = min(math.ceil(hi / tolerance_pct) * tolerance_pct, 100.0)
    if minimum_pct != hi:
        # Re-confirm feasibility at the rounded-up number, since hi itself
        # (not the rounded value) is what the loop actually verified.
        feasible, path_result, sim = check(minimum_pct)
        if feasible:
            best_path_result, best_sim = path_result, sim

    # Empirical monotonicity guard: one more pair of check() calls, just below
    # and just above the found minimum, reusing this SAME graph object -- this
    # is deliberately the exact mechanism find_minimum_starting_battery relies
    # on (repeated find_shortest_path calls against one already-built graph
    # with different origin_battery_pct), not a fresh independent rebuild, so
    # it actually tests the reuse assumption rather than API response variance
    # across separate builds. Catches a subtle bug silently breaking
    # monotonicity (e.g. graph mutation between calls) rather than assuming it.
    below_pct = max(minimum_pct - tolerance_pct, 0.0)
    above_pct = min(minimum_pct + tolerance_pct, 100.0)
    below_feasible, _pr, _sim = check(below_pct) if below_pct < minimum_pct else (True, None, None)
    above_feasible, _pr, _sim = check(above_pct) if above_pct > minimum_pct else (True, None, None)
    monotonicity_check = {
        "below_pct": below_pct, "below_feasible": below_feasible,
        "above_pct": above_pct, "above_feasible": above_feasible,
        "holds": (below_pct == minimum_pct or not below_feasible) and above_feasible,
    }

    path_indices = best_path_result["path"]
    path_nodes = [nodes[i] for i in path_indices]
    stop_nodes = path_nodes[1:-1]

    return {
        "feasible": True,
        "minimum_starting_battery_pct": round(minimum_pct, 2),
        "iterations": iterations,
        "reason": None,
        "path_node_indices": path_indices,
        "stops": [
            {
                "station_id": s.get("id"), "station_name": s.get("station_name"),
                "lat": s["lat"], "lon": s["lon"],
            }
            for s in stop_nodes
        ],
        "stop_count": len(stop_nodes),
        "total_energy_kwh": best_path_result.get("total_energy_kwh"),
        "final_battery_pct": best_sim.get("final_battery_pct") if best_sim else None,
        "monotonicity_check": monotonicity_check,
    }


def plan_journey_via_graph(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    battery_pct: float, vehicle_profile: str,
    load_state: str = "half_load", payload_kg: Optional[float] = None,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
    stations: Optional[list] = None,
) -> dict:
    """
    Single entry point for the graph-based pipeline: filter_candidate_stations
    -> build_station_graph -> compute_reachable_to_destination ->
    compute_onward_degree -> find_shortest_path -> resolve the winning path
    back into real station dicts -> a real turn-by-turn route for each
    origin->stop1->...->stopN->destination segment (reused from
    build_station_graph's own per-edge refinement via its route_cache when
    available, so a chosen edge's get_route call isn't paid for twice in one
    request) -> battery_simulator.plan_full_journey to simulate it.

    Returns plan_full_journey's shape (legs/stops/final_battery_pct/
    total_predicted_energy_kwh/status/gap) plus:
      - distance_km / duration_min: summed across every real routed segment
        (None if no path was ever assembled -- see the early-exit cases).
      - feasible / reason: True/None on success; False + a human-readable
        reason (from find_shortest_path, or a routing failure) otherwise --
        richer than the old status=="unreachable_gap" alone, since
        plan_full_journey's own "gap" is about *where* the plan broke down,
        not *why* no plan could be built at all.
    """
    if stations is None:
        stations = charger_service.get_stations()

    trip_distance_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    candidates = filter_candidate_stations(
        origin_lat, origin_lon, dest_lat, dest_lon, stations, vehicle_profile, trip_distance_km
    )

    try:
        graph = build_station_graph(
            origin_lat, origin_lon, dest_lat, dest_lon, candidates, vehicle_profile,
            load_state=load_state, payload_kg=payload_kg,
        )
    except routing_service.RoutingError as exc:
        # OSRM's Table Service call (get_distance_matrix) can fail for
        # reasons that have nothing to do with whether this trip is
        # actually feasible -- the provider timing out, or (for a very
        # long/densely-covered trip) exceeding the public demo server's
        # request-size limit despite MAX_CANDIDATE_STATIONS. Report it the
        # same explicit-failure way as every other infeasible case rather
        # than letting a routing-provider hiccup crash the whole request.
        return _gap_result(
            f"Could not build the station graph (routing provider failure): {exc}",
            origin_lat, origin_lon, battery_pct,
        )
    nodes, edges = graph["nodes"], graph["edges"]
    route_cache = graph.get("route_cache", {})

    reachable = compute_reachable_to_destination(nodes, edges)
    onward_degree = compute_onward_degree(nodes, edges, reachable)
    path_result = find_shortest_path(nodes, edges, reachable, onward_degree, battery_pct, vehicle_profile)

    if not path_result["feasible"]:
        return _gap_result(path_result["reason"], origin_lat, origin_lon, battery_pct)

    path_indices = path_result["path"]
    path_nodes = [nodes[i] for i in path_indices]
    stop_nodes = path_nodes[1:-1]  # exclude origin & destination

    # Every edge on the chosen path already got a real route fetched during
    # build_station_graph's per-edge refinement (route_cache, keyed by the
    # same (i, j) node-index pairs) -- reuse it here instead of calling
    # get_route a second time for the same pair. Falls back to a fresh call
    # only if the cache somehow doesn't have it (defensive, not expected:
    # every edge Dijkstra can choose survived exactly that refinement).
    route_leg_groups = []
    total_distance_m = 0.0
    total_duration_s = 0.0
    try:
        for a_idx, b_idx, a, b in zip(path_indices, path_indices[1:], path_nodes, path_nodes[1:]):
            route = route_cache.get((a_idx, b_idx))
            if route is None:
                route = routing_service.get_route(a["lat"], a["lon"], b["lat"], b["lon"])
            route_leg_groups.append(route["legs"])
            total_distance_m += route["distance_m"]
            total_duration_s += route["duration_s"]
    except routing_service.RoutingError as exc:
        return _gap_result(
            f"Could not fetch a real driving route for the planned stop sequence: {exc}",
            origin_lat, origin_lon, battery_pct,
        )

    # Attach each stop's top-3 nearest real graph neighbors (redundancy
    # charge-sizing + "backup stations" list) before handing stops off to
    # plan_full_journey -- additive per-stop metadata only, computed from
    # the already-built graph, not part of the Dijkstra-chosen path itself.
    # See get_top_n_neighbors/REDUNDANCY_NEIGHBOR_COUNT and
    # tests/redundancy_charging_implementation_2026-08-24.md.
    stop_nodes_with_redundancy = [
        {**station_node, "_redundancy_top3_neighbors": get_top_n_neighbors(graph, node_idx)}
        for node_idx, station_node in zip(path_indices[1:-1], stop_nodes)
    ]

    journey = plan_full_journey(
        route_leg_groups=route_leg_groups,
        stops=stop_nodes_with_redundancy,
        battery_pct=battery_pct,
        vehicle_profile=vehicle_profile,
        load_state=load_state,
        payload_kg=payload_kg,
        safety_margin=safety_margin,
    )

    exceeded_ceiling_stops = _log_exceeded_ceiling_drift(path_result["path"], edges, journey)

    display_stops = [s for s in journey["stops"] if s.get("charging_required", True)]
    display_stops = _merge_adjacent_stops(display_stops, journey["legs"], vehicle_profile)
    journey = {**journey, "stops": display_stops}

    return {
        **journey,
        "distance_km": round(total_distance_m / 1000.0, 2),
        "duration_min": round(total_duration_s / 60.0, 1),
        "feasible": journey["status"] == "ok",
        "reason": None if journey["status"] == "ok" else journey["gap"]["reason"],
        "exceeded_ceiling_stops": exceeded_ceiling_stops,
    }
