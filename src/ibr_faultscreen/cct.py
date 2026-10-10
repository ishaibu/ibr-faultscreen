"""Critical clearing time by bisection.

"Stable" here means rotor-angle stable: the run completes and the largest
rotor-angle spread stays within the angle limit. This is the classical CCT
definition, the one checked against the equal-area criterion in the
single-machine clearing-time test.
Voltage recovery and ride-through are judged separately by the labeler.

A run that cannot be solved (see the milder-fault check) counts as not stable,
so a CCT reported as
"bracketed" never rests on a failed run; if the boundary falls on failed runs
the result is a "lower_bound", with the milder fault's CCT as an upper estimate.

Bisection assumes one stability boundary: stable below the CCT and unstable
above it. This holds for most single-fault cases but is not guaranteed
(for example with strong controller nonlinearities). Every evaluated clearing
time is returned, so the assumption can be checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .criteria import DEFAULT_ANGLE_LIMIT_DEG, angle_separation
from .scenario import Scenario
from .simulate import BOLTED_X_MAX_PU, BRACKET_X_PU, simulate

CCT_STATUSES = ("bracketed", "lower_bound", "below_min", "above_max", "unsolved")


@dataclass
class CCTResult:
    scenario_id: str
    status: str                      # one of CCT_STATUSES
    cct_s: float | None              # midpoint of the final bracket (only when "bracketed")
    stable_s: float | None           # longest clearing time found stable
    unstable_s: float | None         # shortest clearing time found unstable or unsolvable
    tolerance_s: float
    evaluations: list = field(default_factory=list)   # (clear_time, verdict, status)
    had_sim_fail: bool = False
    #: Milder-fault check, when status is "lower_bound": CCT of the milder fault, an upper
    #: estimate for the bolted CCT (a milder fault is assumed no more severe).
    upper_s: float | None = None
    upper_x_pu: float | None = None

    def summary(self) -> dict:
        return {"scenario_id": self.scenario_id, "cct_status": self.status,
                "cct_s": self.cct_s, "stable_s": self.stable_s, "unstable_s": self.unstable_s,
                "upper_s": self.upper_s, "upper_x_pu": self.upper_x_pu,
                "tolerance_s": self.tolerance_s, "n_sims": len(self.evaluations),
                "had_sim_fail": self.had_sim_fail}

    def text(self) -> str:
        """One-line plain description."""
        if self.status == "bracketed":
            return f"CCT {self.cct_s * 1000:.0f} ms ({self.cct_s * 60:.1f} cycles)"
        if self.status == "lower_bound":
            up = ("" if self.upper_s is None else
                  f"; a milder fault (x = {self.upper_x_pu:g} pu) has CCT "
                  f"{self.upper_s * 1000:.0f} ms")
            return (f"CCT at least {self.stable_s * 1000:.0f} ms; longer clearing times "
                    f"could not be solved for the bolted fault{up}")
        if self.status == "below_min":
            return f"unstable even at {self.unstable_s * 1000:.0f} ms"
        if self.status == "unsolved":
            return f"could not be solved even at {self.unstable_s * 1000:.0f} ms"
        return f"stable up to {self.stable_s * 1000:.0f} ms (search limit)"


def classify(scenario: Scenario, angle_limit_deg: float = DEFAULT_ANGLE_LIMIT_DEG):
    """Run `scenario`. Returns 'stable', 'unstable' or 'fail', and the run status."""
    result = simulate(scenario, stop_angle_deg=max(angle_limit_deg, DEFAULT_ANGLE_LIMIT_DEG),
                      bracket=False)
    if result.status == "stopped_angle":
        return "unstable", result.status
    if result.status != "completed":
        return "fail", result.status
    check = angle_separation(result.traj.t, result.traj.delta, angle_limit_deg)
    return ("unstable" if check.passed is False else "stable"), result.status


def is_angle_stable(scenario: Scenario, angle_limit_deg: float = DEFAULT_ANGLE_LIMIT_DEG):
    """(stable, status). A failed simulation counts as NOT stable (conservative)."""
    verdict, status = classify(scenario, angle_limit_deg)
    return verdict == "stable", status


def _bisect(base, t_min_s, t_max_s, tol, angle_limit_deg, t_end, out):
    def check(ct):
        sc = replace(base, fault=replace(base.fault, clear_time=ct), t_end=t_end)
        verdict, status = classify(sc, angle_limit_deg)
        out.evaluations.append((round(ct, 6), verdict, status))
        out.had_sim_fail |= verdict == "fail"
        return verdict

    first = check(t_min_s)
    if first != "stable":
        out.status = "below_min" if first == "unstable" else "unsolved"
        out.unstable_s = t_min_s
        return
    if check(t_max_s) == "stable":
        out.status, out.stable_s = "above_max", t_max_s
        return
    lo, hi = t_min_s, t_max_s
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if check(mid) == "stable" else (lo, mid)
    out.stable_s, out.unstable_s = lo, hi
    verdict_hi = dict((ct, v) for ct, v, _ in out.evaluations)[round(hi, 6)]
    if verdict_hi == "fail":
        out.status = "lower_bound"
    else:
        out.status, out.cct_s = "bracketed", 0.5 * (lo + hi)


def critical_clearing_time(base: Scenario, t_min_s: float = 0.02, t_max_s: float = 0.5,
                           tolerance_s: float | None = None,
                           angle_limit_deg: float = DEFAULT_ANGLE_LIMIT_DEG,
                           fn_hz: float = 60.0, upper_bound: bool = True) -> CCTResult:
    """CCT for the fault in `base` (its clearing time is ignored).

    tolerance_s defaults to half a cycle at `fn_hz` (8.33 ms at 60 Hz).
    A failed (unsolvable) run counts as not stable. If the stability boundary
    ends on a failed run, the result is a "lower_bound"; with upper_bound=True
    the CCT of the milder fault is then added as an upper estimate.
    """
    if base.fault is None:
        raise ValueError("CCT needs a scenario with a fault")
    if not 0 < t_min_s < t_max_s:
        raise ValueError("need 0 < t_min_s < t_max_s")
    tol = 0.5 / fn_hz if tolerance_s is None else float(tolerance_s)
    if tol <= 0:
        raise ValueError("tolerance must be greater than zero")
    # Keep enough simulated time after the latest clearing for a swing to develop.
    t_end = max(base.t_end, base.fault.t_start + t_max_s + 2.0)
    out = CCTResult(base.scenario_id, "bracketed", None, None, None, tol)
    _bisect(base, t_min_s, t_max_s, tol, angle_limit_deg, t_end, out)
    if out.status == "lower_bound" and upper_bound and base.fault.x_pu <= BOLTED_X_MAX_PU:
        for x in BRACKET_X_PU:
            milder = replace(base, fault=replace(base.fault, x_pu=x))
            sub = CCTResult(base.scenario_id, "bracketed", None, None, None, tol)
            _bisect(milder, out.stable_s, t_max_s, tol, angle_limit_deg, t_end, sub)
            out.evaluations += [(ct, f"{v} (x={x:g})", st) for ct, v, st in sub.evaluations]
            if sub.status == "bracketed":
                out.upper_s, out.upper_x_pu = sub.cct_s, x
                break
            if sub.status == "above_max":
                out.upper_s, out.upper_x_pu = t_max_s, x
                break
    return out
