"""
Retry pass for the 54/246 edges that failed (returned None from
_fetch_edge_sub_legs) during redundancy_cutoff_selection_diagnostic.py's
run -- live Ola Maps 429 rate-limiting, same documented degraded-network
condition noted in the original diagnostic, not a new issue. These are the
SAME edges already in scope (identical top-8 windows), not new candidate
pairs -- this fills gaps in the already-approved re-run rather than
expanding it. Retries with a short stagger and more retries per request to
ride out transient rate limiting, then merges results into the existing
output file in place.

    uv run python tests/redundancy_cutoff_selection_retry_dropped.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import graph_planner as gp
from backend.app.services.battery_simulator import (
    SIMULATION_MARGIN_FLOOR_KWH,
    SIMULATION_MARGIN_RATE,
)
from backend.app.services.route_optimizer import (
    VEHICLE_PROFILES,
    estimate_leg_energy_kwh_batch,
    estimate_total_mass_kg,
)
from tests.fanout_local_reconstruction import build_node_list

DATA_PATH = Path(__file__).resolve().parent / "redundancy_cutoff_selection_data_2026-08-24.json"
VEHICLE_PROFILE = "mid_ev"
LOAD_STATE = "half_load"


def main():
    data = json.loads(DATA_PATH.read_text())
    nodes = build_node_list()

    missing_pairs = []
    for x in data["per_stop"]:
        if x.get("node_idx") is None:
            continue
        i = x["node_idx"]
        computed_j = {n["neighbor_node_idx"] for n in x.get("neighbors", [])}
        for j in x.get("top8_node_indices", []):
            if j not in computed_j:
                missing_pairs.append((i, j))
    missing_pairs = list(dict.fromkeys(missing_pairs))
    print(f"Retrying {len(missing_pairs)} previously-dropped edges (sequential, with pauses to avoid repeat 429s)")

    route_cache: dict = {}
    sub_legs_by_pair = {}
    still_dropped = []
    for n, (i, j) in enumerate(missing_pairs):
        for attempt in range(3):
            result = gp._fetch_edge_sub_legs(i, j, nodes, route_cache)
            if result is not None:
                sub_legs_by_pair[(i, j)] = result
                break
            time.sleep(2.0)
        else:
            still_dropped.append((i, j))
        if n % 5 == 0:
            print(f"  {n+1}/{len(missing_pairs)}...")
        time.sleep(0.3)

    print(f"Recovered {len(sub_legs_by_pair)}/{len(missing_pairs)}, still dropped: {len(still_dropped)}")
    if still_dropped:
        print("Still-dropped pairs:", still_dropped)

    total_mass_kg = estimate_total_mass_kg(VEHICLE_PROFILE, LOAD_STATE, None)
    pair_ij = []
    pair_sub_leg_counts = []
    feature_rows = []
    for (i, j), sub_legs in sub_legs_by_pair.items():
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

    capacity_kwh = VEHICLE_PROFILES[VEHICLE_PROFILE]["battery_capacity_kwh"]

    def target_pct_with_margin(energy_kwh: float) -> float:
        margin_kwh = max(SIMULATION_MARGIN_FLOOR_KWH, SIMULATION_MARGIN_RATE * energy_kwh)
        return ((energy_kwh + margin_kwh) / capacity_kwh) * 100.0

    # Merge recovered edges back into data["per_stop"], preserving distance_rank.
    for x in data["per_stop"]:
        if x.get("node_idx") is None:
            continue
        i = x["node_idx"]
        dist_by_j = {int(k): v for k, v in x.get("top8_distance_km_by_j", {}).items()} if isinstance(x.get("top8_distance_km_by_j"), dict) else {}
        existing_j = {n["neighbor_node_idx"] for n in x.get("neighbors", [])}
        for rank, j in enumerate(x.get("top8_node_indices", []), start=1):
            if j in existing_j:
                continue
            e = edge_energy_kwh.get((i, j))
            if e is None:
                continue
            x.setdefault("neighbors", []).append({
                "distance_rank": rank,
                "neighbor_node_idx": j,
                "neighbor_station_id": nodes[j].get("id"),
                "neighbor_station_name": nodes[j].get("station_name"),
                "distance_km": round(dist_by_j.get(j, 0.0), 2) if dist_by_j else None,
                "energy_kwh": round(e, 3),
                "target_pct": round(target_pct_with_margin(e), 1),
            })
        x["neighbors"] = sorted(x["neighbors"], key=lambda n: n["distance_rank"])
        x["edges_computed"] = len(x["neighbors"])
        x["edges_dropped"] = len(x.get("top8_node_indices", [])) - len(x["neighbors"])

    data["meta"]["retry_pass"] = {
        "attempted": len(missing_pairs),
        "recovered": len(sub_legs_by_pair),
        "still_dropped": still_dropped,
    }
    DATA_PATH.write_text(json.dumps(data, indent=2, default=str))
    print(f"Merged and saved to {DATA_PATH}")


if __name__ == "__main__":
    main()
