"""Build ANDES systems for screening.

Load a case, replace synchronous machines with WECC generic IBR models,
scale the load, add a fault, and run the power flow.

Cases are edited as tables (one table per ANDES model), then loaded into
ANDES. This removes a replaced machine *and every control attached to it*
(governor, exciter, stabilizer). Only disabling them left initialization
residuals in an early test.
"""

from __future__ import annotations

import functools
import os
import tempfile

import andes
import pandas as pd

from .scenario import FaultSpec, IBRSpec, Scenario

#: Cases shipped with ANDES that IBR-FaultScreen supports out of the box.
BUILTIN_CASES = {"ieee39": "ieee39/ieee39_full.xlsx"}

#: IBR model parameters are copied from this ANDES example case
#: (REGCA1 + REECA1 + REPCA1 solar plant), then adjusted per IBRSpec.
IBR_TEMPLATE_CASE = "ieee14/ieee14_solar.xlsx"

#: ANDES sheets for scheduled events.
EVENT_SHEETS = ("Toggler", "Toggle", "Alter")

#: Synchronous machine models that can be replaced by an IBR.
SYNC_MACHINE_SHEETS = ("GENROU", "GENSAL", "GENCLS")

#: Active-power limits (pu on the IBR's own MVA rating).
#: ANDES's REECA1 fixes its minimum active current at zero, so a charging
#: battery cannot be represented in v0.1 (REEC_C is not in ANDES 2.0.0).
#: A "bess" IBR therefore uses the same models as "solar" and must be
#: discharging or idle; the kind label is kept for reporting and v0.2.
P_LIMITS = {"solar": (0.0, 1.0), "bess": (0.0, 1.0)}


#: Model formats that can be read: ANDES .xlsx, and PSS/E .raw (+ .dyr).
PSSE_RAW = (".raw",)


def _mtime(path):
    return os.path.getmtime(path) if path and os.path.exists(path) else 0.0


@functools.lru_cache(maxsize=8)
def _read_tables(path: str, dyr: str | None, _mt: tuple) -> dict:
    if path.lower().endswith(".xlsx"):
        sheets = pd.read_excel(path, sheet_name=None)
    else:                                          # PSS/E: let ANDES parse, then export tables
        import andes.io.xlsx
        system = andes.load(path, addfile=dyr, no_output=True, default_config=True)
        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            andes.io.xlsx.write(system, tmp, overwrite=True)
            sheets = pd.read_excel(tmp, sheet_name=None)
        finally:
            os.remove(tmp)
    return {k: v for k, v in sheets.items() if k != "Summary" and not k.startswith("_")}


def find_dyr(raw_path) -> str | None:
    """The .dyr file next to a .raw file with the same name, if there is one."""
    cand = os.path.splitext(str(raw_path))[0] + ".dyr"
    return cand if os.path.exists(cand) else None


def read_case(case: str = "ieee39", dyr: str | None = None) -> dict[str, pd.DataFrame]:
    """Read a case into tables (one DataFrame per ANDES model).

    case: a built-in name ("ieee39"), an ANDES .xlsx file, or a PSS/E .raw file.
    dyr:  PSS/E dynamic data for a .raw case; if omitted, a .dyr file with the
          same name next to the .raw file is used when present.
    Results are cached; each call returns a fresh copy that is safe to edit.
    """
    path = andes.get_case(BUILTIN_CASES[case]) if case in BUILTIN_CASES else str(case)
    low = path.lower()
    if not (low.endswith(".xlsx") or low.endswith(PSSE_RAW)):
        raise ValueError(f"{case!r}: use a built-in case name, an ANDES .xlsx file, "
                         "or a PSS/E .raw file (with its .dyr)")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if low.endswith(PSSE_RAW):
        dyr = dyr or find_dyr(path)
        if dyr is None:
            raise ValueError(f"{case!r}: no .dyr file given or found next to the .raw file; "
                             "dynamic data are needed for fault simulation")
    tables = _read_tables(path, dyr, (_mtime(path), _mtime(dyr)))
    return {k: v.copy() for k, v in tables.items()}


def remove_events(sheets: dict[str, pd.DataFrame]) -> list[str]:
    """Drop every scheduled event and fault already in the case (switched on or
    off), so a scenario contains only the disturbance it defines. ANDES's
    PSS/E import, for example, can add events of its own. Returns the removed ids."""
    removed = []
    for name in EVENT_SHEETS + ("Fault",):
        df = sheets.pop(name, None)
        if df is not None and not df.empty:
            removed += df["idx"].astype(str).tolist()
    return removed


