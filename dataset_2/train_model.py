import pandas as pd
from pathlib import Path
import numpy as np
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.preprocessing import OneHotEncoder, FunctionTransformer, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.dummy import DummyRegressor
from sklearn.model_selection import KFold, cross_val_score, cross_validate, cross_val_predict
from sklearn.metrics import mean_absolute_error, root_mean_squared_error, r2_score
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import RandomForestRegressor, HistGradientBoostingRegressor
from sklearn.model_selection import RandomizedSearchCV
from scipy.stats import randint, loguniform
from sklearn.inspection import permutation_importance
import joblib

SCRIPT_DIR = Path(__file__).parent
TRAIN_DATA_PATH = SCRIPT_DIR / "trip_energy_train.csv"
TEST_DATA_PATH = SCRIPT_DIR / "trip_energy_test.csv"
# Training data stays with the rest of dataset_2/, but the serialized model
# lives in a top-level models/ dir (standard ML project layout) -- must stay
# in sync with backend/app/config.ENERGY_MODEL_PATH.
MODEL_OUT_PATH = SCRIPT_DIR.parent / "models" / "energy_model.joblib"



# our decisions from Step 1
target = "energy_consumed_kwh"

numeric_features = [
    "distance_km", "elevation_gain_m", "elevation_loss_m", "avg_speed_kmh",
    "traffic_congestion_level", "ambient_temperature_c",
    "vehicle_battery_capacity_kwh", "vehicle_efficiency_baseline_wh_per_km",
    "total_mass_kg",
]

categorical_features = ["road_type", "weather_condition", "vehicle_profile", "load_state"]

log_transform_features = ["distance_km", "elevation_gain_m", "elevation_loss_m"]

scale_only_features = [f for f in numeric_features if f not in log_transform_features]


def load_data():
    df = pd.read_csv(TRAIN_DATA_PATH)
    X = df[numeric_features + categorical_features]
    y = df[target]
    return X, y

def load_test_data():
    df = pd.read_csv(TEST_DATA_PATH)
    X = df[numeric_features + categorical_features]
    y = df[target]
    return X, y

def build_preprocessors():
    log_scale_pipeline = Pipeline(steps=[
        ("log1p", FunctionTransformer(np.log1p)),
        ("scale", StandardScaler()),
    ])

    linear_preprocessor = ColumnTransformer(transformers=[
        ("log_scale", log_scale_pipeline, log_transform_features),
        ("scale_only", StandardScaler(), scale_only_features),
        ("onehot", OneHotEncoder(handle_unknown="ignore"), categorical_features),
    ])

    tree_preprocessor = ColumnTransformer(transformers=[
        ("passthrough", "passthrough", numeric_features),
        ("onehot", OneHotEncoder(handle_unknown="ignore"), categorical_features),
    ])

    return linear_preprocessor, tree_preprocessor


def build_linear_preprocessor(numeric_cols, categorical_cols, log_cols):
    scale_only_cols = [c for c in numeric_cols if c not in log_cols]
    log_scale_pipeline = Pipeline(steps=[
        ("log1p", FunctionTransformer(np.log1p)),
        ("scale", StandardScaler()),
    ])
    return ColumnTransformer(transformers=[
        ("log_scale", log_scale_pipeline, [c for c in log_cols if c in numeric_cols]),
        ("scale_only", StandardScaler(), scale_only_cols),
        ("onehot", OneHotEncoder(handle_unknown="ignore"), categorical_cols),
    ])


def evaluate_model(X, y, model, preprocessor=None):
    # Build either a bare model or a Pipeline, depending on whether
    # preprocessing is needed (Dummy needs none; Linear/Ridge/trees do)
    if preprocessor is not None:
        estimator = Pipeline(steps=[
            ("preprocessor", preprocessor),
            ("model", model),
        ])
    else:
        estimator = model

    # Same folds every call, since random_state is fixed -> fair comparison
    kfold = KFold(n_splits=5, shuffle=True, random_state=42)

    scoring = {
        "MAE": "neg_mean_absolute_error",
        "RMSE": "neg_root_mean_squared_error",
        "R2": "r2",
    }

    results = cross_validate(
        estimator, X, y,
        cv=kfold,
        scoring=scoring,
    )

    # neg_* scorers return negative values by convention (sklearn maximizes
    # everything internally) -> flip sign for MAE and RMSE. R2 is already
    # "higher is better" so no flip needed.
    summary = {}
    for name in ["MAE", "RMSE", "R2"]:
        raw_scores = results[f"test_{name}"]
        if name in ("MAE", "RMSE"):
            raw_scores = -raw_scores
        summary[f"{name}_mean"] = raw_scores.mean()
        summary[f"{name}_std"] = raw_scores.std()

    return summary


