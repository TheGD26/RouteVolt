# RouteVolt — Build-time vs. simulation-time energy prediction consistency

12 direct A→C edges (the 12 finding-(2) segments; station 181/stop 24 excluded, separate closed finding-(1) case). **Zero new API calls** — all 12 rows recovered from `/tmp/fanout_flagged_run.log`, produced by `graph_planner._log_exceeded_ceiling_drift` (`graph_planner.py:684-734`) during the earlier fanout diagnostic's real `plan_journey_via_graph` run. Both the build-time and simulation-time figures below are real-data computations of the same physical edge, per the correction in this task's brief — not coarse-vs-fine.

## Margin mechanism — confirmed, not assumed

Checked both call sites directly rather than assuming a matched "same operation, different constant" pair:

- **`GRAPH_EDGE_SAFETY_MARGIN=0.05` (build time)** shrinks the *available capacity budget*, not the predicted energy figure: `usable_kwh_at_ceiling = capacity_kwh * (BATTERY_CEILING_PCT/100) * (1 - safety_margin)` (`graph_planner.py:552-553`) → for mid_ev, `40 * 0.8 * 0.95 = 30.40 kWh`; `usable_kwh_at_full_charge = 40 * 0.95 = 38.00 kWh`. The stored edge value itself (`energy_kwh`, `graph_planner.py:564`) is never margin-adjusted — it's raw, compared against a margin-*reduced* budget.
- **`DEFAULT_SAFETY_MARGIN=0.12` (simulation time)** is applied completely differently: as an **additive percentage-point buffer** on the required charge percent — `safety_margin_pct = safety_margin * 100.0` then `required_pct_all = (remaining_energy_kwh/capacity_kwh)*100.0 + safety_margin_pct` (`battery_simulator.py:167,173`). For a 40kWh pack this is a flat +4.8 kWh-equivalent add-on, not a multiplicative discount.

These are **not the same mechanism with different constants** — one discounts capacity multiplicatively, the other pads required-percent additively. The "margin-adjusted" columns below are therefore **derived equivalents for side-by-side comparison only**, not literal code-computed values: build-time's is `raw / (1 - 0.05)`; simulation-time's is `raw + (0.12 * 40)`.

## Per-edge data

| Stop | A | C | Build raw (kWh) | Build margin-equiv | Sim raw (kWh) | Sim margin-equiv | Raw diff | Raw diff % |
|---|---|---|---|---|---|---|---|---|
| 6 | MM Legacy | Mamallapuram Radisson Blu | 15.6760 | 16.5010 | 15.9610 | 20.7610 | +0.285 | +1.82% |
| 7 | Mamallapuram Radisson Blu | Silavattam Village | 20.0969 | 21.1546 | 19.9080 | 24.7080 | -0.189 | -0.94% |
| 14 | Cuddalore Argunt Aggregates | IOCL Raja Traders | 7.3024 | 7.6867 | 7.0780 | 11.8780 | -0.224 | -3.07% |
| 15 | IOCL Raja Traders | Quality Inn Viha | 30.0552 | 31.6371 | 30.6810 | 35.4810 | +0.626 | +2.08% |
| 16 | Quality Inn Viha | Kumbakonam Cholaa Dynasty | 3.7119 | 3.9073 | 3.7280 | 8.5280 | +0.016 | +0.43% |
| 17 | Kumbakonam Cholaa Dynasty | Trichy Hotel Balaji Ariyas | 30.1473 | 31.7340 | 28.8420 | 33.6420 | -1.305 | -4.33% |
| 20 | Kaveri Garage Piratiyur | IOCL Sugan Tradings | 9.6089 | 10.1147 | 9.5060 | 14.3060 | -0.103 | -1.07% |
| 21 | IOCL Sugan Tradings | Melur Zeon X A.N Energy | 34.3240 | 36.1305 | 33.1710 | 37.9710 | -1.153 | -3.36% |
| 25 | Ather-TRZ Virudhunagar | Ather-TRZ Sivakasi | 9.9355 | 10.4585 | 10.1970 | 14.9970 | +0.262 | +2.63% |
| 26 | Ather-TRZ Sivakasi | IOCL Rajam Agency | 21.9442 | 23.0992 | 21.7040 | 26.5040 | -0.240 | -1.09% |
| 27 | IOCL Rajam Agency | IOCL GSM | 21.4213 | 22.5487 | 22.2230 | 27.0230 | +0.802 | +3.74% |
| 28 | IOCL GSM | Nagercoil Thovalai Carnival City | 28.9480 | 30.4716 | 30.1510 | 34.9510 | +1.203 | +4.16% |

