"""
Route optimization core.

Vehicle mass constants mirror generate_dataset.py / dataset_schema.md (v2) so that
whatever model gets trained on trip_energy_dataset.csv sees the same feature
definitions at inference time as it did at training time.
"""

import math
from datetime import datetime, timezone
from typing import Optional

import joblib
import pandas as pd

from backend.app.config import ENERGY_MODEL_PATH
from backend.app.services import elevation_service, traffic_service
from backend.app.services.charger_service import estimate_wait_time, get_stations
from backend.app.utils.connector_normalizer import AC_TYPE2, CCS2, normalize_connector_types

# ---- Energy model, loaded once at import time (not per-request) ----
_MODEL_PAYLOAD = joblib.load(ENERGY_MODEL_PATH)
_ENERGY_PIPELINE = _MODEL_PAYLOAD["pipeline"]
_NUMERIC_FEATURES = _MODEL_PAYLOAD["numeric_features"]
_CATEGORICAL_FEATURES = _MODEL_PAYLOAD["categorical_features"]
_MODEL_FEATURE_ORDER = _NUMERIC_FEATURES + _CATEGORICAL_FEATURES

# ---- Vehicle profiles (must stay in sync with generate_dataset.py) ----
VEHICLE_PROFILES = {
    "small_ev": {
        "battery_capacity_kwh": 24,
        "efficiency_baseline_wh_per_km": 140,
        "curb_weight_kg": 1400,
        "max_payload_kg": 300,
    },
    "mid_ev": {
        "battery_capacity_kwh": 40,
        "efficiency_baseline_wh_per_km": 160,
        "curb_weight_kg": 1900,
        "max_payload_kg": 450,
    },
    "large_ev": {
        "battery_capacity_kwh": 75,
        "efficiency_baseline_wh_per_km": 190,
        "curb_weight_kg": 2400,
        "max_payload_kg": 600,
    },
}

# ---- Vehicle -> compatible connector buckets (see connector_normalizer.py) ----
# ASSUMPTION, not sourced from real vehicle spec data: nothing in
# VEHICLE_PROFILES (or anywhere else in this dataset) ties a vehicle class to
# an actual connector standard. CCS2 is the current mandated DC fast-charging
# standard for EVs sold in India, so every profile is assumed CCS2-capable;
# small_ev additionally allows AC_TYPE2 on the assumption that a smaller
# vehicle is more likely to rely on slower AC charging too. Treat this as a
# placeholder to override once real per-model connector data exists -- it's
# one dict, not logic scattered across the file, specifically so that's easy.
VEHICLE_CONNECTOR_COMPATIBILITY = {
    "small_ev": {CCS2, AC_TYPE2},
    "mid_ev": {CCS2},
    "large_ev": {CCS2},
}

# Midpoint payload fraction per load_state, used when the caller doesn't supply
# an exact payload_kg (mirrors the sampling ranges in dataset_schema.md).
LOAD_STATE_PAYLOAD_FRACTION = {
    "unladen": 0.05,     # midpoint of uniform(0, 0.10)
    "half_load": 0.50,   # midpoint of uniform(0.40, 0.60)
    "full_load": 0.925,  # midpoint of uniform(0.85, 1.00)
}


def estimate_total_mass_kg(
    vehicle_profile: str,
    load_state: str = "half_load",
    payload_kg: Optional[float] = None,
) -> float:
    """
    Resolve total_mass_kg the same way the training data defines it:
    curb_weight_kg + payload_kg.

    If payload_kg is not supplied, it's estimated from load_state as a fraction
    of the profile's max_payload_kg.
    """

    if vehicle_profile not in VEHICLE_PROFILES:
        raise ValueError(
            f"Unknown vehicle_profile '{vehicle_profile}'. "
            f"Expected one of {list(VEHICLE_PROFILES)}."
        )

    profile = VEHICLE_PROFILES[vehicle_profile]

    if payload_kg is None:
        if load_state not in LOAD_STATE_PAYLOAD_FRACTION:
            raise ValueError(
                f"Unknown load_state '{load_state}'. "
                f"Expected one of {list(LOAD_STATE_PAYLOAD_FRACTION)}."
            )
        payload_kg = LOAD_STATE_PAYLOAD_FRACTION[load_state] * profile["max_payload_kg"]

    return profile["curb_weight_kg"] + payload_kg


