"""
Charging station catalogue + occupancy heuristics for RouteVolt.

Stations are read from the `stations` table (see
backend/app/scripts/seed_stations.py), not the CSV directly. The CSV
(dataset/tamil_nadu_ev_charging_stations_CONSOLIDATED.csv) stays the source
of truth for re-seeding: download_chargers.py regenerates it, seed_stations.py
reloads it into the DB. This module only ever queries the DB.
"""

import itertools
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.app.database.db import SessionLocal
from backend.app.database.models import OccupancyReport, OccupancyStatus, Station
from backend.app.utils.connector_normalizer import normalize_connector_types

# ---- Occupancy heuristic constants (PLACEHOLDER -- see get_blended_occupancy) ----
BASE_OCCUPANCY_RATIO = 0.35

MORNING_PEAK_HOURS = {8, 9}
EVENING_PEAK_HOURS = {17, 18, 19}
NIGHT_HOURS = {23, 0, 1, 2, 3, 4, 5}
PEAK_HOUR_MULTIPLIER = 1.8
NIGHT_HOUR_MULTIPLIER = 0.6

HIGHWAY_WEEKEND_MULTIPLIER = 1.4  # highway-corridor stations see more weekend traffic
CITY_WEEKDAY_MULTIPLIER = 1.3     # city stations see more weekday commuter traffic

# ---- Crowdsourced-report blend constants (Phase 4) ----
RECENT_REPORTS_BLEND_LIMIT = 10
OCCUPANCY_REPORT_WINDOW_MINUTES = 45.0   # "recent" window for blending -- configurable per call
REPORT_DECAY_HALF_LIFE_MINUTES = 15.0    # a report's blend weight halves every 15 min
# Blend weight (how much the reported ratio pulls the estimate away from the
# heuristic) scales with how much weighted signal has accumulated, rather
# than being fixed -- one stale-ish report shouldn't swing the estimate as
# hard as several fresh ones. Saturates at MAX_REPORT_BLEND_WEIGHT once
# REPORT_BLEND_SATURATION_EFFECTIVE_COUNT worth of decayed report weight has
# accumulated (e.g. ~4 fresh reports, or more older ones).
MAX_REPORT_BLEND_WEIGHT = 0.85
REPORT_BLEND_SATURATION_EFFECTIVE_COUNT = 4.0
# confidence flips from "blended" to "crowdsourced" once at least this much
# decayed report weight has accumulated -- enough real signal that the
# estimate is mostly reported reality, not mostly heuristic.
CROWDSOURCED_CONFIDENCE_THRESHOLD = 2.5

# ---- Wait-time / queueing constants (Phase 5) ----
# Based on DOE real-world paid DCFC session data (avg 42 min full session
# incl. dwell time) and UK rapid-charger studies (~28-29 min pure charging
# time); 35 min is an interim default for paid public DCFC -- replace with a
# measured average, ideally per vehicle_profile, once real RouteVolt session
# logs accumulate.
AVERAGE_SESSION_MINUTES = 35.0

# ---- Reservation-based queue (demo layer, in-memory) ----
# Deterministic, per-charger-slot reservations for a live multi-tab demo --
# sits on top of the heuristic/blend/crowdsourced tiers above, not a
# replacement: get_blended_occupancy/estimate_wait_time only consult this
# store when a station has at least one live reservation (see
# _reservation_occupancy/_reservation_wait below), so every station nobody
# has joined a queue at keeps behaving exactly as before. Deliberately
# in-memory, not a DB table -- process-lifetime demo state, not something
# that needs to survive a restart.
RESERVATION_TURNOVER_BUFFER_MINUTES = 5.0  # gap between back-to-back sessions on the same slot (plug/unplug time)

# Duplicated (not imported) from route_optimizer.VEHICLE_PROFILES's
# battery_capacity_kwh: route_optimizer imports estimate_wait_time/
# get_stations from this module, so importing route_optimizer here would
# create a charger_service -> route_optimizer -> charger_service cycle.
# Keep these numbers in sync with route_optimizer.VEHICLE_PROFILES.
_VEHICLE_BATTERY_CAPACITY_KWH = {
    "small_ev": 24,
    "mid_ev": 40,
    "large_ev": 75,
}
DEFAULT_CHARGE_POWER_KW = 7.0  # fallback when a station's power_kw can't be parsed as a plain number


@dataclass
class Reservation:
    reservation_id: int
    station_id: int
    charger_slot: int
    user_id: str
    user_name: Optional[str]
    requested_eta: datetime
    start_time: datetime
    end_time: datetime
    status: str = "active"  # "active" | "released"


