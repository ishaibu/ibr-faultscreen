# IBR-FaultScreen design spec (v0.2)

*Updated 2026-10-23. Author: Shaibu Ibrahim, P.E. License: GPL-3.0-or-later.
Abbreviations are explained in `docs/glossary.md`.*

Labels used in this document:
**[Fact]** verified, with how it was verified ·
**[Assumption]** a working choice to confirm or replace ·
**[To verify]** must be checked before relying on it.

---

## 1. Purpose

IBR-FaultScreen simulates many fault scenarios on a grid with solar, wind, and
battery storage (inverter-based resources, IBRs). It flags risky scenarios
(instability, slow voltage recovery, ride-through exposure), and it trains a
fast classifier, so engineers know which contingencies to study in detail first.

It is a **screening** tool. It prioritizes detailed studies; it does not replace
them.

## 2. Users and use cases

| User | Question the tool answers |
|---|---|
| Interconnection study engineer (developer or consultancy) | Which contingencies near my point of interconnection (POI) are riskiest, and why? |
| Utility planning or protection engineer | How do critical clearing times (CCTs) change as IBR penetration grows? |
| Researcher | A reproducible, labeled IBR fault dataset and baseline models. |
| Manager or non-engineer stakeholder | A one-page, plain-language summary of where the risk is. |

## 3. Scope

**In v0.1**
- IEEE 39-bus system with IBRs at selectable buses and penetration levels.
- User-supplied models in PSS/E `.raw` + `.dyr` format, loaded through ANDES.
- Balanced three-phase faults (bolted and through an impedance); CCT per location.
- Multi-label risk outcome per scenario, plus an overall RISKY / OK label.
- Labeled dataset with a data card; classifier with honest held-out evaluation.
- Outputs: tables, plots, an animated network map, a CCT table, and a one-page summary.

**Added in v0.2**
- Equal-area view of each scenario (single-machine equivalent) and around the CCT.
- Fixed IEEE 39-bus map layout; run any scenario list in parallel (`dataset --scenarios`).
- Demo page: "Build your own grid" explorer (every combination simulated in
  advance), shareable links, quiz mode.

**Later (not in v0.2)**
- Single-line-to-ground (SLG) equivalent faults.
- Grid-forming vs. grid-following comparison (first confirm which grid-forming
  models ANDES provides).
- IEEE 118-bus scalability demo (needs documented assumed dynamic data).
- PSLF and other formats; line faults with breaker trips; N-1-1.

**Out of scope**
- EMT simulation; unbalanced-fault IBR behavior (negative-sequence current).
- Relay coordination and protection settings.
- Proprietary (OEM) inverter models.

## 4. Key decisions

| Topic | Decision |
|---|---|
| Base test system | IEEE 39-bus (ANDES `ieee39/ieee39_full.xlsx`) |
| Simulation engine | ANDES (Python, RMS positive-sequence) |
| License | GPL-3.0-or-later |
| Meaning of "fault classification" | Scenario risk classification by failure mode |
| Criteria | All four in §8; every threshold user-editable; defaults entered by the author from the standard texts |
| IBR models | Grid-following solar + BESS (discharging or idle only, see §5); grid-forming later |
| Fault types | Three-phase (bolted + impedance) only in v0.1 |
| Users' own models | PSS/E `.raw/.dyr` import in v0.1 |
| Machine-learning approach | Gradient boosting (scikit-learn) plus rule and logistic baselines |
| Unsolvable bolted faults (the **milder-fault check**) | Bolted (1e-4 pu) stays the default. If it cannot be solved, rerun at 0.001 then 0.003 pu as a diagnostic: milder fault RISKY → "RISKY (confirmed)"; milder fault OK → "RISKY (unresolved)", study in a commercial RMS tool or EMT. NERC TPL-001-5.1 specifies no fault impedance |
| Default IBR parameter profile | `andes_example` (decided 2026-10-09). `wecc_mvwg_2019` stays available (`--ibr-profile`, or `profile:` per IBR). On the same 28 scenarios `andes_example` solved far more often (4 vs. 15 failures, all 15 unresolved) and the labels agreed on every scenario both solved (13 of 13). See §16 item 1 |
| Default time step and retries | One cycle (1/60 s) instead of ANDES's two cycles (decided 2026-10-10): the two-cycle step overstates post-clearing IBR overvoltage (§5). Non-physical answers are retried at finer steps like solver errors. Slower, especially for hard cases |
| Unknown outcomes in screening (**solver-hard fault buses**) | `screen` always simulates scenarios at fault buses where at least 20% of the training dataset's scenarios had an unknown outcome, and at buses not in the dataset (decided 2026-10-10). The model alone would pass about 40% of unknown-outcome scenarios unsimulated, and solver failure is not predictable from the features (AUC 0.56 on unseen buses) |

