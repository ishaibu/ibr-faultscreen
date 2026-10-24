"""Running a given scenario list (`dataset --scenarios`) and the explorer list."""

import csv
import importlib.util
from pathlib import Path

from ibr_faultscreen.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _explorer_module():
    spec = importlib.util.spec_from_file_location("explorer_scenarios",
                                                  ROOT / "tools" / "explorer_scenarios.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_explorer_list_covers_every_combination(tmp_path):
    mod = _explorer_module()
    out = tmp_path / "explorer.csv"
    mod.main(["--out", str(out)])
    with open(out, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 256 * 29 * 5 == 37120
    assert len({r["scenario_id"] for r in rows}) == len(rows)
    assert {r["load_scale"] for r in rows} == {"1.0"}
    assert {round(float(r["clear_time_s"]) * 60) for r in rows} == {3, 5, 6, 9, 12}
    assert max(len([x for x in r["ibrs"].split(";") if x]) for r in rows) == 4
    assert not any("10:" in r["ibrs"] for r in rows)          # the slack is never replaced


def test_dataset_runs_a_scenario_list(tmp_path):
    lst = tmp_path / "list.csv"
    lst.write_text("scenario_id,ibrs,fault_bus,clear_time_s,t_end_s\n"
                   "L1,,16,0.1,2.0\nL2,2:solar,26,0.1,2.0\n", encoding="utf-8")
    out = tmp_path / "run"
    assert main(["dataset", "--scenarios", str(lst), "--workers", "2", "--out", str(out)]) == 0
    with open(out / "dataset.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["scenario_id"] for r in rows] == ["L1", "L2"]
    assert rows[1]["ibrs"] == "2:solar"
    card = (out / "data-card.md").read_text(encoding="utf-8")
    assert "listed in the file `list.csv`" in card and "Latin hypercube" not in card


def test_duplicate_ids_are_refused(tmp_path):
    import pytest
    lst = tmp_path / "dup.csv"
    lst.write_text("scenario_id,fault_bus\nA,16\nA,26\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be unique"):
        main(["dataset", "--scenarios", str(lst), "--out", str(tmp_path / "x")])
