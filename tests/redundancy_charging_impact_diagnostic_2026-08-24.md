# RouteVolt — Adjacent-station redundancy charging: impact diagnostic

Measurement only. No production code changed — `plan_charge_to_percent`, `build_station_graph`, `MAX_EDGES_PER_NODE`, and Dijkstra are all untouched. This answers one question: if the proposed rule ("charge to whatever covers the worst-case of a station's real top-8 graph neighbors, at every stop") were applied to the current baseline path, how big would the impact be — and does it show the same over-aggressive pattern the full-trip-lookahead regression (leg-97) showed?

## Data note — read before the table

The full per-stop, per-neighbor real-energy data this diagnostic needs was **not** already available from logged data (checked first, per task scope): the most recent baseline (`tests/health_check_output_2026-08-25.json`) has the 31-stop path and each stop's post-Option-C `charge_to_percent`, but the graph's `edges` dict (real per-edge kWh to each neighbor) is discarded after every build, never persisted. A prior diagnostic (`tests/fanout_cap_diagnostic_output_2026-08-24.json` / `fanout_osrm_ranking_result.json`) covered only 13 of the 31 stops, and only real driving **distance**, not real **energy**.

Per the task's own scope rule, this was flagged to the user before running anything new. **Approved approach: a targeted partial build** (`tests/redundancy_charging_impact_diagnostic.py`), not a full graph rebuild — for exactly the 31 path stops' own top-8 windows, reusing `graph_planner`'s own node reconstruction, snap-guard, and per-edge refinement functions directly (`_fetch_edge_sub_legs`, `_sub_leg_feature_kwargs`, `MAX_EDGES_PER_NODE`, `MAX_PLAUSIBLE_ROAD_DETOUR_RATIO`) so there's no reimplementation drift risk. This computed real energy for **246 unique edges** (31 stops × up to 8 neighbors, minus a few shared neighbors and one 6-neighbor window near the destination) in **24.3s, 0 dropped** — real OSRM route + traffic calls, degraded by the same live Ola Maps 429 rate-limiting already documented in recent baseline runs (fell back to Google, then straight-line, for several sub-legs — expected, not a new issue).

**Combination rule tested** (per the brief, not the additive/stacking version):
`final_target_pct = max(current post-Option-C charge_to_percent, worst_neighbor_target_pct)`, capped at 100%.

`worst_neighbor_target_pct` uses the **same margin formula** `plan_charge_to_percent` already applies to `next_leg_required_pct` (`SIMULATION_MARGIN_RATE=0.06`, `SIMULATION_MARGIN_FLOOR_KWH=1.5`), for apples-to-apples comparison with the current target — this is a modeling choice made for this diagnostic, not yet a design decision.

Stops #15 and #21 (1-indexed, station 91 and 253) were **not** re-diagnosed, per scope — their rows are included only because they're part of every stop's table, and both show `delta_pct = 0.0` (their current targets, already 91.2% and 100.0% from the known/parked issue, already exceed what the worst-neighbor rule would additionally demand).

---

## Part 1 — Per-stop table

`current` = post-Option-C `charge_to_percent` from the baseline. `worst_kWh` = real energy of the single most expensive of the stop's (up to 8) real top-8 neighbors. `worst_%` = that energy converted to a charge target (with margin). `final` = `max(current, worst_%)`, capped at 100. `Δ` = `final − current`. `rank` = the worst neighbor's position in the top-8 when ranked by real driving distance (1 = nearest, 8 = farthest) — a proxy for "was this an outlier that barely survived the fan-out cap, or a genuinely close neighbor."

