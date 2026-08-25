# RouteVolt — Graph builder + Dijkstra ground truth

Pure fact-finding, no diagnosis. Every claim below is backed by a verbatim
quote and file:line range read directly from the live repo checkout at the
time of this report. `/mnt/project/` was not used — it's a stale snapshot
that doesn't contain `graph_planner.py` at all.

## 1. File location

`backend/app/services/graph_planner.py` — 1071 lines total, confirmed via `wc -l`.

## 2. `build_station_graph`

**Location: `backend/app/services/graph_planner.py:338-574`**

```python
def build_station_graph(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    candidates: list, vehicle_profile: str,
    load_state: str = "half_load", payload_kg: Optional[float] = None,
    safety_margin: float = GRAPH_EDGE_SAFETY_MARGIN,
) -> dict:
```
(`graph_planner.py:338-343`)

### 2a. What determines which station-pairs are even considered as candidate edges

Two separate pre-filters run before any per-edge network call, at two different levels:

**Level 1 — which stations become graph nodes at all.** This happens *before*
`build_station_graph` is even called, in `filter_candidate_stations`
(`graph_planner.py:195-255`), a pure-haversine detour filter:

```python
    Cheap pre-filter before the expensive graph build: a station only makes
    the cut if routing through it doesn't add more detour than a full
    charge's range (MAX_DETOUR_FRACTION_OF_LEFTOVER_RANGE) -- e.g. a station
    a few km off a 400km trip on a 300km-range vehicle is fine, one that
    would add 400km of its own detour is not, regardless of how "close" it
    looks on a map. Uses haversine, not OSRM, on purpose -- this runs
    against every station in range (hundreds), and only the survivors go on
    to the real distance-matrix/routing calls below.
```
(`graph_planner.py:200-207`, docstring)

```python
    direct_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    ...
    for station in stations:
        ...
        origin_dist_km = haversine_km(origin_lat, origin_lon, lat, lon)
        detour_km = origin_dist_km + haversine_km(lat, lon, dest_lat, dest_lon) - direct_km
        if detour_km <= detour_threshold_km:
            candidates.append({**station, "detour_km": round(detour_km, 2), "_origin_dist_km": origin_dist_km})
```
(`graph_planner.py:219, 228, 233-236`)

**Level 2 — which node *pairs* (i, j) are even looked at as candidate edges,
inside `build_station_graph` itself.** This is a second, separate haversine
pre-filter, the "DAG-order gap break":

```python
        for j in range(i + 1, n):
            approx_gap_km = nodes[j]["dist_from_origin_km"] - nodes[i]["dist_from_origin_km"]
            if approx_gap_km > vehicle_range_km:
                break  # every later j is farther still -- DAG order guarantees this
```
(`graph_planner.py:436-439`)

`dist_from_origin_km` here is itself haversine, computed once per node at
construction time:

```python
    dest_dist = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    dest_node = {"lat": dest_lat, "lon": dest_lon, "dist_from_origin_km": dest_dist, "kind": "destination"}
```
(`graph_planner.py:383-384`, one representative line; the same haversine call
constructs `dist_from_origin_km` for every candidate node at `graph_planner.py:396`)

So: **yes, a haversine pre-filter runs before any real distance/routing call
is even read for a given pair** — the `approx_gap_km` break at line 438
happens *before* line 441 reads that pair's real driving distance out of the
already-fetched OSRM matrix. This break is on distance-from-*origin*
positioning (a DAG-order argument — nodes are sorted by haversine distance
from the global origin, so if node j is farther from the origin than
`vehicle_range_km` beyond node i, every later j is farther still and the
loop stops), not a per-pair haversine-distance check between i and j
specifically.

### 2b. Where/how `MAX_EDGES_PER_NODE` is applied — ranking key

Applied to a list already carrying **real OSRM driving distance**
(`distances_km`), not haversine and not energy:

```python
            distance_km = matrix["distances_km"][i][j]
            duration_min = matrix["durations_min"][i][j]
```
(`graph_planner.py:441-442`)

```python
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
```
(`graph_planner.py:467, 469-477`)