def evaluate_model_log_target(X, y_raw, model, preprocessor=None):
    # y_raw: the ORIGINAL untransformed target (energy_consumed_kwh)
    # internally trains on log1p(y_raw), then inverts predictions with expm1
    # before scoring, so metrics come back in real kWh units

    if preprocessor is not None:
        estimator = Pipeline(steps=[
            ("preprocessor", preprocessor),
            ("model", model),
        ])
    else:
        estimator = model

    kfold = KFold(n_splits=5, shuffle=True, random_state=42)

    y_log = np.log1p(y_raw)

    # out-of-fold predictions, still in log-space
    log_preds = cross_val_predict(estimator, X, y_log, cv=kfold)

    # invert back to real kWh units
    preds = np.expm1(log_preds)

    return {
        "MAE": mean_absolute_error(y_raw, preds),
        "RMSE": root_mean_squared_error(y_raw, preds),
        "R2": r2_score(y_raw, preds),
    }


def evaluate_model_log_target_with_preds(X, y_raw, model, preprocessor=None):
    if preprocessor is not None:
        estimator = Pipeline(steps=[("preprocessor", preprocessor), ("model", model)])
    else:
        estimator = model

    kfold = KFold(n_splits=5, shuffle=True, random_state=42)
    y_log = np.log1p(y_raw)
    log_preds = cross_val_predict(estimator, X, y_log, cv=kfold)
    preds = np.expm1(log_preds)

    metrics = {
        "MAE": mean_absolute_error(y_raw, preds),
        "RMSE": root_mean_squared_error(y_raw, preds),
        "R2": r2_score(y_raw, preds),
    }
    return metrics, preds


def run_baselines(X, y, linear_preprocessor):
    dummy = DummyRegressor()
    dummy.fit(X, y)

    dummy_mean = DummyRegressor(strategy="mean")
    dummy_median = DummyRegressor(strategy="median")

    #print(evaluate_model(X, y, dummy_mean))
    #print(evaluate_model(X, y, dummy_median))
    #print(y.describe())

    print(evaluate_model_log_target(X, y, LinearRegression(), linear_preprocessor))
    print(evaluate_model_log_target(X, y, Ridge(), linear_preprocessor))


def run_ablation(X, y, linear_preprocessor):
    full_result = evaluate_model_log_target(X, y, Ridge(), linear_preprocessor)
    print("Full features:", full_result)

    numeric_features_list = X.select_dtypes(include="number").columns.tolist()
    leave_one_out_results = {}
    for col in numeric_features_list:
        X_reduced = X.drop(columns=[col])
        remaining_numeric = [c for c in numeric_features_list if c != col]
        reduced_preprocessor = build_linear_preprocessor(
            remaining_numeric, categorical_features, log_transform_features
        )
        result = evaluate_model_log_target(X_reduced, y, Ridge(), reduced_preprocessor)
        leave_one_out_results[col] = result["R2"]

    for col, r2 in sorted(leave_one_out_results.items(), key=lambda kv: kv[1]):
        print(f"Dropped {col:35s} -> R2 = {r2:.4f}  (drop from full: {full_result['R2'] - r2:+.4f})")


def run_residual_analysis(X, y, linear_preprocessor):
    metrics, preds = evaluate_model_log_target_with_preds(X, y, Ridge(), linear_preprocessor)
    residuals = y - preds

    print(residuals.describe())

    # residuals vs. each numeric feature — look for pattern vs. pure noise
    numeric_features_list = X.select_dtypes(include="number").columns.tolist()
    resid_df = X[numeric_features_list].copy()
    resid_df["residual"] = residuals.values
    resid_df["abs_residual"] = residuals.abs().values

    # quick numeric check: correlation between |residual| and each feature
    print(resid_df[numeric_features_list + ["abs_residual"]].corr()["abs_residual"].sort_values(ascending=False))


