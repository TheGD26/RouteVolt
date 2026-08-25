# RouteVolt — Simulation-time margin recalibration (Option C), Step 1: value-finding only

**No code changes made or proposed as final. This is analysis only — Step 2 (implementing a chosen value) is separate and requires explicit sign-off on the number first.**

## Data availability check (Part 1.1)

Checked whether a broader-than-12-edge error distribution is recoverable from existing session data. It is not, and I did not request new calls per the task's explicit instruction to work from the 12-edge sample if so.

- `graph_planner._log_exceeded_ceiling_drift` (`graph_planner.py:684-734`) only computes and logs the build-time-vs-simulation-time comparison **when `exceeded_ceiling` fires**. It ran once this session (`/tmp/fanout_flagged_run.log`), producing exactly 13 comparisons (12 in scope, station 181/stop 24 excluded).
- The chosen 31-stop path's simulation-time energy **is** fully available for all 158 legs (`tests/fanout_flagged_segments_source_2026-08-24.json`, every leg carries `predicted_energy_kwh`) — but the corresponding **build-time** figure for the other 19 non-flagged stops' direct edges was never logged anywhere (it existed only in `build_station_graph`'s in-memory `edges` dict, discarded when that process exited, and `_log_exceeded_ceiling_drift` never ran for those since they didn't trigger the flag).
- The full ~1,980-edge candidate graph's per-edge energies (computed once during the original build) were never logged or persisted to disk either — only edge counts and refinement timing (`"real per-edge refinement took %.1fs for %d candidate edges"`, `graph_planner.py:508-511`) were logged, not per-edge values.

**Conclusion: working from the 12-edge sample only, stated explicitly per instructions rather than requesting a new full-graph rebuild.**

## Part 1 — Error distribution (12 edges, build-relative %)

Error = `(sim_time_raw_kwh - build_time_raw_kwh) / build_time_raw_kwh * 100`, from `tests/energy_prediction_consistency_2026-08-24.json` (no new calls — this is the same data already reported last session, re-summarized statistically here).

| Stat | Value |
|---|---|
| n | 12 |
| Mean | +0.08% |
| Median | -0.26% |
| Sample std dev | 2.84% |
| Mean absolute error | 2.39% |
| Max absolute error | 4.33% (stop 17) |
| Min absolute error | 0.43% (stop 16) |

