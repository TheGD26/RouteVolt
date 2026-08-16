# RouteVolt

AI-powered EV route planning for Tamil Nadu: mass-aware energy prediction,
real routing + battery simulation, and charging-station intelligence.

## Setup

```bash
uv sync                      # installs deps from pyproject.toml / uv.lock
cp .env.example .env         # fill in whichever keys you have (see below)
uv run python -m backend.app.scripts.seed_stations   # one-time DB seed (also runs on app startup)
uv run python -m uvicorn backend.app.main:app --reload
```

The app also works with plain `pip install -r requirements.txt` if you're
not using `uv`.

## Environment variables

Loaded via `python-dotenv` in `backend/app/config.py` (same pattern as
`dataset/download_chargers.py`). All are optional at import time -- a
service that needs a missing key fails loudly when it's actually called,
not when the app boots.

| Variable | Used by | Notes |
|---|---|---|
| `OCM_API_KEY` | `dataset/download_chargers.py` | OpenChargeMap key, for regenerating the station CSV |
| `OSRM_BASE_URL` | `routing_service.py` | Defaults to the public demo server (`https://router.project-osrm.org`); point at a self-hosted instance for production |
| `OLA_MAPS_API_KEY` | `traffic_service.py` | Primary traffic-aware ETA + congestion provider (Ola Maps / Krutrim Distance Matrix) |
| `GOOGLE_MAPS_API_KEY` | `traffic_service.py` | Fallback traffic-aware ETA + congestion provider (Google Distance Matrix) |
| `ELEVATION_API_URL` | `elevation_service.py` | Defaults to the public Open-Elevation instance (`https://api.open-elevation.com/api/v1/lookup`); point at a self-hosted instance for production (see below) |

Nominatim (geocoding) needs no API key.

## Data flow

```
tamil_nadu_ev_charging_stations_CONSOLIDATED.csv  (source of truth)
        │  download_chargers.py regenerates this from OpenChargeMap
        ▼
backend/app/scripts/seed_stations.py  --loads CSV, skips if table populated--
        ▼
routevolt.db  `stations` table  (SQLite, via SQLAlchemy)
        ▼
charger_service.get_stations() / get_station() / get_blended_occupancy() / estimate_wait_time()
        ▼
route_optimizer.calculate_best_route(), battery_simulator, /stations routes
```

Re-running `download_chargers.py` only refreshes the CSV; re-run
`seed_stations.py` (or restart the app -- it seeds on startup) to pull those
changes into the DB. Seeding is a no-op once `stations` already has rows, so
it won't duplicate data on repeated boots.

## API

### `POST /route/optimize`

Geocodes `current_location`/`destination` (Nominatim), fetches up to 3 route
alternatives from OSRM, and runs `battery_simulator.plan_full_journey` over
each one -- a **multi-stop** planner, not a single pass: it chains as many
charging stops as the route actually needs while keeping the battery inside
a `BATTERY_FLOOR_PCT`-`BATTERY_CEILING_PCT` (20-80%) band (both named
constants in `battery_simulator.py`). Each route's response includes:

- `charging_plan`: every planned stop in order (empty list if none needed),
  each with arrival battery %, target charge-to-% (capped at 80% unless the
  very next leg genuinely needs more -- flagged via `exceeded_ceiling`),
  an approximate charge duration (linear kWh/power model, see
  `_estimate_charge_duration_min`), an `expected_wait_minutes`/`queue_length`
  estimate for the chosen station (queueing arithmetic on top of blended
  occupancy -- see [Occupancy + wait time](#occupancy--wait-time) below), and
  its own top-5 reachable alternative stations (detour distance/time from the
  main route, whether that detour is non-trivial, and each alternative's own
  `expected_wait_minutes`/`queue_length`).
- `status`: `"ok"` or `"unreachable_gap"` -- a genuine dead zone (no station
  reachable from some point, or a single leg too energy-intensive for the
  vehicle even at 100%) is returned explicitly, not raised or hidden.
- `legs`: every leg actually driven, index-aligned with each stop's
  `leg_index` -- slice `legs[leg_index:]` to build `remaining_route_legs`
  for `/route/reroute-stop` below.

Returns real traffic-aware `estimated_time` via `traffic_service.py` (Ola
Maps primary, Google Distance Matrix fallback) when either API key is
configured; otherwise falls back to OSRM's own route duration and marks
`traffic_aware: false`. The same live traffic data also feeds each leg's
`traffic_congestion_level` in the energy model -- see
[Traffic congestion](#traffic-congestion) below -- so the displayed ETA and
the predicted battery drain reflect the same real conditions, not two
disconnected numbers.

**Documented simplification:** a stop is simulated as if it happens exactly
on the main route -- the real station's detour is reported (`location` is
the route position; `alternatives[].detour_km`/`detour_time_min` carry the
actual station's offset) but not folded back into the leg-by-leg energy
simulation for legs after the stop. Same spirit as the traffic/weather TODO
stubs still in `route_optimizer._build_trip_features` (elevation itself is
no longer stubbed, see [Elevation](#elevation) below).

### `POST /route/reroute-stop`

A planned stop's station turned out to be unavailable (occupied, offline,
reported busy). Body: `{failed_station_id, current_stop_alternatives,
remaining_route_legs, battery_pct, vehicle_profile, load_state?, payload_kg?}`
-- `current_stop_alternatives` and `remaining_route_legs` are exactly what
`/route/optimize` returned for that stop (`charging_plan[i].alternatives`
and `legs[charging_plan[i].leg_index:]`).

Logs the failure as a crowdsourced `busy` occupancy report (source
`reroute_failure`), then walks the same alternatives list picking the next
station that's actually available (checked via `charger_service`: DB status
+ `get_blended_occupancy`'s blended available-ports count), and re-runs the
multi-stop chaining loop from there. Returns `{"status": "rerouted",
"new_station", "charge_to_percent", "exceeded_ceiling", "remaining_plan"}`,
or `{"status": "unreachable_gap", ...}` if every alternative at that stop is
also unavailable.

### Connector compatibility

`calculate_best_route` (`route_optimizer.py`) now filters candidates by
connector compatibility, not just energy reachability -- a station that's
well within battery range but has the wrong plug is never picked as `best`
and never appears in `battery_simulator`'s alternatives (both key off the
same combined `reachable = energy_reachable and connector_compatible` flag).

- `backend/app/utils/connector_normalizer.py` collapses the CSV's ~30 raw
  `connector_types` strings (casing/formatting variants, comma-separated
  multi-connector cells) into a fixed taxonomy: `CCS2`, `AC_TYPE2`,
  `CHADEMO`, `BHARAT_DC`, `TYPE1_16A` (legacy/basic AC), `UNKNOWN`. Ambiguous
  or unparseable strings ("Unknown", "DC fast charger", "Relux FC", missing)
  map to `UNKNOWN` -- deliberately never guessed into a real standard, since
  routing someone to a plug we can't identify is worse than saying so.
- `route_optimizer.VEHICLE_CONNECTOR_COMPATIBILITY` maps each vehicle
  profile to its compatible bucket(s) (`small_ev`: CCS2+AC_TYPE2; `mid_ev`
  /`large_ev`: CCS2 only). **This is an explicit assumption, not sourced
  data** -- nothing elsewhere ties a vehicle class to a real connector
  standard, so CCS2 (India's mandated DC fast-charging standard) is assumed
  for every profile as a reasonable default pending real per-model spec
  data. Override the dict directly once that data exists.
- Every candidate in `calculate_best_route`'s response carries
  `connector_buckets`, `connector_compatible`, and `energy_reachable`
  (in addition to the combined `reachable`) so a caller can tell "nearby but
  wrong plug" apart from "nearby but too far on energy" -- the response also
  reports `connector_incompatible_count` and `energy_unreachable_count`
  alongside `reachable_count`.

### Wait-time-aware station ranking

`route_optimizer.station_score(detour_km, power_kw, expected_wait_minutes)`
is the single scoring function both `calculate_best_route`'s own `best` pick
and `battery_simulator._rank_reachable_candidates`'s alternatives sort use
(previously two independent copies of the same detour-vs-power tradeoff) --
lower is better: shorter detour and higher charging power both help, and a
predicted queue wait at the station (`charger_service.estimate_wait_time`,
see below) is now an additional penalty term, weighted by
`WAIT_TIME_SCORE_WEIGHT` (0.3: each expected minute of wait is treated as
roughly as costly as 0.3 extra detour-km). This is a simple linear
scalarization, not derived from measured user time-value data -- tune the
constant once real behavior data exists. Wait time is only computed for
candidates already in the reachable set (not all ~450 stations), keeping the
extra DB lookups bounded to the stations actually being compared.

### `GET /stations/`

All operational stations from the DB.

### Occupancy + wait time

**`GET /stations/{station_id}/occupancy`** (`charger_service.get_blended_occupancy`):
hour-of-day / day-of-week / highway-vs-city-corridor heuristics
(`_heuristic_occupancy`), blended with any `occupancy_reports` from the last
`OCCUPANCY_REPORT_WINDOW_MINUTES` (45 min by default, configurable per call)
-- each report's blend weight decays exponentially with a
`REPORT_DECAY_HALF_LIFE_MINUTES` (15 min) half-life, and the overall blend
weight (how far the estimate moves from the heuristic toward reported
reality) scales with how much decayed report weight has accumulated, capped
at `MAX_REPORT_BLEND_WEIGHT` (0.85) so the heuristic always retains some
influence.

**`GET /stations/{station_id}/wait-estimate`** (`charger_service.estimate_wait_time`):
simple queueing arithmetic on top of `get_blended_occupancy`'s current
occupied-ports estimate -- if occupied ports are at or above `num_chargers`,
`queue_length = occupied - num_chargers + 1` and `expected_wait_minutes =
(queue_length / num_chargers) * AVERAGE_SESSION_MINUTES`; otherwise both are
zero. `AVERAGE_SESSION_MINUTES = 35`: based on DOE real-world paid DCFC
session data (avg 42 min full session incl. dwell time) and UK
rapid-charger studies (~28-29 min pure charging time); 35 min is an interim
default for paid public DCFC -- replace with a measured average, ideally per
`vehicle_profile`, once real RouteVolt session logs accumulate. This is
queueing-theory arithmetic, not a Poisson/gradient-boosted forecast -- there
isn't enough logged session history yet to train one.

**Confidence levels** are meant to evolve as real data accumulates, and both
endpoints report one so callers can tell how much to trust a given number:
`"heuristic"` (no recent reports at all -- pure time/corridor heuristic)
&rarr; `"blended"` (some recent reports, but not enough decayed weight to
dominate the heuristic) &rarr; `"crowdsourced"` (enough recent report weight
that the estimate is mostly real signal). A future `"ml"` tier is the
intended next step once enough crowdsourced + session history exists to
train the real occupancy/queue model this is explicitly standing in for --
not built yet, see `get_blended_occupancy`'s and `estimate_wait_time`'s
docstrings.

Verify the blend behavior directly: `uv run python -m
backend.app.scripts.verify_occupancy_blend` shows (a) a station with no
recent reports returning the heuristic unchanged, and (b) the same station
after 5 fresh "busy" reports returning a noticeably higher occupied estimate
with `confidence` flipping to `"crowdsourced"`.

### `POST /stations/{station_id}/report`

Body: `{"station_id": "<id>", "reported_status": "busy" | "free" | "unknown"}`.
Validates the station exists before inserting into `occupancy_reports`.

### `GET /stations/{station_id}/reports/recent?limit=5`

Most recent crowdsourced reports for a station, newest first.

## Architecture notes

- **Energy model**: `dataset_2/energy_model.joblib`, loaded once at import in
  `route_optimizer.py`. Mass-aware (curb weight + payload), trained on
  `dataset_2/trip_energy_dataset.csv`.
- **Live weather** feeding the energy model is still a TODO-stubbed default
  in `route_optimizer._build_trip_features`. **Elevation** and **traffic
  congestion** are no longer stubbed -- see below.

### Elevation

`elevation_gain_m`/`elevation_loss_m` (energy model inputs, see
`dataset_2/dataset_schema.md`'s target formula) come from real terrain data
via `backend/app/services/elevation_service.py`, not a flat-terrain 0.0
stub:

- **Source**: [Open-Elevation](https://www.open-elevation.com/) (`ELEVATION_API_URL`
  above), a free batch elevation API -- no key required. A single call takes
  a list of lat/lng points and returns one elevation per point, which fits
  OSRM's leg geometry naturally (`routing_service.get_route` requests
  `geometries=geojson`, so every leg carries its own sampled `points`, not
  just start/end).
- **Two callers, two sampling strategies**:
  - `battery_simulator._leg_elevation`: walks each real route leg's sampled
    points in order and sums positive/negative deltas separately
    (`elevation_service.compute_leg_elevation`) -- gain and loss don't net
    out within a leg, matching the training data's convention.
  - `route_optimizer.calculate_best_route`: has no real route to a candidate
    station, only a haversine detour distance, so it takes a two-point
    (origin -> candidate) approximation instead
    (`elevation_service.compute_point_to_point_elevation`), batched into one
    call across every candidate rather than one call per station.
- **DEM noise handling**: the public instance's backing DEM is a *surface*
  model (buildings/canopy included, not bare terrain) at ~30m resolution.
  Sampling OSRM's native point spacing (often 10-50m) directly produced
  spurious sawtooth gain/loss on genuinely flat roads (verified on a Chennai
  T. Nagar -> Airport route). Two mitigations in `elevation_service.py`:
  points are downsampled to `MAX_SAMPLE_POINTS_PER_LEG` before querying, and
  an `ELEVATION_NOISE_FLOOR_M` hysteresis filter ignores deltas too small to
  be real climb/descent. A self-hosted instance on a proper bare-earth DTM
  wouldn't need either, but they're cheap insurance regardless.
- **Fallback, per leg/candidate, not per route**: a 5s timeout wraps every
  Open-Elevation call; if it's unreachable (down, rate-limited, timeout),
  only the affected leg or candidate falls back to
  `elevation_gain_m=0.0`/`elevation_loss_m=0.0` -- the rest of the route
  still gets real values. Every leg (`RouteLeg.elevation_source`) and
  candidate carries `"open_elevation"` or `"stub_fallback"` so a caller can
  tell a genuinely flat leg apart from a silently-degraded one instead of
  the two looking identical.
- **Caching**: an in-memory dict keyed by rounded (lat, lon) in
  `elevation_service._elevation_cache`, so repeated legs/candidates within a
  session (e.g. `plan_charge_to_percent` re-summing `remaining_legs`, or
  scoring the same candidate stations across multiple route alternatives)
  don't re-query the same points. No persistence -- fine at this scale.
- **Production note**: the public `api.open-elevation.com` instance is rate
  limited and not meant for production traffic. Self-hosting Open-Elevation
  (it's open source, ships a Docker image with the SRTM dataset) and pointing
  `ELEVATION_API_URL` at it is recommended before any real deployment.

### Traffic congestion

`traffic_congestion_level` (energy model input) is now sourced from the same
Ola Maps / Google Distance Matrix call `estimated_time` already uses for
display, via `backend/app/services/traffic_service.py` -- not the flat 0.3
stub it used to be.

- **One call, two purposes**: `traffic_service.get_leg_traffic()` (single
  leg) / `get_leg_traffic_batch()` (many candidates against one shared point,
  used by `route_optimizer.calculate_best_route`) wrap the same Ola -> Google
  -> straight-line provider chain `get_traffic_eta` already used, and return
  both the traffic-aware `duration_seconds` (what ETA display wants) and
  `congestion_level` (what the energy model wants) from that one response --
  a leg's traffic is never queried twice for two different features.
- **Congestion formula**: `(duration_with_traffic - duration_typical) /
  duration_typical`, clamped to `[0, 1]` (no delay -> 0, double the typical
  time or worse -> 1, capped there). Google's Distance Matrix element
  carries both `duration` (no traffic) and `duration_in_traffic` in the same
  response, so its typical baseline is real. Ola's public Distance Matrix
  product only returns a single traffic-aware duration (a genuinely
  no-traffic baseline is a separate "Distance Matrix Basic" product) --
  querying that too would double Ola's API usage per leg just for a
  baseline, so callers instead supply their own: `battery_simulator` passes
  OSRM's own traffic-free `duration_s` for a real route leg;
  `calculate_best_route` (no real route to a candidate, only a haversine
  detour) estimates one from the road-type bucket-mean speed it already
  falls back to elsewhere.
- **Fallback, per leg/candidate, not per route**: reuses `get_traffic_eta`'s
  5s-timeout, never-raises-for-provider-failure behavior. A leg with no live
  traffic data at all (both providers down, or neither key configured) falls
  back to `traffic_congestion_level=0.3` -- the original stub value -- for
  just that leg. Every leg (`RouteLeg.traffic_source`) and candidate carries
  `"live"` or `"stub_fallback"` so a caller can tell light traffic apart from
  a silently-degraded guess.
- **Caching**: an in-memory dict keyed by rounded origin/destination in
  `traffic_service._leg_traffic_cache`, same spirit as
  `elevation_service._elevation_cache` -- a leg queried more than once in a
  session (e.g. `plan_charge_to_percent` re-summing `remaining_legs`) doesn't
  re-hit the network.
- **Batching**: `calculate_best_route` scores every candidate station's
  congestion in one Ola/Google matrix call (`get_leg_traffic_batch`), not
  one call per station -- same reasoning as its elevation batching.

- **Vehicle profiles / mass estimation**: unchanged from before this phase,
  see `route_optimizer.VEHICLE_PROFILES`.
- **Leg granularity** (`routing_service.py`): OSRM's raw per-maneuver steps
  range from a few meters (a turn) to 100+ km (a straight highway stretch
  with no turns) -- neither end is usable directly. Steps are merged up to
  `MIN_LEG_DISTANCE_KM` (1km, the energy model's training-data floor --
  feeding it shorter legs individually made it plateau/overestimate) and
  split back down at `MAX_LEG_DISTANCE_KM` (20km, so a multi-hundred-km
  highway stretch still gets fine enough resolution for
  `plan_full_journey` to place stops sensibly along it). Also: leg energy
  predictions use each leg's *real* distance/duration-derived speed, not
  the fixed road-type bucket mean `calculate_best_route` falls back to when
  it only has a haversine guess -- mixing a short real leg with a fixed
  85 km/h "highway" speed was another source of wildly out-of-distribution
  (and wildly overestimated) predictions.