def predict_energy_kwh(trip_features: dict) -> float:
    """
    Run the trained energy-consumption model on a single trip.

    trip_features must contain exactly the numeric_features + categorical_features
    the pipeline was trained on (see dataset_2/train_model.py); extra keys are
    ignored, missing ones raise KeyError. Column order doesn't matter to the
    pipeline, but we pass it in a fixed order for a deterministic DataFrame.
    """
    row = {feature: trip_features[feature] for feature in _MODEL_FEATURE_ORDER}
    df = pd.DataFrame([row], columns=_MODEL_FEATURE_ORDER)
    return float(_ENERGY_PIPELINE.predict(df)[0])


# ---- Reachability defaults (used until real terrain/traffic/weather feeds exist) ----

# Road-type -> mean avg_speed_kmh, matching the sampling means in generate_dataset.py
# (this is a physically grounded default, not a stub -- it's derived from the real
# road_type below).
_ROAD_TYPE_AVG_SPEED_KMH = {
    "highway": 85.0,
    "arterial": 45.0,
    "city": 22.0,
}

DEFAULT_SAFETY_MARGIN = 0.12  # keep ~12% of usable battery energy in reserve
EARTH_RADIUS_KM = 6371.0

# Floor for calculate_best_route's own charge_to estimate -- no point
# charging to less than a healthy minimum even if the remaining trip needs
# very little. Mirrors battery_simulator.BATTERY_FLOOR_PCT's value without
# importing it (battery_simulator imports *from* this module, not the other
# way around).
CHARGE_TO_FLOOR_PCT = 20.0

# Each expected minute of charger queue wait (charger_service.estimate_wait_time,
# Phase 5) is weighted roughly like this many extra detour-km in station_score
# below -- a simple linear tradeoff so a station with a shorter total time
# (detour + queue) beats one that's merely geographically closer. Tunable;
# not derived from measured user time-value data yet.
WAIT_TIME_SCORE_WEIGHT = 0.3


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Haversine formula is used to calculate the distance between two points on the earth by factoring in the curvature of the earth. The haversine formula is an equation that can be used to find the distance between two points on a sphere given their longitudes and latitudes.
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _infer_road_type(corridor) -> str:
    """Derive road_type from the CSV's `corridor` column (real data, not a stub)."""
    corridor = str(corridor or "").strip().lower()
    if corridor.startswith("nh") or corridor.startswith("sh"):
        return "highway"
    if "city" in corridor or "town" in corridor:
        return "city"
    return "arterial"


def _clean_number(value, default: Optional[float] = 0.0) -> Optional[float]:
    if value is None:
        return default
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(value) else value


def _build_trip_features(
    distance_km: float,
    road_type: str,
    vehicle_profile: str,
    load_state: str,
    total_mass_kg: float,
    avg_speed_kmh: Optional[float] = None,
    elevation_gain_m: float = 0.0,
    elevation_loss_m: float = 0.0,
    traffic_congestion_level: float = 0.3,
) -> dict:
    """
    avg_speed_kmh: pass the real distance/duration speed when the caller has
    one (battery_simulator does, from OSRM legs). Falls back to road_type's
    bucket mean when it doesn't (calculate_best_route only has a haversine
    detour distance to a candidate station, no real travel time). Mixing a
    short leg's real distance with the *fixed* highway bucket mean (85 km/h)
    was feeding the model combinations it never saw in training (a 1km
    "highway" trip averaging 85 km/h isn't physically to be expected) and
    wildly overestimating energy -- using the real speed when available
    avoids that.

    elevation_gain_m/elevation_loss_m: real per-leg/per-candidate values from
    elevation_service (see callers), not the flat-terrain stub they used to
    be. Defaults to 0.0 for callers that genuinely have no coordinates to
    query (e.g. no origin_lat/origin_lon supplied at all).

    traffic_congestion_level: real per-leg/per-candidate value from
    traffic_service (see callers), not the flat 0.3 stub it used to be.
    Defaults to 0.3 -- the old stub value -- for callers with no coordinates
    to query, same reasoning as the elevation defaults above.
    """
    profile = VEHICLE_PROFILES[vehicle_profile]
    return {
        "distance_km": distance_km,
        "elevation_gain_m": elevation_gain_m,
        "elevation_loss_m": elevation_loss_m,
        "avg_speed_kmh": avg_speed_kmh if avg_speed_kmh is not None else _ROAD_TYPE_AVG_SPEED_KMH[road_type],
        "traffic_congestion_level": traffic_congestion_level,
        "ambient_temperature_c": 31.0,  # TODO: no live weather feed yet; Chennai seasonal mean
        "vehicle_battery_capacity_kwh": profile["battery_capacity_kwh"],
        "vehicle_efficiency_baseline_wh_per_km": profile["efficiency_baseline_wh_per_km"],
        "total_mass_kg": total_mass_kg,
        "road_type": road_type,
        "weather_condition": "clear",  # TODO: no live weather feed yet
        "vehicle_profile": vehicle_profile,
        "load_state": load_state,
    }


