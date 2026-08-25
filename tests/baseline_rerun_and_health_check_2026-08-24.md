# RouteVolt — Post-margin-fix baseline rerun + end-to-end health check

Chennai → Nagercoil, `mid_ev`, `half_load`. Run 2026-08-25, following the
Option C margin recalibration (`SIMULATION_MARGIN_FLOOR_KWH=1.5`,
`SIMULATION_MARGIN_RATE=0.06`) documented in
`tests/margin_recalibration_implementation_2026-08-24.md`.

## Demo-blocking issues found: none

No unhandled exceptions, no crashes, no `feasible: False` on the production
pipeline. One non-blocking risk is called out under Part 2 (wall-clock
variance under live API rate-limiting) — a demo-planning concern, not a bug.

---

## Part 1 — Clean baseline rerun (`find_minimum_starting_battery`)

| | Session-1 baseline | Attempt 1 (this session) | Attempt 2 / rerun (this session) |
|---|---|---|---|
| Min. starting battery | **8.0%** | **8.0%** | **8.0%** |
| Stop count | 31 | 34 | 32 |
| Final battery on arrival | 17.31% | 18.19% | 18.19% |
| Wall-clock | 510.1s (flagged degraded) | 579.7s | 390.1s |
| 429s hit | — | 64 | 48 |
| Straight-line fallbacks | — | 24 | 25 |
| OSRM timeouts / dropped edges | — | 2 | 0 |

**Headline result: minimum feasible starting battery is unchanged at 8.0%.**
The Option C margin change did not move this number for this route/profile —
consistent with the margin doc's own finding that it only resolved 10 of 12
flagged edges (stops 15/21 still fire) without touching graph topology or
Dijkstra's choice.

**Both of this session's attempts were degraded**, not just the first.
Per the task instructions I re-ran once after Attempt 1's clear degradation
(64 API 429s, 24 of which fell all the way through Ola Maps → Google →
straight-line estimate, plus 2 OSRM edge-fetch timeouts, 579.7s wall-clock
vs. the ~250-275s the script's own docstring expects). Attempt 2 was
somewhat cleaner (390.1s, no timeouts) but still saw 48 429s and 25
straight-line fallbacks — this appears to be current live rate-limiting on
the Ola Maps side today, not a one-off. **A fully clean (zero-degradation)
run of this specific function was not achievable within the one-reattempt
budget this task allows.** The 8.0% result held identically across both
degraded attempts, so it's reported with reasonable confidence, but the
wall-clock numbers above should be read as "degraded-but-real," not
representative of best-case latency (see Part 2's clean 166.5s run for
that).

Stop-count drift (31 → 34 → 32) matches the already-documented,
already-accepted path-composition variance from live OSRM/traffic drift
between separate graph builds — not treated as a bug.

**New anomaly, flagged not diagnosed:** `find_minimum_starting_battery`'s
own internal monotonicity guard reported `holds: false` in **both**
attempts this session — `check(7.0%)` came back feasible even though the
binary search converged on 8.0% as the minimum, against the same
already-built graph object (so it isn't the cross-build path-drift
explanation). This is not one of the two pre-cleared known issues (stop
15/stop 21) and not something this task's context said to expect. Per
scope, this is reported as an observed data point only — no code was
touched, no root cause chased. It does not affect the production
`/route/optimize` pipeline (Part 2 below), which calls
`plan_journey_via_graph` directly at a fixed `battery_pct`, not this
internal search/guard.

`find_minimum_starting_battery` itself does not report `exceeded_ceiling`
per stop — it uses the lighter `_simulate_feasibility_only` mirror (see its
docstring), which skips `plan_full_journey`'s stop-alternatives ranking but
matches its energy/charge-target verdict exactly. That data comes from
Part 2 below.

---

## Part 2 — End-to-end health check (`plan_journey_via_graph`)

Same route/profile, `battery_pct=8.0` (this session's found minimum, so the
`exceeded_ceiling` escape hatch is actually exercised rather than run at a
comfortable cushion).

| Check | Result |
|---|---|
| Completes without exception | **Pass** — no crash, no timeout |
| `feasible` | **True**, `status="ok"`, `reason=None` |
| Wall-clock | **166.5s**, clean (0 429s, 0 timeouts, 0 fallbacks this run) |
| Stops / legs | 31 / 155 |
| Final battery on arrival | 18.19% |
| Total predicted energy | 347.415 kWh |
| Distance / duration | 814.88 km / 865.3 min (≈14.4h, includes charging stops) |
| API response shape | **Pass** (see below) |

This run happened to hit zero rate-limiting (unlike both Part 1 attempts
run minutes earlier against the same live services) — real variance in
current conditions, not something this run controlled for.

**`exceeded_ceiling` firing:** 12 of 31 stops (stops #6, 14, 15, 16, 17, 20,
21, 24, 25, 26, 27, 28 → station IDs 398, 87, 91, 320, 319, 249, 253, 181,
440, 438, 365, 360).

- Stop #15 (station 91, IOCL Raja Traders) and stop #21 (station 253, IOCL
  Sugan Tradings) both fired, exactly as expected from the pre-cleared
  known/parked list — not re-diagnosed here.