## 5. Engine facts (ANDES 2.0.0)

- **[Fact]** The IEEE 39-bus case ships with full dynamic data: 10 GENROU machines,
  TGOV1N governors, IEEEX1 exciters, IEEEST stabilizers. Verified by running it
  in a sandbox and on the author's laptop (2026-10-07, exit code 0 at tf = 20 s).
- **[Fact]** WECC generic IBR models are available: REGCA1, REECA1, REECB1, REPCA1;
  wind models WTDTA1, WTARA1, WTPTA1, WTTQA1; grid-forming REGF1–3.
- **[Fact]** The `ieee14_solar` case (REGCA1 + REECA1 + REPCA1) runs cleanly,
  including a line trip at 1.0 s and reclose at 1.1 s.
- **[Fact]** PSS/E RAW versions 32, 33 and 34 are parsed. Other versions fall back
  to the v33 format with a warning. **[To verify]** behavior on v35 files, which
  many transmission providers now use.
- **[Fact]** 41 `.dyr` model names are parsed, including GENROU, GENSAL, GENCLS,
  common exciters and governors, REGCA1, REECA1, REECB1, REPCA1 and the WT models.
  Unlisted models (including OEM user-written models) are not supported. The
  `check` command lists them for the user.
- **[Fact]** A replaced machine is converted to REGCA1 + REECA1 + REPCA1 by
  editing the case tables: the machine and every control linked to it
  (governor, exciter, stabilizer) are removed, not just disabled. Flat runs of
  20 s then hold steady state exactly, with one to three IBRs.
- **[Fact]** ANDES's REECA1 fixes its minimum active current at zero, and
  REECC1 (the WECC battery controller) is not in ANDES 2.0.0. So in v0.1 a BESS
  uses the same models as solar and must be discharging or idle; a charging
  dispatch is rejected with a clear error. Charging BESS is planned for v0.2.
- **[Fact]** IBR parameters are copied from ANDES's `ieee14_solar` example.
  The voltage-dependent current limit tables are capped at the inverter current
  limit, which ANDES needs for a consistent initialization.
- **[Assumption]** Inverter current limit 1.1 pu on the IBR rating (user-editable).
- **[To verify]** IBR control parameters. With the example converter time
  constant (Tg = 0.1 s), reactive current reached only about 0.6 of the current
  limit during a 0.1 s bolted fault. Review Tg, Kqv and Vdip against typical
  plant settings and IEEE 2800 expectations.
- **[Fact, corrected during development]** ANDES 2.0.0 sometimes shrinks its
  final time step to zero exactly at the end time and returns exit code 1,
  although the run reached the end. It is not caused by the case's disabled
  event (the first explanation was wrong). Running to the end time plus 1 µs
  fixes it, and also made short runs about 10 times faster in testing.
- **[Fact]** ANDES stops a run when the rotor-angle spread exceeds
  `ddelta_limit` (180° by default); the tool records this as `stopped_angle`.
- **[Fact]** Near the stability boundary, a coarse step can fail to converge at
  the clearing instant; a finer-step retry fixed the cases tested. A step study
  at bus 16 (steps from 1/30 s to 1/480 s) gave the same peak angle spread.
