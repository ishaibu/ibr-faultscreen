"""Scenario schema tests."""

import pytest

from ibr_faultscreen.scenario import FaultSpec, IBRSpec, Scenario, load_scenarios


def test_valid_scenario():
    s = Scenario("s1", ibrs=(IBRSpec(2), IBRSpec(6, "bess")), fault=FaultSpec(16, clear_time=0.08))
    assert s.case == "ieee39" and s.fault.clear_time == 0.08


@pytest.mark.parametrize("make", [
    lambda: IBRSpec(2, kind="wind"),
    lambda: IBRSpec(2, imax_pu=0),
    lambda: FaultSpec(16, clear_time=0),
    lambda: FaultSpec(16, clear_time=-0.1),
    lambda: FaultSpec(16, r_pu=0, x_pu=0),
    lambda: FaultSpec(16, x_pu=-1),
    lambda: Scenario(""),
    lambda: Scenario("s", load_scale=0),
    lambda: Scenario("s", ibrs=(IBRSpec(2), IBRSpec(2))),
    lambda: Scenario("s", fault=FaultSpec(16, t_start=1.0, clear_time=0.2), t_end=1.1),
])
def test_bad_inputs_rejected(make):
    with pytest.raises(ValueError):
        make()


def test_load_yaml(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "- scenario_id: base\n"
        "- scenario_id: f16\n"
        "  load_scale: 1.05\n"
        "  ibrs: [{gen: 2, kind: solar}, {gen: 6, kind: bess, imax_pu: 1.2}]\n"
        "  fault: {bus: 16, clear_time: 0.1}\n")
    base, f16 = load_scenarios(p)
    assert base.fault is None and f16.ibrs[1].imax_pu == 1.2 and f16.fault.bus == 16


def test_load_csv(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("scenario_id,load_scale,ibrs,fault_bus,clear_time_s\n"
                 "a,1.0,2:solar;6:bess,16,0.1\n"
                 "b,0.9,,,\n")
    a, b = load_scenarios(p)
    assert [i.kind for i in a.ibrs] == ["solar", "bess"] and a.fault.bus == 16
    assert b.ibrs == () and b.fault is None


def test_csv_errors_name_the_row(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("scenario_id,clear_time_s,fault_bus\nok,0.1,16\nbad,abc,16\n")
    with pytest.raises(ValueError, match="scenario 2"):
        load_scenarios(p)


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("scenario_id\nx\nx\n")
    with pytest.raises(ValueError, match="unique"):
        load_scenarios(p)


def test_unsupported_file_type(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("")
    with pytest.raises(ValueError):
        load_scenarios(p)
