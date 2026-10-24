"""Equal-area view (single-machine equivalent built from simulated trajectories).

1. Single machine against an infinite bus (ANDES SMIB case, classical model, no
   damping): the result must match the textbook equal-area calculation already
   used to verify the critical clearing time (test_smib_clearing_time.py).
2. IEEE 39-bus: in a stable case the two areas balance (energy conservation).
3. Cases where the view does not apply say so instead of drawing something.
"""

import math

import andes
import numpy as np
import pandas as pd
import pytest
from scipy.optimize import brentq

from ibr_faultscreen.case import build_system, run_tds
from ibr_faultscreen.equal_area import equal_area, short_names
from ibr_faultscreen.scenario import FaultSpec, Scenario
from ibr_faultscreen.simulate import SimResult, _collect, simulate

from test_smib_clearing_time import H, OMEGA_S, P_M, X_LINES, XD1, equal_area_cct


def _smib(clear_time):
    sheets = pd.read_excel(andes.get_case("smib/SMIB.xlsx"), sheet_name=None)
    sheets.pop("Summary", None)
    sheets.pop("Fault", None)
    sheets["GENCLS"]["D"] = 0.0
    fault = FaultSpec(bus=3, t_start=1.0, clear_time=clear_time, x_pu=1e-6)
    system = build_system(sheets, fault)
    run_tds(system, 5)
    return SimResult(Scenario("smib", fault=fault), "completed", traj=_collect(system, []))


def _hand(clear_time):
    """Textbook values: accelerating area and the angle where the swing stops."""
    e, d0, _, _ = equal_area_cct()
    p_max = e / (XD1 + X_LINES)
    dc = d0 + OMEGA_S * P_M * clear_time ** 2 / (4 * H)        # Pe = 0 during the fault
    acc = P_M * (dc - d0)
    g = lambda dm: p_max * (math.cos(dc) - math.cos(dm)) - P_M * (dm - dc) - acc  # noqa: E731
    d_u = math.pi - math.asin(P_M / p_max)
    d_max = brentq(g, dc, d_u) if g(d_u) > 0 else None
    return acc, d_max


@pytest.mark.parametrize("clear_time", [0.10, 0.15, 0.17])
def test_smib_matches_textbook_when_stable(clear_time):
    ea = equal_area(_smib(clear_time))
    acc, d_max = _hand(clear_time)
    assert ea.applicable and ea.stable
    assert ea.leading == ["GENCLS_1"]
    assert ea.accelerating == pytest.approx(acc, rel=0.01)
    assert ea.decelerating == pytest.approx(ea.accelerating, rel=0.01)
    assert ea.delta_end_deg == pytest.approx(math.degrees(d_max), abs=1.0)


def test_smib_unstable_beyond_cct():
    *_, t_cr = equal_area_cct()                    # 0.179 s
    ea = equal_area(_smib(t_cr + 0.012))
    assert ea.applicable and not ea.stable
    assert ea.decelerating < ea.accelerating
    acc, d_max = _hand(t_cr + 0.012)
    assert d_max is None                           # the textbook also says unstable
    assert "lose step" in ea.text()


def test_ieee39_stable_case_areas_balance():
    r = simulate(Scenario("s", fault=FaultSpec(16, clear_time=0.10), t_end=3.0))
    ea = equal_area(r)
    assert r.status == "completed" and ea.applicable and ea.stable
    assert ea.accelerating > 0.5
    assert ea.decelerating == pytest.approx(ea.accelerating, rel=0.03)
    assert len(ea.leading) + len(ea.others) == 10
    assert not ea.multi_group                      # never flagged for a stable swing
    assert np.all(np.diff(ea.delta_deg[ea.t >= ea.t_clear]) > -1e-3)   # first swing only


def test_not_applicable_cases_say_why():
    assert "no fault" in equal_area(SimResult(Scenario("n"), "completed")).text()
    failed = SimResult(Scenario("f", fault=FaultSpec(16)), "sim_fail")
    assert "did not finish" in equal_area(failed).text()


def test_short_names():
    assert short_names(["GENROU_7", "GENCLS_1", "my_gen"]) == ["G7", "G1", "my_gen"]
