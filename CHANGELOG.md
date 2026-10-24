# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-24

### Added
- **Equal-area view** of every simulated scenario: the machines are reduced to a
  single-machine equivalent built from the simulated trajectories (the leading
  group is the machines that swing furthest during the first swing), and the
  accelerating and decelerating areas are drawn (fifth panel of each PNG figure,
  animated in the interactive report). Checked against the textbook
  single-machine case (`tests/test_equal_area.py`, `docs/verification.md` §3).
- `cct` shows the equal-area view just below and just above the critical
  clearing time and saves a side-by-side figure with `--out`
  (`--no-equal-area` skips it).
- `dataset --scenarios LIST.csv` runs any list of scenarios in parallel,
  resumably, with a data card.
- `tools/explorer_scenarios.py`: the scenario list behind the demo's explorer.
- Python: `ibr_faultscreen.equal_area.equal_area(result)`.
- Demo page: **Build your own grid**. Pick which generators become IBRs, the
  fault bus and the clearing time; every one of the 37,120 combinations (base
  load) was simulated in advance (`tools/explorer_scenarios.py`,
  `tools/explorer_data.py`). Each case shows its reason and the screen view: the
  score of a model trained without that fault bus and whether `screen` would
  simulate it. Every case has a shareable link (`#explore=g26-b16-c6`).
- Demo page: **quiz** (guess RISKY or OK for ten real cases, scored against the
  screening model).
- `examples/colab_quickstart.ipynb`: run in Google Colab with nothing to install
  (sliders, equal-area view, two-scenario comparison, clearing-time sweep,
  critical clearing time).
- README: animations of the explorer and the equal-area view; Colab badge.

### Changed
- Network map: the IEEE 39-bus case uses a fixed drawing that follows the usual
  one-line arrangement (shared with SCR-Screen); transformers are dashed; the
  map says whether positions are fixed, from the case, or automatic (never
  geographic). On phones the drawing is turned, not mirrored.
- Simulation records each synchronous machine's electrical and mechanical torque
  and inertia (needed for the equal-area view).
- The 5,000-scenario demo dataset was regenerated with this version (same
  scenarios, seed 2026); README numbers, demo and model card are from it. Only
  111 of 5,000 outcomes changed: the 107 of the fix below, 3 whose milder-fault
  rerun was itself a false collapse (now unresolved), and 1 former timeout. Model
  AUC on unseen fault buses 0.86 vs. 0.78 for the rule (0.87 before).
- GitHub workflows use the Node 24 versions of the checkout, setup-python and
  artifact actions.

### Fixed
- A run in which a bus snapped to exactly 0 V at the clearing instant and stayed
  there (or the faulted bus never recovered) was reported as angle instability
  when it stopped on the angle limit before the 0.1 s false-collapse rule
  applied. It now counts as non-physical, and the milder-fault check handles
  it. A bus that only decays toward zero as machines slip apart is not flagged. In the 5,000-scenario dataset
  published with 0.1.0 this affected 107 of the 391 angle-unstable scenarios
  (now 82 RISKY confirmed, 25 RISKY unresolved).

## [0.1.0] - 2026-10-09

First public release.

### Added
- Scenario definitions (YAML/CSV) and IBR conversion: synchronous machines on the
  IEEE 39-bus case (or a PSS/E `.raw` + `.dyr` case) replaced by WECC generic IBR
  models REGCA1 + REECA1 + REPCA1, with selectable parameter profiles.
- Simulation wrapper (ANDES): every run ends as completed, stopped on angle,
  power-flow failure or simulation failure; finer-step retry; detection of
  non-physical solutions (negative voltage, buses held at 0 V).
- Milder-fault check: bolted faults that cannot be solved are rerun at milder faults and labeled
  RISKY (confirmed) or RISKY (unresolved).
- Screening criteria: rotor-angle spread, voltage recovery, frequency and RoCoF,
  and ride-through exposure against NERC PRC-029-1 Tables 2 and 3 (built in).
- Critical clearing time by bisection, reported as a lower bound when bolted runs fail.
- Labeled dataset generator (seeded, parallel, resumable, with timeouts) and an
  automatic data card.
- Screening models (fitted rule, logistic regression, gradient boosting) evaluated on
  unseen fault locations; screen-then-verify; explanations; model card.
- Self-contained HTML report that works on phones, tablets and desktops: animated
  network map with a Play button, ranked table that becomes cards on narrow screens,
  and interactive trajectories drawn in the browser (hover read-out, PNG download).
- Review figures (PNG) for every simulated scenario.
- Interactive demo report on GitHub Pages (IEEE 39-bus, 5,000 scenarios), built by
  `tools/build_demo.py`; animated previews in the README.
- Contributing guide and issue templates.
- Command line: `ibr-faultscreen example | check | simulate | cct | dataset | train | screen | report`.
- Validation tests (design spec §14), run on Python 3.11 and 3.13 for every push.

### Fixed before release
- An IBR's lower reactive-current limit is now -Imax (the ANDES example's 0 stopped
  an IBR from absorbing reactive power, so generator 8 failed to initialize).
- A failed dynamic initialization is always reported as a simulation failure
  (ANDES keeps simulating afterwards; such runs were counted as completed).
- Default time step is one cycle (1/60 s) instead of two: the two-cycle step
  overstated IBR overvoltage right after fault clearing and flipped some
  ride-through labels.
- A non-physical answer (negative voltage, bus held at 0 V) is now retried at
  finer steps like any solver failure, instead of being reported as a failure
  at once. Many bolted faults that looked unsolvable now solve.
- `screen` always simulates scenarios at solver-hard fault buses and at buses not
  in the training data; the model card reports this, the study effort at
  recall 0.90/0.95/0.99, and how many unknown-outcome scenarios the model alone
  would have passed.
- Gradient-boosting settings re-chosen on the full dataset (AUC 0.87 on fault
  buses never seen in training, for the published 5,000-scenario dataset).
- The data card shows ride-through exposure by envelope band and unknown
  outcomes by fault bus.

### Known limitations
- RMS positive-sequence simulation; balanced faults only.
- Generic IBR models; parameter profiles are illustrative, not a specific plant.
- Some bolted faults close to large units cannot be solved by ANDES 2.0.0 (see the
  milder-fault check).
- Angle, voltage-recovery and frequency limits in the built-in criteria file are
  illustrative; replace them with your transmission provider's criteria.