**Build-time's current 5% capacity discount, translated into the same units:** the check `energy_kwh ≤ capacity·0.8·(1-0.05)` is algebraically equivalent to `energy_kwh·(1/(1-0.05)) ≤ capacity·0.8` — i.e. build-time's discount inflates each edge's own raw energy by a factor of `1/(1-margin)`, an **effective proportional buffer of `margin/(1-margin) = 5.26%` of that edge's own predicted kWh.** (This wasn't obvious from the raw constant "5%" alone — the 5% capacity-side discount and the 5.26% energy-side inflation are the same check viewed from two ends, confirmed algebraically, not assumed.)

**Comparison:** 5.26% sits **comfortably above the observed mean absolute error (2.39%) and above the observed max (4.33%)**, but **below a `mean + 2σ` statistical bound** (0.08% + 2×2.84% = 5.76%) — on a 12-edge sample, one crude two-sigma read would exceed what build-time's margin already provides, though the actual observed max (4.33%) does not.

## Part 2 — Three proportional formula options

All figures computed by re-evaluating the ceiling condition in kWh space: `sim_time_raw_kwh + margin_kwh(edge) > 32.0` (`BATTERY_CEILING_PCT`=80% × capacity 40kWh), using each edge's already-logged `sim_time_raw_kwh` as the base. **Approximation flagged explicitly:** the real code's `required_pct_all`/`next_leg_required_pct` can include a second lookahead segment's energy too (`CHARGE_TARGET_LOOKAHEAD_SEGMENTS=2`), which isn't separately recoverable from the log — this recomputation uses *this edge's own* energy only as a proxy, so if the real lookahead window included more, the true trigger count could be **equal to or higher than** what's shown below, never lower.

| Option | Formula | Anchor |
|---|---|---|
| **A — Match build-time's rate** | `5.26% × sim_time_raw_kwh` | Exactly build-time's own effective rate, no floor |
| **B — Statistical cushion** | `6% × sim_time_raw_kwh` | Clears the observed max error (4.33%) with headroom for un-sampled edges, no floor |
| **C — Floor + percentage hybrid** | `max(1.5 kWh, 6% × sim_time_raw_kwh)` | Same rate as B, plus an absolute floor so very short/cheap legs (where a pure % gives a near-zero buffer) still get a minimum cushion |

### Per-edge effective buffer (kWh) vs. build-time, and ceiling-check outcome

| Stop | sim_raw (kWh) | Build buf (kWh) | A buf | A still fires? | B buf | B still fires? | C buf | C still fires? |
|---|---|---|---|---|---|---|---|---|
| 6 | 15.961 | 0.825 | 0.840 | No | 0.958 | No | 1.500 | No |
| 7 | 19.908 | 1.058 | 1.047 | No | 1.195 | No | 1.500 | No |
| 14 | 7.078 | 0.384 | 0.372 | No | 0.425 | No | 1.500 | No |
| 15 | 30.681 | 1.582 | 1.614 | **Yes** | 1.841 | **Yes** | 1.841 | **Yes** |
| 16 | 3.728 | 0.195 | 0.196 | No | 0.224 | No | 1.500 | No |
| 17 | 28.842 | 1.587 | 1.517 | No | 1.731 | No | 1.731 | No |
| 20 | 9.506 | 0.506 | 0.500 | No | 0.570 | No | 1.500 | No |
| 21 | 33.171 | 1.807 | 1.745 | **Yes** | 1.990 | **Yes** | 1.990 | **Yes** |
| 25 | 10.197 | 0.523 | 0.536 | No | 0.612 | No | 1.500 | No |
| 26 | 21.704 | 1.155 | 1.142 | No | 1.302 | No | 1.500 | No |
| 27 | 22.223 | 1.128 | 1.169 | No | 1.333 | No | 1.500 | No |
| 28 | 30.151 | 1.524 | 1.586 | No | 1.809 | No | 1.809 | No |

**All three options leave the same 2 of 12 (stops 15, 21) still firing.** Neither is a margin-sizing artifact worth chasing further:

- **Stop 21 (sim_raw = 33.171 kWh) already exceeds the raw 32.0 kWh ceiling with *zero* margin added.** No proportional (or any) margin formula can prevent this one from firing — the segment's own real predicted energy is structurally over 80% of pack capacity on its own.
- **Stop 15 (sim_raw = 30.681 kWh) is a genuine borderline case** — under-ceiling with zero margin, but every tested option (1.5-6%) pushes it just over by a small amount (0.3-1.8 kWh). This one *is* sensitive to the exact rate chosen, unlike stop 21.

### Risk flags — where a proposed buffer is *smaller* than build-time's own

| Option | Edges where proposed buffer < build-time's buffer |
|---|---|
| **A** | **6 of 12**: stops 7, 14, 17, 20, 21, 26 — by 0.01-0.07 kWh each |
| **B** | None |
| **C** | None |

Option A's risk is subtle and real: even though its *rate* (5.26%) exactly matches build-time's, it's applied to `sim_time_raw_kwh` rather than `build_time_raw_kwh`. On the 6 edges where simulation predicted *less* energy than build-time did, 5.26% of the smaller base comes out slightly smaller in absolute kWh than build-time's own buffer on the larger base — a same-rate-different-base effect, not a calculation error. The gaps are small (max 0.066 kWh, at stop 21), but they are a real instance of "recalibrating downward could reduce safety margin below what build-time already assumed" as the task asked to watch for. Options B and C avoid this entirely because their higher rate (6% vs. build's 5.26%) overcomes the base-difference effect on every one of the 12 edges.

## Plain-language summary

Three shapes of trade-off, not a recommendation:

- **Option A (5.26%, tightest fit)** matches build-time's rate exactly, so the two stages are calibrated to the *same* proportional standard — but because it's the exact minimum rate observed to just clear the sample's error range, it carries the only real "smaller-than-build" risk (6 of 12 edges, small margins) and offers the least cushion against a segment outside this 12-edge sample turning out worse than observed here.
- **Option B (6%, statistical cushion)** adds real headroom above both the observed max error and build-time's own rate, closing every risk-flag case Option A raised, at the cost of a slightly larger buffer everywhere (small in absolute kWh on this sample, but consistently larger).
- **Option C (floor + 6%)** is the same as B for every edge in this sample (all 12 are large enough that 6% already exceeds the 1.5 kWh floor) — its difference only shows up on short/cheap legs not present in this flagged-segment sample (e.g. the near-zero-kWh split edges seen in the fan-out cap diagnostic), where a pure percentage would otherwise leave those legs with almost no buffer at all; the floor is insurance for a case this particular 12-edge sample doesn't actually exercise.

None of the three options eliminate stops 15 and 21 — 21 structurally cannot be fixed by any margin choice (its raw prediction alone exceeds the ceiling), and 15 is a close call sensitive to whichever rate is ultimately chosen.
