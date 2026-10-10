"""Check a user's case before screening (`ibr-faultscreen check`).

Reports, in plain words: what was loaded, which PSS/E dynamic models ANDES
cannot read (OEM user-written models, for example), which generators can be
turned into IBRs, and whether the power flow solves.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import andes
import yaml

from .case import SYNC_MACHINE_SHEETS, build_system, find_dyr, read_case, remove_events


def supported_dyr_models() -> set[str]:
    """PSS/E .dyr model names ANDES can parse (from its own mapping file)."""
    path = os.path.join(os.path.dirname(andes.__file__), "io", "psse-dyr.yaml")
    with open(path, encoding="utf-8") as fh:
        return {k.upper() for k in yaml.safe_load(fh)}


def dyr_models(path) -> dict[str, int]:
    """Count of each model name in a .dyr file. Records end with '/'."""
    text = open(path, encoding="utf-8", errors="replace").read()
    counts = {}
    for rec in text.split("/"):
        m = re.match(r"\s*\S+\s+'([^']+)'", rec)
        if m:
            name = m.group(1).strip().upper()
            counts[name] = counts.get(name, 0) + 1
    return counts


@dataclass
class CheckReport:
    case: str
    dyr: str | None
    n_bus: int = 0
    models: dict = field(default_factory=dict)
    dyr_counts: dict = field(default_factory=dict)
    unsupported: dict = field(default_factory=dict)
    ibr_capable: list = field(default_factory=list)      # (gen idx, bus, MVA)
    slack: list = field(default_factory=list)
    removed_events: list = field(default_factory=list)
    pf_converged: bool = False
    errors: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors and self.pf_converged

    def text(self) -> str:
        L = [f"Case: {self.case}"]
        if self.dyr:
            L.append(f"Dynamic data: {self.dyr}")
        L.append(f"Buses: {self.n_bus}")
        L.append("Models: " + ", ".join(f"{k} {v}" for k, v in sorted(self.models.items())))
        if self.dyr_counts:
            L.append(".dyr models: " + ", ".join(f"{k} {v}" for k, v in sorted(self.dyr_counts.items())))
            if self.unsupported:
                L.append("NOT SUPPORTED by ANDES (these devices are left out of the "
                         "simulation): " + ", ".join(f"{k} ({v})" for k, v in
                                                     sorted(self.unsupported.items())))
            else:
                L.append("All .dyr models are supported.")
        L.append("Generators that can become IBRs: " + (", ".join(
            f"gen {g} at bus {b} ({mva:g} MVA)" for g, b, mva in self.ibr_capable) or "none"))
        L.append("Slack (cannot become an IBR): " + (", ".join(map(str, self.slack)) or "none"))
        if self.removed_events:
            L.append("Events already in the case (removed for screening): "
                     + ", ".join(self.removed_events))
        L.append(f"Power flow: {'converged' if self.pf_converged else 'DID NOT CONVERGE'}")
        L += [f"ERROR: {e}" for e in self.errors]
        L.append("Result: " + ("ready for screening" if self.ok else "fix the issues above first"))
        return "\n".join(L)


def check_case(case: str, dyr: str | None = None) -> CheckReport:
    if str(case).lower().endswith(".raw"):
        dyr = dyr or find_dyr(case)
    rep = CheckReport(case, dyr)
    try:
        sheets = read_case(case, dyr)
    except Exception as err:
        rep.errors.append(f"could not read the case: {type(err).__name__}: {err}")
        return rep
    rep.n_bus = len(sheets.get("Bus", []))
    rep.models = {k: len(v) for k, v in sheets.items() if len(v)}
    if dyr:
        rep.dyr_counts = dyr_models(dyr)
        ok = supported_dyr_models()
        rep.unsupported = {k: v for k, v in rep.dyr_counts.items() if k not in ok}
    slack = sheets.get("Slack")
    rep.slack = [] if slack is None else slack["idx"].astype(str).tolist()
    for name in SYNC_MACHINE_SHEETS:
        df = sheets.get(name)
        if df is None:
            continue
        for _, g in df.iterrows():
            if str(g["gen"]) not in rep.slack:
                rep.ibr_capable.append((g["gen"], int(g["bus"]), float(g["Sn"])))
    rep.removed_events = remove_events(sheets)
    try:
        rep.pf_converged = bool(build_system(sheets).PFlow.converged)
    except Exception as err:
        rep.errors.append(f"power flow could not be set up: {type(err).__name__}: {err}")
    return rep
