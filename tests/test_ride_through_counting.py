"""Ride-through counting option (PRC-029 Attachment 1 note 7), the time ratio and
near misses. Synthetic voltages on the built-in PRC-029 Table 2; no simulation."""

import math
from dataclasses import replace

import numpy as np
import pytest

from ibr_faultscreen.criteria import (NEAR_MISS_RATIO, CriteriaSettings, default_criteria,
                                      load_criteria, ride_through_exposure)
from ibr_faultscreen.label import format_ratio, label

DT = 0.001
CRIT = default_criteria()
TABLE2 = next(e for e in CRIT.envelopes if e.quantity == "voltage")


def steps(*parts, start=1.0, end=12.0):
    """Voltage 1.0 pu, then each (level, seconds) in turn from `start`."""
    t = np.arange(0, end + DT / 2, DT)
    v = np.ones(t.size)
    t0 = start
    for level, dur in parts:
        v[(t >= t0 - 1e-9) & (t < t0 + dur - 1e-9)] = level
        t0 += dur
    return t, v


def test_per_band_is_the_default():
    assert CRIT.ride_through_counting == "per_band"


def test_nested_counts_deeper_dips_toward_shallower_bands():
    # 2.5 s at 0.6 pu (0.50-0.70 band, 3 s allowed) then 4 s at 0.8 pu (0.70-0.90, 6 s).
    t, v = steps((0.6, 2.5), (0.8, 4.0))
    per_band = ride_through_exposure(t, v, TABLE2)
    nested = ride_through_exposure(t, v, TABLE2, counting="nested")
    assert per_band.passed is True                 # 2.5 < 3 and 4 < 6
    assert nested.passed is False                  # 2.5 + 4 = 6.5 s below 0.90 pu > 6 s
    assert "nested counting" in nested.detail
    assert nested.ratio == pytest.approx(6.5 / 6.0, abs=0.01)


def test_ratio_and_near_miss():
    t, v = steps((0.6, 2.5), (0.8, 4.0))
    r = ride_through_exposure(t, v, TABLE2)
    assert r.ratio == pytest.approx(2.5 / 3.0, abs=0.01)          # worst band: 0.50-0.70
    assert NEAR_MISS_RATIO <= r.ratio <= 1.0                      # close, but not exposed


def test_band_with_zero_allowed_time_gives_infinite_ratio():
    t, v = steps((1.25, 0.02))                                     # above 1.20 pu: may trip
    r = ride_through_exposure(t, v, TABLE2)
    assert r.passed is False and math.isinf(r.ratio)
    assert format_ratio(r.ratio) == "inf"


def test_nested_does_not_change_a_single_band_dip():
    t, v = steps((0.3, 1.0))                                       # 0.25-0.50 band only
    a = ride_through_exposure(t, v, TABLE2)
    b = ride_through_exposure(t, v, TABLE2, counting="nested")
    assert a.passed is b.passed is True
    # nested adds the 1 s to the shallower bands too, but each allows more time
    assert b.ratio >= a.ratio


def test_counting_setting_is_checked_and_read_from_file(tmp_path):
    with pytest.raises(ValueError):
        CriteriaSettings(ride_through_counting="sideways")
    with pytest.raises(ValueError):
        ride_through_exposure(*steps((0.6, 1.0)), TABLE2, counting="sideways")
    p = tmp_path / "c.yaml"
    p.write_text("angle_limit_deg: 180\nride_through_counting: nested\n", encoding="utf-8")
    assert load_criteria(p).ride_through_counting == "nested"


def test_label_reports_the_largest_ratio():
    from test_label import FAULT, make
    t = np.arange(0, 5 + DT / 2, DT)
    v = np.ones(t.size)
    v[(t >= 1.0) & (t < 1.25)] = 0.15        # 0.25 s in 0.10-0.25 (0.32 s allowed)
    settings = replace(CRIT, envelopes=(TABLE2,))
    lab = label(make(v6=v), settings)
    assert lab.flags["ride_through_exposure"] is False
    assert lab.rt_ratio == pytest.approx(0.25 / 0.32, abs=0.01)
    assert lab.summary()["rt_ratio_max"] == pytest.approx(0.25 / 0.32, abs=0.01)
    assert FAULT.bus == 6
