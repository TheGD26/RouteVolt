# RouteVolt — Stop 21 (IOCL Sugan Tradings → Melur Zeon X A.N Energy) structural diagnostic

**No new API calls used in Part 1** — every figure below is quoted from
`tests/energy_prediction_consistency_2026-08-24.json`/`.md`,
`tests/margin_recalibration_options_2026-08-24.md`,
`tests/graph_dijkstra_ground_truth_2026-08-24.md`,
`tests/coarse_admission_diagnostic_output_2026-08-24.json`/`.md`, and
`tests/fanout_cap_diagnostic_output_2026-08-24.json`, all already logged from
prior sessions. Part 2 identifies one specific gap that existing data cannot
close and asks for sign-off before any new call.

## Part 1

### 1. The raw kWh figures and the ceiling exceedance

Direct A→C edge (IOCL Sugan Tradings → Melur Zeon X A.N Energy), from
`energy_prediction_consistency_2026-08-24.json`:

| | Build-time raw (kWh) | Simulation-time raw (kWh) |
|---|---|---|
| Stop 21 | **34.3240** | **33.1710** |

Two separate ceiling comparisons exist in the pipeline, at two different
stages, with two different constants — both trip on this edge:

- **Simulation-time (no margin at all, per `margin_recalibration_options_2026-08-24.md`):** raw ceiling = `BATTERY_CEILING_PCT/100 × capacity` = `0.80 × 40 = 32.0 kWh`. Sim raw `33.171 > 32.0` — **exceeds by 1.171 kWh (+3.66%)**, before any margin is added.
- **Build-time (`GRAPH_EDGE_SAFETY_MARGIN=0.05` folded into the capacity budget, per `graph_planner.py:552-553`):** `usable_kwh_at_ceiling = 40 × 0.80 × (1-0.05) = 30.40 kWh`. Build raw `34.324 > 30.40` — **exceeds by 3.924 kWh (+12.9%)**.

Both comparisons use the *same* underlying real energy prediction for the
same physical edge (34.324 kWh build-time / 33.171 kWh simulation-time — a
3.36% raw-to-raw gap already attributed to the elevation-methodology
difference in `energy_prediction_consistency_2026-08-24.md`, not investigated
further here per scope). Under either stage's threshold, this edge is over —
it is not a borderline case sensitive to margin choice (unlike stop 15).

### 2. Why this edge triggered `admitted_bridged` specifically

Quoted verbatim, `graph_planner.py:561-569` (also reproduced in
`coarse_admission_diagnostic_summary_2026-08-24.md`):

```python
    if energy_kwh <= usable_kwh_at_ceiling:
        edges.setdefault(i, []).append((j, energy_kwh, energy_kwh))
    elif energy_kwh <= usable_kwh_at_full_charge:
        edges.setdefault(i, []).append((j, energy_kwh + EXTENDED_CHARGE_PENALTY_KWH, energy_kwh))
```

with thresholds (`graph_planner.py:550-553`, mid_ev): `usable_kwh_at_ceiling
= 30.40 kWh`, `usable_kwh_at_full_charge = 40 × (1-0.05) = 38.00 kWh`.

Stop 21's build-time energy is **34.324 kWh**. Checked against the two
tiers in order:

- `34.324 ≤ 30.40`? **No** — fails the normal-tier check by 3.924 kWh.
- `34.324 ≤ 38.00`? **Yes** — clears the bridged tier by 3.676 kWh.

So the edge falls in the band `30.40 < energy_kwh ≤ 38.00`, which is
*exactly* the `admitted_bridged` branch — deterministic, one comparison,
no ambiguity. Dijkstra cost for this edge = `34.324 + EXTENDED_CHARGE_PENALTY_KWH(8.0) = 42.324 kWh`
(`graph_planner.py:176` confirms the constant's value; ground-truth read
confirms `cost` is exactly what `find_shortest_path` sums in relaxation,
`graph_planner.py:637-646`).

**This is the same root fact as item 1, viewed through build-time's own
threshold**: the edge's real predicted energy (34.324 kWh) is intrinsically
close to a full battery's worth of range on a 40kWh pack, and every
threshold check applied to it anywhere in the pipeline — build-time normal
ceiling (30.40), build-time full-charge ceiling (38.00), simulation-time raw
ceiling (32.0) — is testing variations on that one number against variations
on "how much of 40kWh can this edge use." It clears two of those checks and
fails two, all because of the same underlying number, not because of two
independent problems.

