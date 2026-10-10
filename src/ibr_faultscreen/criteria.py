"""Screening criteria evaluated on simulated trajectories.

All functions are pure: they take time series (NumPy arrays) and thresholds,
and return a CriterionResult. No ANDES objects are used here, so every rule
can be hand-checked with synthetic data (ride-through boundary tests).

Thresholds come from the user. Apart from the rotor-angle limit (a common
screening heuristic), IBR-FaultScreen ships no default limits: a criterion
whose limits are not set is reported as "not evaluated" rather than guessed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

#: Rotor-angle separation limit (degrees). A common screening heuristic,
#: not a standard value; replace it with your organization's criterion.
DEFAULT_ANGLE_LIMIT_DEG = 180.0

#: Durations within this many seconds of a limit count as equal to it
#: (guards against floating-point round-off when summing time steps).
TIME_TOL_S = 1e-9


@dataclass(frozen=True)
class CriterionResult:
    """Outcome of one criterion. `passed` is None when not evaluated."""

    name: str
    passed: bool | None
    value: float | None = None
    limit: float | None = None
    detail: str = ""


def _as_2d(x):
    x = np.asarray(x, dtype=float)
    return x[:, None] if x.ndim == 1 else x


def _check_time(t, *series):
    t = np.asarray(t, dtype=float)
    if t.ndim != 1 or t.size < 2:
        raise ValueError("time must be a 1-D array with at least two samples")
    if np.any(np.diff(t) < 0):
        raise ValueError("time must be non-decreasing")
    for s in series:
        if _as_2d(s).shape[0] != t.size:
            raise ValueError("each series must have one row per time sample")
    return t


# ---------------------------------------------------------------- angle

def angle_separation(t, delta_rad, limit_deg: float = DEFAULT_ANGLE_LIMIT_DEG) -> CriterionResult:
    """Largest rotor-angle spread between any two synchronous machines.

    `delta_rad` has one column per machine (radians). Fails when the spread
    exceeds `limit_deg` at any sample. Fewer than two machines: not evaluated.
    """
    _check_time(t, delta_rad)
    d = _as_2d(delta_rad)
    if d.shape[1] < 2:
        return CriterionResult("angle_separation", None, detail="fewer than two synchronous machines")
    spread = np.degrees(d.max(axis=1) - d.min(axis=1)).max()
    return CriterionResult("angle_separation", bool(spread <= limit_deg), float(spread), limit_deg,
                           f"max spread {spread:.1f} deg (limit {limit_deg:g} deg)")


# ---------------------------------------------------------------- voltage recovery

def voltage_recovery(t, v_pu, t_clear: float, v_min_pu: float | None,
                     t_max_s: float | None, labels=None) -> CriterionResult:
    """Every bus must recover to at least `v_min_pu` within `t_max_s` after clearing,
    and stay at or above it until the end of the simulation.

    Recovery time for a bus = (first time after which V stays >= v_min_pu) - t_clear.
    """
    if v_min_pu is None or t_max_s is None:
        return CriterionResult("voltage_recovery", None, detail="limits not set")
    t = _check_time(t, v_pu)
    v = _as_2d(v_pu)
    after = t >= t_clear
    if not after.any():
        raise ValueError("no samples after fault clearing")
    worst, worst_bus = -math.inf, None
    for j in range(v.shape[1]):
        ok = v[after, j] >= v_min_pu
        if ok.all():
            rec = 0.0
        elif not ok[-1]:
            rec = math.inf                      # still below at the end
        else:
            last_bad = np.flatnonzero(~ok)[-1]
            rec = float(t[after][last_bad + 1] - t_clear)
        if rec > worst:
            worst, worst_bus = rec, (labels[j] if labels is not None else j)
    passed = worst <= t_max_s
    txt = "never recovers" if math.isinf(worst) else f"{worst:.3f} s"
    return CriterionResult("voltage_recovery", bool(passed), worst, t_max_s,
                           f"slowest bus {worst_bus}: {txt} to reach {v_min_pu:g} pu (limit {t_max_s:g} s)")


# ---------------------------------------------------------------- frequency

def rocof_max(t, f_hz, window_s: float, start_s: float | None = None) -> float:
    """Largest |df/dt| (Hz/s) measured over a sliding window of `window_s` seconds.

    `start_s`: only windows that begin at or after this time are used (for
    example the fault-clearing time, since PRC-029-1 excludes the fault
    period from its RoCoF calculation).
    """
    if window_s <= 0:
        raise ValueError("RoCoF window must be greater than zero")
    t = _check_time(t, f_hz)
    f = _as_2d(f_hz)
    start = t - window_s
    use = start >= (t[0] if start_s is None else max(t[0], start_s))
    if not use.any():
        raise ValueError("no complete RoCoF window in the simulation")
    worst = 0.0
    for j in range(f.shape[1]):
        f_then = np.interp(start[use], t, f[:, j])
        worst = max(worst, float(np.abs((f[use, j] - f_then) / window_s).max()))
    return worst


def measured_frequency(t, angle_rad, fn_hz: float = 60.0, window_s: float = 0.1):
    """Bus frequency (Hz) from voltage-angle trajectories, as a relay measures it:
    the average rate of change of angle over the last `window_s` seconds.

        f(t) = fn + (theta(t) - theta(t - window)) / (2 pi window)

    A phase jump (fault inception or clearing) disturbs the result for exactly
    one window and then drops out. Samples less than one window after the start
    return fn. `angle_rad` has one column per bus.
    """
    if window_s <= 0:
        raise ValueError("frequency window must be greater than zero")
    t = _check_time(t, angle_rad)
    a = np.unwrap(_as_2d(angle_rad), axis=0)
    then = t - window_s
    f = np.full(a.shape, float(fn_hz))
    ok = then >= t[0]
    for j in range(a.shape[1]):
        f[ok, j] += (a[ok, j] - np.interp(then[ok], t, a[:, j])) / (2 * np.pi * window_s)
    return f


def _valid(t, ignore):
    """Boolean mask of samples outside the `ignore` interval [start, end)."""
    t = np.asarray(t, dtype=float)
    if ignore is None:
        return np.ones(t.size, dtype=bool)
    a, b = ignore
    return ~((t >= a) & (t < b))


def frequency(t, f_hz, f_min_hz=None, f_max_hz=None, rocof_limit_hz_s=None,
              rocof_window_s=None, rocof_start_s=None, ignore=None) -> CriterionResult:
    """Frequency stays within [f_min_hz, f_max_hz] and RoCoF stays within its limit.

    Each limit is optional; unset limits are skipped. All unset: not evaluated.
    `ignore` = (start, end): samples in this interval are not checked (used for
    the fault-on period, when measured bus frequency is not meaningful).
    """
    if f_min_hz is None and f_max_hz is None and rocof_limit_hz_s is None:
        return CriterionResult("frequency", None, detail="limits not set")
    _check_time(t, f_hz)
    f = _as_2d(f_hz)
    problems, notes = [], []
    keep = _valid(t, ignore)
    nadir, zenith = float(f[keep].min()), float(f[keep].max())
    notes.append(f"nadir {nadir:.3f} Hz, zenith {zenith:.3f} Hz")
    if f_min_hz is not None and nadir < f_min_hz:
        problems.append(f"nadir below {f_min_hz:g} Hz")
    if f_max_hz is not None and zenith > f_max_hz:
        problems.append(f"zenith above {f_max_hz:g} Hz")
    if rocof_limit_hz_s is not None:
        if rocof_window_s is None:
            raise ValueError("set rocof_window_s together with rocof_limit_hz_s")
        r = rocof_max(t, f, rocof_window_s, rocof_start_s)
        notes.append(f"RoCoF {r:.3f} Hz/s over {rocof_window_s:g} s")
        if r > rocof_limit_hz_s:
            problems.append(f"RoCoF above {rocof_limit_hz_s:g} Hz/s")
    return CriterionResult("frequency", not problems, nadir, f_min_hz,
                           "; ".join(problems + notes))


# ---------------------------------------------------------------- ride-through exposure

BAND_EDGES = ("left", "right", "both", "neither")


@dataclass(frozen=True)
class Band:
    """For values in the band, the IBR must ride through for at least `min_s` seconds.

    `closed` says which edges belong to the band: "left" = [low, high) (default),
    "right" = (low, high], "both" = [low, high], "neither" = (low, high).
    Use math.inf for `min_s` on a continuous-operation band, and 0 for a band
    where the standard permits tripping ("may ride-through").
    """

    low: float
    high: float
    min_s: float
    closed: str = "left"

    def __post_init__(self):
        if not self.low < self.high:
            raise ValueError(f"band low ({self.low}) must be below high ({self.high})")
        if self.min_s < 0:
            raise ValueError("band duration cannot be negative")
        if self.closed not in BAND_EDGES:
            raise ValueError(f"band closed must be one of {BAND_EDGES}")

    @property
    def low_closed(self):
        return self.closed in ("left", "both")

    @property
    def high_closed(self):
        return self.closed in ("right", "both")

    def contains(self, x):
        x = np.asarray(x, dtype=float)
        lo = x >= self.low if self.low_closed else x > self.low
        hi = x <= self.high if self.high_closed else x < self.high
        return lo & hi

    def label(self):
        return (f"{'[' if self.low_closed else '('}{self.low:g}, "
                f"{self.high:g}{']' if self.high_closed else ')'}")


@dataclass(frozen=True)
class Envelope:
    """A ride-through envelope: a quantity ('voltage' in pu or 'frequency' in Hz)
    and its bands. `source` must name the standard, version and table used."""

    name: str
    quantity: str
    source: str
    bands: tuple[Band, ...]
    #: 'continuous' (longest unbroken stay in a band) or 'cumulative'
    #: (total time in a band). Which one a standard requires must be taken
    #: from its text; this is a user setting, not a default claim.
    method: str = "continuous"
    #: For 'cumulative': count time within any sliding window of this many
    #: seconds (None = the whole simulation).
    window_s: float | None = None

    def __post_init__(self):
        if self.quantity not in ("voltage", "frequency"):
            raise ValueError("envelope quantity must be 'voltage' or 'frequency'")
        if self.method not in ("continuous", "cumulative"):
            raise ValueError("envelope method must be 'continuous' or 'cumulative'")
        if self.window_s is not None:
            if self.method != "cumulative":
                raise ValueError("window_s applies only to the 'cumulative' method")
            if not self.window_s > 0:
                raise ValueError("window_s must be greater than zero")
        if not self.bands:
            raise ValueError(f"envelope {self.name!r} has no bands")
        b = sorted(self.bands, key=lambda x: (x.low, x.high))
        for a, c in zip(b, b[1:]):
            if a.high > c.low or (a.high == c.low and a.high_closed and c.low_closed):
                raise ValueError(f"envelope {self.name!r}: bands overlap at {c.low}")


def _time_in(t, mask, method, window_s=None):
    """Seconds spent where mask is True. Each interval [t_i, t_i+1) takes the state at t_i.

    cumulative + window_s: the largest total time inside any window of that length.
    """
    dt = np.diff(t)
    m = mask[:-1]
    if method == "cumulative":
        if window_s is None:
            return float(dt[m].sum())
        F = np.concatenate([[0.0], np.cumsum(np.where(m, dt, 0.0))])   # time-in-band so far
        # The sliding total is piecewise linear, so its maximum is reached when
        # the window starts or ends on a sample time.
        starts = np.concatenate([t, t - window_s])
        starts = starts[(starts >= t[0]) & (starts <= t[-1])]
        ends = np.minimum(starts + window_s, t[-1])
        return float((np.interp(ends, t, F) - np.interp(starts, t, F)).max())
    best = run = 0.0
    for inside, step in zip(m, dt):
        run = run + step if inside else 0.0
        best = max(best, run)
    return best


def ride_through_exposure(t, x, envelope: Envelope, label: str = "",
                          ignore=None) -> CriterionResult:
    """Compare one IBR's trajectory `x` with a ride-through envelope.

    passed = True  : the trajectory stayed inside the must-ride-through region
                     (the IBR is required to ride through).
    passed = False : it stayed in some band LONGER than that band's minimum
                     ride-through time, so the standard may permit tripping:
                     a loss-of-IBR risk. Equal to the minimum counts as inside.
    Values outside every band are reported as uncovered (passed = None).
    `ignore` = (start, end): time in this interval is not counted.
    """
    t = _check_time(t, x)
    x = np.asarray(x, dtype=float).ravel()
    keep = _valid(t, ignore)
    covered = np.zeros(x.size, dtype=bool)
    how = envelope.method + (f" over {envelope.window_s:g} s" if envelope.window_s else "")
    for band in sorted(envelope.bands, key=lambda b: b.low):
        mask = band.contains(x) & keep
        covered |= mask
        spent = _time_in(t, mask, envelope.method, envelope.window_s)
        if spent > band.min_s + TIME_TOL_S:
            return CriterionResult(
                f"ride_through:{envelope.name}", False, spent, band.min_s,
                f"{label} {envelope.quantity} in {band.label()} for {spent:.3f} s "
                f"> {band.min_s:g} s ({how}); source: {envelope.source}".strip())
    if not (covered | ~keep)[:-1].all():
        return CriterionResult(f"ride_through:{envelope.name}", None,
                               detail=f"{label} trajectory leaves every band of the envelope; "
                                      "check the envelope covers the full range".strip())
    return CriterionResult(f"ride_through:{envelope.name}", True,
                           detail=f"{label} inside must-ride-through region; "
                                  f"source: {envelope.source}".strip())


# ---------------------------------------------------------------- settings

@dataclass(frozen=True)
class CriteriaSettings:
    """All screening limits in one place. Unset (None) limits are not evaluated."""

    angle_limit_deg: float = DEFAULT_ANGLE_LIMIT_DEG
    v_recovery_min_pu: float | None = None
    v_recovery_time_s: float | None = None
    f_min_hz: float | None = None
    f_max_hz: float | None = None
    rocof_limit_hz_s: float | None = None
    rocof_window_s: float | None = None
    #: Bus frequency is measured over this window (see measured_frequency).
    #: Frequency checks skip the fault-on period plus one window after
    #: clearing. [Assumption: 0.1 s = six cycles; PRC-029-1 Attachment 2
    #: note 2 says frequency is measured over typically 3-6 cycles, and R3
    #: excludes the fault period from RoCoF.]
    freq_window_s: float = 0.1
    envelopes: tuple[Envelope, ...] = field(default_factory=tuple)


def _envelope_from_dict(d):
    bands = tuple(Band(float(b["low"]), float(b["high"]),
                       math.inf if str(b["min_s"]).lower() in ("inf", "continuous") else float(b["min_s"]),
                       closed=b.get("closed", "left"))
                  for b in d["bands"])
    window = d.get("window_s")
    return Envelope(name=str(d["name"]), quantity=d["quantity"], source=str(d["source"]),
                    bands=bands, method=d.get("method", "continuous"),
                    window_s=None if window is None else float(window))


def load_criteria(path: str | Path) -> CriteriaSettings:
    """Read CriteriaSettings from a YAML file (see examples/criteria_example.yaml)."""
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known = {k for k in CriteriaSettings.__dataclass_fields__} - {"envelopes"}
    unknown = set(d) - known - {"envelopes"}
    if unknown:
        raise ValueError(f"unknown criteria settings: {sorted(unknown)}")
    values = {k: (None if d.get(k) is None else float(d[k])) for k in known if k in d}
    if "freq_window_s" in values and values["freq_window_s"] is None:
        values.pop("freq_window_s")
    envs = tuple(_envelope_from_dict(e) for e in d.get("envelopes") or [])
    return CriteriaSettings(**values, envelopes=envs)


#: The PRC-029-1 criteria file shipped with the package (transcribed tables;
#: illustrative angle, voltage-recovery and frequency limits).
def default_criteria_path():
    from importlib.resources import files
    return files("ibr_faultscreen").joinpath("data", "criteria_prc029.yaml")


def default_criteria() -> CriteriaSettings:
    return load_criteria(default_criteria_path())