_reservation_lock = threading.Lock()
_reservations: dict[int, Reservation] = {}
_reservation_id_counter = itertools.count(1)


def _station_to_dict(station: Station) -> dict:
    return {
        "id": station.id,
        "station_name": station.station_name,
        "operator": station.operator,
        "address": station.address,
        "city": station.city,
        "district_area": station.district_area,
        "state": station.state,
        "pincode": station.pincode,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "num_chargers": station.num_chargers,
        "connector_types": station.connector_types,
        "connector_buckets": sorted(normalize_connector_types(station.connector_types)),
        "power_kw": station.power_kw,
        "status": station.status,
        "pricing": station.pricing,
        "source_url": station.source_url,
        "coord_precision": station.coord_precision,
        "corridor": station.corridor,
    }


def get_stations() -> list[dict]:
    """Return all operational charging stations as plain dicts."""
    db = SessionLocal()
    try:
        rows = db.query(Station).filter(Station.status.ilike("operational")).all()
        return [_station_to_dict(row) for row in rows]
    finally:
        db.close()


def get_station(station_id) -> Optional[dict]:
    """Look up one station by id. Returns None if it doesn't exist or the id isn't valid."""
    try:
        station_id = int(station_id)
    except (TypeError, ValueError):
        return None

    db = SessionLocal()
    try:
        row = db.get(Station, station_id)
        return _station_to_dict(row) if row else None
    finally:
        db.close()


def station_exists(station_id) -> bool:
    return get_station(station_id) is not None


def _is_highway_corridor(corridor) -> bool:
    corridor = str(corridor or "").strip().lower()
    return corridor.startswith("nh") or corridor.startswith("sh")


def _heuristic_occupancy(station: dict, timestamp: datetime) -> dict:
    num_chargers = station["num_chargers"] or 1
    is_highway = _is_highway_corridor(station["corridor"])
    is_weekend = timestamp.weekday() >= 5  # Saturday=5, Sunday=6

    hour = timestamp.hour
    if hour in MORNING_PEAK_HOURS or hour in EVENING_PEAK_HOURS:
        time_multiplier = PEAK_HOUR_MULTIPLIER
    elif hour in NIGHT_HOURS:
        time_multiplier = NIGHT_HOUR_MULTIPLIER
    else:
        time_multiplier = 1.0

    if is_highway and is_weekend:
        day_multiplier = HIGHWAY_WEEKEND_MULTIPLIER
    elif not is_highway and not is_weekend:
        day_multiplier = CITY_WEEKDAY_MULTIPLIER
    else:
        day_multiplier = 1.0

    occupancy_ratio = min(BASE_OCCUPANCY_RATIO * time_multiplier * day_multiplier, 1.0)
    expected_occupied = max(0, min(round(occupancy_ratio * num_chargers), num_chargers))

    return {
        "expected_occupied_ports": expected_occupied,
        "expected_available_ports": num_chargers - expected_occupied,
        "confidence": "heuristic",
    }


def _query_reports(station_id: int, limit: int, since: Optional[datetime] = None) -> list[dict]:
    """
    Shared query behind both GET /stations/{id}/reports/recent
    (get_recent_reports) and the occupancy blend (_blend_with_recent_reports)
    -- same "most recent N for this station" query, optionally windowed by
    `since`, so the two don't drift out of sync with separate copies.
    """
    db = SessionLocal()
    try:
        query = db.query(OccupancyReport).filter(OccupancyReport.station_id == station_id)
        if since is not None:
            query = query.filter(OccupancyReport.reported_at >= since)
        rows = query.order_by(OccupancyReport.reported_at.desc()).limit(limit).all()
        # detach the fields we need before the session closes
        return [
            {
                "id": r.id,
                "station_id": r.station_id,
                "reported_status": r.reported_status,
                "reported_at": r.reported_at,
                "source": r.source,
            }
            for r in rows
        ]
    finally:
        db.close()