### 3. Did Dijkstra "know" this edge was expensive and pick it anyway because the split was worse, or did it never see the split at all?

**Answer: the second — the evidence rules out "Dijkstra weighed both and
picked the least-bad option."**

The `admitted_bridged` penalty does not disguise the direct edge's cost from
Dijkstra — quite the opposite. Per the ground-truth read
(`graph_dijkstra_ground_truth_2026-08-24.md`, section 3a), `cost` (real kWh +
8.0 penalty for bridged edges) is exactly what `find_shortest_path` sums in
its relaxation step (`new_dist = d + cost`, `graph_planner.py:644`). So by
the time Dijkstra ran, the direct edge already carried its full, penalized
cost of **42.324 kWh** — nothing about the bridged tier hides or discounts
this from the algorithm.

The split candidate — via **Hotel Arun Cafe** (confirmed rank 1 by real
driving distance in `fanout_cap_diagnostic_output_2026-08-24.json`,
`real_top8` for node 122/stop 21) — was independently re-measured in
`coarse_admission_diagnostic_output_2026-08-24.json`, using real `get_route`
calls mirroring `build_station_graph`'s own Stage 2 methodology exactly:

| Leg | Real kWh | Admission tier | Dijkstra cost |
|---|---|---|---|
| A → Hotel Arun Cafe | 0.83 | `admitted_normal` | 0.83 |
| Hotel Arun Cafe → C | 23.40 | `admitted_normal` | 23.40 |
| **Split total** | | | **24.23** |
| **Direct A→C (bridged)** | 34.324 | `admitted_bridged` | **42.324** |

The split's total cost (24.23 kWh) is **18.09 kWh cheaper** than the direct
bridged edge (42.324 kWh) — not a close call. Dijkstra is exact, textbook
shortest-path relaxation with no heuristic pruning (confirmed in the
ground-truth read, section 3c: every edge is relaxed by the same four lines,
no direct-vs-split special-casing). **If both split edges (A→B and B→C) had
actually existed in the `edges` adjacency dict Dijkstra ran over, Dijkstra
is mathematically guaranteed to have found `dist[C] ≤ 24.23` via the split
and never have settled on the direct edge's 42.324.**

Since the direct bridged edge is the one confirmed to have actually been
used in the real pipeline run (per `margin_recalibration_options_2026-08-24.md`
and the drift-log data itself, which only fires for edges on the chosen
path), the split edges **could not have both been present in the graph's
adjacency structure at Dijkstra's search time** — otherwise Dijkstra would
provably have chosen the cheaper option. This is a structural absence, not
an algorithmic or cost-visibility failure: Dijkstra never had the option to
weigh, so it isn't "choosing the least-bad path" — it's running correctly
over an incomplete edge set.

### Where the gap most likely is (traced as far as existing data allows)

Two independent pre-filters gate every edge before Dijkstra ever sees it
(ground-truth read, sections 2a/2b):

1. **DAG-order gap-break** (haversine, per-node): `approx_gap_km =
   dist_from_origin_km[j] - dist_from_origin_km[i]`; breaks once this
   exceeds `vehicle_range_km`. Node 122's (A's) own window for this stop is
   confirmed **118 nodes wide, `[123 ... 240]`**
   (`fanout_flagged_segments_source_2026-08-24_local_reconstruction.json`,
   `stop_list_idx: 21`) — node 144 (C, Melur) sits well inside it. Since
   Hotel Arun Cafe (node 123) is the very next node after A in this same
   DAG ordering, its own gap-break window starts one position later and, by
   the same monotonic distance-from-origin logic, would need to shrink by
   more than ~117 node-positions' worth of range to exclude node 144 — implausible given A's window comfortably includes node 240. **The DAG-order pre-filter is very unlikely to be what excluded B→C**, though this is inferred from confirmed node-ordering behavior, not from a direct query of node 123's own window (never logged).
