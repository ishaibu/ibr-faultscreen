"""Build the data behind the demo's "Build your own grid" explorer and quiz.

    python tools/explorer_data.py dataset_v3/dataset.csv explorer_run/dataset.csv

Inputs: the 5,000-scenario dataset (to train screening models) and the
explorer run (`tools/explorer_scenarios.py` + `ibr-faultscreen dataset
--scenarios`). Output: docs/demo/explorer-data.js, which sets
window.EXPLORER_DATA for the demo page.

For every explorer scenario it stores the simulated verdict, the failure modes,
a short plain-English reason, and the screen view: the score from a model
trained on the dataset WITHOUT that scenario's fault bus (leave one fault bus
out), its threshold for 95% recall (chosen on the training buses only), and
whether `screen` would send it to simulation (score at or above the threshold,
or a solver-hard fault bus). Note: the solver-hard bus list (buses where at least
20% of the dataset's scenarios had an unknown outcome) is computed from the
whole 5,000-scenario dataset, including the held-out bus; only the model and its
threshold leave the bus out.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from explorer_scenarios import CYCLES, BUSES, GENS, ibr_sets, scenario_id  # noqa: E402

MODES = {"angle": 1, "voltage_recovery": 2, "ride_through": 4, "frequency": 8}


def _band(text):
    m = re.search(r"voltage in ([\[(])([\d.]+), ([\d.]+)([\])])", text)
    if not m:
        return "outside its ride-through band"
    lo, hi = float(m.group(2)), float(m.group(3))
    if lo >= 1.0:
        return f"above {lo:g} pu" if hi > 2 else f"between {lo:g} and {hi:g} pu"
    return f"between {lo:g} and {hi:g} pu" if lo > 0 else f"below {hi:g} pu"


def _one(part):
    """Plain-English sentence for one reason entry, or None to skip it."""
    if part.startswith("ride_through"):
        m = re.search(r"REG_\w+?_(\d+) \(bus (\d+)\).*? for ([\d.]+) s > ([\d.]+) s", part)
        tbl = "Table 3" if "Table3" in part else "Table 2"
        if m:
            gen, bus, dur, lim = m.groups()
            need = (f"does not require riding through any time there" if float(lim) == 0 else
                    f"requires riding through only {float(lim) * 1000:.0f} ms there")
            return (f"Ride-through: the voltage at IBR bus {bus} (generator {gen}) stayed "
                    f"{_band(part)} for {float(dur) * 1000:.0f} ms; PRC-029-1 {tbl} {need}, "
                    f"so the IBR may trip.")
        return "Ride-through: an IBR's voltage went outside the PRC-029-1 must-ride-through region."
    if part.startswith("angle_separation"):
        m = re.search(r"exceeded ([\d.]+) deg at t = ([\d.]+) s", part)
        return ("Rotor-angle instability: the synchronous machines lost step "
                f"(angle spread above {m.group(1)}° at t = {m.group(2)} s)." if m else
                "Rotor-angle instability: the synchronous machines lost step.")
    if part.startswith("voltage_recovery"):
        m = re.search(r"slowest bus (\d+): (.*?) \(limit ([\d.]+) s\)", part)
        return (f"Slow voltage recovery: bus {m.group(1)} {m.group(2).replace('never recovers to reach', 'never recovered to')} "
                f"within {m.group(3)} s." if m else "Slow voltage recovery after clearing.")
    if part.startswith("frequency"):
        m = re.search(r"nadir ([\d.]+) Hz, zenith ([\d.]+) Hz", part)
        return (f"Frequency outside 59–61 Hz (lowest {float(m.group(1)):.2f} Hz, highest "
                f"{float(m.group(2)):.2f} Hz)." if m else "Frequency outside its limits.")
    return None


def explain(row):
    """Short explanation of a verdict (at most two sentences of detail)."""
    status, res = row["status"], row["resolution"]
    parts = [p for p in (row["reasons"] or "").split(" | ") if p]
    if status == "timeout":
        return "No result within the time limit per scenario: outcome unknown, needs a detailed study."
    if res == "unresolved":
        if any("no milder rerun solved" in p for p in parts):
            return ("The bolted fault could not be solved, and neither could slightly milder faults: "
                    "outcome unknown, needs a detailed study (commercial RMS or EMT).")
        return ("The bolted fault could not be solved, and a slightly milder fault was OK: "
                "outcome unknown, needs a detailed study (commercial RMS or EMT).")
    if res == "confirmed":
        text = row["reasons"] or ""
        m = re.search(r"milder fault x = ([\d.]+) pu.*?already RISKY: (.*)$", text, flags=re.S)
        inner_parts = [q for q in m.group(2).split(" | ") if q] if m else []
        details = [d for d in (_one(q) for q in inner_parts) if d]
        detail = " ".join(dict.fromkeys(details[:2])) if details else None
        x = m.group(1) if m else "0.001"
        return (f"The bolted fault could not be solved; a slightly milder fault (x = {x} pu) is already "
                f"RISKY. " + (detail or ""))
    sentences = []
    seen = set()
    for p in parts:
        s = _one(p)
        if s and s not in seen:
            seen.add(s)
            sentences.append(s)
    if not sentences:
        return "All checks passed: machines stayed in step, voltages recovered, IBRs stayed within ride-through, frequency within limits."
    extra = f" (+{len(sentences) - 2} more)" if len(sentences) > 2 else ""
    return " ".join(sentences[:2]) + extra


def modes(row):
    text = row["reasons"] or ""
    out = 0
    if "ride_through" in text:
        out |= MODES["ride_through"]
    if "angle_separation" in text and row["status"] == "stopped_angle":
        out |= MODES["angle"]
    if "voltage_recovery" in text:
        out |= MODES["voltage_recovery"]
    if "frequency:" in text:
        out |= MODES["frequency"]
    if row["resolution"] == "confirmed":            # modes of the milder fault only
        tail = text.split("already RISKY:", 1)[-1] if "already RISKY" in text else ""
        out = 0
        for k, bit in MODES.items():
            key = "angle_separation" if k == "angle" else k
            if key in tail:
                out |= bit
    return out


def code(row):
    if row["status"] == "timeout":
        return "T"
    if row["resolution"] == "unresolved":
        return "U"
    if row["resolution"] == "confirmed":
        return "C"
    return "R" if row["label"] == "RISKY" else "O"


def screen_view(dataset_csv, rows):
    """Leave-one-fault-bus-out scores and screen decisions for the explorer rows."""
    import pandas as pd
    from ibr_faultscreen.model import (DEFAULT_RECALL_TARGET, FEATURES, SOLVER_HARD_RATE, _fit,
                                       _oof, load_table, solver_failure_rates,
                                       threshold_for_recall)
    df = load_table(dataset_csv)                     # known outcomes only
    X = df[list(FEATURES)].to_numpy(float)
    y = df["risky"].to_numpy(int)
    g = df["fault_bus"].to_numpy()
    rates = solver_failure_rates(dataset_csv)
    hard = {int(b) for b, r in rates.items() if r["rate"] >= SOLVER_HARD_RATE}
    ex = pd.DataFrame(rows)
    score = np.full(len(ex), np.nan)
    thr_by_bus = {}
    for bus in sorted({int(b) for b in g}):
        tr = g != bus
        inner = _oof("gbm", X[tr], y[tr], g[tr], DEFAULT_RECALL_TARGET, 5)
        thr = threshold_for_recall(inner, y[tr], DEFAULT_RECALL_TARGET)
        model = _fit("gbm", X[tr], y[tr], DEFAULT_RECALL_TARGET)
        sel = (ex["fault_bus"].astype(int) == bus).to_numpy()
        feats = ex.loc[sel, list(FEATURES)].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        ok = ~np.isnan(feats).any(axis=1)
        idx = np.flatnonzero(sel)[ok]
        score[idx] = model.predict_proba(feats[ok])[:, 1]
        thr_by_bus[bus] = float(thr)
        print(f"  bus {bus:2d}: threshold {thr:.3f}, {ok.sum()} scored", flush=True)
    buses = ex["fault_bus"].astype(int).to_numpy()
    thr = np.array([thr_by_bus.get(b, np.nan) for b in buses])
    flagged = score >= thr
    decision = np.where(np.isin(buses, sorted(hard)), 2, np.where(flagged, 1, 0))
    decision = np.where(np.isnan(score) & (decision == 0), 2, decision)   # no features: simulate
    return score, decision, flagged, thr_by_bus, sorted(hard)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dataset", help="5,000-scenario dataset.csv (trains the screening models)")
    ap.add_argument("explorer", help="explorer run dataset.csv")
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo" / "explorer-data.js"))
    a = ap.parse_args(argv)

    with open(a.explorer, newline="", encoding="utf-8") as fh:
        by_id = {r["scenario_id"]: r for r in csv.DictReader(fh)}
    combos = list(ibr_sets())
    order = [scenario_id(c, b, k) for c in combos for b in BUSES for k in CYCLES]
    missing = [i for i in order if i not in by_id]
    if missing:
        raise SystemExit(f"{len(missing)} explorer scenarios missing, e.g. {missing[:3]}")
    rows = [by_id[i] for i in order]

    print("Screen view (leave one fault bus out):")
    score, decision, flagged, thr_by_bus, hard = screen_view(a.dataset, rows)

    texts, text_idx, ridx = [], {}, []
    for r in rows:
        t = explain(r)
        if t not in text_idx:
            text_idx[t] = len(texts)
            texts.append(t)
        ridx.append(text_idx[t])
    v = "".join(code(r) for r in rows)
    m = "".join(format(modes(r), "x") for r in rows)
    s = "".join("----" if np.isnan(x) else format(min(65535, int(round(x * 65535))), "04x") for x in score)
    d = "".join(str(int(x)) for x in decision)
    f = "".join("1" if x else "0" for x in flagged)     # model alone, full precision

    # Honest summary of the screen view over every explorer scenario.
    risky_known = np.array([c in "RC" for c in v])
    unknown = np.array([c in "UT" for c in v])
    sent = decision > 0
    stats = {
        "n": len(rows), "counts": dict(Counter(v)),
        "risky_caught": float(sent[risky_known].mean()), "unknown_caught": float(sent[unknown].mean()),
        "simulated_share": float(sent.mean()), "solver_hard_buses": hard,
        "model_only_risky_caught": float((decision == 1)[risky_known & (decision != 2)].mean())
        if (risky_known & (decision != 2)).any() else None,
    }
    print(json.dumps(stats, indent=1))
    data = {"gens": list(GENS), "buses": list(BUSES), "cycles": list(CYCLES),
            "combos": ["".join(map(str, c)) for c in combos],
            "v": v, "m": m, "s": s, "d": d, "f": f, "r": ridx, "texts": texts,
            "thr": {str(k): round(t, 6) for k, t in thr_by_bus.items()}, "stats": stats}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("window.EXPLORER_DATA=" + json.dumps(data, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.2f} MB, {len(texts)} distinct explanations)")


if __name__ == "__main__":
    main()