def estimate_leg_energy_kwh(
    distance_km: float,
    road_type: str,
    vehicle_profile: str,
    load_state: str,
    total_mass_kg: float,
    avg_speed_kmh: Optional[float] = None,
    elevation_gain_m: float = 0.0,
    elevation_loss_m: float = 0.0,
    traffic_congestion_level: float = 0.3,
) -> float:
    """
    Single-leg convenience wrapper around _build_trip_features + predict_energy_kwh,
    for callers that need one leg at a time (e.g. battery_simulator.py walking a
    route leg by leg) rather than calculate_best_route's batched per-station scoring.
    """
    trip_features = _build_trip_features(
        distance_km=distance_km,
        road_type=road_type,
        vehicle_profile=vehicle_profile,
        load_state=load_state,
        total_mass_kg=total_mass_kg,
        avg_speed_kmh=avg_speed_kmh,
        elevation_gain_m=elevation_gain_m,
        elevation_loss_m=elevation_loss_m,
        traffic_congestion_level=traffic_congestion_level,
    )
    return predict_energy_kwh(trip_features)


def infer_road_type_from_speed(avg_speed_kmh: float) -> str:
    """
    Classify a route leg into the model's road_type categories using its
    actual average speed (distance/duration from the routing engine), for
    callers that have no `corridor` field to key off of (unlike
    _infer_road_type, which classifies charging stations from the CSV).
    Thresholds mirror _ROAD_TYPE_AVG_SPEED_KMH's bucket means.
    """
    if avg_speed_kmh >= 60.0:
        return "highway"
    if avg_speed_kmh >= 30.0:
        return "arterial"
    return "city"


def station_score(detour_km: float, power_kw: float, expected_wait_minutes: float = 0.0) -> float:
    """
    Lower is better. Shared by calculate_best_route's own `best` pick below
    and battery_simulator._rank_reachable_candidates' sort, so the two don't
    carry independent copies of the same tradeoff. Shorter detour and higher
    charging power both improve the score (original logic); expected queue
    wait (see WAIT_TIME_SCORE_WEIGHT) is an additional penalty term on top,
    not a rewrite -- a station with a shorter total time (detour + charge
    queue) is preferred over one that's merely geographically closer.
    `expected_wait_minutes` defaults to 0.0 for callers that haven't computed
    it (e.g. no origin, so nothing is actually being compared on wait).
    """
    return detour_km - 0.5 * power_kw + WAIT_TIME_SCORE_WEIGHT * expected_wait_minutes


def _compute_charge_to_percent(
    best: dict,
    distance: float,
    vehicle_profile: str,
    load_state: str,
    total_mass_kg: float,
    safety_margin: float,
) -> float:
    """
    Real charge target for the chosen station instead of a fixed 80%: just
    enough to cover the remaining distance to the destination (the requested
    trip `distance` minus the detour_km already spent reaching this station)
    plus the safety margin, floored at CHARGE_TO_FLOOR_PCT.

    Same haversine-only limitation as detour_km itself -- calculate_best_route
    has no real route geometry beyond the candidate station, only the overall
    trip distance, so "remaining distance" is an estimate, not a real
    leg. battery_simulator.plan_charge_to_percent is the real multi-stop
    planner (real per-leg energy, ceiling-exceeded handling) that
    /route/optimize actually uses; this is calculate_best_route's own
    single-stop estimate for direct callers of this function.
    """
    profile = VEHICLE_PROFILES[vehicle_profile]
    capacity_kwh = profile["battery_capacity_kwh"]
    remaining_km = max(distance - best["detour_km"], 0.0)

    if remaining_km <= 0:
        required_pct = 0.0
    else:
        remaining_energy_kwh = estimate_leg_energy_kwh(
            distance_km=remaining_km,
            road_type=best["road_type"],
            vehicle_profile=vehicle_profile,
            load_state=load_state,
            total_mass_kg=total_mass_kg,
        )
        required_pct = (remaining_energy_kwh / capacity_kwh) * 100.0

    target_pct = required_pct + safety_margin * 100.0
    return round(min(max(target_pct, CHARGE_TO_FLOOR_PCT), 100.0), 1)


