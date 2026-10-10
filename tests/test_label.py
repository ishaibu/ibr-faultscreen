"""Labeler tests on synthetic trajectories. No simulation is run."""

import math

import numpy as np

from ibr_faultscreen.criteria import Band, CriteriaSettings, Envelope
from ibr_faultscreen.label import LABELS, label
from ibr_faultscreen.scenario import FaultSpec, Scenario
from ibr_faultscreen.simulate import SimResult, Trajectories

DT = 0.001
T = np.arange(0, 5 + DT / 2, DT)
FAULT = FaultSpec(bus=6, t_start=1.0, clear_time=0.1)
ENV = Envelope("test", "voltage", "TEST", (Band(0.0, 0.5, 0.15), Band(0.5, 0.9, 2.0),
                                          Band(0.9, 9.9, math.inf)))
SETTINGS = CriteriaSettings(v_recovery_min_pu=0.8, v_recovery_time_s=1.0, f_min_hz=59.0,
                            f_max_hz=61.0, envelopes=(ENV,))


def make(v6=None, spread_deg=30.0, f6=None, status="completed"):
    n = T.size
    v = np.ones((n, 2))
    if v6 is not None:
        v[:, 1] = v6
    delta = np.zeros((n, 2))
    delta[:, 1] = np.radians(spread_deg)
    f = np.full(n, 60.0) if f6 is None else f6
    # bus angles that produce frequency f at bus 6 (bus 5 stays at 60 Hz)
    a = np.zeros((n, 2))
    a[1:, 1] = np.cumsum(2 * np.pi * (f[:-1] - 60.0) * np.diff(T))
    tr = Trajectories(t=T, bus_ids=[5, 6], v=v, machine_ids=["G1", "G2"], delta=delta,
                      omega=np.ones((n, 2)), a=a, fn_hz=60.0, ibr_ids=["REG_1"],
                      ibr_bus=[31], ibr_hv_bus=[6], ibr_ip=np.zeros((n, 1)),
                      ibr_iq=np.zeros((n, 1)), ibr_imax=np.ones(1))
    return SimResult(Scenario("s", fault=FAULT), status, traj=tr, t_reached=float(T[-1]))


def dip(level, start, end):
    v = np.ones(T.size)
    v[(T >= start) & (T < end)] = level
    return v


def test_all_ok():
    lab = label(make(v6=dip(0.3, 1.0, 1.1)), SETTINGS)
    assert not lab.risky and set(lab.flags) == set(LABELS)
    assert all(v is False for v in lab.flags.values())


def test_angle_unstable():
    lab = label(make(spread_deg=190), SETTINGS)
    assert lab.risky and lab.flags["angle_unstable"] is True


def test_stopped_angle_counts_as_unstable():
    r = make(spread_deg=150, status="stopped_angle")
    r.message = "rotor-angle spread exceeded 180 deg"
    lab = label(r, SETTINGS)
    assert lab.flags["angle_unstable"] is True
    assert lab.flags["voltage_recovery_fail"] is None        # not judged on a stopped run


def test_slow_voltage_recovery():
    lab = label(make(v6=dip(0.6, 1.0, 2.5)), SETTINGS)      # below 0.8 pu until 2.5 s
    assert lab.flags["voltage_recovery_fail"] is True


def test_ride_through_exposure():
    lab = label(make(v6=dip(0.3, 1.0, 1.2)), SETTINGS)      # 0.2 s below 0.5 pu > 0.15 s
    assert lab.flags["ride_through_exposure"] is True and lab.risky
    assert any("ride_through" in r for r in lab.reasons)


def test_frequency_spike_during_fault_is_ignored():
    f = np.full(T.size, 60.0)
    f[(T >= 1.0) & (T < 1.1)] = 75.0          # during the fault only (skip ends 1.2 s)
    assert label(make(f6=f), SETTINGS).flags["frequency_violation"] is False
    f[(T >= 2.0) & (T < 2.2)] = 62.0          # 0.2 s at 62 Hz later is checked
    assert label(make(f6=f), SETTINGS).flags["frequency_violation"] is True


def test_unset_limits_not_evaluated():
    lab = label(make(), CriteriaSettings())
    assert lab.flags["voltage_recovery_fail"] is None
    assert lab.flags["frequency_violation"] is None
    assert lab.flags["ride_through_exposure"] is None
    assert lab.flags["angle_unstable"] is False and not lab.risky


def test_failed_simulation_is_risky():
    r = SimResult(Scenario("f", fault=FAULT), "sim_fail", message="did not converge")
    lab = label(r, SETTINGS)
    assert lab.risky and lab.flags["sim_fail"] is True
    assert lab.summary()["label"] == "RISKY"