def _blend_with_recent_reports(
    station_id: int, heuristic: dict, num_chargers: int, window_minutes: float
) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    reports = _query_reports(station_id, limit=RECENT_REPORTS_BLEND_LIMIT, since=cutoff)

    if not reports:
        return heuristic

    now = datetime.now(timezone.utc)
    weighted_sum = 0.0
    weight_total = 0.0
    for report in reports:
        reported_status = report["reported_status"]
        reported_at = report["reported_at"]
        if reported_status == OccupancyStatus.unknown:
            continue  # carries no occupancy signal
        if reported_at.tzinfo is None:
            reported_at = reported_at.replace(tzinfo=timezone.utc)
        age_minutes = max((now - reported_at).total_seconds() / 60.0, 0.0)
        weight = 0.5 ** (age_minutes / REPORT_DECAY_HALF_LIFE_MINUTES)
        signal = 1.0 if reported_status == OccupancyStatus.busy else 0.0
        weighted_sum += signal * weight
        weight_total += weight

    if weight_total == 0:
        return heuristic

    reported_ratio = weighted_sum / weight_total
    heuristic_ratio = heuristic["expected_occupied_ports"] / num_chargers if num_chargers else 0.0

    # More/fresher reports (higher weight_total) pull the estimate further
    # from the heuristic and closer to reported reality -- see
    # REPORT_BLEND_SATURATION_EFFECTIVE_COUNT/MAX_REPORT_BLEND_WEIGHT.
    blend_weight = min(weight_total / REPORT_BLEND_SATURATION_EFFECTIVE_COUNT, 1.0) * MAX_REPORT_BLEND_WEIGHT
    blended_ratio = blend_weight * reported_ratio + (1 - blend_weight) * heuristic_ratio
    expected_occupied = max(0, min(round(blended_ratio * num_chargers), num_chargers))

    confidence = "crowdsourced" if weight_total >= CROWDSOURCED_CONFIDENCE_THRESHOLD else "blended"

    return {
        "expected_occupied_ports": expected_occupied,
        "expected_available_ports": num_chargers - expected_occupied,
        "confidence": confidence,
    }


def get_blended_occupancy(
    station_id: str, timestamp: datetime, window_minutes: float = OCCUPANCY_REPORT_WINDOW_MINUTES
) -> dict:
    """
    Real-time occupancy estimate: the heuristic (see _heuristic_occupancy)
    blended with recent crowdsourced reports (Phase 4).

    With zero recent reports (or only "unknown" ones, which carry no
    occupancy signal) in the last `window_minutes`, returns the heuristic
    unchanged -- "confidence": "heuristic". With real signal, each report is
    weighted by exponential recency decay (REPORT_DECAY_HALF_LIFE_MINUTES)
    and blended in as a correction on top of the heuristic prior, and
    "confidence" becomes "blended" or "crowdsourced" depending on how much
    weighted signal contributed (see CROWDSOURCED_CONFIDENCE_THRESHOLD) --
    this is still hour-of-day/day-of-week/corridor-type heuristics plus
    reports, not a trained model; there isn't enough crowdsourced history
    yet to train one (see estimate_wait_time for the arithmetic built on top
    of this, also not ML yet).

    If the station has any live reservations (see join_queue), those take
    over completely -- "confidence": "reserved", ranked above "crowdsourced"
    since it's exact rather than estimated -- and the heuristic/report blend
    below never runs. A station nobody has reserved through the queue demo
    falls through to the heuristic/blend logic exactly as before.
    """
    station = get_station(station_id)
    if station is None:
        raise ValueError(f"Unknown station_id '{station_id}'")

    num_chargers = station["num_chargers"] or 1

    reservation_occupancy = _reservation_occupancy(station["id"], num_chargers, timestamp)
    if reservation_occupancy is not None:
        return reservation_occupancy

    heuristic = _heuristic_occupancy(station, timestamp)
    return _blend_with_recent_reports(station["id"], heuristic, num_chargers, window_minutes)


def estimate_wait_time(station_id: str, timestamp: datetime) -> dict:
    """
    Simple queueing-arithmetic (not ML) wait estimate for a car arriving
    right now, given get_blended_occupancy's current best guess at how many
    ports are already occupied:

    - occupied >= num_chargers: everyone is full, so at least one car is
      already waiting (queue_length = occupied - num_chargers + 1); expected
      wait is that queue spread evenly across num_chargers ports finishing
      in parallel, each taking about AVERAGE_SESSION_MINUTES.
    - occupied < num_chargers: a port is free now, no wait.

    Not a Poisson/gradient-boosted forecast -- there isn't enough logged
    session history yet (see AVERAGE_SESSION_MINUTES). Replace this with a
    real model once RouteVolt has real session logs to train one, same
    spirit as get_blended_occupancy's heuristic-until-there's-data approach.

    If the station has any live reservations, the wait is derived directly
    from their real start/end times (see _reservation_wait) instead of the
    AVERAGE_SESSION_MINUTES arithmetic below -- exact rather than estimated,
    same "reserved" tier as get_blended_occupancy. Falls through to the
    existing arithmetic, unchanged, for every other station.
    """
    station = get_station(station_id)
    if station is None:
        raise ValueError(f"Unknown station_id '{station_id}'")

    num_chargers = station["num_chargers"] or 1

    reservation_wait = _reservation_wait(station["id"], num_chargers, timestamp)
    if reservation_wait is not None:
        return reservation_wait

    occupancy = get_blended_occupancy(station_id, timestamp)
    occupied = occupancy["expected_occupied_ports"]

    if occupied >= num_chargers:
        queue_length = occupied - num_chargers + 1
        wait_minutes = (queue_length / num_chargers) * AVERAGE_SESSION_MINUTES
    else:
        queue_length = 0
        wait_minutes = 0.0

    return {
        "expected_wait_minutes": round(wait_minutes, 1),
        "queue_length": queue_length,
        "confidence": occupancy["confidence"],
    }


