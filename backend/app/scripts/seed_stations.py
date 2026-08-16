"""
One-time seed: load tamil_nadu_ev_charging_stations_CONSOLIDATED.csv into
the `stations` table.

The CSV stays the source of truth -- this script is re-run (not the CSV
itself) whenever download_chargers.py regenerates it. Safe to run repeatedly:
it's a no-op once the table already has rows.

Usage: uv run python -m backend.app.scripts.seed_stations
"""

import math

import pandas as pd

from backend.app.config import CHARGER_DATASET_PATH
from backend.app.database.db import Base, SessionLocal, engine
from backend.app.database.models import Station

STATION_COLUMNS = [
    "station_name", "operator", "address", "city", "district_area", "state",
    "pincode", "latitude", "longitude", "num_chargers", "connector_types",
    "power_kw", "status", "pricing", "source_url", "coord_precision", "corridor",
]


def _clean(value):
    """NaN -> None so SQLite stores NULL instead of the float 'nan'."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def seed_stations() -> int:
    """Insert CSV rows into `stations` if the table is empty. Returns rows inserted."""
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.query(Station).first() is not None:
            print("stations table already populated -- skipping seed.")
            return 0

        df = pd.read_csv(CHARGER_DATASET_PATH)
        rows = [
            Station(**{col: _clean(row[col]) for col in STATION_COLUMNS})
            for row in df.to_dict(orient="records")
        ]
        db.add_all(rows)
        db.commit()
        print(f"Seeded {len(rows)} stations from {CHARGER_DATASET_PATH}")
        return len(rows)
    finally:
        db.close()


if __name__ == "__main__":
    seed_stations()
