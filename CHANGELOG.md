# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/).

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
- Gradient-boosting settings re-chosen on the full dataset (AUC 0.85 to 0.88).
- The data card shows ride-through exposure by envelope band and unknown
  outcomes by fault bus.

### Known limitations
- RMS positive-sequence simulation; balanced faults only.
- Generic IBR models; parameter profiles are illustrative, not a specific plant.
- Some bolted faults close to large units cannot be solved by ANDES 2.0.0 (see the
  milder-fault check).
- Angle, voltage-recovery and frequency limits in the built-in criteria file are
  illustrative; replace them with your transmission provider's criteria.
