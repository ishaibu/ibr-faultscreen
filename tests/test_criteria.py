"""Criteria tests on synthetic data (includes the ride-through boundary tests)."""

import math

import numpy as np
import pytest

from ibr_faultscreen.criteria import (Band, CriteriaSettings, Envelope, angle_separation,
                                      frequency, load_criteria, ride_through_exposure,
                                      rocof_max, voltage_recovery)

DT = 0.001


def dip(level, duration, *, start=1.0, end=3.0, base=1.0):
    """Voltage at `base`, dropping to `level` for `duration` s from `start`."""
    n = int(round(end / DT)) + 1
    t = np.arange(n) * DT
    v = np.full(n, base)
    i0, i1 = int(round(start / DT)), int(round((start + duration) / DT))
    v[i0:i1] = level
    return t, v


# Test envelope for the boundary tests. These numbers are made up for testing only.
ENV = Envelope("test", "voltage", "TEST VALUES ONLY", (
    Band(0.0, 0.5, 0.15), Band(0.5, 0.9, 2.0), Band(0.9, 1.1, math.inf), Band(1.1, 9.9, 0.0)))


# ------------------------------------------------------------- ride-through boundaries
@pytest.mark.parametrize("level,duration,expected", [
    (0.30, 0.149, True),    # a) just inside: shorter than 0.15 s
    (0.30, 0.150, True),    # b) exactly the minimum: counts as inside
    (0.30, 0.151, False),   # c) just outside: longer than 0.15 s
    (0.50, 0.300, True),    # d) 0.50 pu belongs to the [0.5, 0.9) band (2.0 s)
    (0.4999, 0.300, False),  # e) just below 0.5 pu: [0, 0.5) band, 0.3 s > 0.15 s
    (1.15, 0.010, False),   # f) overvoltage band with 0 s: any time there is exposure
])
def test_ride_through_boundaries(level, duration, expected):
    t, v = dip(level, duration)
    assert ride_through_exposure(t, v, ENV).passed is expected


def test_continuous_vs_cumulative():
    # g) two 0.1 s dips to 0.3 pu, 0.5 s apart
    t, v = dip(0.3, 0.1)
    v[int(round(1.6 / DT)):int(round(1.7 / DT))] = 0.3
    assert ride_through_exposure(t, v, ENV).passed is True          # longest stay 0.1 s
    cumulative = Envelope("c", "voltage", "TEST", ENV.bands, method="cumulative")
    assert ride_through_exposure(t, v, cumulative).passed is False  # total 0.2 s > 0.15 s


def test_ride_through_uncovered_values_not_evaluated():
    env = Envelope("gap", "voltage", "TEST", (Band(0.9, 1.1, math.inf),))
    t, v = dip(0.5, 0.1)
    assert ride_through_exposure(t, v, env).passed is None


def test_envelope_validation():
    with pytest.raises(ValueError):
        Band(0.9, 0.5, 1.0)
    with pytest.raises(ValueError):
        Envelope("x", "voltage", "s", (Band(0.0, 0.6, 1), Band(0.5, 1.0, 1)))   # overlap
    with pytest.raises(ValueError):
        Envelope("x", "current", "s", (Band(0, 1, 1),))


# ------------------------------------------------------------- angle
def test_angle_separation():
    t = np.array([0.0, 1.0, 2.0])
    d = np.radians([[0, 10], [0, 179.9], [0, 20]])
    assert angle_separation(t, d).passed is True
    d[1, 1] = np.radians(180.1)
    r = angle_separation(t, d)
    assert r.passed is False and r.value == pytest.approx(180.1)
    assert angle_separation(t, d[:, :1]).passed is None             # one machine


# ------------------------------------------------------------- voltage recovery
def test_voltage_recovery():
    t, v = dip(0.3, 0.1)                      # cleared at 1.1 s, back to 1.0 immediately
    assert voltage_recovery(t, v, 1.1, 0.8, 1.0).passed is True
    v[(t >= 1.1) & (t < 2.3)] = 0.7           # slow: below 0.8 pu until 2.3 s
    r = voltage_recovery(t, v, 1.1, 0.8, 1.0)
    assert r.passed is False and r.value == pytest.approx(1.2, abs=2 * DT)
    v[t >= 2.9] = 0.7                         # sags again at the end: never recovers
    assert math.isinf(voltage_recovery(t, v, 1.1, 0.8, 1.0).value)
    assert voltage_recovery(t, v, 1.1, None, None).passed is None


# ------------------------------------------------------------- frequency
def test_rocof_ramp():
    t = np.arange(0, 3 + DT / 2, DT)
    f = 60 - 0.5 * np.clip(t - 1, 0, None)    # falls 0.5 Hz/s from t = 1 s
    assert rocof_max(t, f, 0.5) == pytest.approx(0.5, rel=1e-6)


def test_frequency_limits():
    t = np.arange(0, 3 + DT / 2, DT)
    f = 60 - 0.5 * np.clip(t - 1, 0, None)    # nadir 59.0 Hz at 3 s
    assert frequency(t, f, f_min_hz=58.9).passed is True
    assert frequency(t, f, f_min_hz=59.1).passed is False
    assert frequency(t, f, rocof_limit_hz_s=0.4, rocof_window_s=0.5).passed is False
    assert frequency(t, f).passed is None
    with pytest.raises(ValueError):
        frequency(t, f, rocof_limit_hz_s=1.0)  # window missing


