"""
Coarse admission check diagnostic: for each of the 12 finding-(2)-leaning
flagged segments (tests/fanout_cap_diagnostic_output_2026-08-24.json), checks
whether the A->B edge (origin station -> best on-corridor intermediate
candidate) and B->C edge (candidate -> next station on the chosen path) would
be admitted (normal), admitted-bridged (extended-charge), or rejected by
build_station_graph's real admission check -- mirrors graph_planner.py:561-569
exactly, using the same inputs (real route + real per-sub-leg traffic +
coarse 2-point elevation -> route_optimizer.estimate_leg_energy_kwh_batch)
against the same two thresholds.

Diagnostic-only script -- does not modify build_station_graph or any
production file. New real API calls: one routing_service.get_route +
per-sub-leg battery_simulator._leg_traffic per edge (~24 edges total,
A->B and B->C for 12 segments), same real calls build_station_graph itself
would make for these pairs were they on a chosen path.

    uv run python tests/coarse_admission_check.py
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import elevation_service, routing_service
from backend.app.services.battery_simulator import _leg_traffic
from backend.app.services.graph_planner import (
    BATTERY_CEILING_PCT,
    EXTENDED_CHARGE_PENALTY_KWH,
    GRAPH_EDGE_SAFETY_MARGIN,
    _sub_leg_feature_kwargs,
)
from backend.app.services.route_optimizer import (
    VEHICLE_PROFILES,
    estimate_leg_energy_kwh_batch,
    estimate_total_mass_kg,
)

VEHICLE_PROFILE = "mid_ev"
LOAD_STATE = "half_load"
SAFETY_MARGIN = GRAPH_EDGE_SAFETY_MARGIN

DIAGNOSTIC_PATH = Path(__file__).resolve().parent / "fanout_cap_diagnostic_output_2026-08-24.json"
SOURCE_PATH = Path(__file__).resolve().parent / "fanout_flagged_segments_source_2026-08-24.json"
OUT_PATH = Path(__file__).resolve().parent / f"coarse_admission_diagnostic_output_{date.today().isoformat()}.json"

EXCLUDED_STATION_ID = 181  # station 181 / stop 24 -- already-confirmed finding (1), out of scope here


def real_edge_energy_kwh(a: tuple, b: tuple, total_mass_kg: float) -> dict:
    """
    Mirrors build_station_graph's Stage 2 exactly for ONE edge: real route
    (routing_service.get_route), real per-sub-leg traffic (_leg_traffic),
    coarse 2-point elevation (elevation_service.compute_leg_elevation),
    batched energy prediction (estimate_leg_energy_kwh_batch) -- see
    graph_planner.py:296-335 (_fetch_edge_sub_legs) and :514-542 (feature-row
    assembly) for the production code this mirrors.
    """
    a_lat, a_lon = a
    b_lat, b_lon = b
    try:
        route = routing_service.get_route(a_lat, a_lon, b_lat, b_lon)
    except routing_service.RoutingError as exc:
        return {"status": "route_fetch_failed", "error": str(exc)}

    sub_legs = [(leg, _leg_traffic(leg)) for leg in route["legs"]]
    if not sub_legs:
        return {"status": "empty_route"}

    coarse_elevation = elevation_service.compute_leg_elevation([(a_lat, a_lon), (b_lat, b_lon)])
    total_distance_km = sum(leg["distance_km"] for leg, _traffic in sub_legs) or 1e-9

    feature_rows = []
    for leg, traffic in sub_legs:
        share = leg["distance_km"] / total_distance_km
        feature_rows.append(_sub_leg_feature_kwargs(
            leg, traffic, VEHICLE_PROFILE, LOAD_STATE, total_mass_kg,
            elevation_gain_m=coarse_elevation["elevation_gain_m"] * share,
            elevation_loss_m=coarse_elevation["elevation_loss_m"] * share,
        ))

    sub_leg_energies_kwh = estimate_leg_energy_kwh_batch(feature_rows)
    energy_kwh = sum(sub_leg_energies_kwh)
    return {
        "status": "ok", "energy_kwh": round(energy_kwh, 4), "sub_leg_count": len(sub_legs),
        "real_distance_km": round(sum(leg["distance_km"] for leg, _t in sub_legs), 2),
    }


def classify(energy_result: dict, usable_kwh_at_ceiling: float, usable_kwh_at_full_charge: float) -> dict:
    """Mirrors graph_planner.py:561-569's exact three-way branch."""
    if energy_result["status"] != "ok":
        return {"verdict": "route_unavailable", **energy_result}
    energy_kwh = energy_result["energy_kwh"]
    if energy_kwh <= usable_kwh_at_ceiling:
        return {"verdict": "admitted_normal", "dijkstra_cost": energy_kwh, **energy_result}
    elif energy_kwh <= usable_kwh_at_full_charge:
        return {"verdict": "admitted_bridged", "dijkstra_cost": energy_kwh + EXTENDED_CHARGE_PENALTY_KWH, **energy_result}
    else:
        return {
            "verdict": "rejected",
            "reason": f"energy_kwh={energy_kwh:.2f} exceeds usable_kwh_at_full_charge={usable_kwh_at_full_charge:.2f}",
            **energy_result,
        }