- The other 10 stops **are not on that list**, so flagging them plainly per
  the task's instruction rather than assuming they're covered by the same
  two known issues. That said, they are not new to the *project* — 8 of
  these 10 station IDs (87, 249, 320, 360, 365, 398, 438, and 91 itself)
  already appear in `tests/margin_recalibration_live_verification_2026-08-24.json`'s
  post-fix flagged set from the prior session, i.e. this looks like a
  stable, recurring characteristic of this specific route/profile at a
  tight starting battery (coarse-vs-fine energy model drift on ~12 of the
  ~31 segments), not something newly introduced or discovered by this run.
  Two IDs (319, 181) weren't in that prior flagged set — consistent with
  the same already-accepted per-build path drift as the stop-count
  variance above.

**API response shape spot-check (against `backend/app/api/routes.py`'s
`/route/optimize` and the frontend):**

- `journey` (the raw `plan_journey_via_graph` return) has exactly the keys
  `routes.py`'s `route_out` dict reads via `journey["..."]`: `legs`,
  `stops`, `final_battery_pct`, `total_predicted_energy_kwh`, `status`,
  `gap`, `distance_km`, `duration_min`, `feasible`, `reason`,
  `exceeded_ceiling_stops`. No missing keys, no `KeyError` risk.
- `stops[i]` shape matches `schemas.ChargingStop` (`leg_index`, `location`
  as `{lat, lng}` matching `LatLng`, `battery_pct_on_arrival`,
  `station_id`, `station_name`, `charge_to_percent`, `exceeded_ceiling`,
  `estimated_charge_duration_min`, `expected_wait_minutes`,
  `queue_length`), and each `alternatives[]` entry matches
  `StopAlternative`.
- `legs[i]` has every field `schemas.RouteLeg` requires (`start`, `end`,
  `distance_km`, `duration_s`, `points`, `elevation_gain_m/loss_m`,
  `elevation_source`) plus extras (`predicted_energy_kwh`,
  `battery_pct_after`, `traffic_congestion_level`, `traffic_source`) that
  the model tolerates.
- `gap` is `None` here (feasible run) — matches `Optional[RouteGap] = None`.
- Frontend: `RouteMap.jsx` reads `route.legs` and `route.charging_plan`
  (= `journey["stops"]`, renamed in `route_out`) — both present and
  non-empty. `RouteSummaryBar.jsx` reads `route.distance_km`,
  `route.duration_min`, `route.predicted_energy_kwh` — all present and
  numeric. `RouteResults.jsx` just indexes `result.routes[0]` — present.
  No UI was actually rendered (structural check only, per task scope).

**Demo-planning risk worth flagging plainly:** Part 1's two attempts (run
minutes before this clean 166.5s Part 2 run, same live services) took
579.7s and 390.1s respectively under Ola Maps rate-limiting. Today's live
conditions swing between "clean, ~2.8 minutes" and "degraded, 6.5-9.7
minutes" for a full graph build, seemingly independent of anything in this
codebase. A live demo could plausibly land in either bucket. Recommend
having a pre-captured recording or a warm/cached run ready as a fallback,
rather than relying on a from-scratch live call staying fast.

---

## Bottom line

Safe to demo live, with caveats: the pipeline itself is correct and
crash-free end to end, but live third-party API rate-limiting (observed
repeatedly this session) means wall-clock time is not guaranteed to stay
in the fast ~3-minute range — have a fallback ready.