#: Earlier name, kept for compatibility.
remove_disabled_events = remove_events


def scale_load(sheets: dict[str, pd.DataFrame], factor: float) -> None:
    """Scale every load and every generator dispatch by the same factor.

    The slack generator picks up the remaining mismatch and losses.
    """
    if factor <= 0:
        raise ValueError("load scale must be greater than zero")
    for sheet, cols in (("PQ", ("p0", "q0")), ("PV", ("p0",))):
        df = sheets.get(sheet)
        if df is not None:
            for col in cols:
                df[col] = df[col].astype(float) * factor


def _machine_for_gen(sheets, gen):
    for name in SYNC_MACHINE_SHEETS:
        df = sheets.get(name)
        if df is not None and not df.empty:
            hit = df[df["gen"].astype(str) == str(gen)]
            if not hit.empty:
                return name, hit.iloc[0]
    raise ValueError(f"generator {gen!r} has no synchronous machine model to replace")


def _gen_dispatch(sheets, gen):
    """Active-power dispatch of a PV generator (pu, system base), or None for the slack."""
    pv = sheets.get("PV")
    if pv is not None:
        hit = pv[pv["idx"].astype(str) == str(gen)]
        if not hit.empty:
            return float(hit.iloc[0]["p0"])
    return None


def _is_slack(sheets, gen):
    slack = sheets.get("Slack")
    return slack is not None and str(gen) in slack["idx"].astype(str).tolist()


def _step_up_branch(sheets, bus):
    """The IBR's main power transformer: (branch idx, high-side bus).

    PRC-029-1 measures ride-through voltage and frequency at the high side of
    the main power transformer, so the screening checks use that bus. If the
    IBR bus has no transformer, its only branch is used and the high-side bus
    is the IBR bus itself.
    """
    lines = sheets["Line"]
    at_bus = lines[(lines["bus1"] == bus) | (lines["bus2"] == bus)]
    if at_bus.empty:
        raise ValueError(f"bus {bus} has no line or transformer for plant control")
    xfmr = at_bus[at_bus["trans"] == 1] if "trans" in at_bus.columns else at_bus.iloc[0:0]
    if len(xfmr) == 1:
        row = xfmr.iloc[0]
        hv = int(row["bus2"] if int(row["bus1"]) == bus else row["bus1"])
        return row["idx"], hv
    return at_bus.iloc[0]["idx"], bus


def _drop_linked(sheets, column, ids):
    """Drop rows in any sheet whose `column` points at one of `ids`; return dropped ids."""
    dropped = []
    ids = {str(i) for i in ids}
    for name, df in list(sheets.items()):
        if column in df.columns:
            hit = df[column].astype(str).isin(ids)
            if hit.any():
                dropped += df.loc[hit, "idx"].astype(str).tolist()
                sheets[name] = df[~hit]
    return dropped


#: IBR control-parameter profile used when an IBRSpec does not name one.
DEFAULT_IBR_PROFILE = "andes_example"


@functools.lru_cache(maxsize=1)
def ibr_profiles() -> dict:
    """Parameter profiles from data/ibr_profiles.yaml."""
    from importlib.resources import files

    import yaml
    text = files("ibr_faultscreen").joinpath("data", "ibr_profiles.yaml").read_text("utf-8")
    return yaml.safe_load(text)


@functools.lru_cache(maxsize=1)
def _example_rows():
    t = pd.read_excel(andes.get_case(IBR_TEMPLATE_CASE), sheet_name=None)
    return {k: t[k].drop(columns="uid", errors="ignore").iloc[0].to_dict()
            for k in ("REGCA1", "REECA1", "REPCA1")}


def _template_rows(profile: str | None = None):
    name = profile or DEFAULT_IBR_PROFILE
    profiles = ibr_profiles()
    if name not in profiles:
        raise ValueError(f"unknown IBR profile {name!r}; choose from {sorted(profiles)}")
    rows = {k: dict(v) for k, v in _example_rows().items()}
    for model in rows:
        rows[model].update(profiles[name].get(model) or {})
    return rows


