"""Command line.

    ibr-faultscreen example                 quick demo on the IEEE 39-bus case, opens a report
    ibr-faultscreen check CASE [--dyr F]    is a case ready for screening?
    ibr-faultscreen simulate SCENARIOS      run scenarios (.yaml/.csv), plots + report
    ibr-faultscreen cct --bus B             critical clearing time at a bus
    ibr-faultscreen dataset --n N           generate a labeled dataset (resumable)
    ibr-faultscreen train DATASET.csv       evaluate models honestly and train one
    ibr-faultscreen screen --model M        rank new scenarios, verify by simulation
    ibr-faultscreen report DATASET.csv      HTML report from a dataset or results file

Run any command with --help for its options.
"""

from __future__ import annotations

import argparse
import csv
import sys
import webbrowser
from pathlib import Path


def _quiet():
    import andes
    andes.config_logger(stream_level=50)


def _criteria(path):
    from .criteria import default_criteria_path, load_criteria
    p = path or default_criteria_path()
    return load_criteria(p), str(path or "built-in PRC-029-1 file (illustrative non-standard limits)")


def _open(path, flag):
    if flag:
        webbrowser.open(Path(path).resolve().as_uri())


def _ibr_specs(gens, kind="solar"):
    from .scenario import IBRSpec
    return tuple(IBRSpec(int(g) if str(g).isdigit() else g, kind) for g in gens or [])


def make_output_dir(path) -> Path:
    """Create an output folder that Git ignores (it holds a one-line .gitignore),
    so results never end up in a commit by accident."""
    path = Path(path)
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    gi = path / ".gitignore"
    # Only in a folder this command created: never in an existing folder such as
    # the project root, where "*" would hide the user's own files from Git.
    if created or (gi.exists() and "created by ibr-faultscreen" in gi.read_text("utf-8")):
        gi.write_text("# created by ibr-faultscreen: results are not committed\n*\n",
                      encoding="utf-8")
    return path


def _write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ commands

def cmd_check(a):
    from .check import check_case
    _quiet()
    rep = check_case(a.case, a.dyr)
    print(rep.text())
    return 0 if rep.ok else 1


def _run_scenarios(scenarios, settings, crit_src, out, plots, open_report, title, case, dyr):
    from .features import compute_features
    from .label import label
    from .plots import plot_scenario, trace_payload
    from .report import build_report
    from .simulate import simulate
    make_output_dir(out)
    rows, figs, traces = [], {}, {}
    for i, sc in enumerate(scenarios, 1):
        res = simulate(sc, stop_angle_deg=max(180.0, settings.angle_limit_deg))
        lab = label(res, settings)
        row = {**res.summary(), **lab.summary()}
        try:
            row.update(compute_features(sc))
        except Exception as err:
            row["feature_error"] = str(err)
        rows.append(row)
        if plots:
            figs[sc.scenario_id] = plot_scenario(res, lab, settings, out / f"{sc.scenario_id}.png")
            traces[sc.scenario_id] = trace_payload(res, lab, settings)
        print(f"[{i}/{len(scenarios)}] {sc.scenario_id}: {row['label']}"
              f"{' (' + row['resolution'] + ')' if row['resolution'] else ''} [{res.status}]")
    _write_csv(out / "results.csv", rows)
    ranked = sorted(rows, key=lambda r: r["label"] != "RISKY")
    top = {r["scenario_id"]: figs[r["scenario_id"]] for r in ranked[:12] if r["scenario_id"] in figs}
    rep = build_report(rows, out / "report.html", title=title, case=case, dyr=dyr,
                       criteria_source=crit_src, figures=top,
                       traces={k: traces[k] for k in top})
    print(f"\nResults: {out / 'results.csv'}\nReport:  {rep}")
    _open(rep, open_report)
    return 0


