"""
Battery simulation for RouteVolt.

Walks a route leg-by-leg (as returned by routing_service.get_route), running
each leg through the trained energy model via route_optimizer, and chains as
many charging stops as a route actually needs -- not just the first one --
while keeping the battery inside a healthy [BATTERY_FLOOR_PCT,
BATTERY_CEILING_PCT] band. This is the multi-stop planner /route/optimize
and /route/reroute-stop are built on.

Documented simplification: a planned stop is simulated as if it happens
exactly on the main route -- the actual station's detour distance/time is
reported to the caller (each stop's "location" is the route position, its
"alternatives" carry the real station coords) but is not folded back into
the leg-by-leg energy simulation. Same spirit as the weather TODO stub still
in route_optimizer._build_trip_features -- elevation and traffic are no
longer stubbed, see elevation_service.py / traffic_service.py.
"""

from datetime import datetime, timezone
from typing import Optional

from backend.app.services import charger_service, elevation_service, traffic_service
from backend.app.services.route_optimizer import (
    DEFAULT_SAFETY_MARGIN,
    VEHICLE_PROFILES,
    calculate_best_route,
    estimate_leg_energy_kwh,
    estimate_total_mass_kg,
    infer_road_type_from_speed,
    station_score,
)

# ---- Battery band (tune here, not as magic numbers scattered elsewhere) ----
BATTERY_FLOOR_PCT = 20.0    # "must charge soon" -- a stop is planned before crossing this
BATTERY_CEILING_PCT = 80.0  # normal charge target cap; only exceeded if the very next leg demands it

TOP_N_ALTERNATIVES = 5
DETOUR_ASSUMED_SPEED_KMH = 40.0     # rough mixed-road speed for estimating detour time off the main route
DETOUR_ON_ROUTE_THRESHOLD_KM = 1.0  # below this, a station counts as "on route" rather than a real detour
DEFAULT_FALLBACK_CHARGE_POWER_KW = 7.0  # used only when a station's power_kw can't be parsed (see charger CSV notes)

# A leg should need at most one recharge stop planned before it. If charging
# still can't clear the floor for the same leg, no amount of charging will
# (the leg itself exceeds what the vehicle can carry) -- stop looping and
# report it as a gap instead.
MAX_STOP_ATTEMPTS_PER_LEG = 1


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
) -> dict:
    """
    Minimum-but-sensible charge target for a stop, in RouteVolt's 20-80% band:

    - Normally: enough to cover every leg remaining after this stop plus the
      safety margin, floored at BATTERY_FLOOR_PCT (no point charging to less
      than a healthy minimum) and capped at BATTERY_CEILING_PCT (don't
      overcharge if the trip doesn't need it).
    - Exception: if BATTERY_CEILING_PCT alone wouldn't be enough to survive
      *just the very next leg* (remaining_legs[0]) -- i.e. the ceiling would
      strand the vehicle one leg later -- raise the target above the ceiling,
      capped at 100%, and flag it via "exceeded_ceiling" so callers/UI can
      show that this stop is a deliberate exception, not silent overcharging.
    """
    total_mass_kg = estimate_total_mass_kg(vehicle_profile, load_state, payload_kg)
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]
    safety_margin_pct = safety_margin * 100.0

    remaining_energy_kwh = sum(
        _leg_energy_kwh(leg, vehicle_profile, load_state, total_mass_kg)
        for leg in remaining_legs
    )
    required_pct_all = (remaining_energy_kwh / capacity_kwh) * 100.0 + safety_margin_pct

    target_pct = max(required_pct_all, BATTERY_FLOOR_PCT)
    target_pct = min(target_pct, BATTERY_CEILING_PCT)

    exceeded_ceiling = False
    if remaining_legs:
        next_leg_energy_kwh = _leg_energy_kwh(remaining_legs[0], vehicle_profile, load_state, total_mass_kg)
        next_leg_required_pct = (next_leg_energy_kwh / capacity_kwh) * 100.0 + safety_margin_pct
        if next_leg_required_pct > BATTERY_CEILING_PCT:
            target_pct = min(max(target_pct, next_leg_required_pct), 100.0)
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


