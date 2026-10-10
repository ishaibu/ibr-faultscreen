"""Turn a simulation result into risk labels.

Each scenario gets one flag per failure mode plus an overall RISKY / OK:

=========================  ====================================================
``angle_unstable``         rotor-angle spread above the angle limit, or ANDES
                           stopped the run on its angle limit
``voltage_recovery_fail``  some bus did not recover in time after clearing
``ride_through_exposure``  an IBR stayed in some envelope band longer than the
                           band's minimum ride-through time (loss-of-IBR risk)
``frequency_violation``    frequency or RoCoF outside the set limits
``sim_fail``               power flow or simulation failed (counted as RISKY:
                           a case that cannot be solved needs a closer look)
=========================  ====================================================

A failed bolted fault (milder-fault check) also gets a `resolution`: "confirmed" when its
milder-fault rerun is already RISKY, "unresolved" when that rerun is OK.

A flag is True (violated), False (checked and passed) or None (not evaluated,
for example because its limits are not set). Only True flags make a scenario
RISKY. Ride-through and frequency checks are made at the high side of each
IBR's step-up transformer, where PRC-029-1 measures them. Frequency checks skip
the fault-on period plus one measurement window after clearing, because measured bus
frequency is not meaningful while a fault depresses the voltage. Bus frequency
is measured from the voltage angle over `freq_window_s` (six cycles by default).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .criteria import (CriteriaSettings, CriterionResult, angle_separation, frequency,
                       ride_through_exposure, voltage_recovery)
from .simulate import SimResult

LABELS = ("angle_unstable", "voltage_recovery_fail", "ride_through_exposure",
          "frequency_violation", "sim_fail")


@dataclass
class ScenarioLabels:
    scenario_id: str
    status: str
    flags: dict
    risky: bool
    results: list = field(default_factory=list)      # CriterionResult objects
    #: For failed bolted faults (milder-fault check): "confirmed" if a milder rerun is already
    #: RISKY, "unresolved" if not (needs a detailed study). Otherwise None.
    resolution: str | None = None
    diagnostic: "ScenarioLabels | None" = None

    @property
    def reasons(self) -> list[str]:
        """One line per violated check."""
        return [f"{r.name}: {r.detail}" for r in self.results if r.passed is False]

    def summary(self) -> dict:
        row = {"scenario_id": self.scenario_id, "status": self.status,
               "label": "RISKY" if self.risky else "OK",
               "resolution": self.resolution or ""}
        row.update({k: ("" if v is None else bool(v)) for k, v in self.flags.items()})
        row["reasons"] = " | ".join(self.reasons)
        return row


def _merge(results):
    """Combine several CriterionResults for one flag: True if any failed,
    False if all evaluated ones passed, None if none was evaluated."""
    passed = [r.passed for r in results if r.passed is not None]
    if not passed:
        return None
    return not all(passed)


def label(result: SimResult, settings: CriteriaSettings) -> ScenarioLabels:
    """Apply `settings` to `result`. Pure: no simulation is run here."""
    sid = result.scenario.scenario_id
    flags = dict.fromkeys(LABELS)
    results: list[CriterionResult] = []

    if result.status in ("pf_fail", "sim_fail") or result.traj is None:
        flags["sim_fail"] = True
        results.append(CriterionResult("sim_fail", False, detail=result.message or result.status))
        out = ScenarioLabels(sid, result.status, flags, True, results)
        if result.diagnostics:
            _resolve(out, result, settings)
        return out
    flags["sim_fail"] = False
    tr = result.traj
    fault = result.scenario.fault
    t_clear = None if fault is None else fault.t_start + fault.clear_time
    # Measured bus frequency is not meaningful while the fault is on, nor for
    # one measurement window after the phase jump at clearing.
    f_win = settings.freq_window_s
    f_ignore = None if fault is None else (fault.t_start, t_clear + f_win)

    # Rotor angle
    ang = angle_separation(tr.t, tr.delta, settings.angle_limit_deg)
    if result.status == "stopped_angle":
        ang = CriterionResult(ang.name, False, ang.value, settings.angle_limit_deg,
                              result.message)
    results.append(ang)
    flags["angle_unstable"] = None if ang.passed is None else not ang.passed

    # Voltage recovery (only meaningful after a fault; skipped if the run stopped early)
    if t_clear is not None and result.status == "completed":
        vr = voltage_recovery(tr.t, tr.v, t_clear, settings.v_recovery_min_pu,
                              settings.v_recovery_time_s, labels=tr.bus_ids)
    else:
        vr = CriterionResult("voltage_recovery", None,
                             detail="no fault" if t_clear is None else "run stopped early")
    results.append(vr)
    flags["voltage_recovery_fail"] = None if vr.passed is None else not vr.passed

    # Frequency at every measured bus
    if tr.t[-1] - tr.t[0] > f_win:
        fr = frequency(tr.t, tr.frequency(f_win), settings.f_min_hz, settings.f_max_hz,
                       settings.rocof_limit_hz_s, settings.rocof_window_s,
                       rocof_start_s=None if f_ignore is None else f_ignore[1],
                       ignore=f_ignore)
    else:
        fr = CriterionResult("frequency", None, detail="run shorter than the frequency window")
    results.append(fr)
    flags["frequency_violation"] = None if fr.passed is None else not fr.passed

    # Ride-through exposure, per IBR and envelope
    rt = []
    for ibr, hv in zip(tr.ibr_ids, tr.ibr_hv_bus):
        for env in settings.envelopes:
            x = tr.bus_v(hv) if env.quantity == "voltage" else tr.bus_f(hv, f_win)
            ign = f_ignore if env.quantity == "frequency" else None
            rt.append(ride_through_exposure(tr.t, x, env, label=f"{ibr} (bus {hv})", ignore=ign))
    results += rt
    flags["ride_through_exposure"] = _merge(rt)

    risky = any(v is True for v in flags.values())
    return ScenarioLabels(sid, result.status, flags, risky, results)


def _resolve(out: ScenarioLabels, result: SimResult, settings: CriteriaSettings):
    """Milder-fault check: judge a failed bolted fault from its milder-fault reruns.

    A milder fault is assumed to be no more severe than the bolted one (standard
    engineering reasoning, not a proven law with IBR controls). So:
    milder rerun RISKY -> 'confirmed'; milder rerun OK -> 'unresolved'.
    The label stays RISKY either way.
    """
    diag = result.solved_diagnostic()
    tried = ", ".join(f"{d.scenario.fault.x_pu:g} pu: {d.status}" for d in result.diagnostics)
    if diag is None:
        out.resolution = "unresolved"
        out.results.append(CriterionResult(
            "bolted_fault", False, detail=f"no milder rerun solved either ({tried}); "
                                          "study in a commercial RMS tool or EMT"))
        return
    dl = label(diag, settings)
    out.diagnostic = dl
    resid = ("" if diag.fault_residual_pu is None
             else f", faulted bus at {diag.fault_residual_pu:.2f} pu")
    where = f"milder fault x = {diag.scenario.fault.x_pu:g} pu{resid}"
    if dl.risky:
        out.resolution = "confirmed"
        out.results.append(CriterionResult(
            "bolted_fault", False,
            detail=f"bolted run failed; {where} is already RISKY: " + " | ".join(dl.reasons)))
    else:
        out.resolution = "unresolved"
        out.results.append(CriterionResult(
            "bolted_fault", False,
            detail=f"bolted run failed; {where} is OK, so the bolted outcome is unknown: "
                   "study in a commercial RMS tool or EMT"))
