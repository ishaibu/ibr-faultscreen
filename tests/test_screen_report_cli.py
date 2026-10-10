"""Screen-then-verify, HTML report, PSS/E import and command-line tests."""

import os

import andes
import numpy as np
import pytest

from ibr_faultscreen.check import check_case, dyr_models, supported_dyr_models
from ibr_faultscreen.cli import main
from ibr_faultscreen.criteria import default_criteria
from ibr_faultscreen.features import FEATURES
from ibr_faultscreen.model import ModelBundle
from ibr_faultscreen.report import build_report
from ibr_faultscreen.scenario import FaultSpec, IBRSpec, Scenario
from ibr_faultscreen.screen import all_risky_verified, screen
from ibr_faultscreen.simulate import simulate

RAW = os.path.join(os.path.dirname(andes.get_case("ieee14/ieee14.raw")), "ieee14.raw")


class ClearTimeModel:
    """Stand-in classifier: P(RISKY) rises with clearing time (known, simple)."""

    def predict_proba(self, X):
        p = np.clip((X[:, FEATURES.index("clear_time_s")] - 0.05) / 0.3, 0, 1)
        return np.column_stack([1 - p, p])


def bundle():
    return ModelBundle("stand-in", ClearTimeModel(), threshold=0.5,
                       medians={f: 0.1 if f == "clear_time_s" else 1.0 for f in FEATURES})


def test_screen_then_verify():
    cands = [Scenario("short", fault=FaultSpec(16, clear_time=0.06), t_end=3),
             Scenario("long", fault=FaultSpec(16, clear_time=0.4), t_end=3),
             Scenario("mid", ibrs=(IBRSpec(2),), fault=FaultSpec(26, clear_time=0.2), t_end=3)]
    rows = screen(cands, bundle(), default_criteria(), borderline=0.1)
    assert [r.scenario.scenario_id for r in rows] == ["long", "mid", "short"]   # ranked
    assert all_risky_verified(rows)
    long_ = rows[0]
    assert long_.source == "simulated" and long_.verdict.startswith("RISKY")
    assert long_.why and long_.why[0][0] == "clear_time_s"
    short = rows[-1]
    assert short.source == "predicted" and short.verdict == "OK"                # not simulated


def test_budget_never_turns_prediction_into_risky_verdict():
    cands = [Scenario("long", fault=FaultSpec(16, clear_time=0.4), t_end=3)]
    rows = screen(cands, bundle(), default_criteria(), max_verify=0)
    assert rows[0].verdict == "predicted RISKY, not yet verified" and all_risky_verified(rows)


def test_screen_always_verifies_solver_hard_buses():
    b = bundle()
    b.meta = {"solver_rates": {16: {"n": 10, "unknown": 5, "rate": 0.5},
                               4: {"n": 10, "unknown": 0, "rate": 0.0}}, "solver_hard_rate": 0.2}
    cands = [Scenario("hard", fault=FaultSpec(16, clear_time=0.06), t_end=2),     # predicted OK
             Scenario("easy", fault=FaultSpec(4, clear_time=0.06), t_end=2),      # predicted OK
             Scenario("new", fault=FaultSpec(26, clear_time=0.06), t_end=2)]      # bus not in data
    rows = {r.scenario.scenario_id: r for r in screen(cands, b, default_criteria(), borderline=0)}
    assert rows["hard"].source == "simulated" and "solver-hard" in rows["hard"].verify_reason
    assert rows["new"].source == "simulated" and "not in the training" in rows["new"].verify_reason
    assert rows["easy"].source == "predicted" and rows["easy"].verify_reason == ""
    assert all_risky_verified(list(rows.values()))


