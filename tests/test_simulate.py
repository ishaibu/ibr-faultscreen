"""Simulation wrapper tests."""

import pytest

import ibr_faultscreen.simulate as sim
from ibr_faultscreen.scenario import FaultSpec, IBRSpec, Scenario
from ibr_faultscreen.simulate import simulate


def test_completed_run_collects_trajectories():
    r = simulate(Scenario("s", ibrs=(IBRSpec(2),), fault=FaultSpec(16), t_end=5))
    assert r.status == "completed" and r.ok and r.t_reached >= 5
    tr = r.traj
    n = tr.t.size
    assert tr.v.shape == (n, 39)
    assert tr.delta.shape == (n, 9)              # 10 machines minus the replaced one
    assert tr.ibr_hv_bus == [6]                  # gen 2: bus 31 -> step-up to bus 6
    f6 = tr.bus_f(6)                             # frequency at the high side
    assert abs(f6[0] - 60.0) < 1e-9
    # by the end, the bus frequency agrees with the machines' average speed
    assert abs(f6[-1] - 60.0 * tr.omega[-1].mean()) < 0.02
    assert tr.ibr_imax.shape == (1,)
    assert r.summary()["ibrs"] == "2:solar@31"


def test_unstable_run_stops_on_angle():
    r = simulate(Scenario("u", fault=FaultSpec(16, clear_time=0.4), t_end=5))
    assert r.status == "stopped_angle" and r.t_reached < 5
    assert r.traj is not None


def test_power_flow_failure_is_an_outcome_not_a_crash():
    r = simulate(Scenario("pf", load_scale=3.0))
    assert r.status == "pf_fail" and r.traj is None and not r.ok


def test_numerical_exception_is_captured(monkeypatch):
    real = sim.build_scenario

    def broken(scenario):
        system, changes = real(scenario)

        def boom():
            raise FloatingPointError("singular matrix")
        system.TDS.run = boom
        return system, changes

    monkeypatch.setattr(sim, "build_scenario", broken)
    r = simulate(Scenario("x", fault=FaultSpec(16), t_end=2))
    assert r.status == "sim_fail" and "singular matrix" in r.message
    assert r.message.count("singular matrix") == 3      # default step + two retries


def test_retry_result_is_still_checked_for_physics():
    # Bolted fault at bus 16 cleared at 0.2 s: the default step fails at the
    # clearing instant; the finer-step retries "solve" it, but with buses stuck
    # at 0 V (non-physical). Every step is tried, and it must end as sim_fail,
    # not as instability.
    r = simulate(Scenario("r", fault=FaultSpec(16, clear_time=0.2), t_end=3.5), bracket=False)
    assert r.status == "sim_fail"
    assert r.message.count("failed with step") == 2          # default step + first retry
    # which non-physical check fires can differ by platform (Linux vs Windows)
    assert "non-physical solution" in r.message


def test_bad_input_still_raises():
    with pytest.raises(ValueError):
        simulate(Scenario("bad", ibrs=(IBRSpec(10),)))          # gen 10 is the slack


def _fake(v, t_end=2.0):
    import numpy as np
    t = np.arange(0, t_end + 1e-9, 1 / 60)
    V = np.ones((t.size, 3))
    v(t, V)
    n = t.size
    tr = sim.Trajectories(t=t, bus_ids=[1, 2, 3], v=V, machine_ids=[], delta=np.zeros((n, 0)),
                          omega=np.zeros((n, 0)), a=np.zeros((n, 3)), fn_hz=60.0, ibr_ids=[],
                          ibr_bus=[], ibr_hv_bus=[], ibr_ip=np.zeros((n, 0)),
                          ibr_iq=np.zeros((n, 0)), ibr_imax=np.zeros(0))
    r = sim.SimResult(Scenario("f", fault=FaultSpec(2, clear_time=0.1)), "completed", traj=tr)
    sim._check_physical(r)
    return r


def test_negative_voltage_is_non_physical():
    def neg(t, V):
        V[(t > 1.2) & (t < 1.3), 2] = -0.2
    r = _fake(neg)
    assert r.status == "sim_fail" and "voltage magnitude -0.200 pu at bus 3" in r.message


