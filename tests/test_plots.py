"""Plot smoke tests."""

from ibr_faultscreen.criteria import default_criteria
from ibr_faultscreen.label import label
from ibr_faultscreen.plots import plot_scenario
from ibr_faultscreen.scenario import FaultSpec, IBRSpec, Scenario
from ibr_faultscreen.simulate import SimResult, simulate

CRIT = default_criteria()


def test_plot_written(tmp_path):
    r = simulate(Scenario("p", ibrs=(IBRSpec(2),), fault=FaultSpec(6, clear_time=0.1), t_end=4))
    out = plot_scenario(r, label(r, CRIT), CRIT, tmp_path / "p.png")
    assert out.exists() and out.stat().st_size > 20_000


def test_plot_without_trajectories(tmp_path):
    r = SimResult(Scenario("f"), "pf_fail", message="power flow did not converge")
    out = plot_scenario(r, label(r, CRIT), CRIT, tmp_path / "f.png")
    assert out.exists()


def test_trace_payload_matches_trajectories():
    import json

    from ibr_faultscreen.plots import trace_payload
    r = simulate(Scenario("p", ibrs=(IBRSpec(2),), fault=FaultSpec(6, clear_time=0.1), t_end=3))
    d = trace_payload(r, label(r, CRIT), CRIT)
    json.dumps(d, allow_nan=False)                       # embeddable in the report
    assert [p["key"] for p in d["panels"]] == ["v", "a", "f", "i"]
    n = len(d["t"])
    for p in d["panels"]:
        for s in p.get("series", []):
            assert len(s["y"]) == n
        if p.get("band"):
            assert all(lo <= hi for lo, hi in zip(*p["band"]) if lo is not None and hi is not None)
    v = d["panels"][0]
    assert v["series"][0]["name"] == "faulted bus 6"
    assert min(x for x in v["series"][0]["y"] if x is not None) < 0.1     # bolted fault dips


def test_trace_payload_without_trajectories():
    from ibr_faultscreen.plots import trace_payload
    r = SimResult(Scenario("f"), "pf_fail", message="power flow did not converge")
    d = trace_payload(r, label(r, CRIT), CRIT)
    assert d["panels"] is None and d["risky"] and "power flow" in d["message"]
