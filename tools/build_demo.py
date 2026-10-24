"""Build the interactive demo report published on GitHub Pages (docs/demo/index.html).

Run from the project folder, after generating a dataset (and, for the
"Build your own grid" explorer and the quiz, after tools/explorer_data.py has
written docs/demo/explorer-data.js):

    python tools/build_demo.py dataset_v3/dataset.csv

It uses every scenario in the dataset for the summary, network map, ranked table
and risk-versus-IBR-share chart; trains the screening model to include its model
card; and re-simulates one representative scenario per outcome for the
interactive trajectories. Takes a few minutes.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

DOI_LINKS = ('<br>Dataset (version 2.0 with this demo; the link opens the latest): <a href="https://doi.org/10.5281/zenodo.23290449">doi.org/10.5281/zenodo.23290449</a> · Software: <a href="https://doi.org/10.5281/zenodo.23290219">doi.org/10.5281/zenodo.23290219</a>')

# One representative scenario per outcome: (label shown in the log, row filter)
PICKS = [
    ("OK with several IBRs", lambda r: r["label"] == "OK" and r["n_ibr"] in ("3", "4")),
    ("Sustained overvoltage (1.1-1.2 pu)", lambda r: r["status"] == "completed" and _only_band(r, "(1.1, 1.2]")),
    ("Overvoltage above 1.2 pu", lambda r: r["status"] == "completed" and _only_band(r, "(1.2, 9.99)")),
    ("Angle instability", lambda r: r["status"] == "stopped_angle" and r["n_ibr"] in ("2", "3")),
    ("Frequency violation", lambda r: r.get("flag_frequency_violation") == "1"
     and r["status"] == "completed"),
    ("Unsolvable bolted fault, confirmed", lambda r: r["resolution"] == "confirmed"),
    ("Unsolvable bolted fault, unresolved", lambda r: r["resolution"] == "unresolved"),
]


def _only_band(r, band):
    parts = [p for p in r["reasons"].split(" | ") if p.startswith("ride_through")]
    return bool(parts) and all(band in p for p in parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dataset", help="dataset.csv from 'ibr-faultscreen dataset'")
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo" / "index.html"))
    ap.add_argument("--seed", type=int, default=2026, help="seed the dataset was made with")
    a = ap.parse_args(argv)

    import andes
    andes.config_logger(stream_level=50)
    logging.disable(logging.WARNING)
    from ibr_faultscreen.criteria import default_criteria
    from ibr_faultscreen.dataset import SamplingPlan, file_sha256, sample_scenarios
    from ibr_faultscreen.label import label
    from ibr_faultscreen.model import (evaluate_models, load_table, load_unknown,
                                       solver_failure_rates, train_final, write_model_card)
    from ibr_faultscreen.plots import trace_payload
    from ibr_faultscreen.report import build_report
    from ibr_faultscreen.simulate import simulate

    with open(a.dataset, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    n = len(rows)
    settings = default_criteria()
    scenarios = {s.scenario_id: s for s in sample_scenarios(SamplingPlan(n=n, seed=a.seed))}

    traces = {}
    for name, keep in PICKS:
        for r in sorted((r for r in rows if keep(r)), key=lambda r: r["scenario_id"]):
            sc = scenarios.get(r["scenario_id"])
            if sc is None:
                continue
            res = simulate(sc)
            lab = label(res, settings)
            got = ("RISKY" if lab.risky else "OK", lab.resolution or "")
            if got == (r["label"], r["resolution"]):       # same outcome as in the dataset
                traces[sc.scenario_id] = trace_payload(res, lab, settings)
                print(f"{name}: {sc.scenario_id}")
                break
        else:
            print(f"{name}: no matching scenario")

    print("Training the screening model for the model card...")
    df = load_table(a.dataset)
    rep = evaluate_models(df, unknown=load_unknown(a.dataset))
    bundle = train_final(df, report=rep, dataset_sha=file_sha256(a.dataset),
                         solver_rates=solver_failure_rates(a.dataset))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    card = write_model_card(bundle, out.with_name("model-card.md")).read_text(encoding="utf-8")
    out.with_name("model-card.md").unlink()

    build_report(rows, out, title=f"IBR-FaultScreen demo: IEEE 39-bus, {n:,} fault scenarios",
                 case="ieee39", criteria_source="built-in PRC-029-1 file (illustrative "
                 "non-standard limits)", model_card_md=card, traces=traces,
                 dataset_note=("Demo on the IEEE 39-bus test system with generic IBR "
                               "parameters and illustrative angle, recovery and frequency "
                               "limits. Not a real system."))
    html = out.read_text(encoding="utf-8")
    from demo_explorer import inject
    from ibr_faultscreen.case import read_case
    from ibr_faultscreen.report import network_layout
    layout, edges, _ = network_layout(read_case("ieee39"), "ieee39")
    html = inject(html, layout, edges)
    if not (out.parent / "explorer-data.js").exists():
        print("Note: explorer-data.js not found next to the page; the explorer and quiz stay hidden "
              "until tools/explorer_data.py writes it.")
    html = re.sub(r"(<div class=\"ver\">IBR-FaultScreen v[^<]*<br>)[^<]*", r"\1demo", html)
    html = html.replace("Not a real system.</p>", "Not a real system." + DOI_LINKS + "</p>", 1)
    out.write_text(html, encoding="utf-8")
    print(f"Demo: {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
