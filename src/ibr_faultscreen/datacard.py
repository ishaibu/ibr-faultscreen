"""Data card for a generated dataset.

Written automatically next to dataset.csv. It records how the data were made,
what each label means, the class balance and the known limits, so the dataset
can be published (Zenodo) and cited without guesswork.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import date
from pathlib import Path

from .dataset import file_sha256, plan_to_json
from .features import FEATURES
from .label import LABELS


def _pct(n, total):
    return f"{n} ({100 * n / total:.1f}%)" if total else "0"


_BAND = re.compile(r"ride_through:(\S+): .*? (?:voltage|frequency) in (\S+ \S+) for")


def ride_through_bands(rows):
    """Scenarios with ride-through exposure, counted per envelope band (one scenario
    can appear in several bands)."""
    c = Counter()
    for r in rows:
        bands = set()
        for part in str(r.get("reasons", "")).split(" | "):
            m = _BAND.match(part)
            if m:
                bands.add((m.group(1), m.group(2)))
        c.update(bands)
    return c


def _known(r):
    return r["status"] in ("completed", "stopped_angle") or (
        r["status"] == "sim_fail" and r.get("resolution") == "confirmed")


def unknown_by_bus(rows):
    """(fault bus, scenarios, unknown outcomes) sorted by unknown share."""
    n, u = Counter(), Counter()
    for r in rows:
        b = r.get("fault_bus", "")
        if b in ("", None):
            continue
        n[b] += 1
        u[b] += 0 if _known(r) else 1
    return sorted(((b, n[b], u[b]) for b in n if u[b]), key=lambda t: (-t[2] / t[1], -t[2]))


def write_data_card(dataset_csv, path, plan=None, settings=None, criteria_source=""):
    with open(dataset_csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    n = len(rows)
    status = Counter(r["status"] for r in rows)
    lab = Counter(r["label"] for r in rows)
    res = Counter(r["resolution"] for r in rows if r.get("resolution"))
    flags = {k: sum(r.get(f"flag_{k}") == "1" for r in rows) for k in LABELS}
    # from the scenario definition, which every row has (a timed-out run has no features)
    nibr = Counter(len([x for x in str(r.get("ibrs") or "").split(";") if x]) for r in rows)
    versions = sorted({r.get("andes_version") for r in rows} - {"", None})
    import ibr_faultscreen
    lines = [
        "# Data card: IBR-FaultScreen fault-screening dataset",
        "",
        f"*Generated {date.today().isoformat()} by IBR-FaultScreen {ibr_faultscreen.__version__} "
        f"with ANDES {', '.join(versions)}.*",
        "",
        "## What it is",
        "",
        "Simulated three-phase fault scenarios on a power system with inverter-based "
        "resources (IBRs), each labeled RISKY or OK by failure mode, with pre-fault "
        "features for screening models. One row per scenario.",
        "",
        "## How it was made",
        "",
        "- RMS positive-sequence time-domain simulation (ANDES). IBRs replace synchronous "
        "machines using the WECC generic models REGCA1 + REECA1 + REPCA1 with ANDES "
        "example parameters.",
        "- Scenarios: Latin hypercube over load level and clearing time, fault buses spread "
        "evenly, random IBR sets (see the sampling plan below). Same seed, same file.",
        "- Bolted faults (milder-fault check): a bolted fault that cannot be solved is rerun at milder "
        "faults; `resolution` is `confirmed` (milder fault already RISKY) or `unresolved`.",
        f"- Criteria file: {criteria_source or 'not recorded'}.",
        "",
        f"SHA-256 of dataset.csv: `{file_sha256(dataset_csv)}`",
        "",
    ]
    if plan is not None:
        lines += ["### Sampling plan", "", "```json", plan_to_json(plan), "```", ""]
    if settings is not None:
        lines += ["### Criteria used", "",
                  f"- Angle limit: {settings.angle_limit_deg:g} deg",
                  f"- Voltage recovery: {settings.v_recovery_min_pu} pu within "
                  f"{settings.v_recovery_time_s} s",
                  f"- Frequency: {settings.f_min_hz}-{settings.f_max_hz} Hz, measured over "
                  f"{settings.freq_window_s:g} s",
                  *[f"- Envelope `{e.name}`: {e.source}" for e in settings.envelopes], ""]
    lines += [
        "## Contents", "",
        f"- Scenarios: {n}",
        f"- Labels: {', '.join(f'{k} {_pct(v, n)}' for k, v in sorted(lab.items()))}",
        f"- Run status: {', '.join(f'{k} {_pct(v, n)}' for k, v in sorted(status.items()))}",
        f"- Failed bolted faults: {', '.join(f'{k} {v}' for k, v in sorted(res.items())) or 'none'}",
        f"- Scenarios by number of IBRs: "
        f"{', '.join(f'{k}: {v}' for k, v in sorted(nibr.items()))}",
        f"- Scenarios used for training (known outcome): {n - sum(1 for r in rows if not _known(r))}; "
        f"unknown outcome (unresolved failures, timeouts, errors): "
        f"{sum(1 for r in rows if not _known(r))}",
        "",
        "| Failure-mode flag | Scenarios flagged |",
        "|---|---|",
        *[f"| `{k}` | {_pct(v, n)} |" for k, v in flags.items()],
        "",
    ]
    bands = ride_through_bands(rows)
    if bands:
        lines += ["Ride-through exposure by envelope band (scenarios; one scenario can count "
                  "in several bands):", "",
                  "| Envelope | Band | Scenarios |", "|---|---|---|",
                  *[f"| `{e}` | {b} | {v} |" for (e, b), v in
                    sorted(bands.items(), key=lambda t: -t[1])], ""]
    unk = unknown_by_bus(rows)
    if unk:
        lines += ["Fault buses with unknown outcomes (unresolved solver failures), highest "
                  "share first:", "",
                  "| Fault bus | Scenarios | Unknown outcome |", "|---|---|---|",
                  *[f"| {b} | {m} | {_pct(k, m)} |" for b, m, k in unk[:12]], ""]
    lines += [
        "## Columns", "",
        "- Scenario: `scenario_id`, `case`, `load_scale`, `ibrs` (generator:kind), "
        "`fault_bus`, `clear_time_s`, `fault_x_pu`, `t_end_s`.",
        "- Outcome: `status`, `label`, `risky` (1/0), `resolution`, `flag_*` "
        "(1 violated, 0 passed, empty not evaluated), `reasons`, `message`.",
        f"- Features (known before the fault): {', '.join(f'`{f}`' for f in FEATURES)}.",
        "- Diagnostics: `fault_residual_pu` (faulted-bus voltage during the fault), "
        "`diagnostic_runs`, `scr_by_ibr`.",
        "",
        "## Known limits", "",
        "- RMS positive-sequence models: no EMT effects, no unbalanced faults.",
        "- Generic IBR models with example parameters, not a specific manufacturer. "
        "Post-fault overvoltage at IBR buses (the main source of ride-through exposure "
        "here) depends strongly on these parameters.",
        "- Unknown outcomes cluster at a few fault buses, also without IBRs: an ANDES "
        "limitation with near-bolted faults, not a grid finding.",
        "- Faults are bus faults cleared without a topology change.",
        "- Labels depend on the criteria file; angle, voltage-recovery and frequency limits "
        "may be illustrative (see the criteria file).",
        "- Short-circuit features use machine transient reactance X'd (the bundled IEEE 39-bus "
        "X''d values look inconsistent); generator 10 is a large equivalent with H = 50 s, "
        "which dominates the inertia features.",
        "- Screening data: it prioritizes detailed studies and does not replace them.",
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return Path(path)