def main():
    diagnostic = json.loads(DIAGNOSTIC_PATH.read_text())
    source = json.loads(SOURCE_PATH.read_text())
    stops = source["result"]["stops"]
    dest_lat, dest_lon = source["meta"]["destination"]

    total_mass_kg = estimate_total_mass_kg(VEHICLE_PROFILE, LOAD_STATE, None)
    capacity_kwh = VEHICLE_PROFILES[VEHICLE_PROFILE]["battery_capacity_kwh"]
    usable_kwh_at_ceiling = capacity_kwh * (BATTERY_CEILING_PCT / 100.0) * (1 - SAFETY_MARGIN)
    usable_kwh_at_full_charge = capacity_kwh * (1 - SAFETY_MARGIN)
    print(f"capacity_kwh={capacity_kwh} usable_kwh_at_ceiling={usable_kwh_at_ceiling:.2f} "
          f"usable_kwh_at_full_charge={usable_kwh_at_full_charge:.2f}")

    segments = [s for s in diagnostic["per_segment"] if s["station_id"] != EXCLUDED_STATION_ID]
    print(f"segments to check: {len(segments)}")

    t0 = time.monotonic()
    results = []
    for seg in segments:
        stop_idx = seg["stop_list_idx"]
        a_station = stops[stop_idx]
        a_coord = (a_station["location"]["lat"], a_station["location"]["lng"])

        best_b = seg["best_unused_candidates_checked"][0]
        b_coord = (best_b["lat"], best_b["lon"])

        if stop_idx + 1 < len(stops):
            c_station = stops[stop_idx + 1]
            c_coord = (c_station["location"]["lat"], c_station["location"]["lng"])
            c_label = c_station["station_name"]
        else:
            c_coord = (dest_lat, dest_lon)
            c_label = "DESTINATION (Nagercoil)"

        print(f"\n[stop {stop_idx}] A={a_station['station_name']!r} ({a_coord}) "
              f"-> B={best_b['station_name']!r} ({b_coord}) -> C={c_label!r} ({c_coord})")

        ab_energy = real_edge_energy_kwh(a_coord, b_coord, total_mass_kg)
        ab_verdict = classify(ab_energy, usable_kwh_at_ceiling, usable_kwh_at_full_charge)
        print(f"  A->B: {ab_verdict['verdict']}"
              + (f" (energy_kwh={ab_verdict.get('energy_kwh')}, real_km={ab_verdict.get('real_distance_km')})"
                 if ab_verdict['verdict'] != 'route_unavailable' else f" ({ab_verdict.get('error')})"))

        bc_energy = real_edge_energy_kwh(b_coord, c_coord, total_mass_kg)
        bc_verdict = classify(bc_energy, usable_kwh_at_ceiling, usable_kwh_at_full_charge)
        print(f"  B->C: {bc_verdict['verdict']}"
              + (f" (energy_kwh={bc_verdict.get('energy_kwh')}, real_km={bc_verdict.get('real_distance_km')})"
                 if bc_verdict['verdict'] != 'route_unavailable' else f" ({bc_verdict.get('error')})"))

        results.append({
            "stop_list_idx": stop_idx, "station_id": seg["station_id"],
            "A": {"station_name": a_station["station_name"], "coord": a_coord},
            "B": {"station_name": best_b["station_name"], "station_id": best_b["station_id"], "coord": b_coord},
            "C": {"station_name": c_label, "coord": c_coord},
            "A_to_B": ab_verdict, "B_to_C": bc_verdict,
        })

    wall_clock_s = time.monotonic() - t0
    print(f"\n=== wall_clock_s: {wall_clock_s:.1f} ===")

    out = {
        "meta": {
            "vehicle_profile": VEHICLE_PROFILE, "load_state": LOAD_STATE,
            "capacity_kwh": capacity_kwh, "usable_kwh_at_ceiling": round(usable_kwh_at_ceiling, 3),
            "usable_kwh_at_full_charge": round(usable_kwh_at_full_charge, 3),
            "segments_checked": len(segments), "wall_clock_s": round(wall_clock_s, 1),
        },
        "results": results,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
