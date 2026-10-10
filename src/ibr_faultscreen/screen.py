"""Screen-then-verify.

1. Compute pre-fault features for every candidate scenario (power flow only).
2. The classifier gives P(RISKY) and ranks the scenarios.
3. Every predicted-RISKY scenario and every borderline one is re-simulated and
   labeled. Simulation always overrides the prediction.
4. Each row says where its verdict came from: ``simulated`` or ``predicted``.
   A RISKY verdict is never shown from a prediction alone; if the simulation
   budget runs out, the row says ``predicted RISKY, not yet verified``.
5. Scenarios at solver-hard fault buses (many unknown outcomes in the training
   dataset) and at buses not in the dataset are always simulated: the model
   cannot predict where the solver fails, so it must not quietly pass them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .criteria import CriteriaSettings
from .features import FEATURES, compute_features
from .label import label
from .model import SOLVER_HARD_RATE, ModelBundle
from .scenario import Scenario
from .simulate import simulate


@dataclass
class ScreenRow:
    scenario: Scenario
    p_risky: float
    predicted: str               # RISKY / OK from the model
    verdict: str                 # final verdict shown to users
    source: str                  # "simulated" or "predicted"
    why: list                    # top features pushing toward RISKY
    sim_result: object = None
    sim_labels: object = None
    features: dict = None
    verify_reason: str = ""

    def summary(self) -> dict:
        sl = self.sim_labels
        return {"scenario_id": self.scenario.scenario_id, "p_risky": round(self.p_risky, 4),
                "predicted": self.predicted, "verdict": self.verdict, "source": self.source,
                "agrees": "" if sl is None else ("yes" if (sl.risky == (self.predicted == "RISKY"))
                                                 else "no"),
                "resolution": "" if sl is None else (sl.resolution or ""),
                "reasons": "" if sl is None else " | ".join(sl.reasons),
                "why": "; ".join(f"{f} (+{d:.2f})" for f, d in self.why),
                "verify_reason": self.verify_reason}


def _solver_reason(bus, rates, hard_rate):
    if not rates or bus is None:
        return ""
    r = rates.get(int(bus))
    if r is None:
        return "fault bus not in the training dataset"
    if r["rate"] >= hard_rate:
        return f"solver-hard fault bus ({r['rate']:.0%} unknown outcomes in the dataset)"
    return ""


def screen(candidates: list[Scenario], bundle: ModelBundle, settings: CriteriaSettings,
           borderline: float = 0.15, max_verify: int | None = None, progress=None,
           solver_hard_rate: float | None = None):
    """Rank `candidates` and verify by simulation. Returns rows, riskiest first.

    borderline: predicted-OK scenarios with P(RISKY) within this distance of the
        threshold are also simulated.
    max_verify: simulation budget (None = no limit).
    solver_hard_rate: fault buses with at least this share of unknown outcomes in
        the training dataset are always simulated (default: the bundle's value).
    """
    rates = (getattr(bundle, "meta", None) or {}).get("solver_rates") or {}
    hard_rate = (solver_hard_rate if solver_hard_rate is not None else
                 (bundle.meta or {}).get("solver_hard_rate", SOLVER_HARD_RATE))
    feats = [compute_features(sc) for sc in candidates]
    X = np.array([[f[c] for c in FEATURES] for f in feats], float)
    p = bundle.predict_proba(X)
    order = np.argsort(-p, kind="stable")
    rows = []
    budget = len(order) if max_verify is None else max_verify
    for rank, i in enumerate(order, 1):
        sc, pi = candidates[i], float(p[i])
        pred = "RISKY" if pi >= bundle.threshold else "OK"
        why = bundle.explain(X[i]) if pred == "RISKY" else []
        border = abs(pi - bundle.threshold) <= borderline
        solver = _solver_reason(sc.fault.bus if sc.fault else None, rates, hard_rate)
        need = pred == "RISKY" or border or bool(solver)
        reason = ("predicted RISKY" if pred == "RISKY" else "borderline" if border else solver)
        row = ScreenRow(sc, pi, pred, pred, "predicted", why, features=feats[i],
                        verify_reason=reason if need else "")
        if need and budget > 0:
            budget -= 1
            t0 = time.perf_counter()
            res = simulate(sc, stop_angle_deg=max(180.0, settings.angle_limit_deg))
            lab = label(res, settings)
            row.sim_result, row.sim_labels = res, lab
            row.verdict = "RISKY" if lab.risky else "OK"
            if lab.resolution:
                row.verdict += f" ({lab.resolution})"
            row.source = "simulated"
            if progress:
                progress(f"[{rank}/{len(order)}] {sc.scenario_id} p={pi:.2f} -> {row.verdict} "
                         f"({time.perf_counter() - t0:.1f} s)")
        elif pred == "RISKY":
            row.verdict = "predicted RISKY, not yet verified"
        rows.append(row)
    return rows


def all_risky_verified(rows) -> bool:
    """Every row has a source, and no RISKY verdict rests on a prediction alone."""
    for r in rows:
        if r.source not in ("simulated", "predicted"):
            return False
        if r.source == "predicted" and r.verdict.startswith("RISKY"):
            return False
    return True