`c[1]` is `distance_km` (the tuple is `(j, distance_km, duration_min)` per
line 435's comment and line 467's append). **The ranking key is real OSRM
driving distance from `get_distance_matrix`, sorted ascending, top
`MAX_EDGES_PER_NODE=8` kept.** Not haversine (haversine was already used one
step earlier only to decide the DAG-order window, not to rank within it),
not energy (energy isn't computed until Stage 2, after this cap already ran).

`MAX_EDGES_PER_NODE = 8` is defined at `graph_planner.py:116`.

### 2c. `routing_service.get_distance_matrix` — called with what, and when relative to the cap

Called **once, in bulk, for every node in the graph, before the per-node
candidate loop (and therefore before the cap) runs at all**:

```python
    coordinates = [(n["lat"], n["lon"]) for n in nodes]
    matrix = routing_service.get_distance_matrix(coordinates)
```
(`graph_planner.py:405-406`)

This happens at line 406; the per-node loop that reads `matrix["distances_km"][i][j]` starts at line 434 and the cap is applied at line 475-477 — i.e. the full bulk matrix call is already complete and cached in the local `matrix` variable well before any per-node ranking or capping happens. One HTTP-call-worth of real distances (chunked by `routing_service._get_distance_matrix_chunked` if needed) covers every node pair the DAG-order loop will ever look up.

### 2d. The coarse admission check — quoted with real current line numbers

**Reported last diagnostic as lines 556-561; the real current lines are
`graph_planner.py:561-569`** (shifted by +5/+8 since the last diagnostic — this
session added `_simulate_feasibility_only` and `find_minimum_starting_battery`
earlier in the file, between `find_shortest_path` and `plan_journey_via_graph`,
which pushed everything after it down). Quoted verbatim at its current location:

```python
    edges: dict[int, list[tuple[int, float, float]]] = {}
    cursor = 0
    for (i, j), count in zip(pair_ij, pair_sub_leg_counts):
        energy_kwh = sum(sub_leg_energies_kwh[cursor:cursor + count])
        cursor += count
        if energy_kwh <= usable_kwh_at_ceiling:
            edges.setdefault(i, []).append((j, energy_kwh, energy_kwh))
        elif energy_kwh <= usable_kwh_at_full_charge:
            edges.setdefault(i, []).append((j, energy_kwh + EXTENDED_CHARGE_PENALTY_KWH, energy_kwh))
```
(`graph_planner.py:561-569`)

with the two thresholds it checks against, defined just above:

```python
    sub_leg_energies_kwh = estimate_leg_energy_kwh_batch(feature_rows)
    capacity_kwh = VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"]
    usable_kwh_at_ceiling = capacity_kwh * (BATTERY_CEILING_PCT / 100.0) * (1 - safety_margin)
    usable_kwh_at_full_charge = capacity_kwh * (1 - safety_margin)
```
(`graph_planner.py:550-553`)

**Literally what this checks:** for every candidate edge `(i, j)` that
survived Stage 1's cap, `estimate_leg_energy_kwh_batch` has already produced
a real predicted-kWh figure (`energy_kwh`) from the real per-sub-leg
route/traffic/elevation data fetched in Stage 2 (`_fetch_edge_sub_legs`).
This one number is then compared against two tiers:

- `energy_kwh <= usable_kwh_at_ceiling` (an 80%-charge-minus-margin budget) →
  admitted as a **normal edge**, Dijkstra cost = real energy (no penalty).
- else, `energy_kwh <= usable_kwh_at_full_charge` (a 100%-charge-minus-margin
  budget) → admitted as a **bridging/extended-charge edge**, Dijkstra cost =
  real energy **plus `EXTENDED_CHARGE_PENALTY_KWH` (8.0)**, so Dijkstra only
  reaches for it when nothing cheaper clears.
- else (energy exceeds even a full 100% charge) → **no branch matches, the
  edge is simply never added to `edges`** — rejected outright, no `else` clause exists.

This is a **single direct-edge admission decision per (i, j) pair** — it
does not compare the direct edge against any alternative multi-hop routing
through a third node. See 2e below.

### 2e. Are "split" options ever explicitly constructed, or purely emergent from Dijkstra?

**No explicit split construction exists anywhere in this file.** Every edge
built in the loop above (`graph_planner.py:562-569`) is a **direct
station-to-station hop** — `(i, j)` with `j` drawn only from `i`'s own
DAG-order/top-8-capped candidate list. There is no code path anywhere in
`build_station_graph` that, having considered a direct `A→C` edge, then
constructs or compares it against an `A→B→C` two-edge alternative. The
function's entire output is just a flat `edges: dict[int, list[tuple[int,
float, float]]]` adjacency structure (`graph_planner.py:561`,
`graph_planner.py:574`'s return) — a plain graph, no path-level reasoning at
build time at all.

A "split" (using an intermediate station B instead of going A→C directly)
can only emerge as **Dijkstra's side effect of the A→B and B→C edges both
independently existing in this adjacency structure and their combined cost
undercutting the direct A→C edge's cost** (or the direct edge simply not
existing at all, e.g. if it fell outside `vehicle_range_km` or failed the
energy check). There is no explicit "should I split this" decision point in
`build_station_graph` or anywhere else in this file — confirmed by reading
the full function; the only per-pair decision is the binary/ternary
admit-as-normal / admit-as-bridging / reject classification quoted in 2d,
made independently for every pair with no awareness of other pairs.

**Model confirmed: direct-edges-only graph; multi-hop "splits" are purely
emergent from Dijkstra traversing two edges instead of one, never an
explicit construct.**

## 3. `find_shortest_path`

**Location: `backend/app/services/graph_planner.py:601-659`**

```python
def find_shortest_path(
    nodes: list, edges: dict, reachable: list, onward_degree: list,
    origin_battery_pct: float, vehicle_profile: str,
    safety_margin: float = GRAPH_EDGE_SAFETY_MARGIN,
) -> dict:
```
(`graph_planner.py:601-605`)

### 3a. Edge weight = predicted energy in kWh

```python
        for j, cost, real_energy_kwh in edges.get(i, []):
            if not reachable[j]:
                continue
            if i == 0 and real_energy_kwh > usable_kwh_from_origin:
                continue
            new_dist = d + cost
            if new_dist < dist[j]:
                dist[j] = new_dist
