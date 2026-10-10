# IBR-FaultScreen owner's guide

For the maintainer (Shaibu Ibrahim, P.E.). How the tool works, how to answer user
questions, and how to release new versions. Plain language first, details after.

## 1. The tool in one paragraph

IBR-FaultScreen tells an interconnection engineer which fault scenarios on a grid
with solar, wind or batteries deserve a detailed study first. It simulates
three-phase faults (ANDES, RMS), judges each one against screening criteria
(rotor angle, voltage recovery, frequency, NERC PRC-029-1 ride-through), and can
train a classifier that ranks thousands of new scenarios quickly. Every RISKY
verdict shown to a user comes from a simulation, never from the classifier alone.

**One-sentence pitch:** "IBR-FaultScreen ranks fault contingencies on IBR-heavy
grids so engineers study the riskiest ones first, and it is honest about what
it cannot solve."

## 2. How it works

```
case (IEEE 39-bus or your PSS/E .raw + .dyr)
  -> scenarios (which generators become IBRs, load, fault bus, clearing time)
  -> ANDES simulation  -> labels (RISKY / OK by failure mode)
  -> pre-fault features (SCR, distances, inertia)  -> dataset
  -> classifier (tested on fault buses it never saw)  -> screen new scenarios
  -> re-simulate predicted-RISKY and borderline ones  -> HTML report
```

| Step | Module | Key idea |
|---|---|---|
| Build the case | `case.py` | A machine replaced by an IBR is removed with all its controls; the IBR takes the machine's rating and dispatch |
| Simulate | `simulate.py` | Never crashes on numerics; finer-step retry; flags non-physical results |
| Bolted faults that fail | `simulate.py`, `label.py` | Milder-fault check: rerun at milder faults; "confirmed" or "unresolved" |
| Judge | `criteria.py`, `label.py` | Thresholds come from a YAML file; unset limits are "not evaluated", never guessed |
| CCT | `cct.py` | Bisection to half a cycle; "at least X ms" when bolted runs fail |
| Dataset | `dataset.py`, `features.py` | Same seed, same file; resumable |
| Models | `model.py`, `screen.py` | Grouped by fault bus; recall-first threshold; solver-hard buses always simulated |
| Report | `report.py`, `plots.py` | One HTML file; SCR-Screen style |

## 3. Commands users will ask about

| Task | Command |
|---|---|
| See it work | `ibr-faultscreen example` |
| Check my model | `ibr-faultscreen check mycase.raw` |
| Run my scenarios | `ibr-faultscreen simulate my_scenarios.yaml --open` |
| Critical clearing time | `ibr-faultscreen cct --bus 16 --ibr 2` |
| Make a dataset | `ibr-faultscreen dataset --n 5000` |
| Train | `ibr-faultscreen train dataset_output/dataset.csv` |
| Screen new cases | `ibr-faultscreen screen --model model_output/model.joblib --n 200 --open` |

Every output folder contains a one-line `.gitignore`, so results never end up in Git.

## 4. Reading results

- **RISKY / OK**: RISKY if any criterion is violated or the run could not be solved.
- **RISKY (confirmed)**: the bolted fault could not be solved, but a slightly milder
  fault is already RISKY. The bolted case is assumed to be at least as bad.
- **RISKY (unresolved)**: the bolted fault could not be solved and the milder fault
  is OK. The outcome is unknown: study it in a commercial RMS tool or EMT.
- **Source = simulated / predicted**: whether a verdict comes from a simulation.
- **"CCT at least 185 ms"**: longer clearing times could not be solved; the milder-
  fault CCT is given as an upper estimate.

## 5. FAQ (answers you can paste)

**"python is not recognized" (Windows).** Python is not on PATH. Reinstall Python
from python.org and tick "Add python.exe to PATH", then open a new terminal.

