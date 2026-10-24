# RouteVolt Project Index

RouteVolt is an EV route-planning prototype for Tamil Nadu. It combines a
FastAPI backend, a React/Vite frontend, a SQLite charging-station catalogue,
live routing/traffic/elevation providers, and a trained energy model to answer
one main question: given an EV, load, starting battery, origin, and
destination, where should the driver charge and what happens if a station is
busy?

Start with these references:

- [docs/project-context.md](docs/project-context.md) explains the architecture,
  data flow, request lifecycle, and current assumptions.
- [docs/file-inventory.md](docs/file-inventory.md) describes the purpose of the
  files and folders in this repository.
- [docs/audit-tasks-1-to-5.md](docs/audit-tasks-1-to-5.md) records the first
  five audit tasks, where they were implemented, and how to verify them.
- [README.md](README.md) remains the operational setup guide.

## Quick Run

Backend:

```bash
uv sync
cp .env.example .env
uv run python -m backend.app.scripts.seed_stations
uv run python -m uvicorn backend.app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Useful offline verification:

```bash
uv run python tests/test_audit_fixes_1_to_5.py
```

## Current State

The route API is centered on `POST /route/optimize`. The endpoint geocodes
free-text locations with Ola Maps, fetches OSRM route alternatives, builds a
connector-compatible charging-station graph, runs Dijkstra to choose a feasible
stop sequence, then simulates every leg with mass-aware energy prediction,
traffic congestion, elevation, charging time, and queue estimates.

Demo mode is available through `DEMO_MODE=true`. It returns saved real fixtures
for selected Chennai-origin routes and labels them with `precomputed: true` so
the frontend can show that the plan came from a capture rather than a live
optimization.

## High-Value Files

- `backend/app/api/routes.py`: route optimization and reroute endpoints.
- `backend/app/services/graph_planner.py`: station graph build plus Dijkstra.
- `backend/app/services/battery_simulator.py`: detailed leg-by-leg battery and
  charging simulation.
- `backend/app/services/route_optimizer.py`: energy model inference, vehicle
  profiles, connector compatibility, and station scoring.
- `backend/app/services/traffic_service.py`: live ETA/congestion lookup and
  per-leg traffic cache.
- `backend/app/services/routing_service.py`: geocoding, OSRM routing, distance
  matrices, and optional Google routing fallback.
- `backend/app/services/charger_service.py`: station lookup, occupancy blending,
  queue estimates, and in-memory demo reservations.
- `frontend/src/App.jsx`: top-level UI state and route-planning workflow.
- `dataset_2/train_model.py`: trains `models/energy_model.joblib`.
- `dataset/tamil_nadu_ev_charging_stations_CONSOLIDATED.csv`: station-catalogue
  source of truth used to seed `routevolt.db`.

