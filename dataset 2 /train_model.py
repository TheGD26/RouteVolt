"""
Trains and evaluates the RouteVolt energy-consumption model on the pre-split
trip_energy_train.csv / trip_energy_test.csv (see split_dataset.py).

Leakage guards (see obsidian md file from claude/08 - Leakage.md):
  - trip_id is an identifier, not a feature.
  - wh_per_km is algebraically derived from energy_consumed_kwh
    (wh_per_km = energy_consumed_kwh * 1000 / distance_km), so it can only be
    used as a secondary *evaluation* target, never as an input feature.
  - Categorical encoding is fit only on the train split (via Pipeline) so the
    test split never leaks into preprocessing.
"""

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

TRAIN_PATH = "trip_energy_train.csv"
TEST_PATH = "trip_energy_test.csv"

TARGET_COL = "energy_consumed_kwh"

# Columns that must never enter X: identifier + anything derived from the target.
LEAK_COLS = ["trip_id", TARGET_COL, "wh_per_km"]

CATEGORICAL_COLS = ["road_type", "weather_condition", "vehicle_profile", "load_state"]
BASELINE_NUMERIC_COLS = ["distance_km"]
BASELINE_CATEGORICAL_COLS = ["vehicle_profile"]


def load_splits(train_path: str = TRAIN_PATH, test_path: str = TEST_PATH):
    train_df = pd.read_csv(train_path)
    test_df = pd.read_csv(test_path)
    return train_df, test_df


def get_X_y(df: pd.DataFrame):
    X = df.drop(columns=LEAK_COLS)
    y = df[TARGET_COL]
    return X, y


def build_baseline_pipeline() -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), BASELINE_CATEGORICAL_COLS),
        ],
        remainder="passthrough",
    )
    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("model", LinearRegression()),
        ]
    )


def build_full_pipeline() -> Pipeline:
    numeric_cols = [
        c
        for c in [
            "distance_km",
            "elevation_gain_m",
            "elevation_loss_m",
            "avg_speed_kmh",
            "traffic_congestion_level",
            "ambient_temperature_c",
            "vehicle_battery_capacity_kwh",
            "vehicle_efficiency_baseline_wh_per_km",
            "vehicle_curb_weight_kg",
            "payload_kg",
            "total_mass_kg",
        ]
    ]
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_COLS),
            ("num", "passthrough", numeric_cols),
        ]
    )
    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("model", RandomForestRegressor(n_estimators=300, random_state=42)),
        ]
    )


def evaluate(name: str, pipeline: Pipeline, X_test: pd.DataFrame, y_test: pd.Series, distance_test: pd.Series):
    y_pred = pipeline.predict(X_test)

    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    wh_per_km_actual = y_test * 1000.0 / distance_test
    wh_per_km_pred = y_pred * 1000.0 / distance_test
    wh_per_km_mae = mean_absolute_error(wh_per_km_actual, wh_per_km_pred)

    print(f"\n{name}")
    print(f"  RMSE (kWh):        {rmse:.4f}")
    print(f"  MAE  (kWh):        {mae:.4f}")
    print(f"  R^2:               {r2:.4f}")
    print(f"  MAE (Wh/km):       {wh_per_km_mae:.2f}")

    return {"rmse": rmse, "mae": mae, "r2": r2, "wh_per_km_mae": wh_per_km_mae}


def main():
    train_df, test_df = load_splits()

    X_train, y_train = get_X_y(train_df)
    X_test, y_test = get_X_y(test_df)

    baseline = build_baseline_pipeline()
    baseline.fit(X_train[BASELINE_NUMERIC_COLS + BASELINE_CATEGORICAL_COLS], y_train)
    evaluate(
        "Baseline (distance_km + vehicle_profile, LinearRegression)",
        baseline,
        X_test[BASELINE_NUMERIC_COLS + BASELINE_CATEGORICAL_COLS],
        y_test,
        X_test["distance_km"],
    )

    full_model = build_full_pipeline()
    full_model.fit(X_train, y_train)
    evaluate(
        "Full model (all features, RandomForestRegressor)",
        full_model,
        X_test,
        y_test,
        X_test["distance_km"],
    )

    print(
        "\nIf the full model's R^2 is suspiciously close to 1.0 (>0.98) or its "
        "Wh/km MAE collapses to ~0, re-check LEAK_COLS before trusting the result "
        "(see obsidian md file from claude/08 - Leakage.md)."
    )


if __name__ == "__main__":
    main()