**"Running scripts is disabled" when activating the environment.** Run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` in that window,
then activate again. It applies to that window only.

**"An Application Control policy has blocked this file."** Windows Smart App
Control is blocking a compiled scientific library. On a personal computer it can be
turned off in Windows Security; on a work computer, ask IT.

**The first run is slow.** ANDES generates code for its models once (a few
minutes on some machines). Later runs are fast.

**What does sim_fail mean? Is the tool broken?** No. ANDES could not solve that case
(or reached a physically impossible answer, which the tool detects). It is labeled
RISKY, never OK, and the milder-fault check reruns it at a milder fault to say more.

**Why does `screen` simulate so many scenarios?** It simulates every scenario the
model ranks as RISKY or borderline, plus every scenario at a fault bus where the
solver often fails (it cannot predict those, and it must never quietly pass
them). On the IEEE 39-bus dataset that is about 70% of scenarios at 95% recall.
The model card shows the trade-off (`--recall` in `train`).

**Why do my results differ from PSS/E or PSLF?** Different engines, models and
settings: generic WECC IBR parameters vs. the plant's own, fault impedance, solver
step, protection not modeled, criteria file. Use IBR-FaultScreen to rank; confirm
the top cases in your study tool.

**Can I use it on my company's real network?** Yes, on your own computer, with data
you are permitted to use. Nothing is sent anywhere. Never send your network files
to the maintainer; send a description or an anonymized example instead.

**Which criteria should I use?** Your transmission provider's. The built-in file
has NERC PRC-029-1 ride-through tables (verify the version in force) and
illustrative angle, voltage-recovery and frequency limits. Copy it, edit it, and
pass `--criteria myfile.yaml`.

**Can it model unbalanced faults, EMT effects or my inverter's exact controls?**
Not in v0.1. It uses balanced faults, RMS models and generic WECC IBR models.

**Which PSS/E versions work?** RAW versions 32, 33 and 34 are parsed by ANDES;
v35 is untested. `ibr-faultscreen check` lists dynamic models ANDES cannot read.

**Why is a battery not allowed to charge?** ANDES 2.0.0 has no WECC battery
controller (REEC_C), and REECA1 cannot absorb active power. Planned for v0.2.

## 6. Supporting users

1. Ask for: the exact command, the full error text (screenshot), Windows/macOS,
   Python version (`python --version`) and `ibr-faultscreen --version`.
2. Reproduce on the IEEE 39-bus case if you can.
3. Answer from the FAQ; if it is a bug, open a GitHub issue with a minimal example.

GitHub labels to create: `bug`, `question`, `documentation`, `enhancement`,
`solver` (ANDES convergence), `criteria`.

**Reply template:** "Thanks for trying IBR-FaultScreen. Could you send the exact
command you ran, a screenshot of the full error, and the output of
`ibr-faultscreen --version` and `python --version`? Please don't share network
files; an anonymized description is enough."

## 7. Releasing a new version

In short (the maintainer keeps a detailed click-by-click checklist privately):

1. Update the version in `pyproject.toml` and `CITATION.cff`, and add a section to
   `CHANGELOG.md`.
2. `pytest` (all pass) and `python tools/prerelease_check.py` (all pass).
3. Commit and push; wait for the green check on GitHub Actions.
4. Optional practice: Actions → release → Run workflow (uploads to TestPyPI).
5. GitHub → Releases → Draft a new release → tag `vX.Y.Z` → paste the changelog →
   Publish. The release workflow uploads to PyPI; Zenodo archives the release and
   issues a DOI.

## 8. Keeping it trustworthy

- Every new rule or formula gets a test, and a hand check where possible
  (`docs/verification.md`).
- Never commit employer or client data, credentials or results folders.
- Keep employer names off the tool's records (Zenodo, ORCID, CITATION.cff): it is
  a personal project, cleared as such.
- When ANDES or a standard changes, re-run the tests and re-check the criteria file.

## 9. Glossary

| Term | Meaning |
|---|---|
| IBR | Inverter-based resource: solar, wind (type 3/4), battery storage |
| RMS simulation | Phasor (positive-sequence) dynamic simulation, as in PSS/E or PSLF |
| EMT | Electromagnetic transient simulation (detailed waveforms, PSCAD-type) |
| CCT | Critical clearing time: longest fault duration that stays stable |
| SCR | Short-circuit ratio at an IBR's point of interconnection |
| Bolted fault | Fault with (nearly) zero impedance; here 1e-4 pu |
| PRC-029-1 | NERC standard for IBR voltage and frequency ride-through |
| Milder-fault check | Rerunning an unsolvable bolted fault at milder faults to learn more |
| Solver-hard fault bus | A fault location where the solver often fails; always simulated by `screen` |
