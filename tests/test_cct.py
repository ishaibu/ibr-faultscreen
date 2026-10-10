"""Critical clearing time tests on the IEEE 39-bus case.

Correction made during development: with a bolted fault at bus 16, runs cleared later
than about 0.19 s reach non-physical solutions in ANDES 2.0.0 (buses stuck at
0 V). They were first mistaken for instability (CCT 0.189 s). The bolted CCT
is now reported as a lower bound, with the milder-fault CCT as an upper estimate.
"""

import pytest

from ibr_faultscreen.cct import critical_clearing_time
from ibr_faultscreen.scenario import FaultSpec, Scenario

HALF_CYCLE = 0.5 / 60


@pytest.fixture(scope="module")
def cct_bolted():
    return critical_clearing_time(Scenario("c", fault=FaultSpec(16)))


def test_bolted_is_a_lower_bound(cct_bolted):
    r = cct_bolted
    assert r.status == "lower_bound" and r.cct_s is None and r.had_sim_fail
    assert r.stable_s == pytest.approx(0.185, abs=HALF_CYCLE)
    # milder fault (x = 0.001 pu): regression value, ANDES 2.0.0, model-specific
    assert r.upper_x_pu == 1e-3
    assert r.upper_s == pytest.approx(0.256, abs=HALF_CYCLE)
    assert r.upper_s > r.stable_s
    assert "at least 185 ms" in r.text()


def test_milder_fault_bracketed():
    r = critical_clearing_time(Scenario("m", fault=FaultSpec(16, x_pu=1e-3)), t_min_s=0.2,
                               t_max_s=0.3)
    assert r.status == "bracketed" and not r.had_sim_fail
    assert r.unstable_s - r.stable_s <= HALF_CYCLE
    assert r.cct_s == pytest.approx(0.256, abs=HALF_CYCLE)


def test_out_of_range_reported():
    r = critical_clearing_time(Scenario("c", fault=FaultSpec(16, x_pu=1e-3)), t_min_s=0.3,
                               t_max_s=0.5)
    assert r.status == "below_min" and r.cct_s is None
    r = critical_clearing_time(Scenario("c", fault=FaultSpec(16)), t_min_s=0.02, t_max_s=0.1)
    assert r.status == "above_max" and r.cct_s is None


def test_needs_a_fault():
    with pytest.raises(ValueError):
        critical_clearing_time(Scenario("nofault"))
