# File Inventory

This inventory is grouped by purpose. Generated caches, virtualenv internals,
and OS metadata are called out separately so they are not mistaken for primary
source files.

## Root Files

| Path | Purpose |
|---|---|
| `README.md` | Setup, environment variables, API behavior, demo mode, station/occupancy notes, and operational context. |
| `index.md` | Human entry point for future reference docs. |
| `pyproject.toml` | Python project metadata and backend/data-science dependencies for `uv`. |
| `uv.lock` | Locked Python dependency graph. |
| `requirements.txt` | pip-compatible backend/data dependency list. |
| `.env.example` | Example backend environment variables. |
| `.env` | Local secrets/config; do not commit real keys. |
| `.gitignore` | Git ignore rules. |
| `.python-version` | Local Python version selector. |
| `main.py` | Legacy/simple top-level Python entry file; the active API app is `backend/app/main.py`. |
| `package-lock.json` | Root npm lockfile; the active frontend package is under `frontend/`. |
| `routevolt.db` | Local SQLite database seeded from the station CSV. |
| `models/energy_model.joblib` | Trained energy model used by `route_optimizer.py`. |
| `model_final.zip` / `model_final/` | Packaged model/export artifact. |
| `charging_stations.csv`, `ev_station_dataset.csv`, `candidates_top250.json` | Older or diagnostic station/candidate data artifacts retained for reference. |
| `RouteVolt_StudAI_Pitch_2 [Auto-saved].pptx`, `RouteVolt_TNtourism_Pitch.pptx` | Presentation decks for RouteVolt pitches. |

## Backend

| Path | Purpose |
|---|---|
| `backend/app/main.py` | FastAPI app factory, CORS setup, startup station seed, and router registration. |
| `backend/app/config.py` | Central project paths and env-loaded settings for providers, demo mode, model path, and dataset path. |
| `backend/app/api/routes.py` | `/route/optimize` and reroute API handlers, including demo fixture serving and geocoding error mapping. |
| `backend/app/api/stations.py` | Station listing, occupancy reports, wait estimates, and queue/reservation endpoints. |
| `backend/app/models/schemas.py` | Pydantic request/response models for route, station, queue, reroute, and demo metadata payloads. |
| `backend/app/database/db.py` | SQLAlchemy engine, session factory, base class, and DB dependency. |
| `backend/app/database/models.py` | ORM models for stations and occupancy reports. |
| `backend/app/utils/connector_normalizer.py` | Normalizes raw connector strings into fixed buckets like `CCS2` and `AC_TYPE2`. |
| `backend/app/services/charger_service.py` | Station catalogue reads, occupancy heuristics, crowdsourced blending, wait estimates, and in-memory queue demo state. |
| `backend/app/services/routing_service.py` | Ola geocoding, OSRM routes/matrices, optional Google fallback, and normalized route leg output. |
| `backend/app/services/traffic_service.py` | Ola/Google traffic ETA and congestion lookup, provider cooldowns, batching, and per-leg traffic cache. |
| `backend/app/services/elevation_service.py` | Open-Elevation lookup, cache, downsampling, and elevation gain/loss computation. |
| `backend/app/services/route_optimizer.py` | Energy model inference, vehicle/load profiles, connector compatibility, station scoring, and older best-route logic used by simulator helpers. |
| `backend/app/services/battery_simulator.py` | Detailed battery simulation, charge target sizing, alternatives, reroute-from-stop logic, and queue-aware stop entries. |
| `backend/app/services/graph_planner.py` | Candidate filtering, station graph construction, Dijkstra path search, edge refinement, backup stations, and main graph-planned journey entry point. |

## Backend Fixtures and Scripts

| Path | Purpose |
|---|---|
| `backend/app/fixtures/demo_routes_manifest.json` | Manifest of demo-mode route captures and their captured battery percentages. |
| `backend/app/fixtures/demo_route_chennai_*.json` | Saved real `/route/optimize` responses for demo mode. |
| `backend/app/scripts/seed_stations.py` | Creates tables and seeds `stations` from the consolidated station CSV if empty. |
| `backend/app/scripts/capture_demo_fixture.py` | Captures a live backend route response into a demo fixture and updates the manifest; refuses infeasible captures. |
| `backend/app/scripts/reseed_capacity.py` | Utility for updating/reseeding capacity data. |
| `backend/app/scripts/verify_occupancy_blend.py` | Standalone verification for occupancy blending behavior. |
| `backend/app/scripts/verify_reservation_queue.py` | Standalone verification for queue/reservation behavior. |

## Frontend

