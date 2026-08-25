# RouteVolt — Adjacent-station redundancy charging: implementation (top-3)

Implements the top-3 cutoff confirmed in `tests/redundancy_cutoff_selection_2026-08-24.md`:
size each stop's charge target so the vehicle could still reach the worst of
its top-3 nearest real graph neighbors (a genuine backup option), and expose
that same top-3 as a "recommended backup stations" list in the API response.
No new live API calls were made -- everything below reuses already-built
graph data and the diagnostic's already-logged per-neighbor numbers.

## What was added, and where

### 1. `backend/app/services/graph_planner.py`

- **`REDUNDANCY_NEIGHBOR_COUNT = 3`** (line 133) — the confirmed cutoff, with
  a comment pointing at the cutoff-selection diagnostic's reasoning (top-3
  cleared all 8 hard-infeasible stops; top-5/75th-percentile didn't).
  `MAX_EDGES_PER_NODE`, graph topology, and Dijkstra are untouched — this
  constant only controls how many of an already-built node's real edges
  feed the redundancy calculation below.
- **`build_station_graph`'s return dict** (line 596) — added one new key,
  `"candidates_per_node"`, exposing the Stage-1 pre-refinement
  `(j, distance_km, duration_min)` lists that were already being computed
  internally (used for the fan-out cap) but previously discarded on return.
  Purely additive: `"nodes"`, `"edges"`, `"route_cache"` are unchanged.
- **`get_top_n_neighbors(graph, node_idx, n=REDUNDANCY_NEIGHBOR_COUNT)`**
  (line 599) — new function. Returns a node's nearest `n` real graph
  neighbors with real per-edge `energy_kwh` and `distance_km`. Reuses
  `graph["edges"][node_idx]` directly rather than re-ranking: that list is
  already sorted ascending by real driving distance (Stage 1 sorts+caps
  `candidates_per_node[i]` by distance before Stage 2 appends to `edges` in
  that same order), so its first `n` entries already *are* the answer — no
  new routing/energy calls, no recomputation. Unit-verified against a
  synthetic graph object (see Verification below).
- **`plan_journey_via_graph`** (line ~1113-1122) — after `find_shortest_path`
  has already picked the path, each stop's station dict is augmented with
  `"_redundancy_top3_neighbors": get_top_n_neighbors(graph, node_idx)`
  before being handed to `plan_full_journey`. This runs strictly *after*
  Dijkstra, so it cannot influence path selection.

### 2. `backend/app/services/battery_simulator.py`

- **`plan_charge_to_percent`** (line 141) — new optional parameter
  `redundancy_neighbor_energies_kwh: Optional[list[float]] = None`. When
  provided, computes a redundancy-implied target from the *worst* of the
  given neighbor energies, using the exact same margin formula already used
  for `required_pct_all`/`next_leg_required_pct`
  (`SIMULATION_MARGIN_RATE=0.06`, `SIMULATION_MARGIN_FLOOR_KWH=1.5`, capped
  at 100%), then combines it with the existing post-Option-C target via
  `max()` — the same combination pattern already used elsewhere in this
  function, not additive. If the redundancy target pushes above
  `BATTERY_CEILING_PCT`, `exceeded_ceiling` is flipped `True` too, since
  that's a genuine ceiling exception like the existing checks.
- **`_build_stop_entry`** (line 311 onward) — reads
  `station.get("_redundancy_top3_neighbors")`, passes the neighbor energies
  into `plan_charge_to_percent`, and builds a `"backup_stations"` list
  (station_id, station_name, latitude, longitude, distance_km, energy_kwh)
  from the same top-3 data — "the same top-3" feeds both the charge target
  and the display list, per the task's scope note.

### 3. `backend/app/models/schemas.py`

- **`BackupStation`** — new model (station_id, station_name, latitude,
  longitude, distance_km, energy_kwh).
- **`ChargingStop.backup_stations: list[BackupStation]`** — new field,
  `default_factory=list`. No existing `ChargingStop`/`StopAlternative`
  field was renamed or removed.

### 4. `backend/app/api/routes.py`

No changes needed — `route_out["charging_plan"] = journey["stops"]` already
carries whatever `battery_simulator` puts on each stop entry, and
`backup_stations` defaults to `[]` so old/demo responses that don't have it
validate fine.

### 5. `frontend/src/components/ChargingStationsSummary.jsx`

Added a small secondary block under each stop's existing station-card list:
"Backup options if this station is unavailable", listing each backup's name
and distance (km), rendered only when `stop.backup_stations` is non-empty.
Reuses the section's existing `expanded` gate — no new UI state, no new
component, no layout changes elsewhere. `StationCard.jsx`/`RouteResults.jsx`
were not touched (no natural extra work needed there).

## Verification

**1. The 8 previously-hard-infeasible stops now resolve, matching the
diagnostic's Top-3 column exactly.** Reproduced the exact formula now
implemented in code against the diagnostic's already-logged per-neighbor
`targets by distance-rank` table (`tests/redundancy_cutoff_selection_2026-08-24.md`,
Part 1.1): took `max()` of each stop's first three distance-rank entries and
combined with its post-Option-C current target via `max()`.

