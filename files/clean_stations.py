"""
RouteVolt -- Station Data Cleaning (Step 3 of the dataset checklist)

Cleans the real/public station identity data for use by the recommendation
system (stations.py). This is INDEPENDENT of the ML trip-training dataset
(trips_raw.csv / routevolt_training_data.csv) -- station occupancy/cost/queue
data has no causal relationship to battery energy consumption and must not
be merged into the training table.

Findings this script encodes (see conversation for full evidence):
  - station_name, latitude, longitude, charger_speed_kw: REAL / trustworthy.
  - operator: REAL but sparse (many "(Unknown Operator)").
  - available_ports, occupied_ports, queue_time_minutes, cost_per_kwh,
    traffic_index, temperature, rain_probability, elevation: SYNTHETIC,
    independently randomized per row -- proven by the "Dr. MGR University"
    duplicate, which has identical lat/lon but elevation 339 vs 80,
    temperature 40 vs 22, rain_probability 1 vs 77. These are one-time
    illustrative snapshots, not live/authoritative values.
  - Stations outside Chennai city proper (Vellore, Tiruttani, Oragadam) are
    NOT geocoding errors -- their coordinates match real-world geography for
    those towns. They are kept, since RouteVolt is an inter-city route
    optimizer, not a city-only lookup.
"""

import pandas as pd

STATIONS_PATH = "ev_station_dataset.csv"       # superset of charging_stations.csv
OPERATOR_PATH = "charging_stations.csv"        # source of the `operator` column
OUTPUT_PATH = "data/stations_clean.csv"


def clean_stations(stations_path=STATIONS_PATH, operator_path=OPERATOR_PATH):
    df = pd.read_csv(stations_path)
    ops = pd.read_csv(operator_path)

    # Both files share identical row order (verified) -- merge positionally
    # rather than by name, since `station_name` has a duplicate that would
    # otherwise cause a join explosion (2x2 -> 4 rows for one physical station).
    assert (df["station_name"].values == ops["name"].values).all(), \
        "Row order mismatch between ev_station_dataset.csv and charging_stations.csv"
    df["operator"] = ops["operator"].values

    # --- Deduplicate: same name + same coordinates = same physical station ---
    # The "Dr. MGR University" pair has identical name/lat/lon but wildly
    # different synthetic operational columns (proven random, not two real
    # readings) -- keep the first occurrence rather than averaging, since
    # averaging two independently-randomized values would just manufacture
    # a third meaningless number with no more claim to accuracy.
    before = len(df)
    dupe_mask = df.duplicated(subset=["station_name", "latitude", "longitude"], keep="first")
    dropped = df[dupe_mask][["station_name", "latitude", "longitude"]]
    df = df[~dupe_mask].reset_index(drop=True)
    if len(dropped):
        print(f"Dropped {len(dropped)} duplicate station row(s):")
        print(dropped.to_string(index=False))

    # --- Fix invalid port relationship: occupied_ports must not exceed
    # available_ports (available_ports is treated as total capacity) ---
    invalid = (df["occupied_ports"] > df["available_ports"]).sum()
    if invalid:
        print(f"Capping occupied_ports at available_ports for {invalid} row(s) "
              f"(occupied_ports > available_ports is physically invalid)")
    df["occupied_ports"] = df[["occupied_ports", "available_ports"]].min(axis=1)

    # --- Standardize operator label ---
    df["operator"] = df["operator"].replace("(Unknown Operator)", "Unknown")

    # --- Add stable station_id ---
    df.insert(0, "station_id", range(1, len(df) + 1))

    print(f"\nRows: {before} -> {len(df)} after cleaning")
    return df


if __name__ == "__main__":
    import os
    os.makedirs("data", exist_ok=True)
    clean_df = clean_stations()
    clean_df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved -> {OUTPUT_PATH}")
    print(clean_df.head())
