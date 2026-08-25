"""
Battery simulation for RouteVolt.

plan_full_journey is a *pure simulator*: it no longer searches for charging
stations itself. graph_planner.py owns that decision now -- it builds a
station graph, runs Dijkstra to pick the globally cheapest feasible stop
sequence up front, then hands this module the fixed sequence (as one real
turn-by-turn route segment per origin->stop1->...->stopN->destination hop)
to walk leg-by-leg, running each leg through the trained energy model via
route_optimizer, applying a charge target at each pre-chosen stop, and
reporting exactly where/why the plan breaks down if the detailed simulation
diverges from the graph's coarser estimate. See plan_full_journey's
docstring for the two design decisions this replacement required.

graph_planner.plan_journey_via_graph is the actual multi-stop entry point
/route/optimize is built on; this module no longer decides *which* stations
to use, only *how much* energy each already-decided leg costs and how much
to charge at each already-decided stop.

Elevation and traffic are real per-leg values (elevation_service.py /
traffic_service.py), not stubs -- same spirit as the weather TODO stub still
in route_optimizer._build_trip_features.
"""

from datetime import datetime, timezone
from typing import Optional

from backend.app.services import charger_service, elevation_service, traffic_service
from backend.app.services.route_optimizer import (
    DEFAULT_SAFETY_MARGIN,
    VEHICLE_PROFILES,
    calculate_best_route,
    clean_number,
    estimate_leg_energy_kwh,
    estimate_total_mass_kg,
    infer_road_type_from_speed,
    station_score,
)

# ---- Battery band (tune here, not as magic numbers scattered elsewhere) ----
BATTERY_FLOOR_PCT = 20.0    # "must charge soon" -- a stop is planned before crossing this
BATTERY_CEILING_PCT = 80.0  # normal charge target cap; only exceeded if the very next leg demands it

# How many segments ahead plan_full_journey looks when building the
# remaining_legs it hands to plan_charge_to_percent at each stop -- see
# plan_full_journey's own docstring for why this is bounded rather than
# "every segment to the final destination." 2 means: the segment to the very
# next charging opportunity, plus the segment from there to the one after
# that (or the destination, if fewer remain) -- enough to catch a real
# multi-leg shortfall between two charging opportunities (the actual leg-97
# failure mode) without the cumulative-to-final-destination total tripping
# required_pct_all at nearly every stop on a long multi-stop trip merely
# because the whole remaining trip is (expectedly, by design) bigger than
# one battery.
CHARGE_TARGET_LOOKAHEAD_SEGMENTS = 2

# plan_charge_to_percent's own required-energy margin -- proportional-with-
# floor, replacing a flat DEFAULT_SAFETY_MARGIN=0.12 (12%, ~4.8kWh on a
# 40kWh pack) percentage-point pad that was the same size regardless of how
# big the segment actually was. Root cause (tests/energy_prediction_
# consistency_2026-08-24.md): build-time's graph-edge admission already
# applies an effectively proportional buffer (GRAPH_EDGE_SAFETY_MARGIN=0.05
# capacity discount == margin/(1-margin) = 5.26% of the edge's own predicted
# kWh, confirmed algebraically) -- simulation-time's flat 4.8kWh pad,
# stacked on real per-edge predictions that already agreed with build-time's
# within -4.33%/+4.16% (tests/energy_prediction_consistency_2026-08-24.json),
# was oversized for small segments and underweighted for large ones,
# tripping exceeded_ceiling on segments build-time had already admitted as
# fine. SIMULATION_MARGIN_RATE=0.06 was chosen (tests/margin_recalibration_
# options_2026-08-24.md, "Option C") to clear the observed max raw-prediction
# error (4.33%) with headroom, while sitting close to build-time's own
# effective rate so the two stages are calibrated to a comparable standard.
# SIMULATION_MARGIN_FLOOR_KWH=1.5 exists for short/cheap legs a pure
# percentage would otherwise leave nearly unbuffered (not exercised by the
# 12-edge diagnostic sample, all of which cleared the floor on the
# percentage alone -- insurance for shorter legs elsewhere in the graph).
SIMULATION_MARGIN_RATE = 0.06
SIMULATION_MARGIN_FLOOR_KWH = 1.5

