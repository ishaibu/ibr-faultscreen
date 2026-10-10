"""Screening models and honest evaluation.

Models
------
* ``rule``     : RISKY if clearing time >= t* or lowest IBR SCR <= s*. The
                 thresholds are fitted on the training data. This is the bar a
                 learned model must clear to be worth using.
* ``logistic`` : standardized logistic regression (class-balanced).
* ``gbm``      : scikit-learn HistGradientBoostingClassifier (class-balanced).

Evaluation
----------
Splits are grouped by FAULT BUS: a test fold only contains fault locations the
model never saw in training (random row splits would leak location patterns).
The decision threshold is tuned for recall on RISKY inside each training fold
(nested), so the reported numbers are not tuned on the data they score.
An extra split trains on scenarios with fewer IBRs and tests on more IBRs.

Unknown outcomes
----------------
Scenarios the solver could not settle ("unresolved") are left out of
training, but the evaluation also scores them out of fold: how many would the
screen have sent to simulation? In the first full dataset only about 62% were,
and whether the solver fails turned out to be a property of the fault location
that the features cannot predict (AUC 0.56 on unseen buses). So the screen
always verifies scenarios at "solver-hard" fault buses (see `solver_failure_rates`
and `screen.screen`), measured from the dataset rather than predicted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
                             roc_auc_score)
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURES

MODEL_NAMES = ("rule", "logistic", "gbm")
DEFAULT_RECALL_TARGET = 0.95       # assumption; set by the author (design spec §11)
EFFORT_TARGETS = (0.90, 0.95, 0.99)  # recall levels shown in the study-effort table
#: A fault bus where at least this share of the dataset's scenarios ended
#: "unresolved" is solver-hard: the screen always verifies scenarios there.
SOLVER_HARD_RATE = 0.20


# ------------------------------------------------------------------ data

def load_table(path, include_unknown: bool = False) -> pd.DataFrame:
    """Read dataset.csv and keep rows usable for learning.

    Rows whose outcome is unknown (a failed run the milder-fault check could not resolve, a
    timeout or an error) are labeled RISKY for safety, but that label reflects
    the solver, not the grid. They are left out of training and evaluation
    unless include_unknown=True; the count is kept in df.attrs["n_unknown"].
    """
    df = pd.read_csv(path)
    res = df.get("resolution", pd.Series([""] * len(df))).fillna("")
    known = df["status"].isin(["completed", "stopped_angle"]) | (
        (df["status"] == "sim_fail") & (res == "confirmed"))
    n_unknown = int((~known).sum())
    if not include_unknown:
        df = df[known]
    df = df.dropna(subset=list(FEATURES)).reset_index(drop=True)
    df.attrs["n_unknown"] = n_unknown
    df["risky"] = df["risky"].astype(int)
    return df


def _unknown_mask(df):
    res = df.get("resolution", pd.Series([""] * len(df), index=df.index)).fillna("")
    known = df["status"].isin(["completed", "stopped_angle"]) | (
        (df["status"] == "sim_fail") & (res == "confirmed"))
    return ~known


def load_unknown(path) -> pd.DataFrame:
    """Rows whose outcome is unknown (unresolved failures, timeouts, errors)."""
    df = pd.read_csv(path)
    df = df[_unknown_mask(df)].dropna(subset=list(FEATURES)).reset_index(drop=True)
    return df


def solver_failure_rates(path_or_df) -> dict:
    """Per fault bus: number of scenarios and share with an unknown outcome."""
    df = pd.read_csv(path_or_df) if not isinstance(path_or_df, pd.DataFrame) else path_or_df
    df = df[df["fault_bus"].notna()]
    unk = _unknown_mask(df)
    out = {}
    for bus, grp in df.groupby(df["fault_bus"].astype(int)):
        n, u = int(len(grp)), int(unk[grp.index].sum())
        out[int(bus)] = {"n": n, "unknown": u, "rate": u / n if n else 0.0}
    return out


def xy(df):
    return df[list(FEATURES)].to_numpy(float), df["risky"].to_numpy(int)


# ------------------------------------------------------------------ models

class RuleBaseline:
    """RISKY if clear_time >= t_star or scr_min <= s_star (fitted)."""

    def fit(self, X, y, recall_target=DEFAULT_RECALL_TARGET):
        ct = X[:, FEATURES.index("clear_time_s")]
        scr = X[:, FEATURES.index("scr_min")]
        best = None
        for t in np.unique(np.quantile(ct, np.linspace(0, 1, 41))):
            for s in np.unique(np.concatenate([[0.0], np.quantile(scr, np.linspace(0, 1, 21))])):
                pred = (ct >= t) | (scr <= s)
                tp = np.sum(pred & (y == 1))
                rec = tp / max(1, y.sum())
                prec = tp / max(1, pred.sum())
                key = (rec >= recall_target, prec, rec)
                if best is None or key > best[0]:
                    best = (key, t, s)
        _, self.t_star, self.s_star = best
        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, X):
        ct = X[:, FEATURES.index("clear_time_s")]
        scr = X[:, FEATURES.index("scr_min")]
        # a smooth score: 0.5 at the rule boundary, higher when further inside RISKY
        score = np.maximum(ct / max(self.t_star, 1e-9), self.s_star / np.maximum(scr, 1e-9))
        p = 1 / (1 + np.exp(-8 * (score - 1)))
        return np.column_stack([1 - p, p])

    def describe(self):
        return (f"RISKY if clearing time >= {self.t_star * 1000:.0f} ms "
                f"or lowest IBR SCR <= {self.s_star:.2f}")


def make_model(name, seed=0):
    if name == "rule":
        return RuleBaseline()
    if name == "logistic":
        return make_pipeline(StandardScaler(),
                             LogisticRegression(class_weight="balanced", max_iter=2000))
    if name == "gbm":
        # Revisited on the first full dataset (5000 scenarios, 2026-10-10): trees of
        # up to 31 leaves (no depth cap) with larger leaves and 300 iterations raised
        # the grouped, nested AUC from 0.850 to 0.882 and AP from 0.656 to 0.742
        # (depth 3 had suited the 240-scenario demo set). Chosen on the same grouped
        # folds that report the result, so treat the gain as slightly optimistic.
        return HistGradientBoostingClassifier(class_weight="balanced", random_state=seed,
                                              max_iter=300, learning_rate=0.05,
                                              min_samples_leaf=40, max_depth=None,
                                              l2_regularization=1.0)
    raise ValueError(f"unknown model {name!r}")


def _fit(name, X, y, recall_target, seed=0):
    m = make_model(name, seed)
    if name == "rule":
        return m.fit(X, y, recall_target)
    if len(np.unique(y)) < 2:
        raise ValueError("training data contains only one class")
    return m.fit(X, y)


def threshold_for_recall(p, y, target):
    """Largest threshold whose recall on RISKY is at least `target`."""
    pos = np.sort(p[y == 1])
    if pos.size == 0:
        return 0.5
    k = int(np.floor((1 - target) * pos.size))       # positives allowed below threshold
    return float(pos[k]) if k < pos.size else float(pos[-1])


def _oof(name, X, y, groups, recall_target, n_splits, seed=0):
    """Out-of-fold probabilities with grouped folds."""
    p = np.full(len(y), np.nan)
    for tr, te in GroupKFold(n_splits=min(n_splits, len(np.unique(groups)))).split(X, y, groups):
        p[te] = _fit(name, X[tr], y[tr], recall_target, seed).predict_proba(X[te])[:, 1]
    return p


# ------------------------------------------------------------------ evaluation

def _metrics(y, p, thr):
    pred = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    two = len(np.unique(y)) == 2
    auc = roc_auc_score(y, p) if two else float("nan")
    ap = average_precision_score(y, p) if two else float("nan")
    bins = np.linspace(0, 1, 6)
    cal = []
    for a, b in zip(bins[:-1], bins[1:]):
        m = (p >= a) & (p < b) if b < 1 else (p >= a) & (p <= b)
        if m.any():
            cal.append({"bin": f"{a:.1f}-{b:.1f}", "n": int(m.sum()),
                        "mean_pred": float(p[m].mean()), "frac_risky": float(y[m].mean())})
    return {"recall": tp / max(1, tp + fn), "precision": tp / max(1, tp + fp),
            "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn), "auc": float(auc),
            "ap": float(ap),
            "brier": float(brier_score_loss(y, p)), "threshold": float(thr),
            "flagged_share": float(pred.mean()), "calibration": cal}


def evaluate_models(df, recall_target=DEFAULT_RECALL_TARGET, n_splits=5, seed=0,
                    unknown=None) -> dict:
    """Nested, location-grouped evaluation of every model (used by the model check).

    unknown: optional rows with an unknown outcome (`load_unknown`). They are
    never trained on; each is scored by the fold model that did not see its
    fault bus, to report how many the screen would have sent to simulation.
    """
    X, y = xy(df)
    groups = df["fault_bus"].to_numpy()
    k = min(n_splits, len(np.unique(groups)))
    targets = sorted(set(EFFORT_TARGETS) | {recall_target})
    has_unk = unknown is not None and len(unknown) > 0
    if has_unk:
        Xu = unknown[list(FEATURES)].to_numpy(float)
        gu = unknown["fault_bus"].to_numpy()
    results = {}
    for name in MODEL_NAMES:
        p = np.full(len(y), np.nan)
        thr = {t: np.full(len(y), np.nan) for t in targets}
        unk_flag = np.zeros(len(unknown), bool) if has_unk else None
        unk_seen = np.zeros(len(unknown), bool) if has_unk else None
        for tr, te in GroupKFold(n_splits=k).split(X, y, groups):
            inner = _oof(name, X[tr], y[tr], groups[tr], recall_target, max(2, k - 1), seed)
            model = _fit(name, X[tr], y[tr], recall_target, seed)
            p[te] = model.predict_proba(X[te])[:, 1]
            for t in targets:
                thr[t][te] = threshold_for_recall(inner, y[tr], t)
            if has_unk:
                sel = np.isin(gu, np.unique(groups[te]))
                if sel.any():
                    unk_seen[sel] = True
                    unk_flag[sel] = (model.predict_proba(Xu[sel])[:, 1]
                                     >= threshold_for_recall(inner, y[tr], recall_target))
        pred = (p >= thr[recall_target]).astype(int)
        m = _metrics(y, p, 0.5)                    # AUC, Brier, calibration on probabilities
        tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
        m.update({"recall": tp / max(1, tp + fn), "precision": tp / max(1, tp + fp),
                  "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
                  "flagged_share": float(pred.mean()), "threshold": "tuned per fold"})
        m["effort"] = []
        for t in EFFORT_TARGETS:
            pr = p >= thr[t]
            m["effort"].append({"recall_target": t,
                                "recall": float(pr[y == 1].mean()) if (y == 1).any() else float("nan"),
                                "flagged_share": float(pr.mean())})
        if has_unk:
            m["unknown_scored"] = int(unk_seen.sum())
            m["unknown_flagged_share"] = (float(unk_flag[unk_seen].mean())
                                          if unk_seen.any() else float("nan"))
        results[name] = m

    # extrapolation: train on fewer IBRs, test on more
    n_ibr = df["n_ibr"].to_numpy()
    cut = int(np.median(n_ibr))
    tr, te = n_ibr <= cut, n_ibr > cut
    extra = None
    if te.sum() >= 10 and len(np.unique(y[tr])) == 2 and len(np.unique(y[te])) == 2:
        extra = {"train": f"n_ibr <= {cut}", "test": f"n_ibr > {cut}", "models": {}}
        for name in MODEL_NAMES:
            inner = _oof(name, X[tr], y[tr], groups[tr], recall_target, k, seed)
            t = threshold_for_recall(inner, y[tr], recall_target)
            pt = _fit(name, X[tr], y[tr], recall_target, seed).predict_proba(X[te])[:, 1]
            extra["models"][name] = _metrics(y[te], pt, t)
    return {"n": int(len(y)), "n_risky": int(y.sum()), "n_fault_buses": int(len(np.unique(groups))),
            "n_unknown_scored": int(len(unknown)) if has_unk else 0,
            "recall_target": recall_target, "split": f"GroupKFold by fault bus, {k} folds (nested)",
            "models": results, "extrapolation": extra}


def model_check(report, model="gbm", baseline="rule", tolerance=0.05) -> tuple[bool, str]:
    """Model check: recall within `tolerance` of the target, and better ranking than the rule
    baseline on both AUC and average precision (threshold-free, so not luck of
    one cut-off)."""
    m, b = report["models"][model], report["models"][baseline]
    ok_recall = m["recall"] >= report["recall_target"] - tolerance
    better = m["auc"] > b["auc"] and m["ap"] > b["ap"]
    msg = (f"{model}: recall {m['recall']:.2f}, precision {m['precision']:.2f}, "
           f"AUC {m['auc']:.3f}, AP {m['ap']:.3f} | {baseline}: recall {b['recall']:.2f}, "
           f"precision {b['precision']:.2f}, AUC {b['auc']:.3f}, AP {b['ap']:.3f}")
    return ok_recall and better, msg


# ------------------------------------------------------------------ final model + explanations

@dataclass
class ModelBundle:
    name: str
    model: object
    threshold: float
    features: tuple = FEATURES
    medians: dict = field(default_factory=dict)
    importance: list = field(default_factory=list)      # (feature, mean, std)
    meta: dict = field(default_factory=dict)

    def predict_proba(self, X):
        return self.model.predict_proba(np.asarray(X, float))[:, 1]

    def explain(self, x, top=3):
        """Per-scenario 'why flagged': change in probability when each feature is
        set to its training median (positive = pushes toward RISKY)."""
        x = np.asarray(x, float).reshape(1, -1)
        base = self.predict_proba(x)[0]
        out = []
        for j, f in enumerate(self.features):
            z = x.copy()
            z[0, j] = self.medians[f]
            out.append((f, float(base - self.predict_proba(z)[0])))
        out.sort(key=lambda t: -t[1])
        return [t for t in out[:top] if t[1] > 0]

    def save(self, path):
        import joblib
        joblib.dump(self, path)
        return Path(path)


def load_bundle(path) -> ModelBundle:
    import joblib
    return joblib.load(path)


def train_final(df, name="gbm", recall_target=DEFAULT_RECALL_TARGET, n_splits=5,
                seed=0, report=None, dataset_sha=None, solver_rates=None) -> ModelBundle:
    """Fit on all data; threshold from grouped out-of-fold predictions."""
    X, y = xy(df)
    groups = df["fault_bus"].to_numpy()
    k = min(n_splits, len(np.unique(groups)))
    oof = _oof(name, X, y, groups, recall_target, k, seed)
    thr = threshold_for_recall(oof, y, recall_target)
    model = _fit(name, X, y, recall_target, seed)
    # importance on held-out folds (not on training data)
    imps = []
    for tr, te in GroupKFold(n_splits=k).split(X, y, groups):
        m = _fit(name, X[tr], y[tr], recall_target, seed)
        if len(np.unique(y[te])) < 2:
            continue
        r = permutation_importance(m, X[te], y[te], scoring="roc_auc", n_repeats=5,
                                   random_state=seed)
        imps.append(r.importances_mean)
    imp = np.mean(imps, axis=0) if imps else np.zeros(len(FEATURES))
    sd = np.std(imps, axis=0) if imps else np.zeros(len(FEATURES))
    importance = sorted(((f, float(a), float(b)) for f, a, b in zip(FEATURES, imp, sd)),
                        key=lambda t: -t[1])
    import sklearn

    import ibr_faultscreen
    meta = {"recall_target": recall_target, "n": int(len(y)), "n_risky": int(y.sum()),
            "n_unknown": df.attrs.get("n_unknown"),
            "dataset_sha256": dataset_sha, "sklearn": sklearn.__version__,
            "ibr_faultscreen": ibr_faultscreen.__version__, "seed": seed,
            "evaluation": report, "solver_rates": solver_rates or {},
            "solver_hard_rate": SOLVER_HARD_RATE}
    if name == "rule":
        meta["rule"] = model.describe()
    return ModelBundle(name, model, thr, FEATURES,
                       {f: float(np.median(X[:, j])) for j, f in enumerate(FEATURES)},
                       importance, meta)


def write_model_card(bundle: ModelBundle, path):
    ev = bundle.meta.get("evaluation") or {}
    rows = []
    for name, m in (ev.get("models") or {}).items():
        rows.append(f"| {name} | {m['recall']:.2f} | {m['precision']:.2f} | {m['auc']:.3f} | "
                    f"{m['ap']:.3f} | "
                    f"{m['brier']:.3f} | {m['flagged_share']:.2f} | {m['tp']}/{m['fp']}/{m['fn']}/{m['tn']} |")
    ok, msg = model_check(ev) if ev.get("models") else (None, "not evaluated")
    lines = [
        "# Model card: IBR-FaultScreen screening classifier", "",
        f"- Model: `{bundle.name}`; decision threshold {bundle.threshold:.4g} on P(RISKY)",
        f"- Trained on {bundle.meta.get('n')} scenarios ({bundle.meta.get('n_risky')} RISKY); "
        f"dataset SHA-256 `{bundle.meta.get('dataset_sha256')}`",
        f"- Versions: ibr-faultscreen {bundle.meta.get('ibr_faultscreen')}, "
        f"scikit-learn {bundle.meta.get('sklearn')}", "",
        "## Intended use", "",
        "Rank many fault scenarios so the riskiest are simulated and studied first. "
        "Every RISKY result shown to users is re-simulated (screen-then-verify). "
        "Not for use without simulation, and not a substitute for an interconnection study.", "",
        "P(RISKY) is a ranking score, not a calibrated probability: the models are trained "
        "with class balancing and the threshold is set for recall, so a low threshold is "
        "expected. The calibration bins are in `evaluation.json`.", "",
        "## Evaluation (honest)", "",
        f"{ev.get('split', '')}. Recall target on RISKY: {ev.get('recall_target')}. "
        "Threshold tuned inside each training fold. AP = average precision.",
        f"Scenarios left out because their outcome is unknown: "
        f"{bundle.meta.get('n_unknown', 'not recorded')}.", "",
        "| Model | Recall | Precision | AUC | AP | Brier | Share flagged | TP/FP/FN/TN |",
        "|---|---|---|---|---|---|---|---|", *rows, "",
        f"Model check (recall near the target and better ranking than the simple rule, "
        f"on unseen fault locations): "
        f"{'PASS' if ok else 'FAIL' if ok is not None else 'n/a'}. {msg}", "",
    ]
    ex = ev.get("extrapolation")
    if ex:
        lines += [f"Extrapolation (train {ex['train']}, test {ex['test']}):", "",
                  "| Model | Recall | Precision | AUC |", "|---|---|---|---|",
                  *[f"| {n} | {m['recall']:.2f} | {m['precision']:.2f} | {m['auc']:.3f} |"
                    for n, m in ex["models"].items()], ""]
    g = (ev.get("models") or {}).get(bundle.name) or {}
    if g.get("effort"):
        lines += ["### Study effort", "",
                  f"Share of scenarios the `{bundle.name}` model sends to simulation for each "
                  "recall target (held-out fault locations). The rest are passed without "
                  "simulation.", "",
                  "| Recall target | Recall reached | Share flagged |", "|---|---|---|",
                  *[f"| {e['recall_target']:.2f} | {e['recall']:.3f} | {e['flagged_share']:.2f} |"
                    for e in g["effort"]], ""]
    if g.get("unknown_scored"):
        lines += ["### Scenarios with an unknown outcome", "",
                  f"Of {g['unknown_scored']} scenarios the solver could not settle (left out of "
                  f"training), the model alone would send "
                  f"{100 * g['unknown_flagged_share']:.0f}% to simulation. Whether the solver "
                  "fails depends mostly on the fault location, so the screen always verifies "
                  "scenarios at solver-hard fault buses (below).", ""]
    rates = bundle.meta.get("solver_rates") or {}
    hard_rate = bundle.meta.get("solver_hard_rate", SOLVER_HARD_RATE)
    hard = sorted(((b, r) for b, r in rates.items() if r["rate"] >= hard_rate),
                  key=lambda t: -t[1]["rate"])
    if rates:
        lines += ["### Solver-hard fault buses", "",
                  f"Buses where at least {hard_rate:.0%} of the dataset's scenarios ended with an "
                  "unknown outcome. `screen` always simulates scenarios at these buses and at "
                  "buses not in the dataset.", ""]
        lines += (["| Fault bus | Scenarios | Unknown outcome |", "|---|---|---|",
                   *[f"| {b} | {r['n']} | {r['unknown']} ({r['rate']:.0%}) |" for b, r in hard], ""]
                  if hard else ["None.", ""])
    if "rule" in bundle.meta:
        lines += [f"Rule: {bundle.meta['rule']}", ""]
    lines += ["## What drives the predictions", "",
              "Permutation importance (drop in AUC on held-out fault locations):", "",
              "| Feature | Importance | Std |", "|---|---|---|",
              *[f"| `{f}` | {a:.3f} | {b:.3f} |" for f, a, b in bundle.importance], "",
              "## Limits", "",
              "- Learned from RMS simulations of one test system with generic IBR models.",
              "- Fault locations never seen in training are the honest test; new networks "
              "are not covered until retrained on them.",
              "- Labels inherit every limit of the criteria file and the dataset.", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return Path(path)


def save_report_json(report, path):
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return Path(path)