def add_occupancy_report(station_id, reported_status: str, source: str = "user_report") -> dict:
    station_id = int(station_id)
    db = SessionLocal()
    try:
        report = OccupancyReport(
            station_id=station_id,
            reported_status=OccupancyStatus(reported_status),
            source=source,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        return {
            "id": report.id,
            "station_id": report.station_id,
            "reported_status": report.reported_status.value,
            "reported_at": report.reported_at,
            "source": report.source,
        }
    finally:
        db.close()


def get_recent_reports(station_id, limit: int = 5) -> list[dict]:
    station_id = int(station_id)
    reports = _query_reports(station_id, limit=limit)
    return [{**r, "reported_status": r["reported_status"].value} for r in reports]


# ---- Reservation-based queue (demo layer) ----


def _live_reservations(station_id: int) -> list[Reservation]:
    with _reservation_lock:
        return [r for r in _reservations.values() if r.station_id == station_id and r.status == "active"]


def _reservation_occupancy(station_id: int, num_chargers: int, at_time: datetime) -> Optional[dict]:
    """None if the station has no live reservations (fall through to the
    heuristic/blend tiers); otherwise the exact occupied/available count at
    `at_time`, "confidence": "reserved"."""
    live = _live_reservations(station_id)
    if not live:
        return None

    occupied = sum(1 for r in live if r.start_time <= at_time < r.end_time)
    occupied = min(occupied, num_chargers)
    return {
        "expected_occupied_ports": occupied,
        "expected_available_ports": num_chargers - occupied,
        "confidence": "reserved",
    }


def _reservation_wait(station_id: int, num_chargers: int, at_time: datetime) -> Optional[dict]:
    """None if the station has no live reservations (fall through to the
    AVERAGE_SESSION_MINUTES arithmetic); otherwise the exact wait a new
    arrival at `at_time` would face, derived from real reservation end
    times."""
    live = _live_reservations(station_id)
    if not live:
        return None

    soonest_free_per_slot = []
    for slot in range(1, num_chargers + 1):
        slot_reservations = [r for r in live if r.charger_slot == slot]
        soonest_free_per_slot.append(max((r.end_time for r in slot_reservations), default=at_time))
    soonest_free = min(soonest_free_per_slot)

    wait_minutes = max((soonest_free - at_time).total_seconds() / 60.0, 0.0)
    queue_length = max(len(live) - num_chargers, 0) if wait_minutes > 0 else 0

    return {
        "expected_wait_minutes": round(wait_minutes, 1),
        "queue_length": queue_length,
        "confidence": "reserved",
    }


def _station_power_kw(station: dict) -> float:
    try:
        power = float(station.get("power_kw"))
        if power > 0:
            return power
    except (TypeError, ValueError):
        pass
    return DEFAULT_CHARGE_POWER_KW


def estimate_charge_duration_for_station(
    station_id, current_battery_pct: float, target_battery_pct: float, vehicle_profile: str = "mid_ev"
) -> float:
    """Minutes to charge from current_battery_pct to target_battery_pct at
    this station's power_kw, for a vehicle_profile's battery capacity (see
    _VEHICLE_BATTERY_CAPACITY_KWH). Linear estimate -- no charge-curve
    tapering -- same spirit as the rest of this module's queueing arithmetic
    (AVERAGE_SESSION_MINUTES etc.): good enough for the demo's deterministic
    scheduling, not a physical charge-curve model."""
    station = get_station(station_id)
    if station is None:
        raise ValueError(f"Unknown station_id '{station_id}'")

    capacity_kwh = _VEHICLE_BATTERY_CAPACITY_KWH.get(vehicle_profile, _VEHICLE_BATTERY_CAPACITY_KWH["mid_ev"])
    power_kw = _station_power_kw(station)
    pct_delta = max(target_battery_pct - current_battery_pct, 0.0)
    energy_kwh = (pct_delta / 100.0) * capacity_kwh
    duration_min = (energy_kwh / power_kw) * 60.0
    return round(duration_min, 1)


def join_queue(
    station_id,
    user_id: str,
    charge_duration_min: float,
    user_name: Optional[str] = None,
    eta: Optional[datetime] = None,
) -> dict:
    """
    Reserve whichever charger slot at `station_id` frees up soonest at/after
    `eta` (immediate if a slot is free now; otherwise chained off that
    slot's existing reservations plus RESERVATION_TURNOVER_BUFFER_MINUTES).
    Deterministic and race-free: the whole read-decide-write sequence runs
    under _reservation_lock, so two near-simultaneous joins (e.g. two demo
    browser tabs) can never be assigned the same slot for overlapping times.

    Returns the assignment: reservation_id, station_id, charger_slot,
    wait_minutes (eta -> actual start_time gap), start_time, end_time,
    queue_position (1-indexed position in the chosen slot's queue).
    """
    station_id = int(station_id)
    station = get_station(station_id)
    if station is None:
        raise ValueError(f"Unknown station_id '{station_id}'")

    num_chargers = station["num_chargers"] or 1
    now = datetime.now(timezone.utc)
    eta = eta or now
    if eta.tzinfo is None:
        eta = eta.replace(tzinfo=timezone.utc)

    with _reservation_lock:
        live = [r for r in _reservations.values() if r.station_id == station_id and r.status == "active"]

        best_slot = None
        best_start = None
        best_queue_position = None
        for slot in range(1, num_chargers + 1):
            slot_reservations = sorted((r for r in live if r.charger_slot == slot), key=lambda r: r.start_time)
            if slot_reservations:
                slot_free_at = slot_reservations[-1].end_time + timedelta(minutes=RESERVATION_TURNOVER_BUFFER_MINUTES)
            else:
                slot_free_at = now
            start_time = max(eta, slot_free_at)

            if best_start is None or start_time < best_start:
                best_start = start_time
                best_slot = slot
                best_queue_position = len(slot_reservations) + 1

        end_time = best_start + timedelta(minutes=charge_duration_min)
        reservation_id = next(_reservation_id_counter)
        _reservations[reservation_id] = Reservation(
            reservation_id=reservation_id,
            station_id=station_id,
            charger_slot=best_slot,
            user_id=user_id,
            user_name=user_name,
            requested_eta=eta,
            start_time=best_start,
            end_time=end_time,
            status="active",
        )

    wait_minutes = max((best_start - eta).total_seconds() / 60.0, 0.0)
    return {
        "reservation_id": reservation_id,
        "station_id": station_id,
        "charger_slot": best_slot,
        "wait_minutes": round(wait_minutes, 1),
        "start_time": best_start,
        "end_time": end_time,
        "queue_position": best_queue_position,
    }


def _reservation_to_dict(reservation: Reservation) -> dict:
    return {
        "reservation_id": reservation.reservation_id,
        "user_id": reservation.user_id,
        "user_name": reservation.user_name,
        "start_time": reservation.start_time,
        "end_time": reservation.end_time,
    }


def get_queue_state(station_id, at_time: Optional[datetime] = None) -> dict:
    """Per-charger-slot snapshot: current occupant (if any) + upcoming queue,
    for the frontend to poll (GET /stations/{id}/queue)."""
    station_id = int(station_id)
    station = get_station(station_id)
    if station is None:
        raise ValueError(f"Unknown station_id '{station_id}'")

    num_chargers = station["num_chargers"] or 1
    at_time = at_time or datetime.now(timezone.utc)
    if at_time.tzinfo is None:
        at_time = at_time.replace(tzinfo=timezone.utc)

    live = _live_reservations(station_id)

    slots = []
    for slot in range(1, num_chargers + 1):
        slot_reservations = sorted((r for r in live if r.charger_slot == slot), key=lambda r: r.start_time)
        current = next((r for r in slot_reservations if r.start_time <= at_time < r.end_time), None)
        upcoming = [r for r in slot_reservations if r.start_time > at_time]
        slots.append({
            "charger_slot": slot,
            "current": _reservation_to_dict(current) if current else None,
            "upcoming": [_reservation_to_dict(r) for r in upcoming],
        })

    return {"station_id": station_id, "num_chargers": num_chargers, "slots": slots}


def release_reservation(reservation_id: int) -> bool:
    """Cancel a reservation (e.g. "leave queue"). Returns False if the id is
    unknown or already released."""
    with _reservation_lock:
        reservation = _reservations.get(reservation_id)
        if reservation is None or reservation.status != "active":
            return False
        reservation.status = "released"
        return True
