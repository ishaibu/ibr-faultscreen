"""Tools behind the demo's explorer and quiz (tools/explorer_data.py, tools/demo_explorer.py)."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


xd = _load("explorer_data")
de = _load("demo_explorer")

RT = ("ride_through:PRC-029-1_Table2_voltage: REG_SOLAR_9 (bus 29) voltage in (1.2, 9.99) for 0.017 s "
      "> 0 s (cumulative over 10 s); source: NERC PRC-029-1")


def row(**kw):
    base = {"status": "completed", "label": "OK", "resolution": "", "reasons": ""}
    base.update(kw)
    return base


def test_codes_and_modes():
    assert xd.code(row()) == "O"
    assert xd.code(row(label="RISKY", reasons=RT)) == "R"
    assert xd.code(row(status="sim_fail", label="RISKY", resolution="confirmed")) == "C"
    assert xd.code(row(status="sim_fail", label="RISKY", resolution="unresolved")) == "U"
    assert xd.code(row(status="timeout", label="RISKY")) == "T"
    assert xd.modes(row(label="RISKY", reasons=RT)) == xd.MODES["ride_through"]
    ang = "angle_separation: rotor-angle spread exceeded 180 deg at t = 1.456 s"
    assert xd.modes(row(status="stopped_angle", label="RISKY", reasons=ang)) == xd.MODES["angle"]
    confirmed = ("sim_fail: failed | bolted_fault: bolted run failed; milder fault x = 0.001 pu, "
                 "faulted bus at 0.05 pu is already RISKY: " + RT)
    assert xd.modes(row(status="sim_fail", label="RISKY", resolution="confirmed",
                        reasons=confirmed)) == xd.MODES["ride_through"]


def test_explanations_are_plain_and_specific():
    ok = xd.explain(row())
    assert ok.startswith("All checks passed")
    rt = xd.explain(row(label="RISKY", reasons=RT))
    assert "IBR bus 29 (generator 9)" in rt and "above 1.2 pu" in rt and "17 ms" in rt and "may trip" in rt
    ang = xd.explain(row(status="stopped_angle", label="RISKY",
                         reasons="angle_separation: rotor-angle spread exceeded 180 deg at t = 1.456 s"))
    assert "lost step" in ang and "1.456" in ang
    unres = xd.explain(row(status="sim_fail", label="RISKY", resolution="unresolved",
                           reasons="bolted_fault: bolted run failed; milder fault x = 0.001 pu, "
                                   "faulted bus at 0.06 pu is OK, so the bolted outcome is unknown"))
    assert "outcome unknown" in unres and "milder fault was OK" in unres
    conf = xd.explain(row(status="sim_fail", label="RISKY", resolution="confirmed",
                          reasons="bolted_fault: bolted run failed; milder fault x = 0.003 pu, faulted "
                                  "bus at 0.05 pu is already RISKY: " + RT))
    assert "x = 0.003 pu" in conf and "Ride-through" in conf


def test_band_wording():
    assert xd._band("voltage in (1.1, 1.2] for") == "between 1.1 and 1.2 pu"
    assert xd._band("voltage in (1.2, 9.99) for") == "above 1.2 pu"
    assert xd._band("voltage in [0, 0.1) for") == "below 0.1 pu"


def test_inject_places_sections_once():
    html = ("<html><head><style>body{}</style></head><body><section><h2>Network map</h2></section>"
            "<section><h2>Ranked scenarios</h2></section></body></html>")
    out = de.inject(html, {1: (0.0, 0.0), 2: (1.0, 1.0)}, [(1, 2, False)])
    assert out.count('id="explore"') == 1 and out.count('id="quiz"') == 1
    assert out.index('id="explore"') < out.index("Ranked scenarios")
    assert 'src="explorer-data.js"' in out and "window.EXPLORER_LAYOUT=" in out
    assert "[hidden] { display:none !important; }" in out


def test_share_link_format_matches_parser():
    # the page writes and reads links of this form
    assert "#explore=g' + (st.gens.slice().sort().join('') || '0') + '-b'" in de.JS
    assert r"/^#explore=g(\d*)-b(\d+)-c(\d+)$/" in de.JS