def run_tree_comparison(X, y, tree_preprocessor):
    rf_model = RandomForestRegressor(random_state=42)
    hgb_model = HistGradientBoostingRegressor(random_state=42)

    # RF, raw y
    rf_raw_results = evaluate_model(X, y, rf_model, tree_preprocessor)
    # RF, log y
    rf_log_results = evaluate_model_log_target(X, y, rf_model, tree_preprocessor)
    # HGB, raw y
    hgb_raw_results = evaluate_model(X, y, hgb_model, tree_preprocessor)
    # HGB, log y
    hgb_log_results = evaluate_model_log_target(X, y, hgb_model, tree_preprocessor)

    print("RF (raw):", rf_raw_results)
    print("RF (log):", rf_log_results)
    print("HGB (raw):", hgb_raw_results)
    print("HGB (log):", hgb_log_results)

    rf_pipeline = Pipeline([
        ("preprocessor", tree_preprocessor),
        ("model", rf_model)
    ])

    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    rf_preds = cross_val_predict(rf_pipeline, X, y, cv=kf)

    abs_residual = (y - rf_preds).abs()

    distance_corr = abs_residual.corr(X["distance_km"])
    speed_corr = abs_residual.corr(X["avg_speed_kmh"])

    print("RF abs_residual vs distance_km:", distance_corr)
    print("RF abs_residual vs avg_speed_kmh:", speed_corr)

    hgb_pipeline = Pipeline([
        ("preprocessor", tree_preprocessor),
        ("model", hgb_model)
    ])

    hgb_preds = cross_val_predict(hgb_pipeline, X, y, cv=kf)

    abs_residual_hgb = (y - hgb_preds).abs()

    distance_corr_hgb = abs_residual_hgb.corr(X["distance_km"])
    speed_corr_hgb = abs_residual_hgb.corr(X["avg_speed_kmh"])

    print("HGB abs_residual vs distance_km:", distance_corr_hgb)
    print("HGB abs_residual vs avg_speed_kmh:", speed_corr_hgb)

    rf_model_constrained = RandomForestRegressor(min_samples_leaf=5, random_state=42)
    rf_constrained_results = evaluate_model(X, y, rf_model_constrained, tree_preprocessor)

    rf_constrained_pipeline = Pipeline([
        ("preprocessor", tree_preprocessor),
        ("model", rf_model_constrained)
    ])
    rf_constrained_preds = cross_val_predict(rf_constrained_pipeline, X, y, cv=kf)

    abs_residual_rf_c = (y - rf_constrained_preds).abs()
    distance_corr_rf_c = abs_residual_rf_c.corr(X["distance_km"])
    speed_corr_rf_c = abs_residual_rf_c.corr(X["avg_speed_kmh"])

    print("RF constrained:", rf_constrained_results)
    print("RF constrained abs_residual vs distance_km:", distance_corr_rf_c)
    print("RF constrained abs_residual vs avg_speed_kmh:", speed_corr_rf_c)


def run_log_target_wrapper_comparison(X, y, linear_preprocessor):
    ridge_log = TransformedTargetRegressor(
        regressor=Ridge(),
        func=np.log1p,
        inverse_func=np.expm1
    )
    ridge_log_results = evaluate_model(X, y, ridge_log, linear_preprocessor)
    print("Ridge with log target:", ridge_log_results)

    linreg_log = TransformedTargetRegressor(
        regressor=LinearRegression(),
        func=np.log1p,
        inverse_func=np.expm1
    )
    linreg_log_results = evaluate_model(X, y, linreg_log, linear_preprocessor)
    print("Linear Regression with log target:", linreg_log_results)


def run_rf_tuning(X, y, tree_preprocessor):
    rf_param_dist = {
        "model__n_estimators": randint(100, 600),
        "model__max_depth": [None, 10, 20, 30],
        "model__min_samples_leaf": randint(1, 10),
        "model__max_features": ["sqrt", "log2", None],
    }

    rf_pipeline = Pipeline([
        ("preprocessor", tree_preprocessor),
        ("model", RandomForestRegressor(random_state=42)),
    ])

    kf = KFold(n_splits=5, shuffle=True, random_state=42)

    rf_search = RandomizedSearchCV(
        rf_pipeline,
        param_distributions=rf_param_dist,
        n_iter=40,
        cv=kf,
        scoring="r2",
        random_state=42,
        n_jobs=-1,
    )
    rf_search.fit(X, y)
    best_std = rf_search.cv_results_["std_test_score"][rf_search.best_index_]
    print("RF best params:", rf_search.best_params_)
    print("RF best CV R2:", rf_search.best_score_)
    print("RF best CV R2 std:", best_std)
    return rf_search


def run_hgb_tuning(X, y, tree_preprocessor):
    hgb_param_dist = {
        "model__learning_rate": loguniform(0.01, 0.3),
        "model__max_iter": randint(100, 500),
        "model__max_leaf_nodes": randint(15, 63),
        "model__min_samples_leaf": randint(5, 50),
    }

    hgb_pipeline = Pipeline([
        ("preprocessor", tree_preprocessor),
        ("model", HistGradientBoostingRegressor(random_state=42)),
    ])

    kf = KFold(n_splits=5, shuffle=True, random_state=42)

    hgb_search = RandomizedSearchCV(
        hgb_pipeline,
        param_distributions=hgb_param_dist,
        n_iter=40,
        cv=kf,
        scoring="r2",
        random_state=42,
        n_jobs=-1,
    )
    hgb_search.fit(X, y)
    best_std = hgb_search.cv_results_["std_test_score"][hgb_search.best_index_]
    print("HGB best params:", hgb_search.best_params_)
    print("HGB best CV R2:", hgb_search.best_score_)
    print("HGB best CV R2 std:", best_std)
    return hgb_search


