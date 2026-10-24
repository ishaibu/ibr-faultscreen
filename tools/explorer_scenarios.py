"""Write the scenario list behind the "Build your own grid" explorer on the demo page.

Every combination is simulated in advance, so the page can show a real result
for whatever the visitor picks:

* IBRs: every set of 0 to 4 of generators 1 to 9 (256 sets; generator 10 is
  the slack). All solar: in this version a battery uses the same models, so it
  would give the same result.
* Fault: a bolted three-phase fault at each of buses 1 to 29.
* Clearing time: 3, 5, 6, 9 and 12 cycles at 60 Hz.
* Load: the base case (load scale 1.0).

256 x 29 x 5 = 37,120 scenarios. Run them with:

    python tools/explorer_scenarios.py
    ibr-faultscreen dataset --scenarios explorer_run/explorer_scenarios.csv --out explorer_run

The run is resumable: if it stops, run the same dataset command again.
"""

from __future__ import annotations

import argparse
import csv
from itertools import combinations
from pathlib import Path

GENS = range(1, 10)
MAX_IBRS = 4
BUSES = range(1, 30)
CYCLES = (3, 5, 6, 9, 12)
FN_HZ = 60.0
T_END_S = 6.0
FAULT_X_PU = 1e-4


def ibr_sets():
    for k in range(MAX_IBRS + 1):
        yield from combinations(GENS, k)


def scenario_id(gens, bus, cycles):
    return f"E{''.join(map(str, gens)) or '0'}_b{bus:02d}_c{cycles:02d}"


def rows():
    for gens in ibr_sets():
        for bus in BUSES:
            for c in CYCLES:
                yield {"scenario_id": scenario_id(gens, bus, c), "case": "ieee39", "load_scale": 1.0,
                       "ibrs": ";".join(f"{g}:solar" for g in gens), "fault_bus": bus,
                       "clear_time_s": round(c / FN_HZ, 6), "fault_x_pu": FAULT_X_PU,
                       "t_end_s": T_END_S}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="explorer_run/explorer_scenarios.csv")
    a = ap.parse_args(argv)
    from ibr_faultscreen.cli import make_output_dir   # the folder ignores itself in Git
    make_output_dir(Path(a.out).parent)
    out = list(rows())
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    print(f"Wrote {len(out):,} scenarios to {Path(a.out).resolve()}")


if __name__ == "__main__":
    main()