def convert_to_ibr(sheets: dict[str, pd.DataFrame], spec: IBRSpec) -> dict:
    """Replace the synchronous machine on generator `spec.gen` with an IBR.

    Removes the machine and all its controls, then adds REGCA1 (generator /
    converter), REECA1 (electrical control) and REPCA1 (plant control).
    The IBR keeps the machine's MVA rating and the generator's dispatch.
    Returns a summary of what changed.
    """
    sheet, machine = _machine_for_gen(sheets, spec.gen)
    syn, bus, sn = str(machine["idx"]), int(machine["bus"]), float(machine["Sn"])
    dispatch = _gen_dispatch(sheets, machine["gen"])
    if dispatch is not None and dispatch < 0:
        raise ValueError(
            f"generator {spec.gen!r} is dispatched at {dispatch:g} pu (absorbing power). "
            "v0.1 IBR models cannot absorb active power, so a charging battery is "
            "not supported yet.")

    sheets[sheet] = sheets[sheet][sheets[sheet]["idx"].astype(str) != syn]
    controls = _drop_linked(sheets, "syn", [syn])          # governors, exciters
    controls += _drop_linked(sheets, "avr", controls)      # stabilizers on those exciters

    if _is_slack(sheets, machine["gen"]):
        raise ValueError(f"generator {spec.gen!r} is the slack (swing) generator; "
                         "it cannot be replaced by an IBR")
    branch, hv_bus = _step_up_branch(sheets, bus)

    tag = f"{spec.kind.upper()}_{spec.gen}"
    t = _template_rows(spec.profile)
    pmin, pmax = P_LIMITS[spec.kind]
    rows = {
        "REGCA1": {**t["REGCA1"], "idx": f"REG_{tag}", "name": f"REG_{tag}",
                   "bus": bus, "gen": machine["gen"], "Sn": sn,
                   # Lower reactive-current limit. The ANDES example uses 0, so the
                   # IBR cannot absorb reactive power, and initialization fails
                   # whenever the replaced machine was absorbing it (generator 8
                   # in the IEEE 39-bus case; found in v0.1 release checks). An
                   # inverter can absorb reactive current up to its current limit.
                   "Iolim": -spec.imax_pu},
        "REECA1": {**t["REECA1"], "idx": f"REE_{tag}", "name": f"REE_{tag}",
                   "reg": f"REG_{tag}", "Imax": spec.imax_pu,
                   "PMIN": pmin, "PMAX": pmax,
                   # Voltage-dependent current limit tables capped at Imax, so
                   # the limits agree at initialization (found in early testing).
                   **{f"I{c}{k}": spec.imax_pu for c in "qp" for k in "1234"}},
        "REPCA1": {**t["REPCA1"], "idx": f"REP_{tag}", "name": f"REP_{tag}",
                   "ree": f"REE_{tag}", "line": branch, "busr": bus},
    }
    for name, row in rows.items():
        new = pd.DataFrame([row])
        sheets[name] = new if name not in sheets else pd.concat(
            [sheets[name], new], ignore_index=True)
    return {"gen": spec.gen, "bus": bus, "hv_bus": hv_bus, "mva": sn, "kind": spec.kind,
            "imax_pu": spec.imax_pu, "profile": spec.profile or DEFAULT_IBR_PROFILE,
            "removed": [syn] + controls}


def build_system(sheets: dict[str, pd.DataFrame], fault: FaultSpec | None = None):
    """Load tables into ANDES, add the fault, set up, and run the power flow.

    Returns the ANDES System. Check ``system.PFlow.converged`` before
    running a time-domain simulation.
    """
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        with pd.ExcelWriter(path) as writer:
            for name, df in sheets.items():
                df.to_excel(writer, sheet_name=name, index=False)
        system = andes.load(path, no_output=True, default_config=True, setup=False)
    finally:
        os.remove(path)
    if fault is not None:
        system.add("Fault", {"bus": fault.bus, "tf": fault.t_start,
                             "tc": fault.t_start + fault.clear_time,
                             "rf": fault.r_pu, "xf": fault.x_pu})
    system.setup()
    system.PFlow.run()
    return system


def build_scenario(scenario: Scenario):
    """Build the ANDES System for one scenario. Returns (system, changes)."""
    sheets = read_case(scenario.case, scenario.dyr)
    remove_events(sheets)
    scale_load(sheets, scenario.load_scale)
    changes = [convert_to_ibr(sheets, spec) for spec in scenario.ibrs]
    return build_system(sheets, scenario.fault), changes


#: Added to the requested end time. Without it, ANDES 2.0.0 sometimes shrinks
#: its final time step to zero exactly at the end time and reports exit code 1,
#: even though the run reached the end (found and diagnosed in early testing).
END_PAD_S = 1e-6


def run_tds(system, t_end: float):
    """Run the time-domain simulation to `t_end` seconds. Returns the exit code.

    Minimal helper for quick checks; the full wrapper is `simulate.simulate`.
    """
    system.TDS.config.tf = t_end + END_PAD_S
    system.TDS.config.no_tqdm = 1
    system.TDS.run()
    return system.exit_code