def cmd_simulate(a):
    from .scenario import load_scenarios
    _quiet()
    settings, src = _criteria(a.criteria)
    scenarios = load_scenarios(a.scenarios)
    case = scenarios[0].case if scenarios else "ieee39"
    return _run_scenarios(scenarios, settings, src, Path(a.out), not a.no_plots, a.open,
                          a.title, case, scenarios[0].dyr if scenarios else None)


def cmd_example(a):
    from .scenario import FaultSpec, IBRSpec, Scenario
    _quiet()
    settings, src = _criteria(None)
    sc = [Scenario("E1_no_ibr_b16_6c", fault=FaultSpec(16, clear_time=0.1)),
          Scenario("E2_ibr2_b16_6c", ibrs=(IBRSpec(2),), fault=FaultSpec(16, clear_time=0.1)),
          Scenario("E3_ibr2_b6_9c", ibrs=(IBRSpec(2),), fault=FaultSpec(6, clear_time=0.15)),
          Scenario("E4_ibr269_b16_5c", ibrs=(IBRSpec(2), IBRSpec(6), IBRSpec(9)),
                   fault=FaultSpec(16, clear_time=0.0833)),
          Scenario("E5_ibr269_b26_6c", ibrs=(IBRSpec(2), IBRSpec(6), IBRSpec(9)),
                   fault=FaultSpec(26, clear_time=0.1))]
    print("Running 5 example scenarios on the IEEE 39-bus case (about a minute)...")
    return _run_scenarios(sc, settings, src, Path(a.out), True, not a.no_open,
                          "IBR-FaultScreen example (IEEE 39-bus)", "ieee39", None)


def cmd_cct(a):
    from .cct import critical_clearing_time
    from .scenario import FaultSpec, Scenario
    _quiet()
    settings, _ = _criteria(a.criteria)
    out = []
    for bus in a.bus:
        sc = Scenario(f"cct_b{bus}", case=a.case, dyr=a.dyr, load_scale=a.load,
                      ibrs=_ibr_specs(a.ibr), fault=FaultSpec(int(bus), x_pu=a.x))
        r = critical_clearing_time(sc, t_min_s=a.t_min, t_max_s=a.t_max,
                                   angle_limit_deg=settings.angle_limit_deg)
        out.append({"fault_bus": bus, **r.summary(), "text": r.text()})
        print(f"bus {bus}: {r.text()}")
        if not a.no_equal_area and r.stable_s is not None:
            _cct_equal_area(sc, r, a, bus)
    if a.out:
        make_output_dir(Path(a.out).parent)
        _write_csv(a.out, out)
        print(f"Saved {a.out}")
    return 0


def _cct_equal_area(sc, r, a, bus):
    """Equal-area view just below and above the CCT (two more simulations)."""
    from dataclasses import replace
    from .equal_area import equal_area
    from .simulate import simulate
    runs = []
    for label_, ct in (("just stable", r.stable_s), ("just unstable", r.unstable_s)):
        if ct is None:
            continue
        res = simulate(replace(sc, fault=replace(sc.fault, clear_time=ct), t_end=sc.fault.t_start + ct + 2.0))
        ea = equal_area(res)
        print(f"  equal-area view, {label_} ({ct * 1000:.0f} ms): {ea.text()}")
        runs.append(res if ea.applicable else None)
    if a.out and runs and runs[0] is not None:
        from .plots import plot_cct_equal_area
        png = Path(a.out).with_name(f"cct_b{bus}_equal_area.png")
        make_output_dir(png.parent)
        plot_cct_equal_area(runs[0], runs[1] if len(runs) > 1 else None, png,
                            title=f"Fault at bus {bus}: equal-area view around the critical clearing time")
        print(f"  saved {png}")


