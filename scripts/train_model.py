"""
Trains a model to predict, for a (failed_service, affected_service) pair:
  1. was_impacted (classification) -- will this service be affected at all
  2. severity_delta (regression) -- how badly, if so

Evaluates against the naive baseline the README calls for: "any direct
dependency is equally likely to be affected" -- i.e. predict impacted iff
graph_distance == 1, with a fixed severity guess. This comparison is the
number to quote in interviews.

Usage:
    python scripts/train_model.py
Writes: data/model.joblib, data/model_metrics.json
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, mean_absolute_error,
)
from sklearn.model_selection import train_test_split
import xgboost as xgb

ROOT = Path(__file__).parent.parent
ML_DATASET = ROOT / "data" / "ml_dataset.csv"
MODEL_PATH = ROOT / "data" / "model.joblib"
METRICS_PATH = ROOT / "data" / "model_metrics.json"

FEATURE_COLS = [
    "graph_distance",
    "injected_severity",
    "fault_mode_down",
    "failed_betweenness",
    "failed_in_degree",
    "failed_out_degree",
    "failed_is_articulation_point",
    "affected_betweenness",
    "affected_in_degree",
    "affected_out_degree",
    "has_direct_edge",
    "direct_edge_timeout_ms",
    "direct_edge_retries",
]


def load_data():
    df = pd.read_csv(ML_DATASET)
    # graph_distance is NaN when affected service can't reach failed service
    # at all (no dependency path) -- fill with a large "infinite distance"
    # sentinel rather than dropping rows, since "not dependent" is itself
    # informative (should almost always mean not impacted).
    df["graph_distance"] = df["graph_distance"].fillna(99)
    for col in ["failed_is_articulation_point", "has_direct_edge"]:
        df[col] = df[col].astype(int)
    return df


def naive_baseline_predictions(df):
    """'Any direct dependency is equally likely to be affected': predict
    impacted iff the affected service is a direct (distance-1) dependency,
    with a flat severity guess equal to the training-set average among
    distance-1 pairs."""
    pred_impacted = (df["graph_distance"] == 1).astype(int)
    return pred_impacted


def main():
    df = load_data()
    X = df[FEATURE_COLS]
    y_clf = df["was_impacted"].astype(int)
    y_reg = df["severity_delta"]

    X_train, X_test, yclf_train, yclf_test, yreg_train, yreg_test, df_train, df_test = train_test_split(
        X, y_clf, y_reg, df, test_size=0.25, random_state=42, stratify=y_clf
    )

    # --- classifier: will this service be impacted at all? ---
    pos_weight = (yclf_train == 0).sum() / max((yclf_train == 1).sum(), 1)
    clf = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.08,
        scale_pos_weight=pos_weight, eval_metric="logloss", random_state=42,
    )
    clf.fit(X_train, yclf_train)
    clf_pred = clf.predict(X_test)
    clf_proba = clf.predict_proba(X_test)[:, 1]

    # --- regressor: how severe, given it's impacted? (trained on all rows;
    # severity_delta is ~0 for non-impacted rows, which is correct signal) ---
    reg = RandomForestRegressor(n_estimators=200, max_depth=6, random_state=42)
    reg.fit(X_train, yreg_train)
    reg_pred = reg.predict(X_test)

    # --- naive baseline on the SAME test split, for a fair comparison ---
    baseline_pred = naive_baseline_predictions(df_test)

    def clf_metrics(y_true, y_pred, y_proba=None):
        m = {
            "accuracy": round(accuracy_score(y_true, y_pred), 4),
            "precision": round(precision_score(y_true, y_pred, zero_division=0), 4),
            "recall": round(recall_score(y_true, y_pred, zero_division=0), 4),
            "f1": round(f1_score(y_true, y_pred, zero_division=0), 4),
        }
        if y_proba is not None:
            try:
                m["roc_auc"] = round(roc_auc_score(y_true, y_proba), 4)
            except ValueError:
                m["roc_auc"] = None
        return m

    model_metrics = clf_metrics(yclf_test, clf_pred, clf_proba)
    baseline_metrics = clf_metrics(yclf_test, baseline_pred)
    reg_mae = round(mean_absolute_error(yreg_test, reg_pred), 4)
    baseline_reg_mae = round(mean_absolute_error(yreg_test, baseline_pred.values * yreg_train[df_train.was_impacted == 1].mean() if (yreg_train[df_train.was_impacted==1]).any() else baseline_pred.values), 4)

    results = {
        "n_train": len(X_train),
        "n_test": len(X_test),
        "positive_rate_test": round(float(yclf_test.mean()), 4),
        "model": {"classifier": model_metrics, "regressor_mae": reg_mae},
        "naive_baseline": {"classifier": baseline_metrics, "regressor_mae": baseline_reg_mae},
        "f1_improvement_over_baseline": round(model_metrics["f1"] - baseline_metrics["f1"], 4),
        "feature_importances": dict(sorted(
            zip(FEATURE_COLS, [round(float(v), 4) for v in clf.feature_importances_]),
            key=lambda kv: -kv[1],
        )),
    }

    joblib.dump({"classifier": clf, "regressor": reg, "feature_cols": FEATURE_COLS}, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps(results, indent=2))

    print(json.dumps(results, indent=2))
    print(f"\nSaved model -> {MODEL_PATH}")
    print(f"Saved metrics -> {METRICS_PATH}")


if __name__ == "__main__":
    main()