def build_tree_preprocessor(numeric_cols, categorical_cols):
    return ColumnTransformer(transformers=[
        ("passthrough", "passthrough", numeric_cols),
        ("onehot", OneHotEncoder(handle_unknown="ignore"), categorical_cols),
    ])


def run_hgb_ablation(X, y, hgb_search, tree_preprocessor):
    best_hgb_params = {
        k.replace("model__", ""): v
        for k, v in hgb_search.best_params_.items()
    }

    full_model = HistGradientBoostingRegressor(random_state=42, **best_hgb_params)
    full_result = evaluate_model(X, y, full_model, tree_preprocessor)
    print("HGB tuned, full features:", full_result)

    numeric_features_list = X.select_dtypes(include="number").columns.tolist()
    leave_one_out_results = {}
    for col in numeric_features_list:
        X_reduced = X.drop(columns=[col])
        remaining_numeric = [c for c in numeric_features_list if c != col]
        reduced_preprocessor = build_tree_preprocessor(remaining_numeric, categorical_features)
        model = HistGradientBoostingRegressor(random_state=42, **best_hgb_params)
        result = evaluate_model(X_reduced, y, model, reduced_preprocessor)
        leave_one_out_results[col] = result["R2_mean"]

    for col, r2 in sorted(leave_one_out_results.items(), key=lambda kv: kv[1]):
        print(f"Dropped {col:35s} -> R2 = {r2:.4f}  (drop from full: {full_result['R2_mean'] - r2:+.4f})")


def run_final_test_evaluation(X_train, y_train, X_test, y_test, hgb_search, tree_preprocessor):
    best_hgb_params = {
        k.replace("model__", ""): v
        for k, v in hgb_search.best_params_.items()
    }

    final_pipeline = Pipeline([
        ("preprocessor", tree_preprocessor),
        ("model", HistGradientBoostingRegressor(random_state=42, **best_hgb_params)),
    ])

    final_pipeline.fit(X_train, y_train)
    test_preds = final_pipeline.predict(X_test)
    test_metrics = {
        "MAE": mean_absolute_error(y_test, test_preds),
        "RMSE": root_mean_squared_error(y_test, test_preds),
        "R2": r2_score(y_test, test_preds),
    }

    print("Final HGB — untouched test set:", test_metrics)
    return final_pipeline, test_metrics


def save_model(pipeline, path=MODEL_OUT_PATH, metadata=None):
    """
    Persist the fitted pipeline (preprocessing + model) as a single joblib
    artifact so route_optimizer.py can load it with joblib.load(path) and
    call .predict(X) directly — X must be a DataFrame with exactly
    numeric_features + categorical_features as columns, in any order.
    """
    payload = {
        "pipeline": pipeline,
        "numeric_features": numeric_features,
        "categorical_features": categorical_features,
        "target": target,
        "metadata": metadata or {},
    }
    joblib.dump(payload, path)
    print(f"Saved model pipeline -> {path}")
    return path



def run_permutation_importance(final_pipeline, X_test, y_test):
    result = permutation_importance(
        final_pipeline, X_test, y_test,
        n_repeats=20,
        random_state=42,
        scoring="r2",
        n_jobs=-1,
    )

    importances = pd.Series(
        result.importances_mean, index=X_test.columns
    ).sort_values(ascending=False)

    stds = pd.Series(
        result.importances_std, index=X_test.columns
    )

    print("Permutation importance (mean R2 drop when shuffled), test set:")
    for feature, importance in importances.items():
        print(f"  {feature:40s} {importance:.4f}  (+/- {stds[feature]:.4f})")

    return importances

if __name__ == "__main__":
    X, y = load_data()
    print(X.shape, y.shape)

    linear_preprocessor, tree_preprocessor = build_preprocessors()

    run_baselines(X, y, linear_preprocessor)
    run_ablation(X, y, linear_preprocessor)
    run_residual_analysis(X, y, linear_preprocessor)
    run_tree_comparison(X, y, tree_preprocessor)
    run_log_target_wrapper_comparison(X, y, linear_preprocessor)

    # Tune HGB, evaluate once on the held-out test set, then persist the
    # fitted pipeline. This is the artifact route_optimizer.py loads.
    hgb_search = run_hgb_tuning(X, y, tree_preprocessor)
    run_hgb_ablation(X, y, hgb_search, tree_preprocessor)

    X_test, y_test = load_test_data()
    final_pipeline, test_metrics = run_final_test_evaluation(
        X, y, X_test, y_test, hgb_search, tree_preprocessor
    )
    run_permutation_importance(final_pipeline, X_test, y_test)

    save_model(
        final_pipeline,
        metadata={
            "best_params": hgb_search.best_params_,
            "test_metrics": test_metrics,
        },
    )








