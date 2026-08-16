"""
Central filesystem paths + env-loaded settings for the RouteVolt backend.

Keeping these here (instead of hardcoding paths in the services that use
them) means dataset_2/ can be relocated or restructured without touching
route_optimizer.py / charger_service.py.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Same pattern as dataset/download_chargers.py: read .env at the repo root
# (python-dotenv no-ops if the file doesn't exist, e.g. in prod where real
# env vars are set directly).
load_dotenv()

# backend/app/config.py -> parents[2] is the repo root (Routevolt/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---- External service config (all optional at import time; services that
# need a given key fail loudly when *called* without it, not at import) ----
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
OLA_MAPS_API_KEY = os.environ.get("OLA_MAPS_API_KEY")
# Must match the domain whitelisted on the Ola Maps (Krutrim) credential --
# Ola's domain check applies even to server-to-server calls, so requests
# need a matching Origin/Referer header, not just the api_key. Update this
# (not the code) when the credential's whitelist moves to a real production
# domain.
OLA_MAPS_ALLOWED_ORIGIN = os.environ.get("OLA_MAPS_ALLOWED_ORIGIN", "http://127.0.0.1")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

# Public Open-Elevation instance by default -- no API key needed, but rate
# limited. Swappable via env var so a self-hosted instance (recommended for
# production, see README) is a config change, not a code change.
ELEVATION_API_URL = os.environ.get("ELEVATION_API_URL", "https://api.open-elevation.com/api/v1/lookup")

DATASET_2_DIR = PROJECT_ROOT / "dataset_2"

# Produced by dataset_2/train_model.py (joblib.dump of
# {"pipeline", "numeric_features", "categorical_features", "target", "metadata"}).
# Lives in a top-level models/ dir, separate from the training data in
# dataset_2/ -- must stay in sync with train_model.py's MODEL_OUT_PATH.
ENERGY_MODEL_PATH = PROJECT_ROOT / "models" / "energy_model.joblib"

# NOTE: the consolidated station CSV currently lives under dataset/, not
# dataset_2/ (dataset_2/ holds the trip-energy training data; the station
# catalogue is a separate, independently-sourced dataset — see
# files/clean_stations.py). Pointing this at dataset_2/ would 404 at import
# time, so it stays here until/unless the two datasets get consolidated.
CHARGER_DATASET_PATH = PROJECT_ROOT / "dataset" / "tamil_nadu_ev_charging_stations_CONSOLIDATED.csv"