TOP_N_ALTERNATIVES = 5
DETOUR_ASSUMED_SPEED_KMH = 40.0     # rough mixed-road speed for estimating detour time off the main route
DETOUR_ON_ROUTE_THRESHOLD_KM = 1.0  # below this, a station counts as "on route" rather than a real detour
DEFAULT_FALLBACK_CHARGE_POWER_KW = 7.0  # used only when a station's power_kw can't be parsed (see charger CSV notes)


def _leg_elevation(leg: dict) -> dict:
    """
    Real elevation_gain_m/loss_m sampled along the leg's OSRM geometry (see
    routing_service._leg_from_step / _merge_steps_into_legs), not the flat-
    terrain stub this used to be. Falls back to leg["start"]/leg["end"] alone
    if a leg somehow has no "points" (e.g. a hand-built leg from an older
    client on /route/reroute-stop that doesn't echo them back).
    """
    points = leg.get("points") or [leg["start"], leg["end"]]
    return elevation_service.compute_leg_elevation(points)


def _leg_traffic(leg: dict) -> dict:
    """
    Real traffic_congestion_level for this leg (traffic_service.get_leg_traffic),
    not the flat 0.3 stub this used to be. leg["duration_s"] (OSRM's own,
    traffic-free duration) is passed as the no-traffic baseline for providers
    that don't supply their own (Ola doesn't -- see traffic_service._ola_matrix).
    """
    start_lat, start_lon = leg["start"]
    end_lat, end_lon = leg["end"]
    return traffic_service.get_leg_traffic(
        start_lat, start_lon, end_lat, end_lon, typical_duration_s=leg["duration_s"]
    )


def _leg_energy_kwh(
    leg: dict,
    vehicle_profile: str,
    load_state: str,
    total_mass_kg: float,
    elevation: Optional[dict] = None,
    traffic: Optional[dict] = None,
) -> float:
    distance_km = leg["distance_km"]
    duration_s = leg["duration_s"]
    avg_speed_kmh = (distance_km / (duration_s / 3600.0)) if duration_s > 0 else 0.0
    road_type = infer_road_type_from_speed(avg_speed_kmh)
    if elevation is None:
        elevation = _leg_elevation(leg)
    if traffic is None:
        traffic = _leg_traffic(leg)
    return estimate_leg_energy_kwh(
        distance_km=distance_km,
        road_type=road_type,
        vehicle_profile=vehicle_profile,
        load_state=load_state,
        total_mass_kg=total_mass_kg,
        avg_speed_kmh=avg_speed_kmh,
        elevation_gain_m=elevation["elevation_gain_m"],
        elevation_loss_m=elevation["elevation_loss_m"],
        traffic_congestion_level=traffic["congestion_level"],
    )


