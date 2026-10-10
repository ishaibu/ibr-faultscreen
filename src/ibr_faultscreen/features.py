"""Pre-fault features for the classifier.

Every feature is known BEFORE the fault is simulated, so the classifier never
sees the answer (no leakage). Network quantities come from a short-circuit
model built from the case tables:

* lines and transformers (pi model with off-nominal taps), system base;
* each in-service synchronous machine as a source behind its transient
  reactance X'd. The bundled IEEE 39-bus X''d values look inconsistent
  (ANDES warns that xl <= xd2 is out of the typical range), so X'd is used.
  This gives lower short-circuit levels, i.e. conservative (lower) SCR;
* IBRs contribute nothing (the usual SCR convention), loads are ignored.

SCR at an IBR = short-circuit MVA at its high-side bus / IBR rating (MVA).
"""

from __future__ import annotations

import numpy as np

from .case import (SYNC_MACHINE_SHEETS, build_system, convert_to_ibr, read_case,
                   remove_events, scale_load)
from .scenario import Scenario

#: Feature columns, in a fixed order (the classifier relies on it).
FEATURES = (
    "load_scale",            # load level (fraction of the base case)
    "total_load_mw",         # total active load
    "n_ibr",                 # number of IBRs
    "ibr_mw",                # IBR active power dispatch
    "ibr_share",             # IBR share of total generation (0-1)
    "inertia_mws",           # synchronous inertia online, sum of H*S (MW*s)
    "inertia_h_s",           # same, per MW of synchronous generation (s)
    "scr_min",               # lowest SCR among the IBRs (large if no IBR)
    "fault_sc_mva",          # short-circuit MVA at the faulted bus
    "fault_v_pre_pu",        # pre-fault voltage at the faulted bus
    "z_fault_ibr_min_pu",    # electrical distance fault -> nearest IBR (pu)
    "z_fault_gen_min_pu",    # electrical distance fault -> nearest machine (pu)
    "clear_time_s",          # fault duration
    "fault_x_pu",            # fault reactance
)

#: Stand-in values when a scenario has no IBR (keeps the table numeric).
NO_IBR_SCR = 99.0
NO_IBR_DIST = 9.99


def _ybus(sheets, sbase=100.0):
    bus_ids = [int(b) for b in sheets["Bus"]["idx"]]
    pos = {b: i for i, b in enumerate(bus_ids)}
    n = len(bus_ids)
    Y = np.zeros((n, n), dtype=complex)
    lines = sheets["Line"]
    if "u" in lines.columns:
        lines = lines[lines["u"] == 1]
    for _, ln in lines.iterrows():
        scale = sbase / float(ln.get("Sn", sbase) or sbase)     # to system base
        z = complex(ln["r"], ln["x"]) * scale
        y = 1 / z
        b = float(ln.get("b", 0) or 0) / scale
        tap = float(ln.get("tap", 1) or 1)
        phi = np.radians(float(ln.get("phi", 0) or 0))
        t = tap * np.exp(1j * phi)
        i, k = pos[int(ln["bus1"])], pos[int(ln["bus2"])]
        Y[i, i] += (y + 1j * b / 2) / (tap ** 2)
        Y[k, k] += y + 1j * b / 2
        Y[i, k] += -y / np.conj(t)
        Y[k, i] += -y / t
    return Y, bus_ids, pos


def machine_table(sheets):
    """(bus, MVA, X'd on system base, H) for every synchronous machine left in the case."""
    rows = []
    for name in SYNC_MACHINE_SHEETS:
        df = sheets.get(name)
        if df is None or df.empty:
            continue
        if "u" in df.columns:
            df = df[df["u"] == 1]
        for _, g in df.iterrows():
            sn = float(g["Sn"])
            xd1 = float(g["xd1"]) if "xd1" in g and g["xd1"] == g["xd1"] else 0.3
            rows.append((int(g["bus"]), sn, xd1 * 100.0 / sn, float(g["M"]) / 2.0))
    return rows


def short_circuit_z(sheets):
    """Thevenin impedance matrix Z (system base) with machines behind X'd."""
    Y, bus_ids, pos = _ybus(sheets)
    for bus, _sn, xd, _h in machine_table(sheets):
        Y[pos[bus], pos[bus]] += 1 / complex(0, xd)
    return np.linalg.inv(Y), bus_ids, pos


def compute_features(scenario: Scenario) -> dict:
    """Feature dict for one scenario (runs only a power flow, no dynamics)."""
    sheets = read_case(scenario.case, scenario.dyr)
    remove_events(sheets)
    scale_load(sheets, scenario.load_scale)
    changes = [convert_to_ibr(sheets, spec) for spec in scenario.ibrs]
    system = build_system(sheets)                     # power flow only
    Z, bus_ids, pos = short_circuit_z(sheets)
    sbase = 100.0

    pf_ok = bool(system.PFlow.converged)
    v = dict(zip(system.Bus.idx.v, system.Bus.v.v)) if pf_ok else {}
    pq = system.PQ
    total_load = float(np.sum(pq.p0.v)) * sbase
    gen_p = {}
    for model in ("PV", "Slack"):
        m = getattr(system, model)
        for idx, p in zip(m.idx.v, (m.p.v if pf_ok else m.p0.v)):
            gen_p[str(idx)] = float(p) * sbase
    # sorted, not a set: a set's order changes between processes (hash
    # randomization) and changes the last digit of the float sum (found by the
    # determinism test)
    ibr_gens = sorted({str(c["gen"]) for c in changes})
    ibr_mw = sum(gen_p.get(g, 0.0) for g in ibr_gens)
    total_gen = sum(gen_p.values()) or 1.0

    machines = machine_table(sheets)
    hs = sum(sn * h for _b, sn, _x, h in machines)
    sync_mw = sum(p for g, p in gen_p.items() if g not in ibr_gens) or 1.0

    scr = [abs(1 / Z[pos[c["hv_bus"]], pos[c["hv_bus"]]]) * sbase / c["mva"] for c in changes]

    def dist(a, b):
        ia, ib = pos[a], pos[b]
        return abs(Z[ia, ia] + Z[ib, ib] - 2 * Z[ia, ib])

    f = scenario.fault
    row = {"load_scale": scenario.load_scale, "total_load_mw": total_load,
           "n_ibr": len(changes), "ibr_mw": ibr_mw, "ibr_share": ibr_mw / total_gen,
           "inertia_mws": hs, "inertia_h_s": hs / sync_mw,
           "scr_min": min(scr) if scr else NO_IBR_SCR}
    if f is not None:
        fb = int(f.bus)
        row.update({
            "fault_sc_mva": abs(1 / Z[pos[fb], pos[fb]]) * sbase,
            "fault_v_pre_pu": float(v.get(fb, np.nan)),
            "z_fault_ibr_min_pu": min((dist(fb, c["hv_bus"]) for c in changes),
                                      default=NO_IBR_DIST),
            "z_fault_gen_min_pu": min((dist(fb, b) for b, *_ in machines), default=NO_IBR_DIST),
            "clear_time_s": f.clear_time, "fault_x_pu": f.x_pu})
    else:
        row.update(dict.fromkeys(("fault_sc_mva", "fault_v_pre_pu", "z_fault_ibr_min_pu",
                                  "z_fault_gen_min_pu", "clear_time_s", "fault_x_pu"), np.nan))
    row["pf_converged"] = pf_ok
    row["scr_by_ibr"] = ";".join(f"{c['gen']}@{c['hv_bus']}:{s:.2f}" for c, s in zip(changes, scr))
    return row
