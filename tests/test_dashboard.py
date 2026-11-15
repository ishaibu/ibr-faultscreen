"""Dashboard tests: the page is built from the results file only, and its numbers
match the report's."""

import json
import re

from ibr_faultscreen.cli import main
from ibr_faultscreen.dashboard import (band_name, build_dashboard, dashboard_data, exposures,
                                       failure_modes)

RT = ("ride_through:PRC-029-1_Table2_voltage: GENROU_2 (bus 6) voltage in (1.2, 9.99) for "
      "0.017 s > 0 s (cumulative over 10 s); source: NERC")
ROWS = [
    {"scenario_id": "A", "label": "RISKY", "resolution": "", "fault_bus": "16", "n_ibr": "1",
     "ibrs": "2:solar", "clear_time_s": "0.1", "load_scale": "1.0", "ibr_share": "0.1",
     "scr_min": "3.2", "scr_by_ibr": "2@6:3.20", "rt_ratio_max": "inf", "reasons": RT},
    {"scenario_id": "B", "label": "RISKY", "resolution": "confirmed", "fault_bus": "6",
     "n_ibr": "0", "ibrs": "", "clear_time_s": "0.25", "load_scale": "1.0", "ibr_share": "0",
     "scr_min": "99", "rt_ratio_max": "",
     "reasons": "sim_fail: failed | bolted_fault: bolted run failed; milder fault x = 0.001 pu "
                "is already RISKY: angle_separation: max spread 200 deg > 180 deg"},
    {"scenario_id": "C", "label": "RISKY", "resolution": "unresolved", "fault_bus": "6",
     "n_ibr": "0", "ibrs": "", "clear_time_s": "0.15", "load_scale": "1.0", "ibr_share": "0",
     "scr_min": "99", "rt_ratio_max": "",
     "reasons": "sim_fail: failed | bolted_fault: bolted run failed; milder fault x = 0.001 pu "
                "is OK, so the bolted outcome is unknown"},
    {"scenario_id": "D", "label": "OK", "resolution": "", "fault_bus": "16", "n_ibr": "1",
     "ibrs": "2:solar", "clear_time_s": "0.05", "load_scale": "1.0", "ibr_share": "0.1",
     "scr_min": "3.2", "scr_by_ibr": "2@6:3.20", "rt_ratio_max": "0.85", "reasons": ""},
]


def test_reason_parsing():
    assert failure_modes(RT) == "r"
    assert failure_modes(ROWS[1]["reasons"], "confirmed") == "a"      # from the milder fault
    assert failure_modes(ROWS[2]["reasons"], "unresolved") == ""      # unknown, no mode
    (ibr, bus, q, band, t, lim), = exposures(RT)
    assert (ibr, bus, q, band, t, lim) == ("GENROU_2", 6, "voltage", "above 1.2 pu", 0.017, 0.0)
    assert band_name("[0.0, 0.1)") == "below 0.1 pu"
    assert band_name("(1.1, 1.2]") == "1.1–1.2 pu"


def test_data_classes_and_counts():
    d = dashboard_data(ROWS, criteria_source="test")
    cls = [r[6] for r in d["rows"]]
    assert cls == ["R", "C", "U", "O"]
    assert d["rows"][0][11] == 1e9 and d["rows"][3][11] == 0.85      # inf kept as "exposed"
    assert d["rtx"] == [[0, 6, "v", "above 1.2 pu", 0.017, 0.0, "GENROU_2"]]
    assert 6 in d["ibr_hv"] and d["n"] == 4
    assert d["rows"][1][8] is None or d["rows"][1][8] >= 99           # no IBR: no SCR shown


def test_page_is_self_contained(tmp_path):
    out = build_dashboard(ROWS, tmp_path / "d.html", title="T", criteria_source="test")
    html = out.read_text(encoding="utf-8")
    assert "<script src" not in html and "<link" not in html          # works offline
    data = json.loads(re.search(r'<script id="dashdata" type="application/json">(.*?)</script>',
                                html, re.S).group(1))
    assert data["n"] == 4 and data["title"] == "T"
    for view in ("v-overview", "v-map", "v-scen", "v-modes", "v-rt", "v-traj", "v-eac", "v-method"):
        assert f'id="{view}"' in html
    assert "--side:#12304f" in html                                  # navy sidebar


def test_screening_rows(tmp_path):
    rows = [{"scenario_id": "P1", "verdict": "OK", "source": "predicted", "p_risky": "0.01",
             "fault_bus": "4", "n_ibr": "0", "clear_time_s": "0.05", "reasons": "",
             "why": "clear_time_s (+0.10)"},
            {"scenario_id": "P2", "verdict": "predicted RISKY, not yet verified",
             "source": "predicted", "p_risky": "0.9", "fault_bus": "4", "n_ibr": "0",
             "clear_time_s": "0.3", "reasons": ""},
            {"scenario_id": "P3", "verdict": "needs simulation (borderline), not yet verified",
             "source": "predicted", "p_risky": "0.4", "fault_bus": "4", "n_ibr": "0",
             "clear_time_s": "0.2", "reasons": ""}]
    d = dashboard_data(rows)
    assert [r[6] for r in d["rows"]] == ["P", "Q", "N"]
    assert all(r[7] == "" for r in d["rows"])                         # 'why' is not a reason


def test_cli_dashboard(tmp_path, capsys):
    import csv
    src = tmp_path / "results.csv"
    with open(src, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(dict.fromkeys(k for r in ROWS for k in r)))
        w.writeheader()
        w.writerows(ROWS)
    out = tmp_path / "o" / "dash.html"
    assert main(["dashboard", str(src), "--out", str(out)]) == 0
    assert out.exists() and "Dashboard:" in capsys.readouterr().out


def test_trajectory_player_adapted():
    # The dashboard reuses the report's player with small changes; this fails
    # loudly if the report's player changes so that they no longer apply.
    from ibr_faultscreen.dashboard import _traj_js
    js = _traj_js()
    assert "eacroot" in js and "trRedraw" in js and "min-width: 1500px" in js