def _plan_stop(
    current_position: tuple[float, float],
    leg_index: int,
    battery_pct: float,
    remaining_legs: list[dict],
    vehicle_profile: str,
    load_state: str,
    payload_kg: Optional[float],
    safety_margin: float,
) -> dict:
    alternatives = _rank_reachable_candidates(
        current_position, battery_pct, vehicle_profile, load_state, payload_kg
    )
    if not alternatives:
        return {
            "status": "unreachable_gap",
            "gap": {
                "leg_index": leg_index,
                "location": {"lat": current_position[0], "lng": current_position[1]},
                "battery_pct_at_gap": round(battery_pct, 2),
                "reason": "No charging station is reachable from this point with the remaining battery.",
            },
        }

    best = alternatives[0]
    charge_plan = plan_charge_to_percent(
        remaining_legs=remaining_legs,
        vehicle_profile=vehicle_profile,
        safety_margin=safety_margin,
        load_state=load_state,
        payload_kg=payload_kg,
    )
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]
    energy_needed_kwh = max(charge_plan["charge_to_percent"] - battery_pct, 0.0) / 100.0 * capacity_kwh

    return {
        "status": "planned",
        "stop": {
            "leg_index": leg_index,
            "location": {"lat": current_position[0], "lng": current_position[1]},
            "battery_pct_on_arrival": round(battery_pct, 2),
            "station_id": best.get("station_id"),
            "station_name": best["station_name"],
            "charge_to_percent": charge_plan["charge_to_percent"],
            "exceeded_ceiling": charge_plan["exceeded_ceiling"],
            "estimated_charge_duration_min": round(
                _estimate_charge_duration_min(energy_needed_kwh, best["power_kw"]), 1
            ),
            "expected_wait_minutes": best.get("expected_wait_minutes", 0.0),
            "queue_length": best.get("queue_length", 0),
            "alternatives": alternatives,
        },
    }


def plan_full_journey(
    route_legs: list[dict],
    battery_pct: float,
    vehicle_profile: str,
    load_state: str = "half_load",
    payload_kg: Optional[float] = None,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
) -> dict:
    """
    Multi-stop journey planner. Simulates route_legs in order; whenever the
    battery would cross BATTERY_FLOOR_PCT, plans a charging stop (best
    reachable station + charge-to-% via plan_charge_to_percent), resets the
    simulated battery to that target, and keeps going -- chaining as many
    stops as the route actually needs instead of stopping after the first.

    Returns:
        {
          "legs": [...],                 # every leg actually driven, in order
          "stops": [...],                # every planned charging stop, in order
          "final_battery_pct": float,
          "total_predicted_energy_kwh": float,
          "status": "ok" | "unreachable_gap",
          "gap": {...} | None,           # present only when status == "unreachable_gap"
        }

    A "gap" means a genuine dead zone: either no station is reachable from
    some point on the route, or a single leg needs more energy than the
    vehicle can carry even fully charged. Both are returned explicitly
    rather than raised or silently truncated -- the frontend needs to show
    this, not just fail.
    """
    total_mass_kg = estimate_total_mass_kg(vehicle_profile, load_state, payload_kg)
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]

    remaining_pct = battery_pct
    leg_log: list[dict] = []
    stops: list[dict] = []
    attempts_at_leg: dict[int, int] = {}

    i = 0
    n = len(route_legs)
    while i < n:
        leg = route_legs[i]
        elevation = _leg_elevation(leg)
        traffic = _leg_traffic(leg)
        energy_kwh = _leg_energy_kwh(
            leg, vehicle_profile, load_state, total_mass_kg, elevation=elevation, traffic=traffic
        )
        pct_after = remaining_pct - (energy_kwh / capacity_kwh) * 100.0

        if pct_after < BATTERY_FLOOR_PCT:
            attempts_at_leg[i] = attempts_at_leg.get(i, 0) + 1
            if attempts_at_leg[i] > MAX_STOP_ATTEMPTS_PER_LEG:
                return {
                    "legs": leg_log,
                    "stops": stops,
                    "final_battery_pct": round(remaining_pct, 2),
                    "total_predicted_energy_kwh": round(sum(l["predicted_energy_kwh"] for l in leg_log), 3),
                    "status": "unreachable_gap",
                    "gap": {
                        "leg_index": i,
                        "location": {"lat": leg["start"][0], "lng": leg["start"][1]},
                        "battery_pct_at_gap": round(remaining_pct, 2),
                        "reason": "This leg needs more energy than the vehicle can carry "
                                  "even at a full charge plus safety margin.",
                    },
                }

            outcome = _plan_stop(
                current_position=leg["start"],
                leg_index=i,
                battery_pct=remaining_pct,
                remaining_legs=route_legs[i:],
                vehicle_profile=vehicle_profile,
                load_state=load_state,
                payload_kg=payload_kg,
                safety_margin=safety_margin,
            )
            if outcome["status"] == "unreachable_gap":
                return {
                    "legs": leg_log,
                    "stops": stops,
                    "final_battery_pct": round(remaining_pct, 2),
                    "total_predicted_energy_kwh": round(sum(l["predicted_energy_kwh"] for l in leg_log), 3),
                    "status": "unreachable_gap",
                    "gap": outcome["gap"],
                }

            stops.append(outcome["stop"])
            remaining_pct = outcome["stop"]["charge_to_percent"]
            continue  # retry the same leg now that the battery's topped up

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
        i += 1

    return {
        "legs": leg_log,
        "stops": stops,
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
    available-ports count), and re-runs the multi-stop chaining loop over
    remaining_route_legs starting from that station's battery level.
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
        route_legs=remaining_route_legs,
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