Raw-to-raw differences range **-4.33% to +4.16%**, mean absolute **2.39%**. Margin-equivalent differences are dominated by the fixed +4.8 kWh (12%-of-40kWh) simulation-side add-on, not by the raw prediction gap — visible directly in the table (every sim margin-equiv column sits ~4.8-5 kWh above its raw column, versus each build margin-equiv sitting only ~1.05-1.8 kWh above its own raw column).

**One additional fact worth flagging plainly:** stop 21's direct edge (IOCL Sugan Tradings → Melur) has `build_time_raw_kwh=34.32`, which **exceeds `usable_kwh_at_ceiling=30.40`** — this edge was admitted at build time as `admitted_bridged` (extended-charge tier, `EXTENDED_CHARGE_PENALTY_KWH` applied to its Dijkstra cost), not `admitted_normal` like the other 11. Reported as observed, not evaluated further (out of scope).

## Which inputs differed between the two computations — quoted, not inferred

Checked all four candidate inputs (traffic, elevation, speed, distance) at both call sites for these specific chosen-path edges (which reuse the identical `route_cache` entry from `build_station_graph`, per `graph_planner.py:1040`):

- **Distance & duration & speed: identical.** Both `_sub_leg_feature_kwargs` (build time, `graph_planner.py:276-278`) and `_leg_energy_kwh` (simulation time, `battery_simulator.py:97-100`) derive `avg_speed_kmh = distance_km / (duration_s / 3600.0)` from the same `leg["distance_km"]`/`leg["duration_s"]` fields of the identical, reused `route["legs"]` objects.
- **Traffic: identical.** Both call sites invoke the exact same function, `battery_simulator._leg_traffic(leg)` (`graph_planner.py`'s Stage 2 at `graph_planner.py:296-335`'s `_fetch_edge_sub_legs`, and `battery_simulator.py:407` inside `plan_full_journey`), which wraps `traffic_service.get_leg_traffic` — cached by rounded (origin, destination) coordinates (`traffic_service.py:351-353`). Since both calls pass the same leg's `start`/`end` coords, the second call is a cache hit returning the byte-identical `congestion_level` the first call computed — confirmed by code, not just assumed.
- **Elevation: genuinely different methodology**, the one real, code-confirmed input difference:
  - Build time (`graph_planner.py:529-531`): **one coarse 2-point lookup for the whole edge** — `elevation_service.compute_leg_elevation([(nodes[i]["lat"], nodes[i]["lon"]), (nodes[j]["lat"], nodes[j]["lon"])])`, i.e. station A's coordinate straight to station C's coordinate, computed once, then proportionally distributed across each real sub-leg by distance share (`graph_planner.py:537-541`: `elevation_gain_m=coarse_elevation["elevation_gain_m"] * share`).
  - Simulation time (`battery_simulator.py:63-72`, `_leg_elevation`): **each physical leg's own real sampled OSRM road geometry** — `points = leg.get("points") or [leg["start"], leg["end"]]`, i.e. the actual turn-by-turn road-following points for that specific sub-leg, computed independently per leg, not distributed from a single aggregate.

This matches the codebase's own pre-existing docstring note (read during the ground-truth pass, `graph_planner.py` near `MAX_EDGES_PER_NODE`): elevation is "confirmed a small, ~5% contributor to the coarse-vs-real drift" — consistent with the measured raw differences here landing in the same -4.33%/+4.16% band.

## Conclusion

The raw-to-raw prediction gap is small and consistent (mean |diff| 2.39%, max 4.33%) and traceable to one specific, code-confirmed input difference — elevation methodology (coarse 2-point-per-edge vs. per-leg real sampled geometry) — with traffic, speed, and distance confirmed byte-identical between the two call sites for these reused-route edges. The much larger gap visible in the margin-equivalent columns is **not** a raw-prediction disagreement; it's the two margin mechanisms themselves being structurally different (a ~1-1.8 kWh multiplicative capacity discount at build time vs. a flat 4.8 kWh additive buffer at simulation time). **This is mostly a margin-constant/mechanism gap, not a raw-prediction mismatch** — the raw energy predictions from real data at both call sites agree closely (within ~4%); it is the fixed +4.8 kWh simulation-side buffer, applied on top of an already-close raw figure, that does most of the work of pushing several of these 12 edges over the 80% ceiling check that passed at build time.