| # | Station | Name | current % | worst kWh | worst % | **final %** | **Δ** | worst-rank |
|---|---|---|---:|---:|---:|---:|---:|:---:|
| 1 | 27 | Nungambakkam - Prestige Palladium Bayan | 20.0 | 6.51 | 20.0 | 20.0 | 0.0 | 7/8 |
| 2 | 30 | T Nagar - Grand Chennai by GRT Hotels | 28.9 | 5.24 | 16.9 | 28.9 | 0.0 | 7/8 |
| 3 | 23 | Guindy - Olympia Tech Park | 21.6 | 8.26 | 24.4 | 24.4 | **2.8** | 5/8 |
| 4 | 19 | Chennai Airport - Domestic Parking | 22.4 | 18.96 | 51.1 | 51.1 | **28.7** | 8/8 |
| 5 | 20 | Chennai Airport - International Parking | 68.4 | 23.81 | 63.3 | 68.4 | 0.0 | 8/8 |
| 6 | 398 | Kalyan Grand Business Hotel | 100.0 | 37.23 | 98.7 | 100.0 | 0.0 | 5/8 |
| 7 | 11 | Mamandur - Ram Residency | 60.0 | 44.64 | 118.3 | 100.0 | **40.0** | 5/8 |
| 8 | 425 | Hotel Aishwarya Bhavan | 28.9 | 25.29 | 67.0 | 67.0 | **38.1** | 8/8 |
| 9 | 426 | Zeon Charging - Nalathur | 33.6 | 25.98 | 68.8 | 68.8 | **35.2** | 6/8 |
| 10 | 415 | IOCL - Sakthi Agency | 35.2 | 10.97 | 31.2 | 35.2 | 0.0 | 7/8 |
| 11 | 430 | Vikravandi - Arutperun Jothi | 32.3 | 14.13 | 39.1 | 39.1 | 6.8 | 8/8 |
| 12 | 88 | IOCL - K G P Amaravathi | 23.5 | 24.08 | 64.0 | 64.0 | **40.5** | 8/8 |
| 13 | 85 | IOCL - Shivasakthi Singaram (1) | 39.1 | 38.23 | 101.3 | 100.0 | **60.9** | 8/8 |
| 14 | 87 | Cuddalore - Argunt Aggregates | 100.0 | 40.32 | 106.9 | 100.0 | 0.0 | 8/8 |
| 15* | 91 | IOCL - Raja Traders | 91.2 | 31.59 | 83.7 | 91.2 | 0.0 | 5/8 |
| 16 | 320 | Quality Inn Viha | 86.3 | 51.75 | 137.1 | 100.0 | 13.7 | 7/8 |
| 17 | 319 | Kumbakonam - Cholaa Dynasty | 99.3 | 55.14 | 146.1 | 100.0 | 0.7 | 6/8 |
| 18 | 358 | Trichy - Hotel Balaji Ariyas | 30.7 | 14.48 | 40.0 | 40.0 | 9.3 | 5/8 |
| 19 | 354 | Charge Zone - Tiruchirappalli | 32.9 | 29.22 | 77.4 | 77.4 | **44.5** | 8/8 |
| 20 | 249 | Kaveri Garage Piratiyur | 100.0 | 40.80 | 108.1 | 100.0 | 0.0 | 8/8 |
| 21* | 253 | IOCL - Sugan Tradings | 100.0 | 37.01 | 98.1 | 100.0 | 0.0 | 7/8 |
| 22 | 195 | Melur - Zeon X A.N Energy Solutions | 35.4 | 14.29 | 39.5 | 39.5 | 4.1 | 8/8 |
| 23 | 183 | Madurai - Grand Madurai by GRT Hotels | 58.2 | 4.07 | 13.9 | 58.2 | 0.0 | 8/8 |
| 24 | 181 | Jasmine Filling Station | 80.8 | 20.09 | 54.0 | 80.8 | 0.0 | 8/8 |
| 25 | 440 | Ather - TRZ Virudhunagar | 84.5 | 23.96 | 63.7 | 84.5 | 0.0 | 8/8 |
| 26 | 438 | Ather - TRZ Sivakasi | 100.0 | 47.11 | 124.8 | 100.0 | 0.0 | 8/8 |
| 27 | 365 | IOCL - Rajam Agency | 100.0 | 35.40 | 93.8 | 100.0 | 0.0 | 8/8 |
| 28 | 360 | IOCL - GSM | 100.0 | 43.39 | 115.0 | 100.0 | 0.0 | 8/8 |
| 29 | 373 | Ather-Eagle Traders (Vallioor) | 52.9 | 24.28 | 64.5 | 64.5 | 11.6 | 5/8 |
| 30 | 147 | Nagercoil, Thovalai - Carnival City | 20.0 | 19.29 | 52.0 | 52.0 | **32.0** | 4/8 |
| 31 | 146 | Ather-Nagercoil | 20.0 | 10.34 | 29.6 | 29.6 | 9.6 | 2/6 |

