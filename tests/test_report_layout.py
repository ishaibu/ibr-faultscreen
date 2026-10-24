"""Network map layout: the fixed IEEE 39-bus drawing and the automatic fallback."""

import itertools

import numpy as np
import pytest
import yaml
from importlib.resources import files

from ibr_faultscreen.case import read_case
from ibr_faultscreen.report import network_layout


@pytest.fixture(scope="module")
def sheets():
    return read_case("ieee39")


def _raw_positions():
    data = yaml.safe_load(files("ibr_faultscreen.data").joinpath("layout_ieee39.yaml")
                          .read_text(encoding="utf-8"))
    return {int(b): tuple(map(float, p)) for b, p in data["positions"].items()}


def _cross(p1, p2, p3, p4):
    def side(a, b, c):
        return (c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])
    return (side(p3, p4, p1) * side(p3, p4, p2) < 0) and (side(p1, p2, p3) * side(p1, p2, p4) < 0)


def _dist_to_segment(p, a, b):
    p, a, b = map(np.asarray, (p, a, b))
    t = np.clip(np.dot(p - a, b - a) / np.dot(b - a, b - a), 0, 1)
    return float(np.linalg.norm(p - (a + t * (b - a))))


def test_fixed_layout_is_used_for_ieee39(sheets):
    pos, edges, source = network_layout(sheets, "ieee39")
    assert source == "fixed"
    assert set(pos) == {int(b) for b in sheets["Bus"]["idx"]}
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in pos.values())
    assert len(edges) == len(sheets["Line"])


def test_every_branch_drawn_once_and_transformers_marked(sheets):
    _, edges, _ = network_layout(sheets, "ieee39")
    pairs = [frozenset((a, b)) for a, b, _ in edges]
    assert len(pairs) == len(set(pairs)) == 46
    xf = {frozenset((a, b)) for a, b, t in edges if t}
    assert len(xf) == 12
    assert frozenset((2, 30)) in xf and frozenset((11, 12)) in xf and frozenset((1, 39)) not in xf


def test_fixed_layout_has_no_crossings_or_crowding(sheets):
    pos = _raw_positions()
    _, edges, _ = network_layout(sheets, "ieee39")
    for (a, b, _), (c, d, _) in itertools.combinations(edges, 2):
        if len({a, b, c, d}) == 4:
            assert not _cross(pos[a], pos[b], pos[c], pos[d]), ((a, b), (c, d))
    for n in pos:
        for a, b, _ in edges:
            if n not in (a, b):
                assert _dist_to_segment(pos[n], pos[a], pos[b]) > 0.45, (n, (a, b))
    for i, j in itertools.combinations(pos, 2):
        assert np.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]) >= 0.8, (i, j)


def test_other_cases_fall_back_to_automatic_layout(sheets):
    pos, _, source = network_layout(sheets, case=None)
    assert source == "auto"
    assert set(pos) == {int(b) for b in sheets["Bus"]["idx"]}
    assert network_layout(sheets, case=None)[0] == pos          # deterministic


def test_case_coordinates_are_used_when_present(sheets):
    s = {k: v.copy() for k, v in sheets.items()}
    s["Bus"]["xcoord"] = np.arange(len(s["Bus"]), dtype=float)
    s["Bus"]["ycoord"] = np.arange(len(s["Bus"]), dtype=float) ** 2
    pos, _, source = network_layout(s, case="mycase.raw")
    assert source == "coords"
    b = sorted(pos)
    assert pos[b[0]][0] == 0 and pos[b[-1]][0] == pytest.approx(1, abs=1e-6)
