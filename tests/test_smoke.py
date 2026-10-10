"""Smoke tests: the package installs, and ANDES runs the IEEE 39-bus case."""

import andes

import ibr_faultscreen


def test_package_has_version():
    assert ibr_faultscreen.__version__ != "0.0.0"


def test_ieee39_power_flow_converges():
    case = andes.get_case("ieee39/ieee39_full.xlsx")
    system = andes.run(case, no_output=True, default_config=True)
    assert system.PFlow.converged


def test_ieee39_has_standard_dynamic_data():
    case = andes.get_case("ieee39/ieee39_full.xlsx")
    system = andes.load(case, no_output=True, default_config=True)
    assert system.Bus.n == 39
    assert system.GENROU.n == 10
