"""Single-machine (SMIB) critical clearing time vs. the equal-area criterion.

Case: ANDES's bundled SMIB (data from Sauer, Pai & Chow), classical generator
model, damping set to zero so the equal-area criterion applies exactly.
A bolted three-phase fault at bus 3 cuts the generator off from the infinite
bus (electrical power = 0 during the fault). Clearing restores the original
network, so the pre- and post-fault power-angle curves are the same.
"""

import math

import andes
import numpy as np
import pandas as pd
import pytest

from ibr_faultscreen.case import build_system, run_tds

# SMIB data (ANDES smib/SMIB.xlsx, 100 MVA base)
P_M = 0.9            # mechanical power, pu
V_GEN = 1.05         # generator terminal voltage, pu
X_LINES = 0.15 + 0.4 / 2   # bus1-bus3 line + two parallel 0.4 pu lines
XD1 = 0.245          # transient reactance, pu
H = 5.7512 / 2       # inertia constant, s (ANDES M = 2H)
OMEGA_S = 2 * math.pi * 60


def equal_area_cct():
    """Hand-calculation formulas (checked by hand in docs/verification.md)."""
    theta = math.asin(P_M * X_LINES / V_GEN)                 # terminal-voltage angle
    v1 = V_GEN * complex(math.cos(theta), math.sin(theta))
    current = (v1 - 1.0) / complex(0, X_LINES)
    e = v1 + complex(0, XD1) * current                       # E' behind X'd
    d0 = math.atan2(e.imag, e.real)
    d_cr = math.acos((math.pi - 2 * d0) * math.sin(d0) - math.cos(d0))
    t_cr = math.sqrt(4 * H * (d_cr - d0) / (OMEGA_S * P_M))
    return abs(e), d0, d_cr, t_cr


def smib_stable(clear_time):
    sheets = pd.read_excel(andes.get_case("smib/SMIB.xlsx"), sheet_name=None)
    sheets.pop("Summary", None)
    sheets.pop("Fault", None)
    sheets["GENCLS"]["D"] = 0.0
    from ibr_faultscreen.scenario import FaultSpec
    system = build_system(sheets, FaultSpec(bus=3, t_start=1.0, clear_time=clear_time, x_pu=1e-6))
    run_tds(system, 5)
    d = system.dae.ts.x[:, system.GENCLS.delta.a]
    return np.degrees(np.abs(d[:, 0] - d[:, 1])).max() < 180


def test_hand_values():
    e, d0, d_cr, t_cr = equal_area_cct()
    assert e == pytest.approx(1.1368, abs=1e-4)
    assert math.degrees(d0) == pytest.approx(28.10, abs=0.01)
    assert math.degrees(d_cr) == pytest.approx(82.20, abs=0.01)
    assert t_cr == pytest.approx(0.1789, abs=1e-4)


def test_simulated_cct_matches_equal_area():
    *_, t_cr = equal_area_cct()
    lo, hi = 0.05, 0.40
    assert smib_stable(lo) and not smib_stable(hi)
    while hi - lo > 0.001:                      # bisection to 1 ms
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if smib_stable(mid) else (lo, mid)
    one_cycle = 1 / 60
    assert abs((lo + hi) / 2 - t_cr) <= one_cycle
