"""Pre-fault feature tests."""

import math

import pytest

from ibr_faultscreen.features import FEATURES, compute_features, short_circuit_z
from ibr_faultscreen.case import read_case
from ibr_faultscreen.scenario import FaultSpec, IBRSpec, Scenario


def test_all_features_present_and_finite():
    f = compute_features(Scenario("a", ibrs=(IBRSpec(2),), fault=FaultSpec(6)))
    assert all(k in f for k in FEATURES)
    assert all(math.isfinite(float(f[k])) for k in FEATURES)
    assert f["pf_converged"] and f["n_ibr"] == 1


def test_ibr_at_faulted_bus_has_zero_distance():
    f = compute_features(Scenario("a", ibrs=(IBRSpec(2),), fault=FaultSpec(6)))
    assert f["z_fault_ibr_min_pu"] < 1e-9          # gen 2's high side is bus 6


def test_replacing_machines_lowers_inertia_and_short_circuit_level():
    base = compute_features(Scenario("b", fault=FaultSpec(6)))
    more = compute_features(Scenario("m", ibrs=(IBRSpec(2), IBRSpec(3)), fault=FaultSpec(6)))
    assert more["inertia_mws"] < base["inertia_mws"]
    assert more["fault_sc_mva"] < base["fault_sc_mva"]
    assert more["ibr_share"] > base["ibr_share"] == 0


def test_short_circuit_matrix_is_symmetric_without_phase_shifters():
    Z, _ids, _pos = short_circuit_z(read_case())
    assert abs(Z - Z.T).max() < 1e-9


def test_no_ibr_uses_stand_in_values():
    f = compute_features(Scenario("n", fault=FaultSpec(16)))
    assert f["scr_min"] == 99.0 and f["z_fault_ibr_min_pu"] == 9.99


def test_impedances_converted_to_system_base_as_andes_does():
    # A line rated 200 MVA at twice the bus voltage: ANDES scales its impedance by
    # (100 / 200) * (690 / 345)^2 = 2. The short-circuit model must do the same.
    import numpy as np

    from ibr_faultscreen.case import build_system, remove_events
    from ibr_faultscreen.features import _ybus
    sheets = read_case()
    remove_events(sheets)
    line = sheets["Line"]
    line["Sn"] = line["Sn"].astype(float)
    line["Vn1"] = line["Vn1"].astype(float)
    line.loc[0, ["Sn", "Vn1", "Vn2"]] = [200.0, 690.0, 690.0]
    system = build_system(sheets)
    r_sys, x_sys = float(system.Line.r.v[0]), float(system.Line.x.v[0])
    assert x_sys == pytest.approx(2 * float(line.loc[0, "x"]), rel=1e-12)
    Y, _ids, pos = _ybus(sheets)
    i, k = pos[int(line.loc[0, "bus1"])], pos[int(line.loc[0, "bus2"])]
    assert Y[i, k] == pytest.approx(-1 / complex(r_sys, x_sys), rel=1e-9)
    assert np.allclose(Y, Y.T)
