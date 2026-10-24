# RouteVolt

EV road-trip planner for Tamil Nadu: FastAPI backend (`backend/app/`), Vite React
frontend (`frontend/src/`), and a trained energy model (`models/energy_model.joblib`,
built by `dataset_2/train_model.py`). Full background: `docs/project-context.md`.

## Start here: the knowledge graph

Before exploring the code, read `graphify-out/GRAPH_REPORT.md` (~5k tokens). It maps
the main modules, the most-connected functions, and which docs/reports explain
which code. Use it to pick the files you actually need instead of reading broadly.

- Cross-file questions ("what touches X?", "how does A reach B?"): run
  `/graphify query "<question>"` before opening files.
- Don't read `graphify-out/graph.json` or `graph.html` directly; they are ~270k tokens.
- The graph is a snapshot. After changing code, run `/graphify . --update`
  (code-only updates use no LLM tokens). Always confirm details in the real source.

## Commands

```bash
uv run python -m uvicorn backend.app.main:app --reload   # backend on :8000
cd frontend && npm install && npm run dev                # frontend
uv run --with pytest python -m pytest tests/test_*.py    # unit tests (pytest isn't a project dep)
```

Config lives in `.env` / `frontend/.env` (templates: `.env.example`). Never commit
real keys; `*.env` is gitignored. `DEMO_MODE` serves cached routes from
`backend/app/fixtures/` and must stay off for real use.

## Layout notes

- Route flow: `routes.py` -> `routing_service` (Ola geocode, OSRM routes) ->
  `graph_planner.plan_journey_via_graph` (station graph + Dijkstra) ->
  `battery_simulator.plan_full_journey` -> `charger_service` wait estimates.
- `tests/test_*.py` are the real tests. Other scripts and dated `.md`/`.json`
  files in `tests/` are one-off diagnostics and their recorded outputs.
- `obsidian md file from claude/` holds design notes on the EV physics and ML pipeline.