`*` = stops #15/#21, pre-cleared known/parked issue, not re-diagnosed here (both `Δ = 0`).

`mid_ev` battery capacity = 40 kWh, so any `worst kWh` figure above 40 means the single worst-case neighbor edge, by itself, needs more energy than a full battery physically holds — before any margin is even added.

---

## Part 2 — Aggregate impact

- **No change** (worst-neighbor requirement already below current target): **15 of 31 stops** (48%) — stops #1, 2, 5, 6, 10, 14, 15*, 20, 21*, 23, 24, 25, 26, 27, 28.
- **Meaningful increase**: **16 of 31 stops** (52%). Average increase among those 16: **+23.7 percentage points**. Max increase: **+60.9pp** (stop #13, IOCL Shivasakthi Singaram, 39.1% → 100.0%).
- **Final target at 100%**: **11 of 31 stops** (35%) — #6, 7, 13, 14, 16, 17, 20, 21*, 26, 27, 28. Of these, **7 were already at (or virtually at) 100% under Option C alone** (#6, 14, 20, 21*, 26, 27, 28 — pre-existing, not caused by this rule); **this rule newly drives 3 more to exactly 100%**: #7 (60.0%→100.0%), #13 (39.1%→100.0%), #16 (86.3%→100.0%).
- **Newly crosses the 80% ceiling** (was below 80% under Option C, now at/above 80% under this rule): **2 of 31 stops** — #7 (60.0%→100.0%) and #13 (39.1%→100.0%). Both jump straight past the ceiling to 100%, not to a value near 80% — this rule doesn't produce gentle ceiling-approaches, it either leaves a stop untouched or drives it to the far end.
- **New hard infeasibility — worse than `exceeded_ceiling`**: **8 of 31 stops** (26%) have `worst_neighbor_target_pct > 100%` — meaning **even a full 100% charge would not be enough** to guarantee reaching that stop's worst-case top-8 neighbor. This is a failure mode that **does not exist today**: current `exceeded_ceiling` always has an escape hatch (charge more, up to 100%, and it's enough); this rule, as specified (strict worst-case, top-8, no cap on the resulting target beyond the report's own 100% ceiling), would produce a genuine "no charge percentage solves this" case at stops #7, #13, #14, #16, #17, #20, #26, #28. **7 of those 8** have `worst kWh` that exceeds the 40kWh battery capacity outright — not a margin artifact, the raw energy estimate for that one hop already exceeds what the vehicle can physically hold in one charge.

**Outlier-vs-broad-based (Part 2's key question):** of the 16 stops with a meaningful increase, the worst-case neighbor was the single **farthest-by-distance** entry (rank 7 or 8 of the top-8) at **8 of 16 stops** — a near-even split with the other **8 of 16**, where the worst-case neighbor was mid-pack (rank 2–6), i.e. not even the "barely survived the cap" edge case. **This is broad-based, not a small number of outliers.** Unlike leg-97 (one specific full-trip-lookahead edge case), roughly half of the impacted stops have their worst neighbor sitting comfortably inside a "reasonable" distance rank — the energy spike comes from real elevation/traffic/road-type variation among genuinely nearby candidates, not just distant ones that only qualify because nothing closer existed.

---

## Part 3 — Softening options to test conceptually (not implemented, no recommendation)

Given Part 2 shows a large fraction of stops (52% see an increase, 35% pushed to 100%, 26% would produce a new hard-infeasibility failure), here are three alternative framings and what each would qualitatively change:

1. **Worst-case among top-3 (instead of top-8).**
   Of the 16 increased stops, the worst-case neighbor's distance-rank was 4 or worse at **15 of 16** (ranks observed: 5, 8, 5, 8, 6, 8, 8, 8, 7, 6, 5, 8, 8, 5, 4 — only stop #31 at rank 2/6 falls inside a top-3 window). The same is true of all 8 hard-infeasibility cases (worst-ranks 5, 8, 8, 7, 6, 8, 8, 8 — every one ≥ 5). So a top-3 rule would remove the *specific* worst-case neighbor currently driving the increase at nearly every impacted stop. That is not the same as saying the increase disappears entirely, though: this diagnostic only computed the single worst-case energy per stop, not the full 8-neighbor energy list, so whether the *new* worst-case among just that stop's nearest 3 neighbors is still above the current target is unmeasured here — plausible it's lower (nearer stations are usually cheaper to reach) but not confirmed. Would need the already-fetched edges' full per-neighbor energy (not just the max) to answer precisely — no new API calls required, just re-deriving from data this diagnostic already has in the underlying script's intermediate state.

2. **Exclude neighbors beyond a real-distance/detour cutoff.**
   Would target the same underlying cause differently — instead of a fixed count (top-3), drop any candidate whose real driving distance is disproportionate versus the segment's own direct hop (mirrors the existing `MAX_PLAUSIBLE_ROAD_DETOUR_RATIO`/`MIN_ABSOLUTE_KM_FOR_DETOUR_CHECK` snap-guard idea, reused for a different purpose). This would likely catch both the rank-7/8 cases *and* some of the mid-rank cases that are only "close by distance-rank" but still represent a large real detour in a sparse stretch of the corridor — a more targeted cut than top-3, at the cost of needing a new threshold to calibrate (unlike top-3, which reuses an already-familiar number).

3. **Percentile-based (e.g. 75th percentile of the 8) instead of strict worst-case.**
   Softest of the three — keeps all 8 neighbors' energy in the distribution rather than discarding any, but weights toward the expensive end without letting a single spike dominate. Given the near-even outlier/mid-pack split found here, this would blunt the impact of the single most extreme neighbor at every stop (most valuable exactly where one edge is a genuine spike above the rest, e.g. stop #17's 55kWh worst vs whatever its 2nd-worst is) but would still let a broadly-expensive top-8 list (many genuinely far/costly neighbors, not just one) drive the target up — this diagnostic didn't capture the full 8-neighbor energy distribution per stop (only the max was computed, by design, to answer the brief's specific ask), so the exact percentile-vs-worst-case delta isn't quantified here — would need a follow-up pass reusing the same 246 already-fetched edges' full energy list, not new API calls, if this direction is pursued.