def calculate_best_route(
    battery,
    distance,
    stations: Optional[list] = None,
    vehicle_profile: str = "mid_ev",
    load_state: str = "half_load",
    payload_kg: Optional[float] = None,
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
):
    """
    Pick the best reachable, connector-compatible charging station for the
    current trip.

    For each candidate station, estimates the trip needed to reach it and
    runs it through the trained energy model (predict_energy_kwh) instead of
    comparing against a fixed range. A candidate is "reachable" only if
    BOTH hold:
      - energy_reachable: predicted energy draw fits within the current
        battery charge, minus a safety margin reserved as buffer for
        prediction error / real-world variance.
      - connector_compatible: the station's normalized connector set (see
        connector_normalizer.py) intersects vehicle_profile's compatible
        list (VEHICLE_CONNECTOR_COMPATIBILITY) -- a station on the wrong
        plug is filtered out of "best"/reachable_count entirely, not just
        flagged, since it could never actually charge this vehicle.
      Both flags are still reported per-candidate (not just the AND) so a
      caller can tell "nearby but wrong plug" apart from "nearby but too
      far on energy" instead of both looking like a plain non-recommendation.
    Among reachable candidates, the best one balances shorter detour
    distance against higher charging power.

    Output:
    - optimal charging station (None if nothing is reachable)
    - charging percentage
    - per-candidate reachability breakdown
    """

    total_mass_kg = estimate_total_mass_kg(vehicle_profile, load_state, payload_kg)
    profile = VEHICLE_PROFILES[vehicle_profile]
    usable_energy_kwh = (battery / 100.0) * profile["battery_capacity_kwh"] * (1 - safety_margin)
    compatible_connector_buckets = VEHICLE_CONNECTOR_COMPATIBILITY.get(vehicle_profile, set())

    if not stations:
        stations = get_stations()

    have_origin = origin_lat is not None and origin_lon is not None

    # First pass: resolve per-station distance/road_type and stage everything
    # except the trip feature rows (those need the batched elevation lookup
    # below first, since it depends on knowing every candidate's lat/lon).
    staged = []
    for station in stations:
        lat = _clean_number(station.get("latitude"), default=None)
        lon = _clean_number(station.get("longitude"), default=None)
        if lat is None or lon is None:
            continue

        if have_origin:
            detour_km = _haversine_km(origin_lat, origin_lon, lat, lon)
        else:
            # TODO: RouteRequest.current_location is a free-text address, not
            # coordinates -- no geocoding step exists yet. Fall back to the
            # requested trip distance as a stand-in until one is wired in.
            detour_km = distance

        road_type = _infer_road_type(station.get("corridor"))

        # get_stations() already attaches this; normalize on the fly for
        # callers that pass in their own `stations` list without it.
        station_buckets = station.get("connector_buckets")
        station_buckets = set(station_buckets) if station_buckets is not None else normalize_connector_types(
            station.get("connector_types")
        )

        staged.append({
            "station_id": station.get("id"),
            "station_name": station.get("station_name"),
            "latitude": lat,
            "longitude": lon,
            "power_kw": _clean_number(station.get("power_kw"), default=0.0),
            "detour_km": round(detour_km, 2),
            "road_type": road_type,
            "connector_buckets": sorted(station_buckets),
            "connector_compatible": bool(station_buckets & compatible_connector_buckets),
        })

    # Elevation gain/loss from origin to each candidate, batched into one
    # Open-Elevation call (origin + every candidate's lat/lon) rather than
    # one call per station -- see elevation_service.compute_point_to_point_elevation.
    # This is a straight-line (origin -> candidate) approximation since
    # calculate_best_route has no real route geometry to a candidate, only a
    # haversine detour_km -- same limitation that already applies to
    # detour_km itself.
    origin_point = (origin_lat, origin_lon) if have_origin else None
    elevation_result = elevation_service.compute_point_to_point_elevation(
        origin_point, [(c["latitude"], c["longitude"]) for c in staged]
    )
    elevation_source = elevation_result["elevation_source"]

    # Congestion from each candidate to the origin, batched into one Ola/Google
    # matrix call (candidates as origins, current position as the shared
    # destination -- direction-reversed but congestion is treated as symmetric
    # here, see get_leg_traffic_batch) rather than one call per station.
    # Ola's response carries no no-traffic baseline of its own (see
    # traffic_service._ola_matrix), so each candidate gets a road-type bucket-
    # mean speed estimate as its typical_duration_s -- the same fallback speed
    # _build_trip_features itself uses when it has no real travel time either.
    if have_origin and staged:
        typical_durations_s = [
            (c["detour_km"] / _ROAD_TYPE_AVG_SPEED_KMH[c["road_type"]]) * 3600.0 for c in staged
        ]
        traffic_results = traffic_service.get_leg_traffic_batch(
            origin_point, [(c["latitude"], c["longitude"]) for c in staged], typical_durations_s
        )
    else:
        traffic_results = [
            {"congestion_level": 0.3, "congestion_source": "stub_fallback"} for _ in staged
        ]

    candidates = []
    if staged:
        feature_rows = []
        for candidate, elevation, traffic in zip(staged, elevation_result["by_point"], traffic_results):
            trip_features = _build_trip_features(
                distance_km=candidate["detour_km"],
                road_type=candidate["road_type"],
                vehicle_profile=vehicle_profile,
                load_state=load_state,
                total_mass_kg=total_mass_kg,
                elevation_gain_m=elevation["elevation_gain_m"],
                elevation_loss_m=elevation["elevation_loss_m"],
                traffic_congestion_level=traffic["congestion_level"],
            )
            feature_rows.append({feature: trip_features[feature] for feature in _MODEL_FEATURE_ORDER})

        # Predictions run as a single batch rather than one predict() call per
        # station -- looping predict_energy_kwh here would mean hundreds of
        # individual pipeline invocations per request (~15s for ~450 stations
        # vs. ~50ms batched), which doesn't work for an API call.
        batch_df = pd.DataFrame(feature_rows, columns=_MODEL_FEATURE_ORDER)
        predicted_kwh_batch = _ENERGY_PIPELINE.predict(batch_df)

        for candidate, predicted_kwh, traffic in zip(staged, predicted_kwh_batch, traffic_results):
            predicted_kwh = float(predicted_kwh)
            energy_reachable = predicted_kwh <= usable_energy_kwh
            candidates.append({
                **candidate,
                "predicted_energy_kwh": round(predicted_kwh, 2),
                "energy_reachable": energy_reachable,
                "elevation_source": elevation_source,
                "traffic_source": traffic["congestion_source"],
                # Combined signal everything downstream (battery_simulator's
                # alternative ranking, "best" below) actually keys off of --
                # a connector-incompatible station never counts as reachable
                # even if it's well within energy range.
                "reachable": energy_reachable and candidate["connector_compatible"],
            })

    reachable_candidates = [c for c in candidates if c["reachable"]]
    connector_incompatible_candidates = [c for c in candidates if not c["connector_compatible"]]
    # "unreachable on energy alone" excludes connector-incompatible ones so
    # the two counts don't overlap and double-count the same candidate.
    energy_unreachable_candidates = [
        c for c in candidates if c["connector_compatible"] and not c["energy_reachable"]
    ]

    # Wait-time (Phase 5) is only worth computing for candidates that are
    # actually in the running -- not all ~450 stations, just the reachable
    # subset that detour_km/power_kw scoring is already choosing among.
    now = datetime.now(timezone.utc)
    for c in reachable_candidates:
        try:
            wait = estimate_wait_time(c["station_id"], now)
        except ValueError:
            wait = {"expected_wait_minutes": 0.0, "queue_length": 0, "confidence": "heuristic"}
        c["expected_wait_minutes"] = wait["expected_wait_minutes"]
        c["queue_length"] = wait["queue_length"]
        c["wait_confidence"] = wait["confidence"]

    best = None
    if reachable_candidates:
        best = min(
            reachable_candidates,
            key=lambda c: station_score(c["detour_km"], c["power_kw"], c["expected_wait_minutes"]),
        )

    charge_to = (
        _compute_charge_to_percent(
            best, distance, vehicle_profile, load_state, total_mass_kg, safety_margin
        )
        if best
        else None
    )

    return {
        "station": best["station_name"] if best else None,
        "charge_to": charge_to,
        "vehicle_profile": vehicle_profile,
        "load_state": load_state,
        "total_mass_kg": round(total_mass_kg, 1),
        "reachable_count": len(reachable_candidates),
        "connector_incompatible_count": len(connector_incompatible_candidates),
        "energy_unreachable_count": len(energy_unreachable_candidates),
        "candidates": candidates,
    }
