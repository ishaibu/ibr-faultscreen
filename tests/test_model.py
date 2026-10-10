"""Model tests on synthetic data: fast, and the truth is known."""

import numpy as np
import pandas as pd
import pytest

from ibr_faultscreen.features import FEATURES
from ibr_faultscreen.model import (RuleBaseline, evaluate_models, load_bundle, model_check,
                                   threshold_for_recall, train_final, write_model_card)


def synthetic(n=600, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({f: rng.random(n) for f in FEATURES})
    df["clear_time_s"] = 0.05 + 0.25 * rng.random(n)
    df["scr_min"] = 0.8 + 6 * rng.random(n)
    df["n_ibr"] = rng.integers(0, 5, n)
    df["fault_bus"] = rng.integers(1, 30, n)
    # truth: a smooth interaction a single rule cannot capture exactly
    z = 18 * (df["clear_time_s"] - 0.17) + 1.5 * (2.5 - df["scr_min"]) * (df["n_ibr"] > 1) \
        + 2 * (df["inertia_h_s"] - 0.5)
    df["risky"] = (z + 0.3 * rng.standard_normal(n) > 0).astype(int)
    return df


def test_threshold_for_recall():
    p = np.array([0.1, 0.2, 0.3, 0.9, 0.8, 0.4])
    y = np.array([0, 1, 0, 1, 1, 1])
    t = threshold_for_recall(p, y, 0.75)
    assert ((p >= t) & (y == 1)).sum() / y.sum() >= 0.75


def test_rule_baseline_fits():
    df = synthetic()
    X = df[list(FEATURES)].to_numpy(float)
    r = RuleBaseline().fit(X, df["risky"].to_numpy(), 0.9)
    assert 0.05 <= r.t_star <= 0.3 and "clearing time" in r.describe()


@pytest.fixture(scope="module")
def report():
    return evaluate_models(synthetic(), recall_target=0.9, n_splits=4)


def test_grouped_evaluation(report):
    assert report["split"].startswith("GroupKFold by fault bus")
    for m in report["models"].values():
        assert m["tp"] + m["fp"] + m["fn"] + m["tn"] == report["n"]
    ok, msg = model_check(report)
    assert ok, msg                            # GBM beats the rule on this interaction
    assert report["extrapolation"] is not None


def test_final_model_save_load_explain(tmp_path, report):
    df = synthetic()
    b = train_final(df, "gbm", recall_target=0.9, n_splits=4, report=report)
    assert 0 < b.threshold < 1 and b.importance[0][0] in FEATURES
    path = b.save(tmp_path / "m.joblib")
    b2 = load_bundle(path)
    x = df[list(FEATURES)].to_numpy(float)
    assert np.allclose(b.predict_proba(x[:5]), b2.predict_proba(x[:5]))
    risky_row = x[np.argmax(b.predict_proba(x))]
    why = b.explain(risky_row)
    assert why and all(d > 0 for _f, d in why)
    card = write_model_card(b, tmp_path / "card.md").read_text(encoding="utf-8")
    assert "Model check" in card and "Permutation importance" in card


def test_study_effort_and_unknown_scoring():
    df = synthetic()
    unknown = synthetic(n=60, seed=5)
    rep = evaluate_models(df, recall_target=0.9, n_splits=4, unknown=unknown)
    g = rep["models"]["gbm"]
    shares = [e["flagged_share"] for e in g["effort"]]
    assert [e["recall_target"] for e in g["effort"]] == [0.90, 0.95, 0.99]
    assert shares == sorted(shares)                   # more recall costs more simulations
    assert g["unknown_scored"] == 60 and 0 <= g["unknown_flagged_share"] <= 1


def test_solver_failure_rates():
    from ibr_faultscreen.model import solver_failure_rates
    df = pd.DataFrame({"fault_bus": [5, 5, 5, 5, 7],
                       "status": ["sim_fail", "sim_fail", "completed", "stopped_angle", "completed"],
                       "resolution": ["unresolved", "confirmed", "", "", ""]})
    r = solver_failure_rates(df)
    assert r[5] == {"n": 4, "unknown": 1, "rate": 0.25} and r[7]["unknown"] == 0