def test_report_html(tmp_path):
    rows = []
    for sid, ct in (("a", 0.08), ("b", 0.4)):
        r = simulate(Scenario(sid, ibrs=(IBRSpec(2),), fault=FaultSpec(16, clear_time=ct), t_end=3))
        from ibr_faultscreen.label import label
        from ibr_faultscreen.features import compute_features
        lab = label(r, default_criteria())
        rows.append({**r.summary(), **lab.summary(), **compute_features(r.scenario)})
    out = build_report(rows, tmp_path / "r.html", criteria_source="test")
    html = out.read_text(encoding="utf-8")
    for part in ("Summary", "Network map", "Ranked scenarios", "Risk versus IBR share",
                 "Limits", 'id="nibr"', "data:text/csv;base64"):
        assert part in html
    assert "1 of 2 scenarios" in html


def test_report_interactive_trajectories(tmp_path):
    import json

    from ibr_faultscreen.features import compute_features
    from ibr_faultscreen.label import label
    from ibr_faultscreen.plots import plot_scenario, trace_payload
    crit = default_criteria()
    rows, traces, figs = [], {}, {}
    for sid, ct in (("a_1", 0.08), ("b_2", 0.4)):
        r = simulate(Scenario(sid, ibrs=(IBRSpec(2),), fault=FaultSpec(16, clear_time=ct), t_end=3))
        lab = label(r, crit)
        rows.append({**r.summary(), **lab.summary(), **compute_features(r.scenario)})
        traces[sid] = trace_payload(r, lab, crit)
        figs[sid] = plot_scenario(r, lab, crit, tmp_path / f"{sid}.png")
    html = build_report(rows, tmp_path / "r.html", criteria_source="test", figures=figs,
                        traces=traces).read_text(encoding="utf-8")
    assert 'id="trdata"' in html.replace("'", '"') and "data:image/png" not in html
    assert 'class="sclink" data-sid="b_2"' in html and "a_<wbr>1" in html
    start = html.index("<script id='trdata' type='application/json'>") + 44
    data = json.loads(html[start:html.index("</script>", start)])
    assert set(data) == {"a_1", "b_2"}
    png = html[html.index("<script id='trpng'"):]
    assert '"b_2":"b_2.png"' in png                      # PNG offered as a download
    mstart = html.index("<script id=\"mapdata\" type=\"application/json\">") + 45
    m = json.loads(html[mstart:html.index("</script>", mstart)])
    assert m["max"] == 1 and m["tot"][1] == [1, 2]


def test_psse_import_and_check():
    rep = check_case(RAW)
    assert rep.ok and rep.n_bus == 14 and not rep.unsupported
    assert [g for g, _b, _m in rep.ibr_capable] == [2, 3, 4, 5]
    assert "GENROU" in dyr_models(RAW.replace(".raw", ".dyr"))
    assert {"GENROU", "REGCA1", "REECA1", "REPCA1"} <= supported_dyr_models()
    r = simulate(Scenario("p", case=RAW, ibrs=(IBRSpec(2),), fault=FaultSpec(4, clear_time=0.1),
                          t_end=3))
    assert r.status == "completed" and r.traj.v.shape[1] == 14


def test_cli_check_and_help(capsys):
    assert main(["check", "ieee39"]) == 0
    assert "ready for screening" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--help"])


def test_cli_cct(tmp_path, capsys):
    out = tmp_path / "cct.csv"
    assert main(["cct", "--bus", "16", "--x", "0.001", "--t-min", "0.2", "--t-max", "0.3",
                 "--out", str(out)]) == 0
    assert "CCT" in capsys.readouterr().out and out.exists()


def test_output_folders_ignore_themselves_but_not_existing_folders(tmp_path, monkeypatch):
    from ibr_faultscreen.cli import make_output_dir
    new = make_output_dir(tmp_path / "results")
    assert (new / ".gitignore").read_text(encoding="utf-8").strip().endswith("*")
    monkeypatch.chdir(tmp_path)
    make_output_dir(".")                          # existing folder (e.g. project root)
    assert not (tmp_path / ".gitignore").exists()
