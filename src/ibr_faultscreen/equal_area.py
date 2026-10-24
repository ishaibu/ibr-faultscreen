"""Equal-area view of a simulated fault (single-machine equivalent).

The classical equal-area criterion is exact for one machine against an infinite
bus. For several machines, the machines are split into two groups: the group
that swings ahead (the leading group) and the rest. Each group is replaced by
its centre of inertia, and the two groups by one equivalent machine against the
other (the single-machine equivalent used by the SIME / extended equal-area
methods in the literature). Its angle, mechanical power and electrical power
come straight from the simulated trajectories:

    delta = delta_C - delta_N            (inertia-weighted group angles)
    M     = M_C * M_N / (M_C + M_N)
    Pm    = M * (sum Pm_C / M_C - sum Pm_N / M_N), and the same for Pe

where C is the leading group and N the rest. Torques stand in for powers
(speed stays close to 1 pu), on the system base.

During the fault Pm > Pe and the equivalent machine gains kinetic energy (the
accelerating area). After clearing, if Pe rises above Pm it gives the energy
back (the decelerating area). The first swing is stable when the angle stops
increasing; at that point the two areas are equal (energy balance). It is
unstable when the angle keeps increasing: the energy left over is the
accelerating area minus the decelerating area used so far.

What this view does NOT do:
- It explains rotor-angle stability only. Voltage recovery, IBR ride-through
  and frequency are separate checks.
- It shows only what was simulated. It does not extrapolate a stability margin.
- The two-group split uses the largest gap between rotor angles at the moment
  of largest spread. When machines separate into three or more groups, the
  picture is a simplification (the result says so).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: In an unstable case, a second angle gap at least this fraction of the
#: largest gap (and at least MULTI_GROUP_MIN_DEG) means the machines are
#: separating into more than two groups. Stable cases are never flagged: at
#: their largest spread the machines are swinging, not separating.
MULTI_GROUP_RATIO = 0.5
MULTI_GROUP_MIN_DEG = 30.0
#: Gaps smaller than this (deg) are not a real separation.
MIN_SEPARATION_DEG = 10.0
#: A drop smaller than this (rad) is numerical noise, not the end of the swing.
TURN_TOL_RAD = 1e-5

_trapz = getattr(np, "trapezoid", None) or np.trapz      # numpy < 2 has only trapz


@dataclass
class EqualAreaResult:
    applicable: bool
    reason: str = ""                       # why not applicable, or a caveat
    leading: list = field(default_factory=list)   # machine ids in the leading group
    others: list = field(default_factory=list)
    t: np.ndarray | None = None            # s, first-swing window
    delta_deg: np.ndarray | None = None    # equivalent angle
    pm: np.ndarray | None = None           # pu, system base
    pe: np.ndarray | None = None
    t_fault: float = 0.0
    t_clear: float = 0.0
    accelerating: float = 0.0              # pu·rad
    decelerating: float = 0.0              # pu·rad, used so far
    stable: bool | None = None             # first swing
    delta_end_deg: float = 0.0             # angle where the swing stopped (or the run ended)
    multi_group: bool = False
    #: First swing stable but the run stopped on the angle limit later.
    later_swing_unstable: bool = False

    @property
    def leftover(self):
        """Accelerating minus decelerating area (kinetic energy left), pu·rad."""
        return self.accelerating - self.decelerating

    def detail(self) -> str:
        """One sentence on the areas (no headline, no group names)."""
        if self.stable:
            s = (f"The angle stopped at {self.delta_end_deg:.0f}°: accelerating area "
                 f"{self.accelerating:.2f} = decelerating area {self.decelerating:.2f} pu·rad.")
        else:
            s = (f"Accelerating area {self.accelerating:.2f} pu·rad, decelerating area only "
                 f"{max(self.decelerating, 0):.2f}: {self.leftover:.2f} pu·rad of energy left at "
                 f"{self.delta_end_deg:.0f}°, so the machines lose step.")
        if self.later_swing_unstable:
            s += " The machines lost step in a later swing (the run stopped on the angle limit)."
        if self.multi_group:
            s += " Machines separate into more than two groups: the two-group picture is a simplification."
        return s

    def text(self) -> str:
        if not self.applicable:
            return f"Equal-area view not available: {self.reason}"
        head = "First swing stable" if self.stable else "First swing unstable"
        return f"{head}. {self.detail()} Leading group: {', '.join(short_names(self.leading))}."


def short_names(ids):
    """Compact machine names: "GENROU_7" -> "G7" (other names unchanged)."""
    out = []
    for i in ids:
        s = str(i)
        head, _, tail = s.rpartition("_")
        out.append(f"G{tail}" if head.upper().startswith("GEN") and tail.isdigit() else s)
    return out


def _not(reason):
    return EqualAreaResult(applicable=False, reason=reason)


def _split(angles_deg):
    """Indices of the leading and the other group (largest angle gap), and
    whether a second gap of similar size exists."""
    order = np.argsort(angles_deg)
    gaps = np.diff(angles_deg[order])
    j = int(np.argmax(gaps))
    second = np.sort(gaps)[-2] if len(gaps) > 1 else 0.0
    multi = bool(second >= MULTI_GROUP_RATIO * gaps[j] and second >= MULTI_GROUP_MIN_DEG)
    return order[j + 1:], order[:j + 1], multi, float(gaps[j])


def equal_area(result) -> EqualAreaResult:
    """Equal-area view of one simulation result (simulate.SimResult)."""
    sc = result.scenario
    tr = result.traj
    if sc.fault is None:
        return _not("the scenario has no fault")
    if result.status not in ("completed", "stopped_angle") or tr is None:
        return _not("the simulation did not finish (no valid trajectories)")
    if tr.te is None or tr.te.shape[1] == 0 or tr.machine_m is None:
        return _not("machine torques were not recorded")
    n = tr.delta.shape[1]
    if n < 2:
        return _not("fewer than two synchronous machines are left")
    t = tr.t
    t1 = sc.fault.t_start
    t2 = t1 + sc.fault.clear_time
    if t[-1] <= t2:
        return _not("the simulation ended before the fault was cleared")
    deg = np.degrees(tr.delta)

    after = t >= t1
    k_peak = int(np.argmax(np.where(after, deg.max(1) - deg.min(1), -np.inf)))
    C, N, multi, gap = _split(deg[k_peak])
    if gap < MIN_SEPARATION_DEG and result.status == "completed":
        # No real separation: use the machine that swings furthest from the rest.
        dev = np.abs(deg[after] - deg[after].mean(1, keepdims=True)).max(0)
        lead = int(np.argmax(dev))
        C, N = np.array([lead]), np.array([i for i in range(n) if i != lead])
        multi = False

    M = tr.machine_m
    MC, MN = M[C].sum(), M[N].sum()
    Mt = MC * MN / (MC + MN)
    d_eq = tr.delta[:, C] @ M[C] / MC - tr.delta[:, N] @ M[N] / MN
    pm = Mt * (tr.tm[:, C].sum(1) / MC - tr.tm[:, N].sum(1) / MN)
    pe = Mt * (tr.te[:, C].sum(1) / MC - tr.te[:, N].sum(1) / MN)

    # Orient so the first swing goes to larger angles.
    k0 = int(np.searchsorted(t, t1)) - 1 if np.searchsorted(t, t1) > 0 else 0
    if d_eq[k_peak] < d_eq[k0]:
        C, N = N, C
        d_eq, pm, pe = -d_eq, -pm, -pe

    on = (t >= t1) & (t <= t2)
    post = np.where(t >= t2)[0]
    falls = np.where(np.diff(d_eq[post]) < -TURN_TOL_RAD)[0]
    if len(falls):
        k_end, stable = int(post[falls[0]]), True
    else:
        k_end, stable = int(post[-1]), False
    later = bool(stable and result.status == "stopped_angle")
    multi = bool(multi and not stable)

    win = (t >= t1) & (t <= t[k_end])
    after_clear = (t >= t2) & (t <= t[k_end])
    acc = float(_trapz((pm - pe)[on], d_eq[on]))
    dec = float(-_trapz((pm - pe)[after_clear], d_eq[after_clear]))
    ids = tr.machine_ids
    return EqualAreaResult(
        applicable=True, leading=[ids[i] for i in sorted(C)], others=[ids[i] for i in sorted(N)],
        t=t[win], delta_deg=np.degrees(d_eq[win]), pm=pm[win], pe=pe[win],
        t_fault=float(t1), t_clear=float(t2), accelerating=acc, decelerating=dec,
        stable=stable, delta_end_deg=float(np.degrees(d_eq[k_end])), multi_group=multi,
        later_swing_unstable=later,
        reason=("machines separate into more than two groups; the two-group picture is a "
                "simplification") if multi else "")