```
(`graph_planner.py:639, 642, 644-646`)

`cost` (the second element of each edge tuple) is exactly the `dijkstra_cost`
value constructed in `build_station_graph` at lines 566/568 — real kWh for a
normal edge, real kWh + `EXTENDED_CHARGE_PENALTY_KWH` for a bridging edge
(confirmed in section 2d above). `new_dist = d + cost` (line 644) is the
relaxation step, directly summing these kWh-denominated edge weights. So
**yes, edge weight = predicted energy in kWh**, confirmed by direct reuse of
the same `cost` field `build_station_graph` populated.

### 3b. State = node only, not node+battery, except the origin's outgoing edges

```python
    dist = [math.inf] * n
    prev = [None] * n
    dist[0] = 0.0
    heap = [(0.0, -onward_degree[0], 0)]
```
(`graph_planner.py:626-629`)

`dist` and `prev` are both flat arrays indexed only by node index `i` — no
battery dimension anywhere in the state. **Confirmed: state = node only.**

The one exception — the origin's outgoing edges only — is this explicit
check, found nowhere else in the relaxation loop:

```python
    usable_kwh_from_origin = (
        VEHICLE_PROFILES[vehicle_profile]["battery_capacity_kwh"] * (origin_battery_pct / 100.0) * (1 - safety_margin)
    )
    ...
        for j, cost, real_energy_kwh in edges.get(i, []):
            if not reachable[j]:
                continue
            if i == 0 and real_energy_kwh > usable_kwh_from_origin:
                continue
```
(`graph_planner.py:623-625, 639, 642`)

`i == 0` is the only node-identity check gating an edge by battery level in
this whole function — node 0 is always the origin (`nodes = [origin_node,
*sorted_candidates, dest_node]`, `graph_planner.py:403`). Every other node's
outgoing edges are relaxed with no battery check at all — the function's own
docstring states why: "every edge already assumes whatever charge (ceiling
or full) its own tier needs at its start node" (`graph_planner.py:610-611`),
i.e. the graph's two-tier admission (section 2d) is what stands in for a
battery dimension everywhere except the origin, where the real starting
charge (not an assumed ceiling/full charge) is the actual constraint. Note
`BATTERY_CEILING_PCT` itself is not referenced by name anywhere inside
`find_shortest_path` — it's consumed earlier, in `build_station_graph`'s
`usable_kwh_at_ceiling` (section 2d), which is what makes the node-only state
simplification valid down here.

### 3c. Does Dijkstra ever explicitly compare a direct A→C edge against an A→B→C alternative?

No explicit comparison exists. The full relaxation loop:

```python
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
```
(`graph_planner.py:632-648`)

This is standard Dijkstra — pop the lowest-`dist` unvisited node, relax every
one of its outgoing edges, push updates onto the heap. There is no code here
that identifies "this is a direct A→C edge" versus "this is a two-hop
alternative" as distinct cases — every edge, whether it happens to be part
of what a human would call a "direct" or a "split" route, is relaxed by the
exact same four lines (642-648) with no special-casing. **The "comparison"
between a direct edge and a two-hop alternative through an intermediate
station is entirely implicit in whichever of those edges happen to exist in
the `edges` adjacency dict and their summed costs** — confirmed, matching
section 2e's conclusion from the graph-build side.

## 4. Where `exceeded_ceiling` / the fine-grained check lives

**`plan_charge_to_percent`, `backend/app/services/battery_simulator.py:118-190`**
computes the flag itself:

```python
    exceeded_ceiling = required_pct_all > BATTERY_CEILING_PCT or next_leg_required_pct > BATTERY_CEILING_PCT
    if exceeded_ceiling:
        target_pct = min(max(target_pct, required_pct_all, next_leg_required_pct), 100.0)

    return {
        "charge_to_percent": round(target_pct, 1),
        "exceeded_ceiling": exceeded_ceiling,
    }