- **[Fact, dataset review 2026-10-10]** The two-cycle step does NOT give the same
  IBR high-side voltage right after clearing. In 18 rechecked dataset scenarios,
  2 of 8 labeled RISKY only for a one-sample excursion above 1.2 pu were OK at
  1/2 and 1/4 cycle (peaks up to 0.04 pu lower); sustained 1.1–1.2 pu cases and
  OK cases did not change. One cycle matched the half-cycle results in all 6
  cases rechecked. The default step is therefore one cycle (1/60 s), about 1.6
  times slower; retries use 1/120 and 1/480 s.
- **[Fact, dataset review 2026-10-10]** A non-physical answer (negative voltage,
  bus held at 0 V) used to be marked `sim_fail` without the finer-step retry that
  solver errors get; 317 of the 875 failures in the two-cycle dataset were of
  this kind. It is now retried like any failure. Rerunning 80 dataset scenarios
  with both changes: 6 of 16 "unresolved" scenarios solved (all OK; their RISKY
  label had come from the solver), 15 of 16 "confirmed" stayed RISKY, all 16
  angle-unstable stayed unstable, and 4 of 32 completed ones changed label
  (post-clearing overvoltage near 1.2 pu, both directions). Bolted faults at
  buses 19 and 20 without IBRs, which failed before, now solve.
- **[Fact]** With near-bolted faults close to large units, ANDES can reach a
  solution with a negative voltage magnitude and then a false voltage collapse
  (bus 19, three-cycle fault, no IBRs, two-cycle step). Such runs are flagged
  `sim_fail` if no finer step solves them.
- **[Fact]** ANDES can also "solve" the post-clearing network with non-faulted
  buses stuck at 0 V (bus 16 fault cleared at 0.19–0.2 s: buses 15, 17, 18 and 24
  at 0.000 pu while bus 16 sits at 0.25 pu, which is impossible through the lines
  between them). Such runs looked like instability. A bus held at or below
  0.01 pu for 0.1 s after clearing (plus 0.05 s settling) is treated as a
  non-physical solution. A genuine pole slip passes through low voltage; it does
  not hold neighbouring buses at exactly zero.
- **[Fact]** ANDES's BusFreq measurement spiked to about 76 Hz after clearing at
  a generator bus while machine speeds moved less than 1 Hz. Bus frequency is
  therefore computed from the voltage angle over a six-cycle window instead.

## 6. Scenario model

A scenario = **operating point** + **disturbance**.

| Part | Parameters |
|---|---|
| Operating point | Load level (fraction of base case); IBR buses; IBR penetration (% of dispatched generation); IBR type (solar or BESS) and control mode; which synchronous machines are displaced |
| Disturbance | Faulted bus; fault impedance (0 = bolted); fault start time; clearing time |

**[Assumption]** Faults are cleared without a topology change in v0.1 (bus fault,
cleared in place). Delayed-clearing and breaker-failure style contingencies come
later.

**Contingency set.** Normally cleared faults at the POI and nearby buses first,
ranked by electrical distance, then system-wide. This mirrors common
interconnection-study practice. **[To verify]** with users.

## 7. Simulation

1. Load the case (built-in 39-bus or user `.raw/.dyr`).
2. Apply the operating point: redispatch, add IBRs, remove displaced machines and
   their controls completely (not just disabled; see §5).
3. Run the power flow. If it fails, label the scenario `pf_fail` (never crash).
4. Run the time-domain simulation with the fault, to the end time plus 1 µs
   (see §5), with a one-cycle step. Default end time 10 s. **[Assumption]**
5. Save trajectories: bus voltage magnitudes and angles, machine angles and
   speeds, IBR active and reactive current.
6. Every run ends as `completed`, `stopped_angle` (ANDES stopped it at the angle
   limit), `pf_fail` or `sim_fail`. A numerical failure or a non-physical answer
   (negative voltage magnitude, or a bus held at 0 V after clearing) is retried
   with finer steps (1/120 s, then 1/480 s); if every step fails, the run is
   `sim_fail`. Failed runs are kept in the dataset and shown in the report. The
   parallel dataset runner adds a wall-clock timeout per scenario.