def plan_charge_to_percent(
    remaining_legs: list[dict],
    vehicle_profile: str,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
    load_state: str = "half_load",
    payload_kg: Optional[float] = None,
    redundancy_neighbor_energies_kwh: Optional[list[float]] = None,
) -> dict:
    """
    Minimum-but-sensible charge target for a stop, in RouteVolt's 20-80% band.
    `remaining_legs` is caller-bounded -- plan_full_journey passes only the
    next CHARGE_TARGET_LOOKAHEAD_SEGMENTS segments' worth, not literally every
    leg to the final destination (see its own docstring for why) -- this
    function just sums whatever list it's handed, agnostic to that bound.

    `redundancy_neighbor_energies_kwh` (optional): real per-edge energy cost
    (kWh) for this stop's top-3 nearest real graph neighbors -- see
    graph_planner.REDUNDANCY_NEIGHBOR_COUNT/get_top_n_neighbors and
    tests/redundancy_cutoff_selection_2026-08-24.md. When provided, the
    charge target is also sized to survive the WORST of these (a real
    backup option if the planned station turns out unavailable), combined
    with the normal target via max() -- see the bottom of this function.

    `safety_margin` is accepted but unused here -- kept only so this
    function's signature doesn't force a cascading change through every
    existing caller (_build_stop_entry, reroute_from_stop, graph_planner.
    _simulate_feasibility_only, all threading it down from DEFAULT_SAFETY_
    MARGIN). This function's own required-energy buffer is no longer
    caller-tunable: see SIMULATION_MARGIN_RATE/SIMULATION_MARGIN_FLOOR_KWH's
    module-level comment for why it's now a fixed proportional-with-floor
    kWh addition instead.

    - Normally: enough to cover every leg in `remaining_legs` plus the
      proportional-with-floor margin (see SIMULATION_MARGIN_RATE/
      SIMULATION_MARGIN_FLOOR_KWH), floored at BATTERY_FLOOR_PCT (no point
      charging to less than a healthy minimum) and capped at
      BATTERY_CEILING_PCT (don't overcharge if this stretch doesn't need it).
    - Exception: if BATTERY_CEILING_PCT alone wouldn't be enough to survive
      this stretch, raise the target above the ceiling, capped at 100%, and
      flag it via "exceeded_ceiling" so callers/UI can show that this stop is
      a deliberate exception, not silent overcharging.

      Checked two ways, taking whichever demands more charge: required_pct_all
      (the cumulative energy for every leg in `remaining_legs`, already
      computed above) exceeding the ceiling, and next_leg_required_pct (just
      remaining_legs[0]) exceeding it. required_pct_all alone would already
      catch a single energy-hungry immediate leg (it's included in the
      cumulative sum), but next_leg_required_pct is kept as an independent
      floor since a bounded remaining_legs could in principle model an
      intermediate charging opportunity not reflected in required_pct_all's
      flat sum -- in that case the immediate next leg could demand more than
      the (now-lower) cumulative figure.
      Bug fixed here: previously only next_leg_required_pct was checked, so a
      cumulative shortfall across multiple legs -- none individually exceeding
      the ceiling -- silently clamped target_pct to BATTERY_CEILING_PCT instead
      of raising it, even though required_pct_all had already computed that
      the ceiling wasn't enough. Confirmed real-world instance: a stop charged
      to exactly 80.0% with exceeded_ceiling=false whose next stretch's
      required_pct_all exceeded 80%, then the simulation ran out of energy
      several legs later at a point no single leg's own check could catch.
      (Immediately after this fix landed, `remaining_legs` still meant "every
      leg to the final destination," which made required_pct_all exceed the
      ceiling at nearly every stop of a long multi-stop trip -- true almost
      by construction, not a real per-stop shortfall. The caller-side bound
      described above is what narrowed this back down to real shortfalls.)
    """
    total_mass_kg = estimate_total_mass_kg(vehicle_profile, load_state, payload_kg)
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]

    remaining_energy_kwh = sum(
        _leg_energy_kwh(leg, vehicle_profile, load_state, total_mass_kg)
        for leg in remaining_legs
    )
    remaining_margin_kwh = max(SIMULATION_MARGIN_FLOOR_KWH, SIMULATION_MARGIN_RATE * remaining_energy_kwh)
    required_pct_all = ((remaining_energy_kwh + remaining_margin_kwh) / capacity_kwh) * 100.0

    target_pct = max(required_pct_all, BATTERY_FLOOR_PCT)
    target_pct = min(target_pct, BATTERY_CEILING_PCT)

    next_leg_required_pct = 0.0
    if remaining_legs:
        next_leg_energy_kwh = _leg_energy_kwh(remaining_legs[0], vehicle_profile, load_state, total_mass_kg)
        next_leg_margin_kwh = max(SIMULATION_MARGIN_FLOOR_KWH, SIMULATION_MARGIN_RATE * next_leg_energy_kwh)
        next_leg_required_pct = ((next_leg_energy_kwh + next_leg_margin_kwh) / capacity_kwh) * 100.0

    exceeded_ceiling = required_pct_all > BATTERY_CEILING_PCT or next_leg_required_pct > BATTERY_CEILING_PCT
    if exceeded_ceiling:
        target_pct = min(max(target_pct, required_pct_all, next_leg_required_pct), 100.0)

    # Redundancy charge-sizing (top-3 nearest real graph neighbors -- see
    # this function's docstring and REDUNDANCY_NEIGHBOR_COUNT). Same margin
    # formula as required_pct_all/next_leg_required_pct above, for
    # apples-to-apples with the diagnostic's numbers. Combined via max(),
    # NOT additive -- the same combination pattern already used above
    # (target_pct vs required_pct_all/next_leg_required_pct) -- per
    # tests/redundancy_charging_implementation_2026-08-24.md. Raising the
    # target above BATTERY_CEILING_PCT this way is a genuine ceiling
    # exception, same as the required_pct_all/next_leg_required_pct case
    # above, so it also flips exceeded_ceiling.
    if redundancy_neighbor_energies_kwh:
        worst_neighbor_energy_kwh = max(redundancy_neighbor_energies_kwh)
        redundancy_margin_kwh = max(
            SIMULATION_MARGIN_FLOOR_KWH, SIMULATION_MARGIN_RATE * worst_neighbor_energy_kwh
        )
        redundancy_target_pct = min(
            ((worst_neighbor_energy_kwh + redundancy_margin_kwh) / capacity_kwh) * 100.0, 100.0
        )
        if redundancy_target_pct > target_pct:
            target_pct = redundancy_target_pct
            if target_pct > BATTERY_CEILING_PCT:
                exceeded_ceiling = True

    return {
        "charge_to_percent": round(target_pct, 1),
        "exceeded_ceiling": exceeded_ceiling,
    }