def test_bus_held_at_zero_after_clearing_is_non_physical():
    def dead(t, V):
        V[(t >= 1.0) & (t < 1.5), 0] = 0.0             # cleared at 1.1 s, still 0 V
    r = _fake(dead)
    assert r.status == "sim_fail" and "bus 1 held at 0 V" in r.message


def test_faulted_bus_dead_until_an_early_stop_is_non_physical():
    # A run stopped on the angle limit 0.1 s after clearing: shorter than the
    # 0.1 s rule after the settling delay, but the faulted bus never recovered.
    def dead(t, V):
        V[t >= 1.0, 1] = 0.0                            # faulted bus, cleared at 1.1 s
    r = _fake(dead, t_end=1.2)
    assert r.status == "sim_fail"
    assert "bus 2 held at 0 V" in r.message and "until the run stopped" in r.message


def test_faulted_bus_that_recovered_then_decayed_is_physical():
    # The faulted bus recovers at clearing, then decays toward 0 V as machines
    # slip apart (electrical center near the fault): a real pole slip.
    import numpy as np

    def recover_then_decay(t, V):
        V[(t >= 1.0) & (t < 1.1), 1] = 0.0
        late = t >= 1.1
        V[late, 1] = np.maximum(0.004, 0.5 - 12.0 * (t[late] - 1.1))
    r = _fake(recover_then_decay, t_end=1.2)
    assert (abs(r.traj.v[r.traj.t >= 1.15, 1]) <= 0.01).all()
    assert r.status == "completed"


def test_bus_snapping_to_zero_at_clearing_is_non_physical():
    # The artifact seen in the data: a healthy bus jumps to exactly 0 V at the
    # clearing instant and stays there until the run stops.
    def snap(t, V):
        V[(t >= 1.0) & (t < 1.1), 0] = 0.2
        V[t >= 1.1, 0] = 0.0
    r = _fake(snap, t_end=1.2)
    assert r.status == "sim_fail" and "bus 1 held at 0 V" in r.message


def test_smooth_decay_before_an_early_stop_is_physical():
    # A real pole slip: a bus near the electrical center decays toward 0 V as
    # the machines slip apart; it did not snap at clearing. Not flagged.
    import numpy as np

    def decay(t, V):        # 0.3 pu right after clearing, below 0.01 pu from 1.15 s to the stop
        V[:, 0] = np.where(t < 1.1, 1.0, np.maximum(0.004, 0.3 - 6.5 * (t - 1.1)))
    r = _fake(decay, t_end=1.19)
    assert (abs(r.traj.v[r.traj.t >= 1.15, 0]) <= 0.01).all()      # the old rule flagged this
    assert r.status == "completed"


def test_brief_dip_at_the_end_of_a_run_is_physical():
    # A bus passing through ~0 V only at the very end (a pole slip) is not flagged.
    def dip(t, V):
        V[-1, 2] = 0.0
    assert _fake(dip, t_end=1.3).status == "completed"


def test_faulted_bus_at_zero_during_fault_is_physical():
    def bolted(t, V):
        V[(t >= 1.0) & (t < 1.1), 1] = 0.0             # only while the fault is on
    assert _fake(bolted).status == "completed"


def test_near_bolted_fault_with_small_impedance_is_clean():
    r = simulate(Scenario("z", fault=FaultSpec(19, clear_time=0.05, x_pu=0.003), t_end=2))
    assert r.status == "completed" and r.traj.v.min() > -1e-3


def test_failed_initialization_is_never_completed(monkeypatch):
    real = sim.build_scenario

    def bad_init(scenario):
        system, changes = real(scenario)
        run = system.TDS.run

        def run_then_flag():
            run()
            system.TDS.test_ok = False          # what ANDES sets on a failed init
        system.TDS.run = run_then_flag
        return system, changes

    monkeypatch.setattr(sim, "build_scenario", bad_init)
    r = simulate(Scenario("i", fault=FaultSpec(16), t_end=2))
    assert r.status == "sim_fail" and r.message == "dynamic initialization failed"
    assert r.diagnostics == []                  # no retries or milder-fault reruns after a failed init