7. A bus held at ~0 V after clearing counts as non-physical either when it lasts
   0.1 s (after a 0.05 s settling delay), or when it lasts until the run stopped
   early on the angle limit. **[Fact]** The second case was added in v0.2.0:
   re-simulating the 391 angle-unstable scenarios of the 5,000-scenario dataset
   (published with v0.1.0) showed 107 in which a bus (the faulted bus or
   another) snapped to exactly 0 V at the clearing instant and stayed there
   while the rotor angles moved another 60–70°. Clearing a fault cannot do
   that, so these are numerical. ANDES had reported them as angle instability
   because the run stopped before the 0.1 s rule could apply; finer steps
   (1/120, 1/480 s) did not change them. In v0.2.0 they go through the
   milder-fault check like any unsolvable bolted fault: 82 became RISKY
   (confirmed) and 25 RISKY (unresolved), which are left out of training.

## 8. Screening criteria

Every threshold is user-editable. Defaults are stored in one settings file, and
every report records the values and the standard versions used.

| Criterion | Definition | Default |
|---|---|---|
| Angle stability | Maximum rotor-angle separation between any two synchronous machines during the simulation | 180° **[Assumption: common screening heuristic; replace with your organization's criterion]** |
| Voltage recovery | Each monitored bus voltage must return above a level within a time after clearing | Illustrative (0.8 pu within 1 s); replace with the transmission provider's criteria |
| Frequency | Frequency nadir/zenith and RoCoF at every bus, measured from the bus voltage angle over a six-cycle window. The fault-on period plus one window after clearing is not checked | Illustrative (59–61 Hz) |
| IBR ride-through exposure | Compare the voltage and frequency at the high side of each IBR's step-up transformer (where PRC-029-1 measures them) with the NERC PRC-029-1 envelopes (IEEE 2800-2022 to follow) | PRC-029-1 Tables 2 and 3 transcribed from the NERC PDF and checked row by row by the author (`src/ibr_faultscreen/data/criteria_prc029.yaml`) |

**Ride-through interpretation.** An RMS generic model does not reproduce how a
specific inverter's protection trips. So the tool reports **exposure**:
- *Inside the must-ride-through region:* the IBR is required to ride through.
  The scenario is reported with the region it reached.
- *Outside the region* (deeper or longer than required): the IBR may trip
  under the standard. The scenario is flagged as a **loss-of-IBR risk**.

**PRC-029-1 rules implemented** (from the NERC PDF, version approved October 8,
2024): time is counted per band, cumulative within any 10 s window for voltage
and any 10-minute window for frequency; band edges follow the table (for
example 0.90–1.05 pu closed, "> 1.05" open below). Not implemented: the
"more than four deviations in 10 s" rule, the frequency condition on the
voltage tables, and the phase-jump and V/Hz exemptions. **[To verify]** the
version in force (a modified PRC-029 filing was anticipated in August 2026).

**Overall label:** RISKY if any criterion is violated, or if the simulation fails.
Otherwise OK. Each individual failure mode is kept as its own label.

## 9. Critical clearing time

- Bisection on clearing time for each fault location and operating point.
- Stable means rotor-angle stable: the run completes and the angle spread stays
  within the limit (the classical definition, checked by the single-machine
  clearing-time test). Voltage recovery and ride-through are judged by the
  labeler, not by the CCT.
- A failed (unsolvable) simulation counts as not stable. If the boundary falls
  on failed runs, the CCT is reported as a lower bound ("at least X ms"), with
  the milder fault's CCT as an upper estimate (milder-fault check).
- Bisection assumes a single stability boundary; every evaluated clearing time
  is kept so this can be checked.
- Default tolerance: half a cycle (8.33 ms at 60 Hz). **[Assumption]**
- Search bracket: user-set minimum and maximum clearing times. If the minimum is
  already unstable, report "CCT below minimum." If the maximum is stable, report
  "CCT above maximum."
- `cct` also shows the equal-area view just below and just above the CCT (two
  extra simulations; `--no-equal-area` skips them).

### Equal-area view (v0.2.0)

The classical equal-area criterion is exact only for one machine against an
infinite bus. For a multi-machine system the tool builds a single-machine
equivalent from the simulated trajectories, as in the SIME and extended
equal-area methods in the literature **[To verify: cite the exact references
before using them in a paper]**:

- Split the synchronous machines into a leading group C and the rest N at the
  largest gap between rotor angles, at the moment of largest spread after the
  fault starts (if no gap exceeds 10°, the machine that swings furthest leads).
- δ = δ_C − δ_N (inertia-weighted group angles); M = M_C·M_N / (M_C + M_N);
  P_m = M·(ΣP_m,C/M_C − ΣP_m,N/M_N), and the same for P_e. Torques stand in for
  powers (speed near 1 pu), system base.
- First swing: from the fault until the equivalent angle stops increasing
  (stable) or the run ends (unstable). Accelerating area = ∫(P_m − P_e)dδ while
  the fault is on; decelerating area = −∫(P_m − P_e)dδ after clearing.
- In a stable first swing the two areas are equal by energy conservation, so the
  view explains stability; it is not a separate test of it. No stability margin
  is extrapolated: the view shows only what was simulated.
- "More than two groups" is noted for unstable cases when a second angle gap is
  at least half the largest and at least 30°.
- **[Fact]** Checked against the textbook single-machine case (ANDES SMIB,
  classical model, no damping): accelerating area within 1% and maximum angle
  within 1° of the hand calculation at 0.10, 0.15 and 0.17 s; unstable at 0.191 s
  (`tests/test_equal_area.py`). On the 39-bus case the areas balance within 3%
  in a stable case.
- It covers rotor-angle stability only. Voltage recovery, ride-through and
  frequency are separate checks.

## 10. Dataset

- **Sampling:** Latin hypercube over load level (0.9–1.1) and clearing time
  (0.05–0.30 s); fault buses spread evenly over the 29 network buses; 0–4 IBRs
  on random generators (1–9), 25% batteries. Fixed seed; the same seed gives a
  byte-identical `dataset.csv` (determinism test).
- **Runner:** parallel worker processes, a wall-clock timeout per scenario, and
  resume after interruption (`progress.csv`). Timeouts and errors are labeled
  RISKY, never OK.
- **Size:** 5,000 scenarios on the 39-bus system **[Assumption]**. With the
  two-cycle step, 5,000 took 45 minutes on the author's laptop; the one-cycle
  step and the extra retries make it slower.
- **Features (pre-fault only):** see `features.FEATURES`. SCR uses a
  short-circuit model built from the case (machines behind X'd, IBRs not
  contributing); it should be cross-checked against `scr-screen` **[To verify]**.
  Four extra candidate features (including the pre-fault voltage at IBR buses)
  were tried on the first full dataset and added nothing, so they were left out.
- **Labels:** the §8 failure modes, the overall label and, for failed bolted
  faults, the milder-fault check's resolution.
- **Unknown outcomes:** rows whose failure the milder-fault check could not
  resolve (and timeouts) are kept in the dataset but left out of training and
  evaluation by default, because their RISKY label reflects the solver, not the
  grid. The data card lists them by fault bus.
- **Files:** `dataset.csv` (CSV only; Parquet dropped to avoid another
  dependency) and an automatic `data-card.md`. Published on Zenodo with its own
  DOI (10.5281/zenodo.23290449), linked to the software DOI (10.5281/zenodo.23290219) and ORCID.
- **Withdrawn results:** the first demo run (240 scenarios) is withdrawn. Two
  bugs found before v0.1.0 made it unreliable: (1) with the ANDES example's
  Iolim = 0, generator 8 could not absorb reactive power and its dynamic
  initialization failed; (2) ANDES keeps simulating after a failed initialization,
  and the wrapper counted such runs as completed. Both are fixed and tested. The
  first full dataset (two-cycle step, 2026-10-10) is also not published: it was
  used for the review that led to the one-cycle step and the retry of
  non-physical answers (§5).

## 11. Machine learning

1. **Baselines:** fitted rule (RISKY if clearing time ≥ t* or lowest SCR ≤ s*)
   and class-balanced logistic regression.
2. **Main model:** scikit-learn HistGradientBoosting: up to 31 leaves per tree
   (no depth cap), at least 40 scenarios per leaf, 300 iterations, learning rate
   0.05, L2 = 1. Chosen on the first full dataset (depth 3 had suited the small
   demo set). Chosen on the same grouped folds that report the result, so the
   gain is slightly optimistic.
3. **Honest evaluation:** GroupKFold by fault bus with the threshold tuned inside
   each training fold (nested); extra test training on fewer IBRs and testing on
   more. Recall, precision, AUC, average precision (AP), Brier score,
   calibration bins.
4. **Threshold:** largest threshold giving the target recall on RISKY in
   out-of-fold predictions. **[Assumption: 0.95]**
5. **Model check:** recall within 0.05 of the target, and better than the rule
   on BOTH AUC and AP (threshold-free, so a pass is not the luck of one cut-off).
6. **Screen-then-verify:** every predicted-RISKY and borderline scenario is
   simulated, and every scenario at a solver-hard or unseen fault bus (§4); a
   budget shortfall shows "predicted RISKY, not yet verified". The model card
   reports the study effort (share simulated) at recall 0.90, 0.95 and 0.99 and
   how many unknown-outcome scenarios the model alone would have sent to
   simulation.
7. **Explanations:** permutation importance on held-out fault locations; per
   scenario, the features whose move to the training median lowers P(RISKY) most.
8. **Model card:** written automatically by `ibr-faultscreen train`.
9. **Results:** reported from the dataset made with the one-cycle step. Earlier
   numbers (demo run, two-cycle dataset) are not to be reported (§10).

## 12. Outputs

| Output | Audience |
|---|---|
| Plain-language summary: traffic light, riskiest buses, top 5 risks, what to study next | Managers and non-engineers |
| Animated network map (risk share by bus, slider by number of IBRs) | Everyone |
| Ranked, sortable scenario table with source (simulated/predicted), CSV download | Engineers |
| CCT table (lower bound / milder-fault estimate where bolted runs fail) | Engineers |
| Risk versus IBR share chart | Engineers, managers |
| Interactive trajectories in the report, and PNG figures with criteria overlaid | Engineers, study reports |
| Equal-area view (animated in the report, fifth panel of each PNG, and in `cct`) | Engineers, students |
| Model card and data card | Reviewers |

**Network map layout.** The built-in IEEE 39-bus case uses a fixed drawing that
follows the usual one-line arrangement of that system
(`src/ibr_faultscreen/data/layout_ieee39.yaml`, shared with SCR-Screen so both
tools draw it the same way); transformers are dashed. Other cases use their own
bus coordinates when every bus has them, otherwise an automatic force-directed
layout. The map says which, and that positions are not geographic. On phones the
drawing is turned a quarter turn (not mirrored).

The report is one self-contained HTML file in the SCR-Screen style (white
background, black headings, teal rule; DM Sans), responsive on phones, tablets
and desktops, with reduced-motion support.

## 13. Inputs and commands

Cases: built-in `ieee39`, ANDES `.xlsx`, or PSS/E `.raw` + `.dyr` (RAW v32–34
parsed; v35 untested). `check` lists `.dyr` models ANDES cannot read.

```
ibr-faultscreen example
ibr-faultscreen check CASE [--dyr FILE]
ibr-faultscreen simulate SCENARIOS.yaml [--criteria FILE] [--open]
ibr-faultscreen cct --bus 16 [--ibr 2 6] [--x 0.001] [--out cct.csv] [--no-equal-area]
ibr-faultscreen dataset --n 5000 [--workers 7] [--seed 2026]
ibr-faultscreen dataset --scenarios LIST.csv [--workers 7]
ibr-faultscreen train dataset.csv [--recall 0.95]
ibr-faultscreen screen --model model.joblib [--n 200 | --scenarios FILE] [--open]
ibr-faultscreen report results.csv [--open]
```

## 14. Validation tests

| Test | What it checks | Pass criterion |
|---|---|---|
| Single-machine clearing time | SMIB critical clearing time, classical model, three-phase fault at the machine terminal, no topology change. Hand value from the equal-area criterion: cos δ_cr = (π − 2δ₀) sin δ₀ − cos δ₀, and t_cr = √(4H(δ_cr − δ₀) / (ω_s P_m)) | Simulated CCT within ±1 cycle of the hand value **[Assumption]** |
| Equal-area view | Single-machine equivalent on the SMIB case; energy balance on the 39-bus case | Accelerating area within 1% and maximum angle within 1° of the textbook values; stable/unstable on the right side of the CCT; areas balance within 3% in a stable 39-bus case |
| Map layout | Fixed IEEE 39-bus drawing against the case's branch list | Every bus placed, every branch drawn once, 12 transformers dashed, no crossing lines, no bus on a line it does not belong to |
| Flat run | No disturbance, 39-bus with and without IBRs, 20 s | Voltages and speeds within ±0.001 pu of initial values |
| 39-bus clearing time | Bolted fault at bus 16, no IBRs | Bolted CCT is a lower bound: stable at 0.185 s, longer clearing times not solvable in ANDES 2.0.0. Milder fault (0.001 pu) CCT 0.256 s, bracketed within half a cycle. Regression values (model-specific, not a published benchmark). **Correction:** the earlier value 0.189 s, and the claim that an IBR on generator 2 lowers it to 0.159 s, came from non-physical runs and are withdrawn |
| IBR current limit | IBR current during a nearby bolted fault | Never exceeds its configured limit (plus numeric tolerance) |
| Ride-through boundaries | Ride-through checker with synthetic trajectories just inside, on, and just outside each envelope point | Correct result in every case; the on-the-line rule documented |
| Determinism | Dataset generation twice with the same seed | Identical file hashes |
| Model check | Evaluation on fault locations never seen in training | Recall on RISKY meets the target and the model beats the rule baseline on AUC and AP |
| Verified verdicts | Screen-then-verify | Every result marked simulated or predicted; no RISKY verdict from a prediction alone |
| PSS/E import | Round trip with ANDES's bundled `ieee14.raw` + `ieee14.dyr` | Loads and simulates (exit code 0) |
| No private labels | Every text file in the repository | No internal planning labels (see `tests/test_no_private_labels.py`) |
| Commercial cross-check (optional) | Comparison with a commercial tool on a few cases | Only with permitted license use |

## 15. Limitations (to state in every report)

- RMS positive-sequence models miss fast inverter control and EMT phenomena.
- Generic WECC models may not match a specific inverter's behavior.
- Balanced faults only (this version).
- Results depend on user-entered criteria and envelope values.
- Screening only: it prioritizes detailed studies and does not replace them.

## 16. Open items

1. IBR control parameters (default profile decided 2026-10-09: keep
   `andes_example`). Profile comparison on the same 28 random scenarios
   (seed 11): `andes_example` 4 solver failures, 10 RISKY; `wecc_mvwg_2019`
   15 solver failures, 17 RISKY (all failures unresolved). Where both profiles
   solved (13 scenarios), the labels agreed 13 of 13 (run time 1.3 min vs.
   9.1 min with two workers). On 8 targeted cases the WECC profile removed the
   post-fault overvoltage in review case R13 but failed more often; its reactive
   current during faults was lower (about 0.5 of the limit vs. 0.7). Both
   profiles are illustrative. The example parameters keep reactive current high
   after clearing, giving post-fault overvoltage of 1.1–1.2 pu at IBR buses
   (review cases R13, R15). Charging BESS in a later version.
2. Solver robustness (milder-fault check implemented). Report the reproducible
   cases to the ANDES developers. With bolted faults (1e-4 pu), ANDES 2.0.0
   failed for faults at the high-side bus of IBRs on generators 4, 5 and 9, and
   near the stability boundary at the clearing instant; with 0.003 pu they
   solved, but the faulted bus keeps about 0.09–0.20 pu, so the fault is no
   longer bolted. Recheck with the one-cycle step.
3. PSS/E RAW v35 behavior.
4. PRC-029-1 tables and notes checked by the author (2026-10-09). Open: the
   note 7 reading (separate bands, as implemented, or nested thresholds, more
   conservative). Enter IEEE 2800-2022 envelopes (author).
5. Voltage-recovery and frequency defaults (entered by the author).