def _estimate_charge_duration_min(energy_needed_kwh: float, power_kw: float) -> float:
    """
    Linear kWh/min model: duration = energy / power. Real DC fast charging
    tapers well before 80-100% (charge curve), which this ignores -- a
    documented approximation, not a full charge-curve model.
    """
    effective_power_kw = power_kw if power_kw and power_kw > 0 else DEFAULT_FALLBACK_CHARGE_POWER_KW
    return (energy_needed_kwh / effective_power_kw) * 60.0


def _rank_reachable_candidates(
    position: tuple[float, float],
    battery_pct: float,
    vehicle_profile: str,
    load_state: str,
    payload_kg: Optional[float],
    top_n: int = TOP_N_ALTERNATIVES,
) -> list[dict]:
    """Top reachable stations from `position`, via route_optimizer's existing scoring."""
    lat, lng = position
    result = calculate_best_route(
        battery=battery_pct,
        distance=0,
        vehicle_profile=vehicle_profile,
        load_state=load_state,
        payload_kg=payload_kg,
        origin_lat=lat,
        origin_lon=lng,
    )

    reachable = [c for c in result["candidates"] if c["reachable"]]
    reachable.sort(key=lambda c: station_score(c["detour_km"], c["power_kw"], c.get("expected_wait_minutes", 0.0)))

    alternatives = []
    for c in reachable[:top_n]:
        detour_km = c["detour_km"]
        alternatives.append({
            "station_id": c.get("station_id"),
            "station_name": c["station_name"],
            "latitude": c["latitude"],
            "longitude": c["longitude"],
            "power_kw": c["power_kw"],
            "detour_km": detour_km,
            "detour_time_min": round((detour_km / DETOUR_ASSUMED_SPEED_KMH) * 60.0, 1),
            "requires_detour": detour_km > DETOUR_ON_ROUTE_THRESHOLD_KM,
            "predicted_energy_kwh": c["predicted_energy_kwh"],
            "expected_wait_minutes": c.get("expected_wait_minutes", 0.0),
            "queue_length": c.get("queue_length", 0),
        })
    return alternatives