def cmd_dataset(a):
    from .dataset import SamplingPlan, run_dataset, sample_scenarios
    settings, src = _criteria(a.criteria)
    kw = {"n": a.n, "seed": a.seed, "case": a.case, "max_ibrs": a.max_ibrs,
          "ibr_profile": a.ibr_profile,
          "clear_range_s": (a.clear_min, a.clear_max), "load_range": (a.load_min, a.load_max)}
    if a.fault_buses:
        kw["fault_buses"] = tuple(int(b) for b in a.fault_buses)
    if a.ibr_gens:
        kw["ibr_gens"] = tuple(int(g) for g in a.ibr_gens)
    if a.scenarios:
        from .scenario import load_scenarios
        plan, scenarios = None, load_scenarios(a.scenarios)     # checks ids are unique
    else:
        plan = SamplingPlan(**kw)
        scenarios = sample_scenarios(plan)
    if a.dyr:
        from dataclasses import replace
        scenarios = [replace(s, dyr=a.dyr) for s in scenarios]
    make_output_dir(a.out)
    origin = f"from {Path(a.scenarios).name}" if a.scenarios else f"seed {a.seed}"
    print(f"{len(scenarios)} scenarios ({origin}), {a.workers or 'auto'} workers -> {a.out}")
    r = run_dataset(scenarios, settings, a.out, workers=a.workers, timeout_s=a.timeout,
                    plan=plan, criteria_source=src, progress=lambda m: print(m, flush=True),
                    scenario_source=Path(a.scenarios).name if a.scenarios else "")
    print(f"\nDone in {r.seconds / 60:.1f} min. Outcomes: {r.counts}")
    if r.dataset_csv:
        print(f"Dataset:   {r.dataset_csv}\nData card: {r.data_card}")
    return 0


def cmd_train(a):
    from .dataset import file_sha256
    from .model import (SOLVER_HARD_RATE, evaluate_models, load_table, load_unknown, model_check,
                        save_report_json, solver_failure_rates, train_final, write_model_card)
    df = load_table(a.dataset, include_unknown=a.include_unknown)
    if df.attrs.get("n_unknown") and not a.include_unknown:
        print(f"Left out {df.attrs['n_unknown']} scenarios with unknown outcome "
              "(unresolved failures, timeouts, errors). Use --include-unknown to keep them.")
    out = make_output_dir(a.out)
    print(f"{len(df)} scenarios ({int(df['risky'].sum())} RISKY), "
          f"{df['fault_bus'].nunique()} fault buses. Evaluating (grouped by fault bus)...")
    unknown = None if a.include_unknown else load_unknown(a.dataset)
    rep = evaluate_models(df, recall_target=a.recall, unknown=unknown)
    for name, m in rep["models"].items():
        print(f"  {name:9s} recall {m['recall']:.2f}  precision {m['precision']:.2f}  "
              f"AUC {m['auc']:.3f}  AP {m['ap']:.3f}  flagged {m['flagged_share'] * 100:.0f}%")
    ok, msg = model_check(rep)
    print(f"Model check (unseen fault locations; recall on target, better ranking than the "
          f"simple rule): {'PASS' if ok else 'FAIL'}\n  {msg}")
    g = rep["models"].get(a.model, {})
    if g.get("effort"):
        print("Study effort (" + a.model + "): " + ", ".join(
            f"recall {e['recall_target']:.2f} -> {e['flagged_share'] * 100:.0f}% simulated"
            for e in g["effort"]))
    if g.get("unknown_scored"):
        print(f"Unknown-outcome scenarios the model alone would send to simulation: "
              f"{g['unknown_flagged_share'] * 100:.0f}% of {g['unknown_scored']}")
    rates = solver_failure_rates(a.dataset)
    hard = sorted(b for b, r in rates.items() if r["rate"] >= SOLVER_HARD_RATE)
    print(f"Solver-hard fault buses (always simulated by 'screen'): "
          f"{', '.join(map(str, hard)) or 'none'}")
    bundle = train_final(df, a.model, recall_target=a.recall, report=rep,
                         dataset_sha=file_sha256(a.dataset), solver_rates=rates)
    bundle.save(out / "model.joblib")
    save_report_json(rep, out / "evaluation.json")
    write_model_card(bundle, out / "model-card.md")
    print(f"Model: {out / 'model.joblib'} (threshold {bundle.threshold:.4g})\n"
          f"Card:  {out / 'model-card.md'}")
    return 0


