from dataset_2.train_model import (
    build_preprocessors,
    load_data,
    load_test_data,
    run_final_test_evaluation,
    run_permutation_importance,
    run_rf_tuning,
    run_hgb_tuning,
    run_hgb_ablation,
)


if __name__ == "__main__":
    X, y = load_data()
    linear_preprocessor, tree_preprocessor = build_preprocessors()

    # run_baselines(X, y, linear_preprocessor)
    # run_ablation(X, y, linear_preprocessor)
    # run_residual_analysis(X, y, linear_preprocessor)
    # run_tree_comparison(X, y, tree_preprocessor)
    # run_log_target_wrapper_comparison(X, y, linear_preprocessor)

    rf_search = run_rf_tuning(X, y, tree_preprocessor)
    hgb_search = run_hgb_tuning(X, y, tree_preprocessor)
    run_hgb_ablation(X, y, hgb_search, tree_preprocessor)
    X_test, y_test = load_test_data()
    final_pipeline, test_metrics = run_final_test_evaluation(
        X_train=X, y_train=y, X_test=X_test, y_test=y_test,
        hgb_search=hgb_search, tree_preprocessor=tree_preprocessor,
    )
    importances = run_permutation_importance(final_pipeline, X_test, y_test)