def _build_stop_entry(
    station: dict,
    leg_index: int,
    position_before_segment: tuple[float, float],
    battery_pct_before_segment: float,
    battery_pct_on_arrival: float,
    remaining_legs: list[dict],
    vehicle_profile: str,
    load_state: str,
    payload_kg: Optional[float],
    safety_margin: float,
) -> dict:
    """
    Build one ChargingStop entry for a station the graph already chose (see
    plan_full_journey) -- this does NOT search for a station, only computes
    the charge target and reports it, plus a same-shape "alternatives" list
    (ranked from the position/battery the vehicle had *before* this segment,
    same as the old search would have seen) purely for display and for
    /route/reroute-stop to fall back on if this exact station turns out
    unavailable.

    `station` may carry "_redundancy_top3_neighbors" (attached by
    graph_planner.plan_journey_via_graph, see get_top_n_neighbors) -- this
    stop's top-3 nearest real graph neighbors, each with real energy_kwh/
    distance_km. When present, feeds plan_charge_to_percent's redundancy
    sizing (see that function's docstring) and is also surfaced verbatim as
    "backup_stations" for the frontend/UI -- "the same top-3" used for both,
    per tests/redundancy_charging_implementation_2026-08-24.md.
    """
    alternatives = _rank_reachable_candidates(
        position_before_segment, battery_pct_before_segment, vehicle_profile, load_state, payload_kg
    )
    redundancy_neighbors = station.get("_redundancy_top3_neighbors") or []
    charge_plan = plan_charge_to_percent(
        remaining_legs=remaining_legs,
        vehicle_profile=vehicle_profile,
        safety_margin=safety_margin,
        load_state=load_state,
        payload_kg=payload_kg,
        redundancy_neighbor_energies_kwh=[n["energy_kwh"] for n in redundancy_neighbors],
    )
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]
    energy_needed_kwh = max(charge_plan["charge_to_percent"] - battery_pct_on_arrival, 0.0) / 100.0 * capacity_kwh
    power_kw = clean_number(station.get("power_kw"), default=0.0)

    try:
        wait = charger_service.estimate_wait_time(station.get("id"), datetime.now(timezone.utc))
    except ValueError:
        wait = {"expected_wait_minutes": 0.0, "queue_length": 0, "confidence": "heuristic"}

    backup_stations = [
        {
            "station_id": n.get("station_id"),
            "station_name": n.get("station_name"),
            "latitude": n["lat"],
            "longitude": n["lon"],
            "distance_km": n.get("distance_km"),
            "energy_kwh": n["energy_kwh"],
        }
        for n in redundancy_neighbors
    ]

    return {
        "leg_index": leg_index,
        "location": {"lat": station["lat"], "lng": station["lon"]},
        "battery_pct_on_arrival": round(battery_pct_on_arrival, 2),
        "station_id": station.get("id"),
        "station_name": station.get("station_name"),
        "charge_to_percent": charge_plan["charge_to_percent"],
        "exceeded_ceiling": charge_plan["exceeded_ceiling"],
        "estimated_charge_duration_min": round(
            _estimate_charge_duration_min(energy_needed_kwh, power_kw), 1
        ),
        "expected_wait_minutes": wait["expected_wait_minutes"],
        "queue_length": wait["queue_length"],
        "alternatives": alternatives,
        "backup_stations": backup_stations,
    }


