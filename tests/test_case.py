"""Case building tests: IBR conversion, flat runs and the IBR current limit."""

import numpy as np
import pytest

from ibr_faultscreen.case import (build_scenario, build_system, convert_to_ibr, read_case,
                                  remove_disabled_events, run_tds)
from ibr_faultscreen.scenario import FaultSpec, IBRSpec, Scenario


def series(system, var):
    """Time series of an ANDES variable (states live in x, algebraics in y)."""
    arr = system.dae.ts.x if var.v_code == "x" else system.dae.ts.y
    return arr[:, var.a]


# ------------------------------------------------------------- flat run
@pytest.mark.parametrize("ibrs", [
    (),
    (IBRSpec(2),),
    (IBRSpec(2, "bess"),),
    (IBRSpec(1), IBRSpec(6), IBRSpec(9)),
], ids=["no-ibr", "solar-gen2", "bess-gen2", "three-ibrs"])
def test_flat_run_holds_steady_state(ibrs):
    system, _ = build_scenario(Scenario("flat", ibrs=ibrs, t_end=20))
    assert system.PFlow.converged
    assert run_tds(system, 20) == 0
    v = series(system, system.Bus.v)
    w = series(system, system.GENROU.omega)
    assert np.abs(v - v[0]).max() <= 1e-3
    assert np.abs(w - 1.0).max() <= 1e-3


def test_replaced_machine_and_controls_are_removed():
    sheets = read_case()
    change = convert_to_ibr(sheets, IBRSpec(2))
    assert change["removed"][0] == "GENROU_2"
    assert len(change["removed"]) == 4          # machine + governor + exciter + stabilizer
    assert "GENROU_2" not in sheets["GENROU"]["idx"].tolist()
    assert sheets["REGCA1"]["gen"].tolist() == [2]


def test_disabled_event_removed():
    sheets = read_case()
    assert remove_disabled_events(sheets) == ["Toggler_1"]
    assert "Toggler" not in sheets


@pytest.mark.parametrize("t_end", [3, 5, 10, 12, 15])
def test_runs_of_any_length_end_cleanly(t_end):
    # These end times returned exit code 1 without END_PAD_S.
    sheets = read_case()
    remove_disabled_events(sheets)
    system = build_system(sheets)
    assert run_tds(system, t_end) == 0
    assert system.dae.t >= t_end


# ------------------------------------------------------------- faults and the IBR current limit
@pytest.mark.parametrize("bus", [31, 16], ids=["at-ibr-bus", "remote"])
def test_fault_runs_and_ibr_current_stays_within_limit(bus):
    scenario = Scenario("f", ibrs=(IBRSpec(2, imax_pu=1.1),),
                        fault=FaultSpec(bus=bus, clear_time=0.1), t_end=5)
    system, _ = build_scenario(scenario)
    assert run_tds(system, 5) == 0
    current = np.hypot(series(system, system.REGCA1.Ipout), series(system, system.REGCA1.Iqout_x))
    assert current.max() <= system.REECA1.Imax.v.max() * (1 + 1e-3)


def test_charging_battery_rejected():
    sheets = read_case()
    sheets["PV"]["p0"] = sheets["PV"]["p0"].astype(float)
    sheets["PV"].loc[sheets["PV"]["idx"] == 2, "p0"] = -3.0
    with pytest.raises(ValueError, match="charging battery"):
        convert_to_ibr(sheets, IBRSpec(2, "bess"))


def test_unknown_generator_rejected():
    with pytest.raises(ValueError, match="no synchronous machine"):
        convert_to_ibr(read_case(), IBRSpec(99))


def test_unknown_formats_and_missing_files():
    with pytest.raises(ValueError, match="PSS/E"):
        read_case("mycase.sav")
    with pytest.raises(FileNotFoundError):
        read_case("missing_case.raw")


def test_ibr_profiles():
    from ibr_faultscreen.case import DEFAULT_IBR_PROFILE, ibr_profiles
    profiles = ibr_profiles()
    assert {"andes_example", "wecc_mvwg_2019"} <= set(profiles)
    assert all("source" in p for p in profiles.values())
    with pytest.raises(ValueError, match="unknown IBR profile"):
        convert_to_ibr(read_case(), IBRSpec(2, profile="no_such_profile"))
    sheets = read_case()
    change = convert_to_ibr(sheets, IBRSpec(2, profile="wecc_mvwg_2019"))
    assert change["profile"] == "wecc_mvwg_2019"
    assert sheets["REGCA1"]["Tg"].iloc[0] == 0.02 and sheets["REECA1"]["Kqv"].iloc[0] == 2.0
    assert convert_to_ibr(read_case(), IBRSpec(2))["profile"] == DEFAULT_IBR_PROFILE


def test_wecc_profile_holds_steady_state():
    system, _ = build_scenario(Scenario("flat", ibrs=(IBRSpec(2, profile="wecc_mvwg_2019"),
                                                     IBRSpec(6, profile="wecc_mvwg_2019")),
                                        t_end=10))
    assert run_tds(system, 10) == 0
    v = series(system, system.Bus.v)
    assert np.abs(v - v[0]).max() <= 1e-3


@pytest.mark.parametrize("profile", ["andes_example", "wecc_mvwg_2019"])
def test_every_generator_initializes_as_an_ibr(profile):
    # Release check: generator 8 failed with the ANDES example's Iolim = 0
    # (the IBR could not absorb reactive power). Every machine except the slack
    # must convert and initialize cleanly.
    for gen in range(1, 10):
        system, _ = build_scenario(Scenario("i", ibrs=(IBRSpec(gen, profile=profile),)))
        system.TDS.config.no_tqdm = 1
        system.TDS.init()
        assert system.TDS.test_ok is not False, f"generator {gen} failed to initialize"
