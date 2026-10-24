# RouteVolt Project Context

## Purpose

RouteVolt plans EV road trips by combining route geometry, charging-station
availability, vehicle/load assumptions, and a trained energy model. The current
prototype is tuned around Tamil Nadu station data and live third-party routing
services.

## Runtime Architecture

The backend is a FastAPI app in `backend/app/main.py`. It exposes route APIs
from `backend/app/api/routes.py` and station/queue APIs from
`backend/app/api/stations.py`. On startup it seeds the SQLite database from the
consolidated station CSV when the `stations` table is empty.

The frontend is a Vite React app under `frontend/src`. `App.jsx` owns the
planner state, calls the API through `api.js`, renders the form/sidebar/queue
views, and passes route results to map, chart, station, and summary components.

The model artifact is `models/energy_model.joblib`, produced by
`dataset_2/train_model.py` from synthetic trip-energy data. Backend inference
loads it once in `route_optimizer.py`.

## Main Route Flow

1. `POST /route/optimize` receives a `RouteRequest` with origin, destination,
   battery percentage, vehicle profile, load state, and optional exact payload.
2. `routing_service.geocode` resolves origin and destination with Ola Maps.
   User no-match errors become HTTP 422; missing key/provider failure becomes
   HTTP 503.
3. `routing_service.get_route_alternatives` gets OSRM route candidates.
4. `graph_planner.plan_journey_via_graph` fetches stations, filters them by
   corridor detour and connector compatibility, builds a graph, and runs
   Dijkstra to choose a feasible stop sequence.
5. `battery_simulator.plan_full_journey` simulates the chosen sequence leg by
   leg with live or fallback traffic, elevation, the trained energy model, and
   charge-target rules.
6. `charger_service.estimate_wait_time` adds queue estimates for selected
   stops and alternatives.
7. The response is validated by `RouteOptimizeResponse` in
   `backend/app/models/schemas.py` and rendered by the frontend.

## Key Assumptions

- Vehicle profiles are coarse classes: `small_ev`, `mid_ev`, and `large_ev`.
- Connector compatibility is currently assumed in
  `route_optimizer.VEHICLE_CONNECTOR_COMPATIBILITY`: all profiles support
  `CCS2`; `small_ev` also supports `AC_TYPE2`.
- Unknown or unparseable connectors are treated as incompatible. This is
  intentional because guessing a plug type can route a driver to a station
  they cannot use.
- Normal charging targets stay in a 20-80% band. The planner can exceed 80%
  only when the next leg genuinely needs it.
- Occupancy and wait time are heuristic plus crowdsourced/demo-reservation
  overlays, not measured real-time charger telemetry.
- Public OSRM and public Open-Elevation are development defaults. Production
  should use self-hosted or properly quota-managed providers.

## Demo Mode

`DEMO_MODE=true` enables saved demo fixtures for specific manifest entries in
`backend/app/fixtures/demo_routes_manifest.json`. Matching currently uses
origin, destination, `vehicle_profile`, and `load_state`; requests with exact
`payload_kg` run live. Battery percentage is intentionally not part of fixture
matching, so served fixtures include:

- `precomputed: true`
- `precomputed_battery_percentage`

The frontend shows a saved-plan notice when `precomputed` is true.

## Data Sources

- `dataset/tamil_nadu_ev_charging_stations_CONSOLIDATED.csv` is the station
  source of truth for seeding SQLite.
- `dataset_2/trip_energy_dataset.csv` is the full synthetic training dataset.
- `dataset_2/trip_energy_train.csv` and `dataset_2/trip_energy_test.csv` are
  split training/evaluation inputs.
- `models/energy_model.joblib` is the trained inference artifact used at
  runtime.

## Verification Mindset

The project has a mix of pytest tests and standalone diagnostic scripts.
Network-heavy route diagnostics are preserved as dated artifacts under
`tests/`; offline regressions should be preferred when possible. For the first
five audit fixes, run:

```bash
uv run python tests/test_audit_fixes_1_to_5.py
```