```
(`battery_simulator.py:183-190`)

This is called from `_build_stop_entry` (`battery_simulator.py:245-299`),
which is called once per stop, in order, from `plan_full_journey`
(`battery_simulator.py:302-478`) — the leg-by-leg simulator. `plan_full_journey`
only ever iterates over the `route_leg_groups`/`stops` arguments it was
handed:

```python
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
```
(`battery_simulator.py:401, 405-411`)

`route_leg_groups`/`stops` are constructed entirely by `graph_planner.
plan_journey_via_graph` (`graph_planner.py:963` onward) from the single path
Dijkstra already returned:

```python
    path_indices = path_result["path"]
    path_nodes = [nodes[i] for i in path_indices]
    stop_nodes = path_nodes[1:-1]  # exclude origin & destination
    ...
        for a_idx, b_idx, a, b in zip(path_indices, path_indices[1:], path_nodes, path_nodes[1:]):
            route = route_cache.get((a_idx, b_idx))
            ...
            route_leg_groups.append(route["legs"])
    ...
    journey = plan_full_journey(
        route_leg_groups=route_leg_groups,
        stops=stop_nodes,
        battery_pct=battery_pct,
        ...
    )
```
(`graph_planner.py:1025-1027, 1039, 1043, 1052-1056`)

**Confirmed: `plan_full_journey` runs strictly over the fixed
`route_leg_groups`/`stop_nodes` derived from `path_result["path"]` — the one
path Dijkstra already committed to before `plan_full_journey` is ever
called.** Nothing in `plan_full_journey`, `plan_charge_to_percent`, or
`_build_stop_entry` accepts the graph (`nodes`/`edges`) as an argument or has
any way to re-query it. `graph_planner.py`'s own
`_log_exceeded_ceiling_drift` (`graph_planner.py:684-734`), which runs
*after* `plan_full_journey` returns, only reads the flag back out for
logging/reporting — it looks up the already-existing edge's coarse estimate
for comparison (`graph_planner.py:715-718`, reading `edges.get(node_idx,
[])`), but it does not re-invoke Dijkstra, does not construct any alternative
path, and does not feed anything back into the simulation that already ran:

```python
        coarse_energy_kwh = next(
            (real_energy for j, _cost, real_energy in edges.get(node_idx, []) if j == next_node_idx),
            None,
        )
```
(`graph_planner.py:715-718`)

This confirms the stated understanding: **the fine-grained simulation runs
against the same graph edges Dijkstra already committed to, with no ability
to go back and ask the graph "was there an unused split option here."** The
graph's `edges` dict is only consulted afterward, read-only, for the
drift-logging comparison — never to search for an alternative.

## Two-model summary

**A "split" (intermediate-station A→B→C routing) is not an explicit
construct anywhere in this code.** `build_station_graph` only ever builds
direct, independent station-to-station edges (section 2e); `find_shortest_path`
runs unmodified, textbook Dijkstra relaxation with no A-vs-A→B→C
special-casing (section 3c); and `plan_full_journey`/`plan_charge_to_percent`
simulate strictly along the one path Dijkstra already returned, with no
mechanism to reconsider the graph afterward (section 4). **A "split" exists
only as an emergent side effect of Dijkstra choosing two cheaper edges over
one direct edge from whatever flat adjacency structure `build_station_graph`
happened to produce** — there is no point in the pipeline, at build time,
search time, or simulation time, where "should this segment be split"
is ever asked as an explicit question.