def test_bad_time_input():
    with pytest.raises(ValueError):
        angle_separation([0.0], np.zeros((1, 2)))
    with pytest.raises(ValueError):
        angle_separation([0.0, 1.0, 0.5], np.zeros((3, 2)))


# ------------------------------------------------------------- settings
def test_example_settings_load(tmp_path):
    from pathlib import Path
    example = Path(__file__).parents[1] / "examples" / "criteria_example.yaml"
    s = load_criteria(example)
    assert s.angle_limit_deg == 180 and s.envelopes[0].bands[2].min_s == math.inf
    assert "ILLUSTRATIVE" in s.envelopes[0].source
    bad = tmp_path / "bad.yaml"
    bad.write_text("angle_limt_deg: 170\n")
    with pytest.raises(ValueError, match="unknown"):
        load_criteria(bad)
    assert CriteriaSettings().v_recovery_min_pu is None   # no guessed defaults


# ------------------------------------------------------------- band edges, windows, ignore
def test_band_edges():
    both = Band(0.9, 1.05, math.inf, closed="both")
    right = Band(1.05, 1.10, 1800, closed="right")
    assert both.contains(1.05) and not right.contains(1.05)
    assert right.contains(1.10) and not right.contains(1.05 + 0.0)
    assert Band(0.7, 0.9, 6).contains(0.7) and not Band(0.7, 0.9, 6).contains(0.9)
    with pytest.raises(ValueError):
        Band(0, 1, 1, closed="middle")
    # touching edges that are both closed overlap; one open edge does not
    Envelope("ok", "voltage", "s", (both, right))
    with pytest.raises(ValueError, match="overlap"):
        Envelope("x", "voltage", "s", (both, Band(1.05, 1.1, 1, closed="both")))


def test_cumulative_window():
    # three 0.15 s dips to 0.3 pu: at t = 1, 6 and 13 s. Band [0, 0.5) allows 0.32 s.
    t = np.arange(0, 15 + DT / 2, DT)
    v = np.ones_like(t)
    for start in (1.0, 6.0, 13.0):
        v[(t >= start - DT / 2) & (t < start + 0.15 - DT / 2)] = 0.3
    bands = (Band(0.0, 0.5, 0.32), Band(0.5, 9.9, math.inf))
    whole = Envelope("w", "voltage", "TEST", bands, method="cumulative")
    win10 = Envelope("w10", "voltage", "TEST", bands, method="cumulative", window_s=10)
    win3 = Envelope("w3", "voltage", "TEST", bands, method="cumulative", window_s=3)
    assert ride_through_exposure(t, v, whole).value == pytest.approx(0.45, abs=2 * DT)
    r10 = ride_through_exposure(t, v, win10)   # dips 1 and 6 (or 6 and 13) fit in 10 s: 0.30 s
    assert r10.passed is True
    assert ride_through_exposure(t, v, win3).passed is True
    with pytest.raises(ValueError):
        Envelope("bad", "voltage", "s", bands, window_s=10)          # window needs cumulative


def test_ignore_interval():
    t, v = dip(0.3, 0.2)                     # 0.2 s at 0.3 pu (limit 0.15 s)
    assert ride_through_exposure(t, v, ENV).passed is False
    assert ride_through_exposure(t, v, ENV, ignore=(1.0, 1.2)).passed is True
    f = np.full_like(t, 60.0)
    f[(t >= 1.0) & (t < 1.1)] = 62.0         # spike only during the "fault"
    assert frequency(t, f, f_max_hz=61).passed is False
    assert frequency(t, f, f_max_hz=61, ignore=(1.0, 1.2)).passed is True


def test_prc029_file_loads():
    from ibr_faultscreen.criteria import default_criteria
    s = default_criteria()
    volt, freq = s.envelopes
    assert volt.window_s == 10 and freq.window_s == 600
    assert len(volt.bands) == 9 and len(freq.bands) == 5
    # Every voltage from 0 to 1.5 pu falls in exactly one band
    xs = np.linspace(0, 1.5, 3001)
    assert np.all(sum(b.contains(xs).astype(int) for b in volt.bands) == 1)
    fs = np.linspace(55, 65, 2001)
    assert np.all(sum(b.contains(fs).astype(int) for b in freq.bands) == 1)


# ------------------------------------------------------------- measured frequency
def test_measured_frequency():
    from ibr_faultscreen.criteria import measured_frequency
    t = np.arange(0, 3 + DT / 2, DT)
    a = 2 * np.pi * 0.5 * t                    # constant 60.5 Hz
    f = measured_frequency(t, a, 60.0, 0.1)
    assert np.allclose(f[t >= 0.1], 60.5)
    assert np.all(f[t < 0.1 - DT / 2] == 60.0)
    b = np.where(t >= 1.0, 0.5, 0.0)           # 0.5 rad phase jump at 1 s
    g = measured_frequency(t, b, 60.0, 0.1)[:, 0]
    assert g[(t > 1.0) & (t < 1.1 - DT)].max() > 60.5        # disturbed for one window
    assert np.allclose(g[t >= 1.1 + DT], 60.0)               # then exact again
