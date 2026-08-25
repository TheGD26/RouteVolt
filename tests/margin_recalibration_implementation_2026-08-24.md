# RouteVolt — Margin recalibration implementation (Option C, Step 2)

## Code change

**`backend/app/services/battery_simulator.py`**

Added two module-level constants (after `CHARGE_TARGET_LOOKAHEAD_SEGMENTS`):

```python
SIMULATION_MARGIN_RATE = 0.06
SIMULATION_MARGIN_FLOOR_KWH = 1.5
```

Replaced `plan_charge_to_percent`'s flat additive-percent margin with the proportional-with-floor kWh addition, applied to *each* energy base it's checked against (mirroring the original code's own symmetry — both `required_pct_all` and `next_leg_required_pct` previously added the same flat `safety_margin_pct`; now each adds its own proportional-with-floor margin computed from its own base):

```python
    remaining_margin_kwh = max(SIMULATION_MARGIN_FLOOR_KWH, SIMULATION_MARGIN_RATE * remaining_energy_kwh)
    required_pct_all = ((remaining_energy_kwh + remaining_margin_kwh) / capacity_kwh) * 100.0
    ...
        next_leg_margin_kwh = max(SIMULATION_MARGIN_FLOOR_KWH, SIMULATION_MARGIN_RATE * next_leg_energy_kwh)
        next_leg_required_pct = ((next_leg_energy_kwh + next_leg_margin_kwh) / capacity_kwh) * 100.0
```

**`safety_margin` parameter kept, now unused inside the function.** Removing it would cascade into signature changes across `_build_stop_entry`, `plan_full_journey`, `reroute_from_stop` (battery_simulator.py) and `_simulate_feasibility_only`, `plan_journey_via_graph`, `find_minimum_starting_battery` (graph_planner.py) — all of which thread it through purely to reach this one function. Documented explicitly in the updated docstring rather than left silent.

**Untouched, confirmed:** `GRAPH_EDGE_SAFETY_MARGIN`, `build_station_graph`, `find_shortest_path`, Dijkstra, `MAX_EDGES_PER_NODE`, `MAX_DETOUR_FRACTION_OF_LEFTOVER_RANGE`, `CHARGE_TARGET_LOOKAHEAD_SEGMENTS`, and every API endpoint — no other file was edited.

## Verification 1 — arithmetic recomputation (no new API calls)

Re-ran the ceiling check for the 12 flagged edges using the already-logged raw predictions from `tests/energy_prediction_consistency_2026-08-24.json`, importing the **live implemented constants** (`SIMULATION_MARGIN_RATE`, `SIMULATION_MARGIN_FLOOR_KWH`) rather than re-typing them, to catch any drift between documentation and code:

| Stop | sim_raw (kWh) | margin (kWh) | required_pct | fires? |
|---|---|---|---|---|
| 6 | 15.961 | 1.5000 | 43.65% | No |
| 7 | 19.908 | 1.5000 | 53.52% | No |
| 14 | 7.078 | 1.5000 | 21.44% | No |
| **15** | 30.681 | 1.8409 | 81.30% | **Yes** |
| 16 | 3.728 | 1.5000 | 13.07% | No |
| 17 | 28.842 | 1.7305 | 76.43% | No |
| 20 | 9.506 | 1.5000 | 27.52% | No |
| **21** | 33.171 | 1.9903 | 87.90% | **Yes** |
| 25 | 10.197 | 1.5000 | 29.24% | No |
| 26 | 21.704 | 1.5000 | 58.01% | No |
| 27 | 22.223 | 1.5000 | 59.31% | No |
| 28 | 30.151 | 1.8091 | 79.90% | No |

**Exact match to the diagnostic's prediction: 10 of 12 resolved, stops 15 and 21 still fire.** Zero confounds — same 12 raw predictions, only the margin formula changed.

## Verification 2 — live end-to-end run

Approved and run: `plan_journey_via_graph` at the exact baseline parameters (Chennai→Nagercoil, mid_ev, half_load, battery_pct=8.0). First attempt failed at 13.1s (OSRM table request timeout on the public demo server, before the graph was even built — infrastructure flakiness, not related to the change; retried). Second attempt succeeded: **246.5s wall-clock**, `feasible=True`.

**Important confound, reported plainly rather than absorbed into the headline numbers:** the chosen path itself changed — **31 stops (baseline) → 33 stops (this run)**, only 23 stations shared between them, with meaningfully different routing in two separate stretches of the corridor. Confirmed why: `plan_journey_via_graph` does a **full fresh graph rebuild** each call, hitting live OSRM/traffic APIs — the baseline capture happened hours earlier in this session, and real-time traffic conditions returned by the shared public demo servers differ between calls made at different times, changing build-time edge-cost estimates enough to shift Dijkstra's chosen path in places. This is **not** a margin-fix side effect: the ground-truth read (`tests/graph_dijkstra_ground_truth_2026-08-24.md`) already confirmed simulation-time margin has zero influence on graph topology or path selection — both happen upstream of and independent from `plan_charge_to_percent`.

Confirmed this empirically too, not just structurally: of the 4 "new" flagged station_ids in this run (34, 172, 398, 175) —

- **172, 398, 175** don't appear anywhere in the baseline's 31-stop path at all — they only exist because this run's graph build chose a different route through that stretch of the corridor.
- **34** *does* appear in both paths, but its outgoing edge differs entirely between them — baseline routes `34 → 136`, this run routes `34 → 398`. It's evaluating a completely different physical segment in each run, not the same edge re-flagged under new margin math.

**Because of this path-level confound, the live run's raw station-ID tally (5 resolved / 8 still firing / 4 new, against the original 13) is not a valid isolated measurement of the margin fix's effect** — it's conflating "different segments now exist" with "the same segments now pass or fail differently." **Verification 1's arithmetic recomputation is the clean, confound-free confirmation that the mechanism itself works exactly as designed.** Verification 2's value is different and still real: it confirms the new code runs correctly through a full live pipeline (no crashes, sensible `charge_to_percent`/`exceeded_ceiling` output, `feasible=True`) under real-world conditions — not that it reproduces the exact same 12-segment outcome on a path that no longer exists in this run.

**No further live run was attempted to get a clean live A/B** (e.g. reverting the code temporarily to re-capture the *old* margin's behavior against *this* run's new 33-stop path) — that would mean temporarily undoing this task's own change, out of scope here.

## Side-effect flag

**One unresolved ambiguity worth surfacing, not glossed over:** station 34's segment (`34 → 398` in this run) is *new* to the flagged set, but since it's a physically different edge than anything in the original 12/13, whether the new margin formula handles it "correctly" the way it handled the original 12 hasn't been separately checked — it's an edge outside this diagnostic thread's original sample. Flagged for visibility, not treated as a failure of this implementation (the new formula is a fixed, general mechanism, not tuned to a specific edge list — it should behave consistently on any edge, but that consistency wasn't independently re-verified for edges outside the original 12).

## Summary

- Code change: in place, `battery_simulator.py` only, `safety_margin` parameter kept for interface stability but no longer used by this function.
- Static verification: exact match to prediction, 10/12 resolved, stops 15 & 21 remain (both already known/expected, out of scope for this task).
- Live verification: new code runs correctly end-to-end; direct before/after comparison against the original 12 is confounded by an unrelated path change between the two live API calls (confirmed both structurally and empirically) — not a defect in this change.
