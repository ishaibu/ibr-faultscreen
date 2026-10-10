"""Scenario definitions: an operating point plus a disturbance.

A scenario says which case to use, how to scale the load, which generators
become IBRs, and which fault to apply. Scenarios are validated on creation,
so bad inputs fail early with a clear message.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

IBR_KINDS = ("solar", "bess")


def _positive(name, value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero, got {value}")


@dataclass(frozen=True)
class IBRSpec:
    """Replace the synchronous machine on generator `gen` with an IBR."""

    gen: int | str
    kind: str = "solar"
    #: Inverter current limit, pu on the IBR's MVA rating. The default 1.1 pu
    #: is a screening assumption; use the manufacturer's value when known.
    imax_pu: float = 1.1
    #: Control-parameter profile (see data/ibr_profiles.yaml). None = the
    #: package default (case.DEFAULT_IBR_PROFILE).
    profile: str | None = None

    def __post_init__(self):
        if self.kind not in IBR_KINDS:
            raise ValueError(f"IBR kind must be one of {IBR_KINDS}, got {self.kind!r}")
        _positive("imax_pu", self.imax_pu)


@dataclass(frozen=True)
class FaultSpec:
    """A balanced three-phase bus fault, cleared without a topology change."""

    bus: int | str
    t_start: float = 1.0
    #: Fault duration in seconds (clearing time measured from fault start).
    clear_time: float = 0.1
    r_pu: float = 0.0
    #: Fault reactance, pu on the system base. A tiny value models a bolted fault.
    x_pu: float = 1e-4

    def __post_init__(self):
        _positive("t_start", self.t_start)
        _positive("clear_time", self.clear_time)
        for name in ("r_pu", "x_pu"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{name} must be zero or positive, got {v!r}")
        if self.r_pu == 0 and self.x_pu == 0:
            raise ValueError("fault impedance cannot be exactly zero; use a small x_pu (e.g. 1e-4)")


@dataclass(frozen=True)
class Scenario:
    """One screening case: operating point + optional fault."""

    scenario_id: str
    case: str = "ieee39"
    load_scale: float = 1.0
    ibrs: tuple[IBRSpec, ...] = field(default_factory=tuple)
    fault: FaultSpec | None = None
    t_end: float = 10.0
    #: PSS/E dynamic data file for a .raw case (optional; see case.read_case).
    dyr: str | None = None

    def __post_init__(self):
        if not str(self.scenario_id).strip():
            raise ValueError("scenario_id cannot be empty")
        _positive("load_scale", self.load_scale)
        _positive("t_end", self.t_end)
        gens = [str(s.gen) for s in self.ibrs]
        if len(gens) != len(set(gens)):
            raise ValueError(f"scenario {self.scenario_id}: a generator is listed twice as an IBR")
        if self.fault and self.t_end <= self.fault.t_start + self.fault.clear_time:
            raise ValueError(f"scenario {self.scenario_id}: t_end must be after the fault is cleared")


def _number(value, name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None


def _gen_id(text):
    text = str(text).strip()
    return int(text) if text.isdigit() else text


def scenario_from_dict(d: dict) -> Scenario:
    """Build a Scenario from a plain dict (as read from YAML)."""
    ibrs = tuple(IBRSpec(gen=_gen_id(i["gen"]), kind=i.get("kind", "solar"),
                         imax_pu=_number(i.get("imax_pu", 1.1), "imax_pu"),
                         profile=i.get("profile") or None)
                 for i in d.get("ibrs") or [])
    f = d.get("fault")
    fault = None if not f else FaultSpec(
        bus=_gen_id(f["bus"]),
        t_start=_number(f.get("t_start", 1.0), "t_start"),
        clear_time=_number(f.get("clear_time", 0.1), "clear_time"),
        r_pu=_number(f.get("r_pu", 0.0), "r_pu"),
        x_pu=_number(f.get("x_pu", 1e-4), "x_pu"))
    return Scenario(scenario_id=str(d["scenario_id"]), case=d.get("case", "ieee39"),
                    load_scale=_number(d.get("load_scale", 1.0), "load_scale"),
                    ibrs=ibrs, fault=fault, t_end=_number(d.get("t_end", 10.0), "t_end"),
                    dyr=d.get("dyr") or None)


def _row_to_dict(row: dict) -> dict:
    """CSV row -> dict. `ibrs` column: '2:solar;6:bess' (kind optional)."""
    def get(key, default=None):
        v = (row.get(key) or "").strip()
        return v if v else default

    ibrs = []
    for item in (get("ibrs", "") or "").split(";"):
        if item.strip():
            gen, _, kind = item.partition(":")
            ibrs.append({"gen": gen, "kind": kind.strip() or "solar",
                         "imax_pu": get("imax_pu", 1.1)})
    fault = None
    if get("fault_bus"):
        fault = {"bus": get("fault_bus"), "t_start": get("fault_start_s", 1.0),
                 "clear_time": get("clear_time_s", 0.1), "r_pu": get("fault_r_pu", 0.0),
                 "x_pu": get("fault_x_pu", 1e-4)}
    return {"scenario_id": get("scenario_id", ""), "case": get("case", "ieee39"),
            "load_scale": get("load_scale", 1.0), "ibrs": ibrs, "fault": fault,
            "t_end": get("t_end_s", 10.0), "dyr": get("dyr")}


def load_scenarios(path: str | Path) -> list[Scenario]:
    """Read scenarios from a .yaml/.yml file (a list) or a .csv file (one per row)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(items, list):
            raise ValueError("YAML scenario file must contain a list of scenarios")
        rows = items
    elif suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = [_row_to_dict(r) for r in csv.DictReader(fh)]
    else:
        raise ValueError(f"unsupported scenario file type {suffix!r}; use .yaml or .csv")
    scenarios = []
    for n, item in enumerate(rows, start=1):
        try:
            scenarios.append(scenario_from_dict(item))
        except (KeyError, ValueError) as err:
            raise ValueError(f"{path.name}, scenario {n}: {err}") from None
    ids = [s.scenario_id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name}: scenario_id values must be unique")
    return scenarios
