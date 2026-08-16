"""
One-off model bake-off: Ridge (existing linear baseline) vs. RandomForest vs.
XGBoost vs. the currently-saved HistGradientBoostingRegressor, all evaluated
on the same held-out trip_energy_test.csv split so the numbers are directly
comparable. Reuses train_model.py's feature lists/preprocessors rather than
redefining them, so this can't silently drift out of sync with what
route_optimizer.py actually loads.

Run once when deciding/re-confirming which model backs energy_model.joblib;
not part of the regular training pipeline.
"""

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.pipeline import Pipeline
from xgboost import XGBRegressor

from train_model import (
    MODEL_OUT_PATH,
    build_preprocessors,
    load_data,
    load_test_data,
)

X_train, y_train = load_data()
X_test, y_test = load_test_data()
linear_preprocessor, tree_preprocessor = build_preprocessors()


def _fit_eval(name, estimator, X_train, y_train, X_test, y_test):
    estimator.fit(X_train, y_train)
    preds = estimator.predict(X_test)
    r2 = r2_score(y_test, preds)
    mae = mean_absolute_error(y_test, preds)
    rmse = root_mean_squared_error(y_test, preds)
    print(f"{name:30s}  R2={r2:.4f}  MAE={mae:.4f}  RMSE={rmse:.4f}")
    return {"name": name, "R2": r2, "MAE": mae, "RMSE": rmse}


results = []

# Ridge on log1p(target) -- its best-performing configuration per
# run_baselines/run_ablation in train_model.py, not raw target.
ridge_pipeline = Pipeline([
    ("preprocessor", linear_preprocessor),
    ("model", Ridge()),
])
ridge_pipeline.fit(X_train, np.log1p(y_train))
ridge_preds = np.expm1(ridge_pipeline.predict(X_test))
results.append({
    "name": "Ridge (log target)",
    "R2": r2_score(y_test, ridge_preds),
    "MAE": mean_absolute_error(y_test, ridge_preds),
    "RMSE": root_mean_squared_error(y_test, ridge_preds),
})
print(f"{'Ridge (log target)':30s}  R2={results[-1]['R2']:.4f}  MAE={results[-1]['MAE']:.4f}  RMSE={results[-1]['RMSE']:.4f}")

# RandomForest, raw target -- same convention as the saved HGB model.
rf_pipeline = Pipeline([
    ("preprocessor", tree_preprocessor),
    ("model", RandomForestRegressor(random_state=42, n_estimators=300)),
])
results.append(_fit_eval("RandomForest (raw target)", rf_pipeline, X_train, y_train, X_test, y_test))

# XGBoost, raw target.
xgb_pipeline = Pipeline([
    ("preprocessor", tree_preprocessor),
    ("model", XGBRegressor(random_state=42, n_estimators=400, max_depth=6, learning_rate=0.05)),
])
results.append(_fit_eval("XGBoost (raw target)", xgb_pipeline, X_train, y_train, X_test, y_test))

# Currently-saved final model (tuned HistGradientBoostingRegressor).
saved_payload = joblib.load(MODEL_OUT_PATH)
saved_pipeline = saved_payload["pipeline"]
saved_preds = saved_pipeline.predict(X_test)
results.append({
    "name": "HGB tuned (currently saved)",
    "R2": r2_score(y_test, saved_preds),
    "MAE": mean_absolute_error(y_test, saved_preds),
    "RMSE": root_mean_squared_error(y_test, saved_preds),
})
print(f"{'HGB tuned (currently saved)':30s}  R2={results[-1]['R2']:.4f}  MAE={results[-1]['MAE']:.4f}  RMSE={results[-1]['RMSE']:.4f}")

print()
best = max(results, key=lambda r: r["R2"])
print(f"Best on test R2: {best['name']} (R2={best['R2']:.4f}, MAE={best['MAE']:.4f})")
