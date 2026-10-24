"""Labeled dataset generation.

* `sample_scenarios` draws a reproducible scenario list: Latin hypercube over
  load level and clearing time, fault buses spread evenly, and random IBR sets.
  The same seed always gives the same list.
* `run_dataset` simulates, labels and featurizes every scenario in parallel
  worker processes, with a wall-clock timeout per scenario, and appends each
  finished row to ``progress.csv`` straight away. If the run is interrupted,
  calling it again skips finished scenarios (resume).
* When all scenarios are done it writes ``dataset.csv`` (sorted, without
  run-time columns, so the same seed gives a byte-identical file: determinism test) and
  ``data-card.md``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import multiprocessing as mp
import os
import time
from dataclasses import asdict, dataclass, field
from multiprocessing.connection import wait
from pathlib import Path

import numpy as np

from .criteria import CriteriaSettings
from .scenario import FaultSpec, IBRSpec, Scenario

#: Columns that change from run to run; kept in progress.csv only.
RUNTIME_COLUMNS = ("runtime_s", "worker")


@dataclass(frozen=True)
class SamplingPlan:
    """What to sample. Defaults suit the IEEE 39-bus case."""

    n: int = 200
    seed: int = 2026
    case: str = "ieee39"
    load_range: tuple = (0.9, 1.1)
    clear_range_s: tuple = (0.05, 0.30)        # 3 to 18 cycles at 60 Hz
    fault_buses: tuple = tuple(range(1, 30))   # 39-bus network buses (not generator buses)
    ibr_gens: tuple = (1, 2, 3, 4, 5, 6, 7, 8, 9)   # generator 10 is the slack
    max_ibrs: int = 4
    bess_share: float = 0.25                   # chance that an IBR is a battery
    fault_x_pu: float = 1e-4                   # bolted (see the milder-fault check)
    t_end: float = 6.0
    ibr_profile: str | None = None             # None = case.DEFAULT_IBR_PROFILE


def _lhs(rng, n, d):
    """Latin hypercube sample in [0, 1)^d (one point per stratum in each dimension)."""
    u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
    for j in range(d):
        u[:, j] = u[rng.permutation(n), j]
    return u


def sample_scenarios(plan: SamplingPlan = SamplingPlan()) -> list[Scenario]:
    """Reproducible scenario list for `plan`."""
    rng = np.random.default_rng(plan.seed)
    u = _lhs(rng, plan.n, 2)
    lo, hi = plan.load_range
    clo, chi = plan.clear_range_s
    buses = list(plan.fault_buses)
    order = np.concatenate([rng.permutation(buses) for _ in range(plan.n // len(buses) + 1)])
    out = []
    for i in range(plan.n):
        k = int(rng.integers(0, plan.max_ibrs + 1))
        gens = sorted(rng.choice(plan.ibr_gens, size=k, replace=False).tolist()) if k else []
        ibrs = tuple(IBRSpec(int(g), "bess" if rng.random() < plan.bess_share else "solar",
                             profile=plan.ibr_profile)
                     for g in gens)
        ct = round(float(clo + u[i, 1] * (chi - clo)), 4)
        out.append(Scenario(
            scenario_id=f"S{i + 1:05d}", case=plan.case,
            load_scale=round(float(lo + u[i, 0] * (hi - lo)), 4), ibrs=ibrs,
            fault=FaultSpec(bus=int(order[i]), clear_time=ct, x_pu=plan.fault_x_pu),
            t_end=plan.t_end))
    return out


def _default_profile():
    from .case import DEFAULT_IBR_PROFILE
    return DEFAULT_IBR_PROFILE


def scenario_to_dict(sc: Scenario) -> dict:
    return {"scenario_id": sc.scenario_id, "case": sc.case, "load_scale": sc.load_scale,
            "ibrs": ";".join(f"{i.gen}:{i.kind}" for i in sc.ibrs),
            "ibr_profile": ";".join(sorted({i.profile or _default_profile() for i in sc.ibrs})),
            "fault_bus": None if sc.fault is None else sc.fault.bus,
            "clear_time_s": None if sc.fault is None else sc.fault.clear_time,
            "fault_x_pu": None if sc.fault is None else sc.fault.x_pu, "t_end_s": sc.t_end}


# ------------------------------------------------------------------ one scenario

def evaluate(sc: Scenario, settings: CriteriaSettings) -> dict:
    """Simulate, label and featurize one scenario. Returns one flat table row."""
    from .features import compute_features
    from .label import label
    from .simulate import simulate

    t0 = time.perf_counter()
    result = simulate(sc, stop_angle_deg=max(180.0, settings.angle_limit_deg))
    lab = label(result, settings)
    row = scenario_to_dict(sc)
    try:
        row.update(compute_features(sc))
    except Exception as err:                 # features must never sink the row
        row["feature_error"] = f"{type(err).__name__}: {err}"
    summ = lab.summary()
    row.update({"status": result.status, "label": summ["label"], "risky": int(lab.risky),
                "resolution": summ["resolution"]})
    for k, v in lab.flags.items():
        row[f"flag_{k}"] = "" if v is None else int(v)
    row.update({"fault_residual_pu": result.fault_residual_pu,
                "diagnostic_runs": result.summary()["diagnostic_runs"],
                "reasons": summ["reasons"], "message": result.message,
                "andes_version": result.andes_version,
                "runtime_s": round(time.perf_counter() - t0, 3)})
    return row


# ------------------------------------------------------------------ worker pool

def _worker(conn, settings):
    import andes
    andes.config_logger(stream_level=50)
    while True:
        task = conn.recv()
        if task is None:
            return
        sc = task
        try:
            row = evaluate(sc, settings)
        except Exception as err:            # report, never crash the pool
            row = {**scenario_to_dict(sc), "status": "error", "label": "RISKY", "risky": 1,
                   "message": f"{type(err).__name__}: {err}"}
        conn.send(row)


@dataclass
class _Slot:
    proc: object
    conn: object
    task: Scenario | None = None
    started: float = 0.0
    name: str = ""


def _spawn(ctx, settings, name):
    parent, child = ctx.Pipe()
    p = ctx.Process(target=_worker, args=(child, settings), daemon=True, name=name)
    p.start()
    child.close()
    return _Slot(p, parent, name=name)


def _append(path, row, fields):
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


@dataclass
class RunSummary:
    out_dir: Path
    n_total: int
    n_done: int
    n_skipped: int
    n_timeout: int
    seconds: float
    dataset_csv: Path | None = None
    data_card: Path | None = None
    counts: dict = field(default_factory=dict)


def _fields():
    from .features import FEATURES
    from .label import LABELS
    base = ["scenario_id", "case", "load_scale", "ibrs", "ibr_profile", "fault_bus", "clear_time_s",
            "fault_x_pu", "t_end_s", "status", "label", "risky", "resolution"]
    return (base + [f"flag_{k}" for k in LABELS] + [f for f in FEATURES if f not in base]
            + ["pf_converged", "scr_by_ibr", "fault_residual_pu", "diagnostic_runs",
               "reasons", "message", "feature_error", "andes_version"] + list(RUNTIME_COLUMNS))


def run_dataset(scenarios: list[Scenario], settings: CriteriaSettings, out_dir,
                workers: int | None = None, timeout_s: float = 300.0,
                plan: SamplingPlan | None = None, criteria_source: str = "",
                progress=print, scenario_source: str = "") -> RunSummary:
    """Run all `scenarios` (resumable). See the module docstring."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prog = out / "progress.csv"
    fields = _fields()
    done = set()
    if prog.exists():
        with open(prog, newline="", encoding="utf-8") as fh:
            done = {r["scenario_id"] for r in csv.DictReader(fh)}
    todo = [s for s in scenarios if s.scenario_id not in done]
    n_skip = len(scenarios) - len(todo)
    if n_skip:
        progress(f"Resuming: {n_skip} scenarios already done, {len(todo)} to go.")
    workers = max(1, min(workers or max(1, (os.cpu_count() or 2) - 1), len(todo) or 1))
    ctx = mp.get_context("spawn")
    t0 = time.perf_counter()
    n_timeout = finished = 0
    slots = [_spawn(ctx, settings, f"w{i}") for i in range(workers)] if todo else []
    queue = list(todo)
    try:
        while queue or any(s.task is not None for s in slots):
            for s in slots:
                if s.task is None and queue:
                    s.task, s.started = queue.pop(0), time.perf_counter()
                    s.conn.send(s.task)
            busy = [s for s in slots if s.task is not None]
            ready = wait([s.conn for s in busy], timeout=1.0)
            now = time.perf_counter()
            for s in busy:
                row = None
                if s.conn in ready:
                    try:
                        row = s.conn.recv()
                    except EOFError:          # worker died
                        row = {**scenario_to_dict(s.task), "status": "error", "label": "RISKY",
                               "risky": 1, "message": "worker process ended unexpectedly"}
                elif now - s.started > timeout_s:
                    row = {**scenario_to_dict(s.task), "status": "timeout", "label": "RISKY",
                           "risky": 1, "message": f"no result within {timeout_s:g} s"}
                    n_timeout += 1
                if row is None:
                    continue
                if row.get("status") == "timeout" or not s.proc.is_alive():
                    s.proc.kill()
                    s.proc.join()
                    fresh = _spawn(ctx, settings, s.name)
                    s.proc, s.conn = fresh.proc, fresh.conn
                row["worker"] = s.name
                row.setdefault("runtime_s", round(now - s.started, 3))
                _append(prog, row, fields)
                finished += 1
                progress(f"[{n_skip + finished:>6}/{len(scenarios)}] {row['scenario_id']} "
                         f"{row.get('label', ''):5s} {row.get('status', '')}")
                s.task = None
    finally:
        for s in slots:
            try:
                s.conn.send(None)
            except (BrokenPipeError, OSError):
                pass
            s.proc.join(timeout=5)
            if s.proc.is_alive():
                s.proc.kill()
    summary = RunSummary(out, len(scenarios), finished, n_skip, n_timeout,
                         time.perf_counter() - t0)
    rows = read_rows(prog)
    ids = {s.scenario_id for s in scenarios}
    if ids <= {r["scenario_id"] for r in rows}:
        summary.dataset_csv = finalize(rows, ids, out / "dataset.csv")
        from .datacard import write_data_card
        summary.data_card = write_data_card(summary.dataset_csv, out / "data-card.md",
                                            plan=plan, settings=settings,
                                            criteria_source=criteria_source,
                                            scenario_source=scenario_source)
    summary.counts = _count(rows)
    return summary


def read_rows(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def finalize(rows, ids, path) -> Path:
    """Write the deterministic dataset file: chosen ids, sorted, no run-time columns."""
    keep = sorted((r for r in rows if r["scenario_id"] in ids), key=lambda r: r["scenario_id"])
    seen, uniq = set(), []
    for r in keep:                               # a resumed run may repeat an id
        if r["scenario_id"] not in seen:
            seen.add(r["scenario_id"])
            uniq.append(r)
    fields = [f for f in _fields() if f not in RUNTIME_COLUMNS]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(uniq)
    return Path(path)


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _count(rows):
    c = {}
    for r in rows:
        key = f"{r.get('label')}/{r.get('status')}"
        c[key] = c.get(key, 0) + 1
    return c


def plan_to_json(plan: SamplingPlan) -> str:
    """One line per setting, lists kept on one line."""
    items = [f'  "{k}": {json.dumps(v)}' for k, v in asdict(plan).items()]
    return "{\n" + ",\n".join(items) + "\n}"