| Station | current % | top3-worst | diagnostic's Top-3 col | match |
|---|---:|---:|---:|:---:|
| 11 – Mamandur | 60.0 | 30.7 | 30.7 | ✅ |
| 85 – Shivasakthi Singaram | 39.1 | 34.2 | 34.2 | ✅ |
| 87 – Cuddalore Argunt | 100.0 | 37.6 | 37.6 | ✅ |
| 320 – Quality Inn Viha | 86.3 | 14.8 | 14.8 | ✅ |
| 319 – Kumbakonam Cholaa | 99.3 | 82.6 | 82.6 | ✅ |
| 249 – Kaveri Garage | 100.0 | 29.3 | 29.3 | ✅ |
| 438 – Ather TRZ Sivakasi | 100.0 | 45.7 | 45.7 | ✅ |
| 360 – IOCL GSM | 100.0 | 78.1 | 78.1 | ✅ |

All 8 finals land at or below their pre-existing post-Option-C target (max
result 100.0%, all pre-existing at that ceiling, none newly pushed) — zero
hard-infeasible stops, matching the diagnostic's "0" count for Top-3.
Also spot-checked the *code itself* (not just hand arithmetic) end-to-end:
`plan_charge_to_percent(remaining_legs=[], vehicle_profile='mid_ev',
redundancy_neighbor_energies_kwh=[10.0, 15.0, 20.0])` → `53.8%`, matching
`(20 + max(1.5, 0.06×20)) / 40 × 100 = 53.75` by hand; pushing the worst
neighbor to 35 kWh correctly raised the target to `92.8%` and flipped
`exceeded_ceiling=True`.

**2. The 6/29 stops with modest increases match the diagnostic's reported
deltas.** Same formula applied to the Part 1.2 "other impacted" sample table
(12 stops): computed `Δ = max(0, top3_worst − current)` for each.

| Station | Δ (top-3, reproduced) |
|---|---:|
| 19 | +14.9 |
| 425 | +5.7 |
| 426 | +9.9 |
| 88 | +14.8 |
| 147 | +5.2 |
| 146 | +3.2 |

6 stops with Δ>0, avg **+8.95pp** (diagnostic: +9.0pp), max **+14.9pp**
(diagnostic: +14.9pp) — exact match. The other 6 of the 12 sampled stops
(23, 430, 358, 354, 195, 373) show Δ=0 under top-3, consistent with 23 of 29
stops overall being unaffected.

**3. The 21 unaffected stops remain unaffected.** Structural, not just
empirical: `plan_charge_to_percent` combines the redundancy target via
`max()`, a one-directional operation — it can only raise `target_pct`, never
lower it. Any stop whose top-3-worst-implied target is below its existing
post-Option-C target is mathematically guaranteed to see `Δ=0`, confirmed
above for 6 of the 12 sampled stops (target unchanged, e.g. station 23:
17.9% < 21.6% current → no change) and true for all 8 hard-infeasible
stops' *own* pre-existing target too (station 11: 30.7% < 60.0%, etc.).

**4. Primary path selection is unchanged.** Confirmed by code review rather
than a fresh live run (no new API calls, per scope): `build_station_graph`'s
edge-construction loop, `compute_reachable_to_destination`,
`compute_onward_degree`, and `find_shortest_path`'s Dijkstra body were not
modified — only two purely-additive changes touch `graph_planner.py`
(`REDUNDANCY_NEIGHBOR_COUNT`/`get_top_n_neighbors`, both new, and one new
key added to `build_station_graph`'s return dict). The one place the new
code runs inside `plan_journey_via_graph` is *after*
`find_shortest_path(...)` has already returned `path_result["path"]` — the
augmentation loop reads `path_indices` but cannot feed back into path
selection. In `battery_simulator.py`, `plan_charge_to_percent`'s new
parameter defaults to `None` and only ever receives real data from
`_build_stop_entry`, which itself only runs once a stop is already fixed on
the path. `find_minimum_starting_battery`/`_simulate_feasibility_only` (the
one other caller of the graph, used for a binary-search feature not wired
to `/route/optimize`) were deliberately left untouched — they don't pass
`redundancy_neighbor_energies_kwh`, so their behavior is byte-for-byte
identical to before this change.

**5. Demo-mode fixture still loads correctly (schema compatibility only).**
Called the real `/route/optimize` handler in-process with `DEMO_MODE=true`
and the exact demo origin/destination/profile, so it took the fixture-return
path, then validated the result against the updated
`RouteOptimizeResponse` schema:

```
demo-mode response validates against RouteOptimizeResponse: OK
num stops: 31
first stop backup_stations: []
```

The fixture itself was **not** regenerated (out of scope, per the task) —
every stop simply defaults `backup_stations` to `[]` since the saved JSON
predates this field, exactly as `default_factory=list` is meant to handle.

## Explicitly not touched

`MAX_EDGES_PER_NODE`, graph topology, fan-out cap, Dijkstra/
`find_shortest_path`, primary path selection, the demo-mode fixture file,
and stops 15/21/station 320/the monotonicity-guard anomaly (all out of
scope per the task).