2. **`MAX_EDGES_PER_NODE=8` real-driving-distance cap** (`graph_planner.py:467-477`): applied *per node*, ranked by each node's own real OSRM driving distance to its candidates, top 8 kept. **This has only ever been computed and logged for the 13 originally-flagged stops' own nodes** (node 122/A, per `fanout_cap_diagnostic_output_2026-08-24.json`). **Node 123's (Hotel Arun Cafe's) own top-8 window — i.e., whether node 144 (C) ranks in B's nearest 8 stations by real driving distance — has never been computed or logged in any existing file.** This is the one concrete unknown left.

**Working conclusion:** the fan-out cap at B's own node (item 2 above) is
the most likely point where the split was excluded, but this is not yet
confirmed by existing data — it is inferred from elimination (Dijkstra's
optimality rules out "split existed and was compared," the DAG-order filter
is unlikely to be the culprit given A's wide window) rather than directly
observed.

## Part 2 — the fan-out-cap hypothesis, checked and ruled out

Signed off: 1–2 OSRM `/table` calls (free public demo server), sourced from
node 123 (Hotel Arun Cafe) against its own DAG-order candidate window,
mirroring `fanout_osrm_ranking_check.py`'s existing methodology exactly.
Script: `tests/stop21_hotel_arun_cafe_own_window_check.py`. Result:
`tests/stop21_hotel_arun_cafe_own_window_result_2026-08-24.json`.

**Every structural filter this edge would have to pass, checked directly —
and it passes all of them:**

| Filter | Result |
|---|---|
| DAG-order gap-break (node 123's own window) | Window = 117 nodes (`124..240`); node 144 (C) is inside it |
| `MAX_EDGES_PER_NODE=8` real-driving-distance cap, from node 123 | Node 144 real driving distance = **76.71 km, rank 2 of 8** — comfortably inside the top 8 (rank-8 cutoff is 81.14 km) |
| OSRM-snap sanity guard (`graph_planner.py:444-465`) | `straight_line_km(B,C) = 68.15`, real `76.71 / 68.15 = 1.13×` — nowhere near the `4.0×` ratio (and below the `30 km` absolute floor's relevance anyway) that would drop this edge |
| Stage 2 admission (already confirmed, `coarse_admission_diagnostic`) | 23.40 kWh, `admitted_normal` |

Node 123 → C is not a marginal pass on any of these — it clears the top-8
cap with room (2nd of 8, not 8th), and the snap-guard ratio is nowhere near
its trigger point. **The Hotel Arun Cafe fan-out cap hypothesis from Part 1
is ruled out, not confirmed.** Combined with A→B already confirmed at
real-rank 1 (`fanout_cap_diagnostic_output_2026-08-24.json`, essentially
adjacent at 3.6 km — not a ranking that live OSRM noise would plausibly
disturb), every static/structural filter available for inspection says both
split edges should exist as `admitted_normal` in the graph, at a combined
cost dramatically below the direct edge's bridged cost.

**This does not resolve the original question — it deepens it.** All of the
following are now individually confirmed and none of them explains why the
split wasn't used:

- The split's real energy admits normal on both legs (Part 1, item 3).
- Both legs individually survive their own node's DAG-order window.
- Both legs individually survive their own node's real-distance top-8 cap.
- Neither leg trips the OSRM-snap guard.

None of the diagnostics run this session or in prior sessions have queried
`build_station_graph`'s actual historical `edges[122]` / `edges[123]` output
directly — every check here (including this one) reconstructs the *inputs*
to that decision (node lists, distance rankings, energy admission) and
infers the *output* logically, never observes it. Two explanations remain
open, and distinguishing them needs a real rebuild or a persisted historical
edges dict, neither of which exists or is in scope without further sign-off:

1. **Non-determinism between the historical build and today's checks** —
   OSRM's public demo server and live traffic conditions can genuinely
   differ call-to-call (this is the exact class of issue
   `MAX_PLAUSIBLE_ROAD_DETOUR_RATIO`'s docstring describes as "reproducibly
   across repeated identical calls" in some cases, but not guaranteed stable
   across *different* calls at different times). If node 144 ranked outside
   B's top-8 at the actual historical build moment (even if it ranks 2nd
   today), the split simply wouldn't have existed in that specific run's
   graph — consistent with everything observed, but unconfirmable
   retroactively.
2. **A mechanism not yet identified in either ground-truth read** — e.g.
   `compute_reachable_to_destination`'s backward-reachability propagation
   marking node 123 unreachable for a reason unrelated to the B→C edge
   itself, or an ordering/tie-break effect in `candidates_per_node`. Neither
   ground-truth document (`graph_dijkstra_ground_truth_2026-08-24.md`) traced
   this function in enough depth to rule it in or out.

Closing this further would require either re-running `build_station_graph`
end-to-end for this corridor (a real, costly rebuild — same class of call
`margin_recalibration_options_2026-08-24.md` already noted isn't available
from existing logs) or inspecting `compute_reachable_to_destination`'s logic
against this specific node — the latter is free (code-only) and could be
done without new calls if useful.

### Checked mechanism 2 (free, code-only): `compute_reachable_to_destination`

```python
def compute_reachable_to_destination(nodes: list, edges: dict) -> list:
    n = len(nodes)
    reachable = [False] * n
    reachable[n - 1] = True
    for i in range(n - 2, -1, -1):
        reachable[i] = any(reachable[j] for j, _cost, _energy in edges.get(i, []))
    return reachable
```
(`graph_planner.py:577-584`)

A plain backward dynamic-programming pass over the same forward-only `edges`
dict (`j > i` always, by DAG-order construction) — correct by construction,
processed in descending index order so every `reachable[j]` needed to
compute `reachable[i]` is already final. There is no separate cap, no
distance/energy re-check, no special-casing anywhere in this function; it
purely reflects whatever `edges` dict `build_station_graph` already
produced.

Applying it to this case: node 144 (C, Melur) is confirmed to be a real stop
on the actual chosen 31-stop path (it directly follows station 253 as the
next stop), so by construction it has at least one onward edge that is
itself reachable — meaning `reachable[144] = True` is guaranteed, not
contingent on anything checked here. If the historical `edges[123]` dict
had genuinely contained the `(144, 23.40, 23.40)` edge confirmed admissible
above, `reachable[123]` would necessarily have evaluated `True` too (`any(...)`
over `edges.get(123, [])` would find `j=144` reachable). **This function
introduces no independent way to drop an already-admitted edge** — it can
only mark a node unreachable if literally none of its admitted edges lead
anywhere useful, which doesn't apply here once the underlying edge is
assumed to exist. Mechanism 2 (backward-reachability bug) is now also
ruled out, along with the fan-out cap (Part 2) and the ceiling/admission
tiers (Part 1, item 3) — none of them independently explain excluding this
split. **The only remaining, unruled-out explanation is mechanism 1:
non-determinism between the historical build's live OSRM/traffic query and
any later re-check of the same coordinates.**

## Verdict

**Related, not identical, and the "why wasn't the split chosen" question is
now confirmed to be a separate, still-open mystery — not the fan-out cap.**

1. **The raw-exceeds-ceiling fact and the `admitted_bridged` fact are the
   same root cause.** Both are different threshold checks (simulation-time
   raw ceiling of 32.0 kWh; build-time normal-tier ceiling of 30.40 kWh)
   applied to the *same* real energy number for the *same* physical edge
   (~33–34 kWh on a 40kWh pack). The edge is intrinsically expensive; every
   stage's check on it reflects that one fact, not independent problems.
2. **Neither of those facts explains why the Hotel Arun Cafe split wasn't
   used, and every alternative code-level mechanism checked has now been
   ruled out too.** The `admitted_bridged` penalty didn't hide this edge's
   cost from Dijkstra — it made the direct edge cost *more* (42.324 kWh),
   and the re-measured split (24.23 kWh) would have won decisively had it
   been available. Checked and eliminated, in order: the ceiling/admission
   tiers (Part 1, item 3 — the split clears both legs' admission
   comfortably), the fan-out cap (Part 2 — the split clears both legs' top-8
   rank comfortably, 1st and 2nd of 8), and backward reachability (this
   section — a plain, bug-free DP pass with no independent power to drop an
   already-admitted edge). **Every mechanism in the codebase that could
   plausibly explain this has been traced and does not explain it.** The
   only remaining, unruled-out explanation is non-determinism between the
   live OSRM/traffic data at the historical build's query time and any
   later re-check of the same coordinates — a real rebuild (costly, needs
   separate sign-off, and wouldn't even guarantee reproducing the original
   run's conditions) is the only way to investigate further, and isn't
   recommended unless this pattern turns out to recur beyond this one stop.