def plan_full_journey(
    route_leg_groups: list[list[dict]],
    stops: list[dict],
    battery_pct: float,
    vehicle_profile: str,
    load_state: str = "half_load",
    payload_kg: Optional[float] = None,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
) -> dict:
    """
    Pure leg-by-leg simulator over an *already-decided* stop sequence --
    graph_planner.plan_journey_via_graph is what decides the sequence (via
    Dijkstra over a station graph) and is the only caller that should build
    these arguments; this function just runs the numbers.

    route_leg_groups: one list[RouteLeg] per origin->stop1->...->stopN->destination
    segment (len(stops) + 1 groups total), each fetched from a real
    routing_service.get_route call between consecutive graph nodes.
    stops: the len(route_leg_groups) - 1 intermediate station nodes (graph
    dicts with at least lat/lon/id/station_name/power_kw), in order.

    Two design decisions, made explicitly rather than picked silently:

    (a) Signature: takes a *pre-assembled* list of leg-groups + a fixed stop
    list, rather than a flat route_legs list + doing its own routing HTTP
    calls. The turn-by-turn path was never one continuous route to begin
    with once stations are chosen up front (origin->stop1, stop1->stop2, ...
    are physically separate routed segments) -- plan_journey_via_graph is
    where those routing_service.get_route calls happen, keeping this
    function about simulation only, not network I/O.

    (b) Charge target: still uses plan_charge_to_percent (charge to just
    enough for the legs ahead, not blindly to BATTERY_CEILING_PCT) rather
    than always charging to the ceiling. remaining_legs at each stop is
    bounded to CHARGE_TARGET_LOOKAHEAD_SEGMENTS segments ahead (the next
    charging opportunity, plus the one after that), not every segment to the
    final destination -- summing all the way to the destination made
    plan_charge_to_percent's required_pct_all exceed BATTERY_CEILING_PCT at
    nearly every stop on a long multi-stop trip (the whole remaining trip
    being bigger than one battery is expected and fine, that's why multiple
    stops exist -- it says nothing about whether *this* stop's real next
    stretch has a shortfall). The bounded window still catches a real
    multi-leg shortfall between two charging opportunities (confirmed
    against the real leg-97 Chennai->Nagercoil failure this exists to fix)
    without that false-positive-everywhere behavior. This is a heuristic
    window, not a structural guarantee the way "every remaining leg" was --
    a shortfall spanning more than CHARGE_TARGET_LOOKAHEAD_SEGMENTS segments
    ahead could still be missed by this stop's own check, though each later
    stop gets its own fresh bounded check as the vehicle actually arrives
    there, narrowing the miss window with every stop rather than only
    catching it once, upfront.

    Since the stop sequence is fixed (no more searching), the only failure
    mode left is the detailed simulation (real per-leg speed/elevation/
    traffic) disagreeing with the graph's coarser edge-cost estimate (OSRM
    table distances + a straight 2-point elevation lookup, see
    build_station_graph) badly enough that the battery would go negative
    before reaching the next stop -- reported the same "unreachable_gap" /
    "gap" way the old greedy planner did, for consistency with how
    routes.py and the frontend already consume this shape:
        {
          "legs": [...],                 # every leg actually driven, in order
          "stops": [...],                # every stop actually reached, in order
          "final_battery_pct": float,
          "total_predicted_energy_kwh": float,
          "status": "ok" | "unreachable_gap",
          "gap": {...} | None,
        }
    """
    if len(route_leg_groups) != len(stops) + 1:
        raise ValueError(
            f"route_leg_groups must have exactly one more group than stops "
            f"(got {len(route_leg_groups)} groups for {len(stops)} stops) -- "
            f"one group per origin->stop1->...->stopN->destination segment."
        )

    total_mass_kg = estimate_total_mass_kg(vehicle_profile, load_state, payload_kg)
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]

    # Pre-warm elevation_service's per-point cache with every leg's sampled
    # points in ONE batched call, instead of paying for it lazily leg by leg
    # below -- same trick graph_planner.build_station_graph uses for its own
    # edge loop. _leg_elevation's per-leg compute_leg_elevation call ends up
    # downsampled to a subset of these same points, so it becomes a pure
    # cache hit (no network) once this has run. Best-effort: if Open-Elevation
    # is unreachable (or this batch is too large to answer within its
    # timeout), this silently no-ops and every leg falls back to its own
    # per-leg fetch individually, same as if this call didn't exist.
    all_points = [point for group in route_leg_groups for leg in group for point in (leg.get("points") or [])]
    try:
        elevation_service.get_elevation_profile(all_points)
    except elevation_service.ElevationError:
        pass

    remaining_pct = battery_pct
    leg_log: list[dict] = []
    stop_log: list[dict] = []
    leg_index = 0

    for segment_index, leg_group in enumerate(route_leg_groups):
        position_before_segment = leg_group[0]["start"] if leg_group else None
        battery_pct_before_segment = remaining_pct

        for leg in leg_group:
            elevation = _leg_elevation(leg)
            traffic = _leg_traffic(leg)
            energy_kwh = _leg_energy_kwh(
                leg, vehicle_profile, load_state, total_mass_kg, elevation=elevation, traffic=traffic
            )
            pct_after = remaining_pct - (energy_kwh / capacity_kwh) * 100.0

            if pct_after < 0:
                return {
                    "legs": leg_log,
                    "stops": stop_log,
                    "final_battery_pct": round(remaining_pct, 2),
                    "total_predicted_energy_kwh": round(sum(l["predicted_energy_kwh"] for l in leg_log), 3),
                    "status": "unreachable_gap",
                    "gap": {
                        "leg_index": leg_index,
                        "location": {"lat": leg["start"][0], "lng": leg["start"][1]},
                        "battery_pct_at_gap": round(remaining_pct, 2),
                        "reason": "The detailed leg-by-leg simulation needs more energy for this "
                                  "stretch than the upfront graph plan assumed -- the planned stop "
                                  "sequence turned out infeasible once real route/elevation/traffic "
                                  "data was taken into account.",
                    },
                }

            remaining_pct = pct_after
            leg_log.append({
                **leg,
                "predicted_energy_kwh": round(energy_kwh, 3),
                "battery_pct_after": round(remaining_pct, 2),
                "elevation_gain_m": round(elevation["elevation_gain_m"], 1),
                "elevation_loss_m": round(elevation["elevation_loss_m"], 1),
                "elevation_source": elevation["elevation_source"],
                "traffic_congestion_level": round(traffic["congestion_level"], 3),
                "traffic_source": traffic["congestion_source"],
            })
            leg_index += 1

        if segment_index < len(stops):
            station = stops[segment_index]
            # Bounded to CHARGE_TARGET_LOOKAHEAD_SEGMENTS segments ahead (the
            # next charging opportunity, plus the one after it), not every
            # segment to the final destination -- see plan_full_journey's own
            # docstring (b). Slicing past the end of route_leg_groups is safe
            # and does the right thing near the end of the trip: it just
            # shrinks to whatever segments actually remain.
            lookahead_end = segment_index + 1 + CHARGE_TARGET_LOOKAHEAD_SEGMENTS
            remaining_route_legs = [
                leg for group in route_leg_groups[segment_index + 1:lookahead_end] for leg in group
            ]
            stop_entry = _build_stop_entry(
                station=station,
                leg_index=leg_index,
                position_before_segment=position_before_segment,
                battery_pct_before_segment=battery_pct_before_segment,
                battery_pct_on_arrival=remaining_pct,
                remaining_legs=remaining_route_legs,
                vehicle_profile=vehicle_profile,
                load_state=load_state,
                payload_kg=payload_kg,
                safety_margin=safety_margin,
            )
            stop_log.append(stop_entry)
            remaining_pct = stop_entry["charge_to_percent"]

    return {
        "legs": leg_log,
        "stops": stop_log,
        "final_battery_pct": round(remaining_pct, 2),
        "total_predicted_energy_kwh": round(sum(l["predicted_energy_kwh"] for l in leg_log), 3),
        "status": "ok",
        "gap": None,
    }


