"""
Regenerate `num_chargers` with a synthetic, capped-at-4 distribution.

tamil_nadu_ev_charging_stations_CONSOLIDATED.csv's own num_chargers column
goes as high as 10 for a handful of rows, but it isn't verified real port
data (see README/dataset notes) -- it's not something the reservation-queue
demo (backend/app/services/charger_service.py's join_queue/get_queue_state)
should treat as ground truth, since a station "having 10 chargers" in the
demo would make the queueing behavior essentially never trigger. This script
overrides it with a weighted synthetic distribution capped at 4
({1: 0.50, 2: 0.30, 3: 0.14, 4: 0.06}), seeded for reproducibility, and
writes the result to a NEW file -- it never touches the CONSOLIDATED CSV
itself, which stays the source of truth for every other column (re-run
download_chargers.py + seed_stations.py to refresh those).

Usage:
    uv run python dataset/generate_synthetic_capacity.py

Output:
    dataset/tamil_nadu_ev_charging_stations_with_capacity.csv

Apply the result to the DB with backend/app/scripts/reseed_capacity.py
(matches existing rows by station_name + lat/lon, only updates num_chargers
-- doesn't wipe/re-seed the table).
"""

import random
from pathlib import Path

import pandas as pd

SOURCE_CSV = Path(__file__).resolve().parent / "tamil_nadu_ev_charging_stations_CONSOLIDATED.csv"
OUTPUT_CSV = Path(__file__).resolve().parent / "tamil_nadu_ev_charging_stations_with_capacity.csv"

CAPACITY_WEIGHTS = {1: 0.50, 2: 0.30, 3: 0.14, 4: 0.06}
RANDOM_SEED = 42


def generate_synthetic_capacity(df: pd.DataFrame, seed: int = RANDOM_SEED) -> pd.DataFrame:
    rng = random.Random(seed)
    values = list(CAPACITY_WEIGHTS.keys())
    weights = list(CAPACITY_WEIGHTS.values())
    df = df.copy()
    df["num_chargers"] = [rng.choices(values, weights=weights, k=1)[0] for _ in range(len(df))]
    return df


def main() -> None:
    df = pd.read_csv(SOURCE_CSV)
    df = generate_synthetic_capacity(df)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote {len(df)} rows to {OUTPUT_CSV}")
    print(df["num_chargers"].value_counts().sort_index())


if __name__ == "__main__":
    main()
