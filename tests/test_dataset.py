"""Dataset generation tests: sampling, determinism, resume, timeout."""

import csv

from ibr_faultscreen.criteria import default_criteria
from ibr_faultscreen.dataset import SamplingPlan, file_sha256, run_dataset, sample_scenarios

PLAN = SamplingPlan(n=4, seed=7, t_end=3.0)


def test_sampling_is_reproducible_and_spread():
    a, b = sample_scenarios(PLAN), sample_scenarios(PLAN)
    assert a == b
    c = sample_scenarios(SamplingPlan(n=4, seed=8, t_end=3.0))
    assert a != c
    big = sample_scenarios(SamplingPlan(n=58, seed=1))
    assert len({s.fault.bus for s in big}) == 29           # every fault bus used
    cts = sorted(s.fault.clear_time for s in big)
    assert cts[0] < 0.06 and cts[-1] > 0.29                 # Latin hypercube covers the range


def test_determinism_and_resume(tmp_path, capsys):
    crit = default_criteria()
    r1 = run_dataset(sample_scenarios(PLAN), crit, tmp_path / "a", workers=2, plan=PLAN)
    r2 = run_dataset(sample_scenarios(PLAN), crit, tmp_path / "b", workers=1, plan=PLAN)
    assert r1.dataset_csv and r2.dataset_csv
    assert file_sha256(r1.dataset_csv) == file_sha256(r2.dataset_csv)     # determinism
    r3 = run_dataset(sample_scenarios(PLAN), crit, tmp_path / "a", workers=2, plan=PLAN)
    assert r3.n_skipped == 4 and r3.n_done == 0                           # resume
    assert (tmp_path / "a" / "data-card.md").exists()
    rows = list(csv.DictReader(open(r1.dataset_csv, encoding="utf-8")))
    assert len(rows) == 4 and all(r["label"] in ("OK", "RISKY") for r in rows)


def test_timeout_is_recorded(tmp_path):
    plan = SamplingPlan(n=2, seed=3, t_end=3.0)
    r = run_dataset(sample_scenarios(plan), default_criteria(), tmp_path, workers=1,
                    timeout_s=0.5, plan=plan)
    rows = list(csv.DictReader(open(r.dataset_csv, encoding="utf-8")))
    assert r.n_timeout == 2 and {x["status"] for x in rows} == {"timeout"}
    assert all(x["label"] == "RISKY" for x in rows)       # unknown outcome is never OK


def test_data_card_handles_a_timed_out_row(tmp_path):
    # A timed-out scenario has no features and no ANDES version in its row.
    import csv

    from ibr_faultscreen.datacard import write_data_card
    cols = ["scenario_id", "status", "label", "resolution", "ibrs", "n_ibr", "andes_version",
            "fault_bus", "reasons"]
    rows = [["case-a", "completed", "OK", "", "2:solar", "1", "2.0.0", "16", ""],
            ["case-b", "timeout", "RISKY", "", "1:solar;6:bess", "", "", "17", ""],
            ["case-c", "sim_fail", "RISKY", "unresolved", "", "0", "2.0.0", "19", ""]]
    path = tmp_path / "dataset.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([cols, *rows])
    card = write_data_card(path, tmp_path / "card.md").read_text(encoding="utf-8")
    assert "with ANDES 2.0.0." in card
    assert "Scenarios by number of IBRs: 0: 1, 1: 1, 2: 1" in card
    assert "known outcome): 1; unknown outcome (unresolved failures, timeouts, errors): 2" in card