---

## Verdict

**"Worst-case among top-8, every stop" shows the same over-aggressive pattern the full-trip-lookahead regression (leg-97) showed — arguably worse, because it's not concentrated in a few outlier neighbors.** 16 of 31 stops (52%) see a real increase, 11 of 31 get pushed to 100%, and 8 of 31 (26%) would produce a **new kind of hard failure that doesn't exist in the pipeline today** — a worst-case neighbor requirement that exceeds even a full 100% charge, in 7 of those 8 cases because the raw energy estimate for that single hop already exceeds the 40kWh battery capacity outright. Critically, this isn't the leg-97 shape of "a few genuinely bad rank-8 stragglers slipping through the fan-out cap": half of the impacted stops (8/16) have their worst-case neighbor sitting at a *middle* distance rank (2–6 of 8), meaning the fan-out cap isn't the primary culprit here — real energy variance (elevation, traffic, road type) among otherwise reasonable-looking neighbors is. **Not safe to implement as designed.** Of the three softenings sketched in Part 3, top-3 looks like the most promising starting point empirically (all 8 hard-infeasibility cases have worst-rank ≥ 5, so a top-3 window removes the specific neighbor driving each of them, though whether a smaller residual increase would still surface from within the nearest 3 is unmeasured here), but a detour-based cutoff may be needed on top of it for cases where even a nearby neighbor is a genuine outlier — this diagnostic doesn't decide between them, just lays out what each would change.
