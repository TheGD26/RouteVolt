import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.api import routes, stations
from backend.app.scripts.seed_stations import seed_stations

# Root logger defaults to WARNING -- without this, INFO-level diagnostics
# (e.g. traffic_service's Ola Maps batch-chunking log) are silently dropped
# even with uvicorn's --log-level info, which only configures uvicorn's own
# loggers, not the app's.
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="RouteVolt API",
    description="AI powered EV route planning system",
    version="1.0"
)

# Dev frontend (Vite) runs on a different port -- allow it to call the API
# directly from the browser instead of needing a proxy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _seed_stations_on_startup():
    # No-op once the table already has rows -- safe to run on every boot.
    seed_stations()


app.include_router(routes.router)
app.include_router(stations.router)


@app.get("/")
def home():
    return {
        "message": "RouteVolt backend running 🚗⚡"
    }