def _station_is_available(station_id) -> bool:
    station = charger_service.get_station(station_id)
    if station is None or str(station.get("status", "")).lower() != "operational":
        return False

    occupancy = charger_service.get_blended_occupancy(station_id, datetime.now(timezone.utc))
    return occupancy.get("expected_available_ports", 1) > 0


def reroute_from_stop(
    current_stop_alternatives: list[dict],
    failed_station_id,
    remaining_route_legs: list[dict],
    battery_pct: float,
    vehicle_profile: str,
    load_state: str = "half_load",
    payload_kg: Optional[float] = None,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
) -> dict:
    """
    The station planned at a stop turned out to be unavailable (occupied,
    offline, or reported busy). Logs that as a crowdsourced occupancy report
    (so get_blended_occupancy reflects it for future callers too), then walks
    current_stop_alternatives -- the same top-5 list already computed for
    that stop -- picking the next one that's actually available (checked via
    charger_service: station status + get_blended_occupancy's blended
    available-ports count).

    Documented simplification: the rest of the journey is then simulated as
    a *single* segment with no further pre-planned stops (plan_full_journey
    with one route_leg_groups entry and stops=[]), rather than triggering a
    fresh graph_planner.plan_journey_via_graph solve from the new station's
    position. A full graph replan here would be the more correct fix if
    remaining_route_legs is long enough to need another stop of its own, but
    that's out of scope for this pass -- flagged here, not silently handled,
    same spirit as graph_planner.find_shortest_path's TODO.
    """
    charger_service.add_occupancy_report(failed_station_id, "busy", source="reroute_failure")

    candidates = [
        alt for alt in current_stop_alternatives
        if str(alt.get("station_id")) != str(failed_station_id)
    ]

    chosen = next((alt for alt in candidates if _station_is_available(alt.get("station_id"))), None)
    if chosen is None:
        return {
            "status": "unreachable_gap",
            "reason": "No available alternative station at this stop.",
            "failed_station_id": failed_station_id,
        }

    charge_plan = plan_charge_to_percent(
        remaining_legs=remaining_route_legs,
        vehicle_profile=vehicle_profile,
        safety_margin=safety_margin,
        load_state=load_state,
        payload_kg=payload_kg,
    )

    remaining_plan = plan_full_journey(
        route_leg_groups=[remaining_route_legs],
        stops=[],
        battery_pct=charge_plan["charge_to_percent"],
        vehicle_profile=vehicle_profile,
        load_state=load_state,
        payload_kg=payload_kg,
        safety_margin=safety_margin,
    )

    return {
        "status": "rerouted",
        "new_station": chosen,
        "charge_to_percent": charge_plan["charge_to_percent"],
        "exceeded_ceiling": charge_plan["exceeded_ceiling"],
        "remaining_plan": remaining_plan,
    }
