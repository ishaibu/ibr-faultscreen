"""Run a list of scenarios and write review material for the hand review.

Usage (from the project folder, with .venv active):

    python examples/run_review.py
    python examples/run_review.py SCENARIOS.yaml CRITERIA.yaml OUTPUT_FOLDER

Writes OUTPUT_FOLDER/<scenario_id>.png for each scenario and
OUTPUT_FOLDER/review.csv with the labels and empty columns for your verdict.
"""

import csv
import sys
import time
from pathlib import Path

import andes

from ibr_faultscreen.criteria import default_criteria_path, load_criteria
from ibr_faultscreen.label import label
from ibr_faultscreen.plots import plot_scenario
from ibr_faultscreen.scenario import load_scenarios
from ibr_faultscreen.simulate import simulate

HERE = Path(__file__).parent


def main(scen_file=HERE / "review_scenarios.yaml", crit_file=None,
         out_dir="review_output"):
    andes.config_logger(stream_level=50)          # keep ANDES quiet; outcomes are in the table
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n")          # keep this working folder out of Git
    settings = load_criteria(crit_file or default_criteria_path())
    scenarios = load_scenarios(scen_file)
    rows = []
    start = time.perf_counter()
    for n, sc in enumerate(scenarios, 1):
        result = simulate(sc)
        labels = label(result, settings)
        png = plot_scenario(result, labels, settings, out / f"{sc.scenario_id}.png")
        row = {**result.summary(), **labels.summary(), "plot": png.name,
               "shai_agrees_Y_N": "", "shai_notes": ""}
        rows.append(row)
        print(f"[{n:2d}/{len(scenarios)}] {sc.scenario_id:24s} {row['label']:5s} "
              f"{result.status:13s} {result.runtime_s:5.1f} s")
    with open(out / "review.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    risky = sum(r["label"] == "RISKY" for r in rows)
    print(f"\n{len(rows)} scenarios, {risky} RISKY, {time.perf_counter() - start:.0f} s total.")
    print(f"Plots and review.csv are in: {out.resolve()}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