| Path | Purpose |
|---|---|
| `frontend/package.json` | Frontend dependencies and npm scripts. |
| `frontend/package-lock.json` | Locked frontend dependency graph. |
| `frontend/vite.config.js` | Vite React/Tailwind build configuration. |
| `frontend/index.html` | Vite HTML entry point. |
| `frontend/.env.example` | Example frontend environment variables, including API base/map key. |
| `frontend/README.md` | Vite template/readme notes. |
| `frontend/src/main.jsx` | React root mounting entry. |
| `frontend/src/App.jsx` | Top-level UI state, planner submission, result/error handling, route selection, and demo-mode notice. |
| `frontend/src/api.js` | Fetch wrapper and API methods for route, station reports, wait estimates, and queues. |
| `frontend/src/index.css` | Tailwind theme tokens, page base styles, slider styling, scrollbars, and map popup styling. |
| `frontend/src/components/RouteForm.jsx` | Main route input form. |
| `frontend/src/components/LocationField.jsx` | Text input with location icon and clear button. |
| `frontend/src/components/BatterySlider.jsx` | Starting battery range control. |
| `frontend/src/components/VehicleSelector.jsx` | Vehicle profile selector mirroring backend vehicle profiles. |
| `frontend/src/components/LoadStateToggle.jsx` | Load-state segmented control. |
| `frontend/src/components/PreferenceToggle.jsx` | Route preference UI; only fastest is currently active. |
| `frontend/src/components/RouteResults.jsx` | Map/results shell and route alternative selector. |
| `frontend/src/components/RouteMap.jsx` | Ola Maps rendering for route polyline, origin/destination, and charging stops. |
| `frontend/src/components/RouteSummaryBar.jsx` | Distance, ETA, energy, and navigation summary overlay. |
| `frontend/src/components/BatteryChart.jsx` | Battery percentage chart across driven distance and charging stops. |
| `frontend/src/components/ChargingStationsSummary.jsx` | Aggregated stop/alternative station summary with wait/report controls. |
| `frontend/src/components/StationCard.jsx` | Station row/card for wait, queue, ETA, confidence, and busy/free reporting. |
| `frontend/src/components/QueueDemo.jsx` | Multi-tab queue/reservation demo UI. |
| `frontend/src/components/Sidebar.jsx` | Left navigation shell. |
| `frontend/src/components/Topbar.jsx` | Search/notification/profile header. |
| `frontend/src/components/icons.jsx` | Local stroke icon set used by the UI. |
| `frontend/public/favicon.svg`, `frontend/public/icons.svg` | Static frontend icons. |
| `frontend/dist/` | Built frontend output; generated artifact. |

## Station Data

| Path | Purpose |
|---|---|
| `dataset/download_chargers.py` | Regenerates charger data from OpenChargeMap. |
| `dataset/generate_synthetic_capacity.py` | Adds or synthesizes charger-capacity fields for station data. |
| `dataset/tamil_nadu_ev_charging_stations_CONSOLIDATED.csv` | Current station catalogue source of truth for database seeding. |
| `dataset/tamil_nadu_ev_charging_stations_with_capacity.csv` | Intermediate/enriched station CSV with capacity fields. |
| `files/clean_stations.py` | Cleans raw station data into a normalized CSV. |
| `files/stations_clean.csv` | Cleaned station data output/reference. |

## Energy Dataset and Model Training

| Path | Purpose |
|---|---|
| `dataset_2/dataset_schema.md` | Schema and feature definitions for synthetic trip-energy data. |
| `dataset_2/generate_dataset.py` | Generates synthetic mass-aware trip-energy rows. |
| `dataset_2/split_dataset.py` | Splits the full dataset into train/test CSVs. |
| `dataset_2/train_model.py` | Trains the energy model and writes `models/energy_model.joblib`. |
| `dataset_2/compare_models.py` | Compares candidate model approaches/metrics. |
| `dataset_2/trip_energy_dataset.csv` | Full generated trip-energy dataset. |
| `dataset_2/trip_energy_train.csv` | Training split. |
| `dataset_2/trip_energy_test.csv` | Test split. |
| `dataset_2/archive_n500/*.csv` | Older small train/test split archive. |
| `dataset with mass/trip_energy_dataset.csv` | Earlier mass-aware dataset snapshot retained for reference. |

## Tests and Diagnostics

| Path | Purpose |
|---|---|
| `tests/test_audit_fixes_1_to_5.py` | Offline regression script covering the first five audit fixes. |
| `tests/test_battery_simulator.py` | Pytest coverage for battery simulation behavior. |
| `tests/test_graph_planner.py` | Pytest coverage for graph planning behavior. |
| `tests/test_route_optimizer.py` | Pytest coverage for route optimizer/model helper behavior. |
| `tests/test_routing_service.py` | Pytest coverage for routing service behavior. |
| `tests/run_live_pipeline.py` | Live pipeline diagnostic runner. |
| `tests/run_health_check_2026-08-25*.py` | Health-check rerun scripts. |
| `tests/run_find_minimum_starting_battery.py` | Diagnostic runner for minimum-starting-battery search. |
| `tests/run_margin_recalibration_verification.py` | Margin recalibration verification runner. |
| `tests/run_fanout_flagged_segments.py` | Fanout/segment diagnostic runner. |
| `tests/*diagnostic*.py`, `tests/*check.py`, `tests/sweep_max_edges_per_node.py` | Focused investigation scripts for graph, battery, fanout, redundancy, and stop behavior. |
| `tests/*.md` | Dated investigation notes, design decisions, baselines, and verification writeups. |
| `tests/*.json` | Captured diagnostic outputs and baseline data used to compare behavior over time. |

## Obsidian Notes

| Path | Purpose |
|---|---|
| `obsidian md file from claude/00 - RouteVolt Master Map.md` | Concept map for the ML/physics design. |
| `obsidian md file from claude/01 - Feature Map.md` through `11 - RouteVolt Learning Checklist.md` | Learning notes covering features, EV physics, target generation, data validation, ML pipeline, leakage, and causal reasoning. |
| `obsidian md file from claude/RouteVolt_ML_Pipeline.md` | ML pipeline summary note. |
| `obsidian md file from claude/*.base`, `.obsidian/*` | Obsidian vault metadata and local workspace state. |

## Generated or Local-Only Files

| Path | Purpose |
|---|---|
| `.venv/` | Local Python virtual environment. Generated. |
| `__pycache__/`, `backend/**/__pycache__/`, `tests/__pycache__/`, `dataset_2/__pycache__/` | Python bytecode caches. Generated. |
| `.pytest_cache/` | Pytest cache. Generated. |
| `.DS_Store`, `backend/.DS_Store`, `frontend/.DS_Store`, etc. | macOS metadata. Generated/local. |
| `.claude/settings.local.json` | Local assistant/tooling settings. |