def cmd_screen(a):
    from .dataset import SamplingPlan, sample_scenarios
    from .model import load_bundle
    from .plots import plot_scenario, trace_payload
    from .report import build_report
    from .scenario import load_scenarios
    from .screen import all_risky_verified, screen
    _quiet()
    settings, src = _criteria(a.criteria)
    bundle = load_bundle(a.model)
    if a.scenarios:
        cands = load_scenarios(a.scenarios)
    else:
        from dataclasses import replace
        cands = [replace(s, scenario_id="C" + s.scenario_id[1:])      # C = candidate
                 for s in sample_scenarios(SamplingPlan(n=a.n, seed=a.seed, case=a.case))]
    print(f"Screening {len(cands)} scenarios with model '{bundle.name}' "
          f"(threshold {bundle.threshold:.4g})...")
    rows = screen(cands, bundle, settings, borderline=a.borderline, max_verify=a.max_verify,
                  progress=print, solver_hard_rate=a.solver_hard_rate)
    out = make_output_dir(a.out)
    table, figs, traces = [], {}, {}
    for r in rows:
        row = r.summary()
        row.update(r.features or {})
        row["fault_bus"] = r.scenario.fault.bus if r.scenario.fault else ""
        row["ibrs"] = ";".join(f"{i.gen}:{i.kind}" for i in r.scenario.ibrs)
        table.append(row)
        if r.sim_result is not None and r.verdict.startswith("RISKY") and len(figs) < 12:
            figs[r.scenario.scenario_id] = plot_scenario(r.sim_result, r.sim_labels, settings,
                                                         out / f"{r.scenario.scenario_id}.png")
            traces[r.scenario.scenario_id] = trace_payload(r.sim_result, r.sim_labels, settings)
    _write_csv(out / "screen.csv", table)
    card = Path(a.model).with_name("model-card.md")
    rep = build_report(table, out / "report.html", title="IBR-FaultScreen screening report",
                       case=cands[0].case if cands else "ieee39",
                       criteria_source=src, figures=figs, traces=traces,
                       model_card_md=card.read_text(encoding="utf-8") if card.exists() else None)
    print(f"Every RISKY verdict verified by simulation: "
          f"{'PASS' if all_risky_verified(rows) else 'FAIL'}")
    print(f"Results: {out / 'screen.csv'}\nReport:  {rep}")
    _open(rep, a.open)
    return 0


