"""Run one scenario and collect its trajectories.

`simulate(scenario)` never crashes on a numerical problem. Every run ends
with one of these statuses:

==================  =========================================================
``completed``       reached the end time
``stopped_angle``   ANDES stopped early because the rotor-angle spread
                    exceeded the stop limit (the system is angle-unstable)
``pf_fail``         the power flow did not converge, so no dynamics were run
``sim_fail``        the time-domain simulation failed numerically, or reached
                    a non-physical solution (negative voltage magnitude, or a
                    bus stuck at zero volts after the fault is cleared)
==================  =========================================================

Bad scenario *inputs* (unknown generator, invalid numbers) still raise
ValueError: those are user errors, not simulation outcomes.

Wall-clock timeouts need a separate process and are added with the parallel
dataset runner.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

import andes
import numpy as np

from .case import END_PAD_S, SYNC_MACHINE_SHEETS, build_scenario
from .criteria import DEFAULT_ANGLE_LIMIT_DEG, measured_frequency
from .scenario import Scenario

STATUSES = ("completed", "stopped_angle", "pf_fail", "sim_fail")

_ANGLE_STOP_MSG = "Violated stability criteria"
INIT_FAILED = "dynamic initialization failed"


@dataclass
class Trajectories:
    """Time series from one simulation. Arrays have one row per time sample."""

    t: np.ndarray                      # s
    bus_ids: list
    v: np.ndarray                      # bus voltage magnitude, pu
    machine_ids: list                  # synchronous machines still in service
    delta: np.ndarray                  # rotor angle, rad
    omega: np.ndarray                  # rotor speed, pu
    a: np.ndarray                      # bus voltage angle, rad (synchronous frame)
    fn_hz: float                       # nominal frequency, Hz
    ibr_ids: list
    ibr_bus: list                      # IBR terminal bus
    ibr_hv_bus: list                   # high side of the IBR's step-up transformer
    ibr_ip: np.ndarray                 # active current, pu on system base
    ibr_iq: np.ndarray                 # reactive current, pu on system base
    ibr_imax: np.ndarray               # current limit, pu on system base (one per IBR)
    #: Synchronous machines, same columns as `delta`: electrical (air-gap) and
    #: mechanical torque, pu on the system base, and inertia M = 2H on the
    #: system base (s). Used by the equal-area view. Empty when not recorded.
    te: np.ndarray | None = None
    tm: np.ndarray | None = None
    machine_m: np.ndarray | None = None

    def bus_v(self, bus):
        return self.v[:, self.bus_ids.index(bus)]

    def frequency(self, window_s: float = 0.1):
        """Measured frequency (Hz) at every bus; see criteria.measured_frequency."""
        key = float(window_s)
        cache = self.__dict__.setdefault("_f_cache", {})
        if key not in cache:
            cache[key] = measured_frequency(self.t, self.a, self.fn_hz, key)
        return cache[key]

    def bus_f(self, bus, window_s: float = 0.1):
        return self.frequency(window_s)[:, self.bus_ids.index(bus)]


@dataclass
class SimResult:
    """Outcome of one scenario."""

    scenario: Scenario
    status: str
    message: str = ""
    t_reached: float = 0.0
    runtime_s: float = 0.0
    changes: list = field(default_factory=list)
    traj: Trajectories | None = None
    andes_version: str = andes.__version__
    #: Mean voltage at the faulted bus while the fault is on (pu). Shows how
    #: close to bolted the fault really was.
    fault_residual_pu: float | None = None
    #: Milder-fault check: reruns made after a bolted fault could not be solved.
    diagnostics: list = field(default_factory=list)

    @property
    def ok(self):
        return self.status in ("completed", "stopped_angle")

    def summary(self) -> dict:
        """Plain dict without arrays (for tables and logs)."""
        s = self.scenario
        return {"scenario_id": s.scenario_id, "status": self.status, "message": self.message,
                "t_reached_s": round(self.t_reached, 6), "runtime_s": round(self.runtime_s, 3),
                "ibrs": ";".join(f"{c['gen']}:{c['kind']}@{c['bus']}" for c in self.changes),
                "fault_bus": None if s.fault is None else s.fault.bus,
                "clear_time_s": None if s.fault is None else s.fault.clear_time,
                "fault_x_pu": None if s.fault is None else s.fault.x_pu,
                "fault_residual_pu": None if self.fault_residual_pu is None
                else round(self.fault_residual_pu, 4),
                "diagnostic_runs": ";".join(
                    f"x={d.scenario.fault.x_pu:g}:{d.status}" for d in self.diagnostics),
                "load_scale": s.load_scale, "andes_version": self.andes_version}

    def solved_diagnostic(self):
        """The first milder-fault rerun that solved, or None."""
        return next((d for d in self.diagnostics if d.status != "sim_fail" and d.traj is not None),
                    None)


def _series(system, var):
    arr = system.dae.ts.x if var.v_code == "x" else system.dae.ts.y
    return np.asarray(arr[:, var.a], dtype=float)


def _collect(system, changes) -> Trajectories:
    t = np.asarray(system.dae.ts.t, dtype=float)
    fn = float(system.config.freq)
    machine_ids, deltas, omegas, tes, tms, ms = [], [], [], [], [], []
    for name in SYNC_MACHINE_SHEETS:
        gen = getattr(system, name, None)
        if gen is not None and gen.n:
            machine_ids += list(gen.idx.v)
            deltas.append(_series(system, gen.delta))
            omegas.append(_series(system, gen.omega))
            tes.append(_series(system, gen.te))
            tms.append(_series(system, gen.tm))
            ms.append(np.asarray(gen.M.v, dtype=float))
    empty = np.empty((t.size, 0))
    delta = np.hstack(deltas) if deltas else empty
    omega = np.hstack(omegas) if omegas else empty
    reg = system.REGCA1
    n = reg.n if hasattr(system, "REGCA1") else 0
    hv = {str(c["gen"]): c["hv_bus"] for c in changes}
    return Trajectories(
        t=t, bus_ids=list(system.Bus.idx.v), v=_series(system, system.Bus.v),
        machine_ids=machine_ids, delta=delta, omega=omega,
        a=_series(system, system.Bus.a), fn_hz=fn,
        ibr_ids=list(reg.idx.v) if n else [],
        ibr_bus=list(reg.bus.v) if n else [],
        ibr_hv_bus=[hv.get(str(g), b) for g, b in zip(reg.gen.v, reg.bus.v)] if n else [],
        ibr_ip=_series(system, reg.Ipout) if n else np.empty((t.size, 0)),
        ibr_iq=_series(system, reg.Iqout_x) if n else np.empty((t.size, 0)),
        ibr_imax=np.asarray(system.REECA1.Imax.v, dtype=float) if n else np.empty(0),
        te=np.hstack(tes) if tes else empty, tm=np.hstack(tms) if tms else empty,
        machine_m=np.concatenate(ms) if ms else np.empty(0),
    )


#: Default time step: one cycle at 60 Hz. ANDES's own default (two cycles) gives
#: the same rotor-angle results (step study at bus 16, 1/30 to 1/480 s) but
#: overstates the IBR high-side overvoltage right after clearing: in a 2026-10-10
#: check of 18 dataset scenarios, 2 of 8 labeled RISKY only for a one-sample
#: excursion above 1.2 pu became OK at 1/2 and 1/4 cycle, with peaks lower by up
#: to 0.04 pu. One cycle matched the half-cycle results in all 6 cases rechecked
#: (peaks within 0.004 pu). Cost: about 1.6 times the run time of two cycles.
DEFAULT_STEP_S = 1 / 60

#: Finer time steps tried when a run fails numerically. Near the stability
#: boundary, ANDES 2.0.0 can fail to converge at the fault-clearing instant; a
#: finer step often resolves it.
RETRY_STEPS_S = (1 / 120, 1 / 480)


def _run_once(scenario, stop_angle_deg, step_s):
    """One ANDES run. Returns (system, changes, status, message)."""
    system, changes = build_scenario(scenario)
    if not system.PFlow.converged:
        return system, changes, "pf_fail", "power flow did not converge"
    cfg = system.TDS.config
    cfg.tf = scenario.t_end + END_PAD_S
    cfg.no_tqdm = 1
    cfg.criteria = 1
    cfg.ddelta_limit = float(stop_angle_deg)
    if step_s is not None:
        cfg.tstep = float(step_s)
    try:
        system.TDS.run()
    except Exception as err:                       # numerical failure inside ANDES
        return system, changes, "sim_fail", f"{type(err).__name__}: {err}"
    reached = float(system.dae.t)
    msg = system.TDS.err_msg or ""
    if getattr(system.TDS, "test_ok", None) is False:
        # ANDES keeps simulating after a failed initialization; the result is
        # meaningless, so it must never count as completed.
        return system, changes, "sim_fail", INIT_FAILED
    if msg.startswith(_ANGLE_STOP_MSG):
        return system, changes, "stopped_angle", (
            f"rotor-angle spread exceeded {stop_angle_deg:g} deg at t = {reached:.3f} s")
    if system.exit_code == 0 or reached >= scenario.t_end:
        return system, changes, "completed", ""
    return system, changes, "sim_fail", f"{msg or 'exit code ' + str(system.exit_code)} (t = {reached:.3f} s)"


#: A bus voltage magnitude below this (pu) means the solver reached a
#: non-physical solution (seen with near-bolted faults close to large units).
NEGATIVE_V_TOL = -1e-3


#: After clearing, a bus held at or below this voltage for DEAD_BUS_S is a
#: false collapse: with the network restored, no bus can legitimately sit at
#: zero volts (found at bus 19: buses 20 and 34 stuck at -0.0 pu).
DEAD_BUS_PU = 0.01
DEAD_BUS_S = 0.1
#: Settling time after clearing before the dead-bus check starts.
DEAD_BUS_DELAY_S = 0.05


def _clearing_samples(t, t_clear):
    """Indices of the last sample before and the first sample after the clearing
    instant. ANDES stores extra samples around a switching event (0.1 ms before,
    at, and 0.1 ms after it), so strict inequalities pick values from either side."""
    before = np.flatnonzero(t < t_clear - 1e-7)
    after = np.flatnonzero(t > t_clear + 1e-7)
    if not before.size or not after.size:
        return None
    return int(before[-1]), int(after[0])


def _dead_bus(result):
    """(bus, seconds) of a non-physical stay at ~0 V after clearing, or None.

    Two cases count:
    1. a bus held at ~0 V for at least DEAD_BUS_S after the settling delay;
    2. a run that stopped early (on the angle limit) before rule 1 could apply,
       in which a bus is at ~0 V for the whole time from the settling delay to
       the stop AND shows the artifact's signature: it is the faulted bus and
       never rose above ~0 V after clearing (it must recover once the fault is
       removed), or it snapped from a real voltage to ~0 V at the clearing
       instant (clearing a fault cannot do that).
    Rule 2 was added after 107 of the 391 angle-unstable runs of the
    5,000-scenario dataset published with 0.1.0 turned out to be such
    artifacts (2026-10). A bus that only decays toward zero as machines slip
    apart (a real pole slip near the electrical center) is not flagged.
    """
    f, tr = result.scenario.fault, result.traj
    if f is None:
        return None
    t_clear = f.t_start + f.clear_time
    after = tr.t >= t_clear + DEAD_BUS_DELAY_S
    if after.sum() < 2:
        return None
    t = tr.t[after]
    dead = np.abs(tr.v[after]) <= DEAD_BUS_PU
    dt = np.diff(t)
    worst = (None, 0.0)
    for j in np.flatnonzero(dead.any(axis=0)):
        run = best = 0.0
        for d, step in zip(dead[:-1, j], dt):
            run = run + step if d else 0.0
            best = max(best, run)
        if best > worst[1]:
            worst = (tr.bus_ids[j], best)
    if worst[1] >= DEAD_BUS_S:
        return worst
    stopped_early = tr.t[-1] < result.scenario.t_end - 1e-6
    pair = _clearing_samples(tr.t, t_clear)
    if not stopped_early or pair is None:
        return None
    k_before, k_after = pair
    for j in np.flatnonzero(dead.all(axis=0)):
        bus = tr.bus_ids[j]
        v_before, v_after = abs(tr.v[k_before, j]), abs(tr.v[k_after, j])
        snapped = v_before > 2 * DEAD_BUS_PU and v_after <= DEAD_BUS_PU
        # the faulted bus counts only if it never rose above ~0 V after clearing
        never_recovered = bus == f.bus and bool((np.abs(tr.v[k_after:, j]) <= DEAD_BUS_PU).all())
        if never_recovered or snapped:
            return bus, float(t[-1] - t[0])
    return None


def _check_physical(result):
    """Downgrade a run whose solution is non-physical to sim_fail."""
    if result.status == "sim_fail":
        return
    vmin = float(result.traj.v.min())
    problem = None
    if vmin < NEGATIVE_V_TOL:
        j = int(np.argmin(result.traj.v.min(axis=0)))
        problem = f"voltage magnitude {vmin:.3f} pu at bus {result.traj.bus_ids[j]}"
    else:
        dead = _dead_bus(result)
        if dead:
            until = " until the run stopped" if dead[1] < DEAD_BUS_S else ""
            problem = (f"bus {dead[0]} held at 0 V for {dead[1]:.2f} s after clearing{until} "
                       "(false voltage collapse)")
    if problem:
        result.message = "; ".join(filter(None, [
            f"non-physical solution: {problem} (numerical; would have been "
            f"'{result.status}')", result.message]))
        result.status = "sim_fail"


#: Milder-fault check. A fault at or below this reactance counts as bolted. If a bolted fault
#: cannot be solved, it is rerun at BRACKET_X_PU (milder) as a diagnostic.
#: NERC TPL-001-5.1 does not specify a fault impedance; these values are a
#: numerical aid, never a substitute for the bolted result.
BOLTED_X_MAX_PU = 1e-4
BRACKET_X_PU = (1e-3, 3e-3)


def _residual(result):
    f, tr = result.scenario.fault, result.traj
    if f is None or tr is None or f.bus not in tr.bus_ids:
        return None
    on = (tr.t > f.t_start) & (tr.t < f.t_start + f.clear_time)
    return float(np.abs(tr.bus_v(f.bus)[on]).mean()) if on.any() else None


def simulate(scenario: Scenario, stop_angle_deg: float = DEFAULT_ANGLE_LIMIT_DEG,
             step_s: float | None = None, retry: bool = True,
             bracket: bool = True) -> SimResult:
    """Build and run one scenario. Returns a SimResult (never raises on numerics).

    stop_angle_deg: ANDES stops a run once the rotor-angle spread exceeds this
        (saves time on unstable cases). Keep it at or above your angle criterion.
    step_s: time step; None uses DEFAULT_STEP_S (one cycle at 60 Hz).
    retry: after a numerical failure, rerun with the finer RETRY_STEPS_S.
        The message records any retry, so it is never hidden.
    bracket: milder-fault check. If a bolted fault still fails, rerun it at the milder
        BRACKET_X_PU reactances and keep those runs in `result.diagnostics`.
        The result itself stays `sim_fail`.
    """
    t0 = time.perf_counter()
    step_s = DEFAULT_STEP_S if step_s is None else step_s
    steps = [step_s] + ([s for s in RETRY_STEPS_S if s < step_s] if retry else [])
    notes = []
    for i, step in enumerate(steps):
        system, changes, status, message = _run_once(scenario, stop_angle_deg, step)
        result = SimResult(scenario, status, message=message, changes=changes)
        if status != "pf_fail":
            result.t_reached = float(system.dae.t)
            if system.dae.ts.t is not None and len(system.dae.ts.t) > 1:
                result.traj = _collect(system, changes)
                _check_physical(result)       # a non-physical answer is retried too
                result.fault_residual_pu = _residual(result)
        if (result.status != "sim_fail" or i == len(steps) - 1
                or INIT_FAILED in result.message):
            break   # a finer step cannot fix a failed initialization
        notes.append(f"failed with step {step:.5f} s: {result.message}")
    if notes and result.status != "sim_fail":
        result.message = "; ".join(filter(None, [result.message,
                                                 f"retried with step {step:.5f} s"]))
    elif notes:
        result.message = " | ".join(notes + [result.message])
    f = scenario.fault
    init_failed = INIT_FAILED in result.message
    if (bracket and result.status == "sim_fail" and not init_failed and f is not None
            and f.x_pu <= BOLTED_X_MAX_PU):
        for x in BRACKET_X_PU:
            diag = simulate(replace(scenario, fault=replace(f, x_pu=x)), stop_angle_deg,
                            step_s, retry, bracket=False)
            result.diagnostics.append(diag)
            if diag.status != "sim_fail":
                break
    result.runtime_s = time.perf_counter() - t0
    return result
