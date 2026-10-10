"""Milder-fault check: bolted faults that cannot be solved are rerun at milder faults."""

from ibr_faultscreen.criteria import default_criteria
from ibr_faultscreen.label import label
from ibr_faultscreen.scenario import FaultSpec, IBRSpec, Scenario
from ibr_faultscreen.simulate import simulate

CRIT = default_criteria()


# A bolted fault at bus 6 (no IBRs, 0.1474 s, load x0.9517; dataset scenario
# S04973) that ANDES 2.0.0 cannot solve at any step, while the 0.001 pu rerun is OK.
# (The bus 19 cases used before 2026-10-10 now solve with the one-cycle step.)
UNSOLVABLE = Scenario("u", load_scale=0.9517, fault=FaultSpec(6, clear_time=0.1474), t_end=3)


def test_non_physical_answer_is_retried_at_finer_step():
    # x = 0.001 pu at bus 19 with a two-cycle step: ANDES reaches a non-physical
    # answer (buses held at 0 V on Linux, a negative voltage on Windows). The
    # finer-step retry must solve it instead of reporting a failure.
    r = simulate(Scenario("d", fault=FaultSpec(19, clear_time=0.05, x_pu=1e-3), t_end=3),
                 step_s=1 / 30, bracket=False)
    assert r.status == "completed" and "retried with step" in r.message
    assert r.traj.v.min() > -1e-3


def test_unresolved_when_milder_fault_is_ok():
    r = simulate(UNSOLVABLE)
    assert r.status == "sim_fail"
    assert [d.scenario.fault.x_pu for d in r.diagnostics] == [1e-3]
    lab = label(r, CRIT)
    assert lab.risky and lab.resolution == "unresolved"
    assert lab.summary()["resolution"] == "unresolved"
    assert r.solved_diagnostic().fault_residual_pu > 0.03           # not bolted


def test_confirmed_when_milder_fault_is_risky():
    r = simulate(Scenario("c", ibrs=(IBRSpec(2),), fault=FaultSpec(6, clear_time=0.25), t_end=4))
    assert r.status == "sim_fail" and r.solved_diagnostic() is not None
    lab = label(r, CRIT)
    assert lab.risky and lab.resolution == "confirmed"
    assert any("bolted run failed" in x for x in lab.reasons)


def test_solved_bolted_fault_has_no_diagnostics():
    r = simulate(Scenario("ok", fault=FaultSpec(16, clear_time=0.1), t_end=3))
    assert r.status == "completed" and r.diagnostics == []
    assert r.fault_residual_pu < 0.02                                # truly bolted
    assert label(r, CRIT).resolution is None


def test_bracketing_can_be_switched_off():
    r = simulate(UNSOLVABLE, bracket=False)
    assert r.status == "sim_fail" and r.diagnostics == []