def cmd_report(a):
    from .report import build_report
    with open(a.dataset, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    card = Path(a.model_card).read_text(encoding="utf-8") if a.model_card else None
    make_output_dir(Path(a.out).parent)
    rep = build_report(rows, a.out, title=a.title, case=a.case, dyr=a.dyr,
                       criteria_source=a.criteria_note, model_card_md=card,
                       dataset_note=a.note)
    print(f"Report: {rep}")
    _open(rep, a.open)
    return 0


# ------------------------------------------------------------------ parser

def build_parser():
    p = argparse.ArgumentParser(prog="ibr-faultscreen",
                                description="Fault-scenario screening for grids with IBRs.")
    from . import __version__
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("example", help="quick demo on the IEEE 39-bus case")
    s.add_argument("--out", default="example_output")
    s.add_argument("--no-open", action="store_true", help="do not open the report")
    s.set_defaults(func=cmd_example)

    s = sub.add_parser("check", help="is a case ready for screening?")
    s.add_argument("case", help="ieee39, an ANDES .xlsx file, or a PSS/E .raw file")
    s.add_argument("--dyr", help="PSS/E .dyr file (default: same name next to the .raw)")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("simulate", help="run scenarios from a .yaml or .csv file")
    s.add_argument("scenarios")
    s.add_argument("--criteria", help="criteria .yaml (default: built-in PRC-029-1 file)")
    s.add_argument("--out", default="simulate_output")
    s.add_argument("--title", default="IBR-FaultScreen results")
    s.add_argument("--no-plots", action="store_true")
    s.add_argument("--open", action="store_true", help="open the report when done")
    s.set_defaults(func=cmd_simulate)

    s = sub.add_parser("cct", help="critical clearing time at one or more buses")
    s.add_argument("--bus", nargs="+", required=True)
    s.add_argument("--case", default="ieee39")
    s.add_argument("--dyr")
    s.add_argument("--ibr", nargs="*", help="generators to replace with IBRs")
    s.add_argument("--load", type=float, default=1.0)
    s.add_argument("--x", type=float, default=1e-4, help="fault reactance, pu (default bolted)")
    s.add_argument("--t-min", type=float, default=0.02)
    s.add_argument("--t-max", type=float, default=0.5)
    s.add_argument("--criteria")
    s.add_argument("--out", help="save results to this .csv (and an equal-area figure per bus)")
    s.add_argument("--no-equal-area", action="store_true",
                   help="skip the equal-area view just below and above the CCT")
    s.set_defaults(func=cmd_cct)

    s = sub.add_parser("dataset", help="generate a labeled dataset (resumable)")
    s.add_argument("--scenarios", help="run the scenarios in this .yaml or .csv file instead of "
                                       "sampling them (the sampling options are then ignored)")
    s.add_argument("--n", type=int, default=200)
    s.add_argument("--seed", type=int, default=2026)
    s.add_argument("--case", default="ieee39")
    s.add_argument("--dyr")
    s.add_argument("--max-ibrs", type=int, default=4)
    s.add_argument("--ibr-profile", help="IBR parameter profile (see data/ibr_profiles.yaml)")
    s.add_argument("--ibr-gens", nargs="*", help="generators that may become IBRs")
    s.add_argument("--fault-buses", nargs="*")
    s.add_argument("--clear-min", type=float, default=0.05)
    s.add_argument("--clear-max", type=float, default=0.30)
    s.add_argument("--load-min", type=float, default=0.9)
    s.add_argument("--load-max", type=float, default=1.1)
    s.add_argument("--workers", type=int, help="parallel processes (default: CPUs - 1)")
    s.add_argument("--timeout", type=float, default=300, help="seconds per scenario")
    s.add_argument("--criteria")
    s.add_argument("--out", default="dataset_output")
    s.set_defaults(func=cmd_dataset)

    s = sub.add_parser("train", help="evaluate models and train the screening model")
    s.add_argument("dataset")
    s.add_argument("--model", default="gbm", choices=("gbm", "logistic", "rule"))
    s.add_argument("--recall", type=float, default=0.95, help="recall target on RISKY")
    s.add_argument("--include-unknown", action="store_true",
                   help="also train on unresolved failures (labeled RISKY)")
    s.add_argument("--out", default="model_output")
    s.set_defaults(func=cmd_train)

    s = sub.add_parser("screen", help="rank new scenarios and verify the risky ones")
    s.add_argument("--model", required=True, help="model.joblib from 'train'")
    s.add_argument("--scenarios", help=".yaml/.csv file (default: sample new ones)")
    s.add_argument("--n", type=int, default=100)
    s.add_argument("--seed", type=int, default=99)
    s.add_argument("--case", default="ieee39")
    s.add_argument("--borderline", type=float, default=0.15)
    s.add_argument("--max-verify", type=int, help="simulation budget")
    s.add_argument("--solver-hard-rate", type=float,
                   help="always simulate fault buses with at least this share of unknown "
                        "outcomes in the training dataset (default: the model's value, 0.2)")
    s.add_argument("--criteria")
    s.add_argument("--out", default="screen_output")
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_screen)

    s = sub.add_parser("report", help="HTML report from dataset.csv or results.csv")
    s.add_argument("dataset")
    s.add_argument("--out", default="report_output/report.html")
    s.add_argument("--title", default="IBR-FaultScreen report")
    s.add_argument("--case", default="ieee39")
    s.add_argument("--dyr")
    s.add_argument("--model-card")
    s.add_argument("--criteria-note", default="")
    s.add_argument("--note", default="")
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_report)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
