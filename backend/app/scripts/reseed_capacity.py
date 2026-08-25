"""
Update `num_chargers` on existing `stations` rows from the synthetic
capacity CSV (dataset/generate_synthetic_capacity.py), without wiping or
re-seeding the table.

Why this exists instead of just re-running seed_stations.py: seed_stations
is a no-op once the table has rows, by design (re-seeding would also lose
any accumulated occupancy_reports history and reservation-affecting state).
The CONSOLIDATED CSV's own num_chargers column goes up to 10 and isn't
verified real port data -- see generate_synthetic_capacity.py's docstring
for why it's overridden -- but pincode/pricing/source_url/etc. on already-
seeded rows are real and shouldn't move. This script matches each CSV row to
its DB row by (station_name, latitude, longitude) -- verified unique across
all 449 source rows -- and updates only num_chargers.

Usage:
    uv run python -m backend.app.scripts.reseed_capacity
"""

import math
from pathlib import Path

import pandas as pd

from backend.app.database.db import SessionLocal
from backend.app.database.models import Station

CAPACITY_CSV_PATH = (
    Path(__file__).resolve().parents[3] / "dataset" / "tamil_nadu_ev_charging_stations_with_capacity.csv"
)


def _match_key(station_name, latitude, longitude) -> tuple:
    # Round coordinates so float round-tripping through CSV<->DB can't
    # silently miss an otherwise-identical row.
    return (str(station_name).strip(), round(float(latitude), 6), round(float(longitude), 6))


def reseed_capacity() -> dict:
    """Returns {"updated": n, "unmatched": n} counts."""
    df = pd.read_csv(CAPACITY_CSV_PATH)

    capacity_by_key = {
        _match_key(row["station_name"], row["latitude"], row["longitude"]): int(row["num_chargers"])
        for row in df.to_dict(orient="records")
        if not math.isnan(row["latitude"]) and not math.isnan(row["longitude"])
    }

    db = SessionLocal()
    updated = 0
    unmatched = 0
    try:
        for station in db.query(Station).all():
            key = _match_key(station.station_name, station.latitude, station.longitude)
            new_capacity = capacity_by_key.get(key)
            if new_capacity is None:
                unmatched += 1
                continue
            if station.num_chargers != new_capacity:
                station.num_chargers = new_capacity
                updated += 1
        db.commit()
    finally:
        db.close()

    print(f"reseed_capacity: updated {updated} rows, {unmatched} unmatched against {CAPACITY_CSV_PATH.name}")
    return {"updated": updated, "unmatched": unmatched}


if __name__ == "__main__":
    reseed_capacity()
