# RouteVolt — Redundancy charging: full neighbor-distribution analysis (cutoff selection)

Measurement only. No production code changed — `plan_charge_to_percent`, `build_station_graph`, `MAX_EDGES_PER_NODE`, margin constants, and Dijkstra are all untouched. This answers: what does the full per-neighbor energy distribution look like at each stop (not just the worst-case value the closed diagnostic reported), and do top-3 / top-5 / 75th-percentile cutoffs produce a materially different, survivable outcome — especially for the 8 stops the closed diagnostic flagged as newly hard-infeasible?

## Data provenance — read before the numbers

The closed diagnostic (`tests/redundancy_charging_impact_diagnostic_2026-08-24.md`) computed real energy for 246 edges but its script (`tests/redundancy_charging_impact_diagnostic.py`) only ever persisted the single worst-case value per stop — the full `edge_energy_kwh` dict it built in memory (every neighbor's energy, not just the max) was discarded on exit, and no other file in `tests/` carries a substitute per-edge structure. So the premise that "the 246 already-refined edges cover the per-neighbor costs needed here" did not hold as-is: the full distribution had never actually been saved. This was flagged to the user before doing anything else; **re-running the identical 246-edge refinement (same stops, same top-8 windows, same `graph_planner` functions) was explicitly approved**, on the basis that it reuses the same edge set rather than requesting anything new.

Two scripts implement this:
- `tests/redundancy_cutoff_selection_diagnostic.py` — same refinement as the closed diagnostic, plus (a) persists every neighbor's energy/target, not just the max, and (b) sorts neighbors by genuine real driving distance for every stop (the original diagnostic's node list was only distance-sorted when a stop had *more* than 8 candidates before truncation; here every stop's neighbors are explicitly re-sorted by real `/table` distance so "rank" means the same thing everywhere).
- `tests/redundancy_cutoff_selection_retry_dropped.py` — the first run dropped 54/246 edges (live Ola Maps rate-limiting, concentrated in stops #24–#31 near the end of the corridor — the same documented degraded-network condition the closed diagnostic saw, just harder this time: 0 dropped there vs. 54 dropped initially here). A sequential retry with backoff recovered all 54; **final data has 0 dropped edges across all 246**.

**One live-data caveat that matters for the numbers below:** stop #28 (station 360, IOCL–GSM) was one of the closed diagnostic's 8 hard-infeasibility stops at `worst_% = 115.0%` (run on 2026-08-24). Re-measured today (2026-08-25) with fresh live traffic, its worst-neighbor target comes out at **95.8%** — no longer over 100%. Every other of the 8 reproduces closely (within ~1–7 percentage points of the original run, consistent with ordinary traffic/timing variance between two live fetches). Station 360's swing is larger and flips its classification. This isn't a bug in either diagnostic — `plan_charge_to_percent`'s margin formula and this rule both consume live, time-varying traffic estimates, so a stop sitting close to a threshold can cross it between two fetches taken a day apart. Treat the "8 hard-infeasibility stops" as **7 confirmed + 1 borderline/traffic-sensitive**, not 8 fixed failures — this itself is a finding, not noise to average away.

Margin formula (same as the closed diagnostic, for apples-to-apples): `target_pct = (energy_kWh + max(1.5, 0.06×energy_kWh)) / 40kWh × 100`, capped at 100%. `mid_ev` capacity = 40 kWh.

---

## Part 1 — Full per-neighbor distributions

### 1.1 — The 8 hard-infeasibility stops: rank of the offending neighbor, cliff vs. gradual

`targets by distance-rank` = every neighbor's charge-target %, ordered rank 1 (nearest by real driving distance) → rank 8 (farthest). `gap to 2nd-worst` = the worst value minus the second-worst value in the full list (a big gap = a sharp, isolated spike; a small gap = several neighbors are comparably expensive).

| Station | Name | current % | targets by distance-rank (1→8) | worst | worst-rank | gap to 2nd-worst | # neighbors >100% |
|---|---|---:|---|---:|:---:|---:|:---:|
| 11 | Mamandur - Ram Residency | 60.0 | [30.7, 16.8, 23.4, **110.2**, **117.4**, 77.3, 47.8, 50.2] | 117.4 | 5/8 | 7.2 | 2 |
| 85 | IOCL - Shivasakthi Singaram (1) | 39.1 | [34.2, 33.6, 33.6, 46.4, 55.9, 64.2, 80.9, **101.3**] | 101.3 | 8/8 | 20.4 | 1 |
| 87 | Cuddalore - Argunt Aggregates | 100.0 | [22.7, 37.6, 32.2, 32.2, 66.9, 70.2, 99.8, **106.9**] | 106.9 | 8/8 | 7.1 | 1 |
| 320 | Quality Inn Viha | 86.3 | [3.8, 6.4, 14.8, 66.7, **120.4**, 85.6, **133.1**, 98.9] | 133.1 | 7/8 | 12.7 | 2 |
| 319 | Kumbakonam - Cholaa Dynasty | 99.3 | [56.6, 74.7, 82.6, 89.1, 91.3, **146.1**, 94.1, 88.9] | 146.1 | 6/8 | 52.0 | 1 |
| 249 | Kaveri Garage Piratiyur | 100.0 | [3.8, 4.6, 29.3, 46.7, 27.8, 27.8, 51.4, **108.1**] | 108.1 | 8/8 | 56.7 | 1 |
| 438 | Ather - TRZ Sivakasi | 100.0 | [3.8, 33.1, 45.7, 52.2, 57.1, 57.1, **107.7**, **121.1**] | 121.1 | 8/8 | 13.4 | 2 |
| 360 | IOCL - GSM *(now sub-100, see caveat above)* | 100.0 | [46.3, 56.9, 78.1, 76.7, 67.3, 91.1, 95.2, 95.8] | 95.8 | 8/8 | 0.6 | 0 |

**Shape, stop by stop:**
- **Station 11, 320, 438**: not a single outlier — **two** neighbors independently exceed 100%, sitting close together (7–13pp apart). A top-N cutoff has to exclude *both* to help.
- **Station 85, 87**: one genuine outlier, but the ramp into it is gradual (each rank is 5–20pp above the last) rather than a flat baseline with one spike — a percentile-style cutoff would still get pulled upward by the whole upper half of the list, not just the max.
- **Station 319, 249**: the classic sharp-cliff case — a lone spike 50+ percentage points above everything else in the list (146.1 vs. next-worst 94.1; 108.1 vs. next-worst 51.4). This is the shape a top-N or percentile cutoff handles best, because trimming or de-weighting one point does most of the work.
- **Station 360**: no longer infeasible in this fresh fetch, and even its own list is tightly bunched at the top (0.6pp gap) — a genuinely marginal case either way.

Confirms the closed diagnostic's headline finding at the individual-stop level: this is **not** uniformly "one straggler causes the problem." Three distinct shapes (double-spike, gradual ramp, sharp single spike) coexist across the 7 confirmed stops, which is exactly why a single fixed rule (top-N *or* percentile) can't be expected to behave identically at all of them — confirmed quantitatively in Part 2.

### 1.2 — Sample of the other impacted-but-not-hard-infeasible stops

Of the 16 stops with a `Δ>0` under the original top-8 rule, 4 overlap with the hard-infeasibility set above (#7/#13/#16/#17 in the closed diagnostic's numbering); the remaining 12 are the "impacted but survivable" set. `n_neighbors_above_current` = how many of the up-to-8 neighbors, taken alone, would already require *some* increase over the current target — a spread measure independent of magnitude.

| Station | Name | current % | Δ (top-8 rule) | targets by distance-rank | worst-rank | n neighbors above current |
|---|---|---:|---:|---|:---:|:---:|
| 23 | Guindy - Olympia Tech Park | 21.6 | 2.8 | [15.3, 16.4, 17.9, 23.9, 24.4, 22.1, 19.4, 21.7] | 5/8 | 4/8 |
| 19 | Chennai Airport - Domestic | 22.4 | 28.7 | [3.8, 20.2, 37.3, 37.8, 31.8, 38.6, 27.9, 51.1] | 8/8 | 6/8 |
| 425 | Hotel Aishwarya Bhavan | 28.9 | 38.1 | [3.8, 32.1, 34.6, 46.9, 44.4, 14.9, 19.1, 67.0] | 8/8 | 5/8 |
| 426 | Zeon Charging - Nalathur | 33.6 | 35.2 | [31.5, 34.0, 43.5, 12.1, 16.7, 68.8, 68.8, 68.8] | 6/8 (tie 6-7-8) | 5/8 |
| 430 | Vikravandi - Arutperun Jothi | 32.3 | 6.8 | [19.6, 14.2, 14.0, 14.0, 21.6, 21.6, 22.4, 39.1] | 8/8 | 1/8 |
| 88 | IOCL - K G P Amaravathi | 23.5 | 40.5 | [3.8, 4.5, 38.3, 30.5, 30.5, 42.7, 47.7, 64.0] | 8/8 | 6/8 |
| 358 | Trichy - Hotel Balaji Ariyas | 30.7 | 9.3 | [17.7, 24.4, 24.9, 24.9, 40.0, 36.2, 32.2, 31.6] | 5/8 | 4/8 |
| 354 | Charge Zone - Tiruchirappalli | 32.9 | 44.5 | [8.1, 8.6, 8.6, 18.2, 35.3, 41.9, 41.9, 77.4] | 8/8 | 4/8 |
| 195 | Melur - Zeon X A.N Energy | 35.4 | 4.1 | [4.8, 25.3, 28.4, 36.7, 33.0, 33.1, 35.1, 39.5] | 8/8 | 2/8 |
| 373 | Ather-Eagle Traders (Vallioor) | 52.9 | 0.3 | [33.6, 34.3, 46.0, 47.8, 53.2, 53.2, 53.2, 53.2] | 5/8 (tie 5-8) | 4/8 |
| 147 | Nagercoil, Thovalai - Carnival | 20.0 | 22.1 | [13.8, 16.9, 25.2, 42.1, 42.1, 42.1, 42.1, 35.7] | 4/8 (tie 4-7) | 6/8 |
| 146 | Ather-Nagercoil | 20.0 | 3.2 | [5.5, 23.2, 23.2, 23.2, 23.2, 18.2] | 2/6 (tie 2-5) | 4/6 |

**Pattern:** several of these (station 426, 373, 147) have their "worst" energy tied across 2–4 neighbors — genuinely broad, not a single outlier at all. This reinforces the closed diagnostic's "not concentrated in rank-8 stragglers" conclusion — it's true across the impacted set generally, not just the 8 hard-infeasibility cases.

---

## Part 2 — Cutoff option comparison (recomputed from the full distribution, no new calls beyond the approved re-run)

Aggregate counts exclude stops #15 (station 91) and #21 (station 253) — parked/not re-diagnosed per scope, both `Δ=0` under every option since their current targets already exceed any neighbor-derived requirement. Denominator = 29 stops.

| Option | Unaffected | Increased | Avg increase (of those) | Max increase | Pushed to 100% | **Hard-infeasible (>100% even before cap)** |
|---|---:|---:|---:|---:|---:|---:|
| **Top-8 (current proposal, baseline)** | 13 | 16 | +21.9pp | +60.9pp | 10 | **7** *(was 8 in closed diagnostic; see traffic-variance caveat)* |
| **Top-3** | 23 | 6 | +9.0pp | +14.9pp | 6 *(all pre-existing — none newly pushed)* | **0** |
| **Top-5** | 15 | 14 | +12.1pp | +40.0pp | 8 *(6 pre-existing + 2 new: stations 11, 320)* | **2** *(stations 11 @ 117.4%, 320 @ 120.4%)* |
| **75th percentile** | 15 | 14 | +13.9pp | +35.2pp | 7 *(6 pre-existing + 1 new: station 320)* | **1** *(station 320 @ 104.3%)* |

### How each of the 8 hard-infeasibility stops fares under each option

| Station | current % | Top-8 | Top-3 | Top-5 | 75th %ile |
|---|---:|---:|---:|---:|---:|
| 11 – Mamandur | 60.0 | 117.4 | 30.7 | **117.4** | 85.5 |
| 85 – Shivasakthi Singaram | 39.1 | 101.3 | 34.2 | 55.9 | 68.4 |
| 87 – Cuddalore Argunt | 100.0 | 106.9 | 37.6 | 66.9 | 77.6 |
| 320 – Quality Inn Viha | 86.3 | 133.1 | 14.8 | **120.4** | **104.3** |
| 319 – Kumbakonam Cholaa | 99.3 | 146.1 | 82.6 | 91.3 | 92.0 |
| 249 – Kaveri Garage | 100.0 | 108.1 | 29.3 | 46.7 | 47.9 |
| 438 – Ather TRZ Sivakasi | 100.0 | 121.1 | 45.7 | 57.1 | 69.8 |
| 360 – IOCL GSM *(already sub-100)* | 100.0 | 95.8 | 78.1 | 78.1 | 92.1 |

**Bolded = still exceeds 100%.**

- **Top-3 eliminates the offending neighbor at all 8 stops, no exceptions** — every value drops well clear of 100% (worst residual is 82.6% at station 319). This matches the closed diagnostic's Part-3 prediction (all 8 had worst-rank ≥5) but now *confirmed* rather than inferred, because station 11 and station 320 each had a **second** bad neighbor at rank 4–5 that a naive "worst-rank≥5 ⇒ top-3 is safe" argument would have missed — it happens those secondary neighbors are also outside the top-3 window, but that was not guaranteed a priori and needed the full distribution to check.
- **Top-5 does not fully resolve it**: station 11's worst neighbor sits at rank 5 (right at the top-5/top-8 boundary) so it's untouched — 117.4% unchanged. Station 320 still fails too, because its *second* spike (rank 5, 120.4%) is inside the top-5 window even though its single worst value (rank 7, 133.1%) isn't.
- **75th percentile leaves one stop over the line**: station 320 at 104.3% — barely over, but still a genuine "100% charge isn't enough" case. Every other station drops under 100% because the percentile blunts the effect of the single/double spikes enough, except at station 320 where the upper half of the list itself is expensive (66.7, 85.6, 98.9, 120.4, 133.1 — five of eight values above 65%), so even the 75th-percentile point lands high.

---

## Part 3 — Assessment per option (not a decision — for you to make)

**Top-3 — looks viable.**
- Eliminates all 8 hard-infeasibility cases with margin (worst residual 82.6%, nowhere close to 100%).
- Only 6/29 stops see any increase at all, average +9.0pp, max +14.9pp — a modest, bounded impact, nothing close to the top-8 rule's 52%-of-stops/60.9pp-max profile.
- No new stops get pushed to the 100% ceiling; the 6 that end up there were already there under Option C alone.
- **Redundancy-coverage tradeoff**: top-3 by definition only guarantees a target that survives reaching one of the *nearest* three real neighbors. Whether that's "a real backup option" or "overlapping with the primary path" depends on how close those 3 are to the path's own next-stop choice — this diagnostic didn't cross-reference top-3 membership against which neighbor the optimizer's Dijkstra pass actually picked as the next stop (that's graph-topology territory, out of scope here per the task's own boundary). Flagging as an open question, not resolving it.

**Top-5 — still risky.**
- Leaves 2 of the 8 hard-infeasibility stops unresolved (station 11 at 117.4%, station 320 at 120.4%) — both because a second bad neighbor happens to sit at rank 5, exactly inside the window. This isn't a tuning artifact specific to today's fetch: station 11's rank-4 *and* rank-5 neighbors are both bad (110.2%, 117.4%), so nudging the window from 5 to 6 would still catch only one of them.
- Broader impact than top-3 (14/29 stops increased, avg +12.1pp) without fully solving the problem it's meant to solve.

**75th percentile — closer than top-5, but not clean.**
- Only 1 stop remains hard-infeasible (station 320 at 104.3%) — much closer to resolved than top-5, but "104.3%" is still a genuine no-charge-percentage-solves-this case, not a rounding issue.
- Needs a threshold-tuning step if pursued (85th? 90th percentile?) to see if it clears station 320 too — not evaluated here, out of the three options the task asked to compare.
- Softer overall profile than top-8 (avg +13.9pp vs +21.9pp) but not as clean as top-3's near-total resolution.

**If none of these had worked for a given stop, that would mean something more fundamental — checked explicitly:** for all 8 original hard-infeasibility stops, even **top-1 (nearest neighbor only)** was checked implicitly via the top-3 numbers above (rank-1 values: 30.7, 34.2, 22.7, 3.8, 56.6, 3.8, 3.8, 46.3 — all comfortably low). So **every one of these 8 stops does have at least one genuinely close, cheap neighbor** — the hard-infeasibility isn't because "this stop has no nearby backup at all," it's specifically that the top-8 rule's inclusion of far/expensive neighbors is what creates the failure. That's a cutoff-tuning problem, not a "this stop has zero redundancy options" problem — an important distinction the task asked to check for.

---

## Summary

Re-measuring the full 8-neighbor energy distribution (not just the worst case) at every stop confirms the closed diagnostic's warning was directionally right — real energy variance among a stop's real neighbors is broad-based, not a few rank-8 outliers — and adds a concrete answer on cutoffs: **top-3 is the only one of the three options tested that fully eliminates all 8 originally-flagged hard-infeasibility cases**, with a modest and bounded side-effect (6 of 29 stops see a small increase, none newly pushed to the 100% ceiling). Top-5 and 75th-percentile both leave real hard-infeasible residue (2 stops and 1 stop respectively, one of them — station 320, Quality Inn Viha — resistant to both because it has *two* independently expensive neighbors at different ranks, not one spike a milder cutoff can trim around). None of the 8 stops lack a cheap nearby neighbor entirely — every one has a low-cost rank-1 option — so this reads as a cutoff-window problem to solve, not evidence that any of these stops has no real redundancy path at all. One live-data caveat carries through all of this: these are traffic-sensitive point-in-time energy estimates (station 360 flipped from hard-infeasible to comfortably sub-100% between two live fetches a day apart), so exact percentages should be read as directionally reliable rather than fixed constants — the *shape* of the finding (top-3 clears the bar, top-5/percentile don't fully) is the more load-bearing result here.
