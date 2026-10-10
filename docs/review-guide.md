# Hand review: guide

The goal is engineering-judgment evidence: for each scenario, does the label
(OK / RISKY and the reasons) match what you see in the plots? Disagreements are
the most valuable output. They are how the criteria and models get fixed.

## Run it

From the project folder, with `.venv` active:

```
python examples/run_review.py
```

It takes about 2–3 minutes and writes `review_output/` with one PNG per
scenario and `review.csv`. The folder ignores itself, so it never shows up in
GitHub Desktop.

## How to read each figure

| Panel | What to look for |
|---|---|
| 1. Voltage | Orange = faulted bus, blue = IBR high-side bus, grey = all others. Faint horizontal lines are the PRC-029 band edges. Does the voltage recover above the dashed line within the stated time? |
| 2. Rotor-angle spread | Largest minus smallest machine angle. A first swing that turns back is stable; one that keeps rising to the dashed limit is not. |
| 3. Frequency | Measured over six cycles from the bus voltage angle. The shaded period (fault plus one window) is not checked, because the phase jumps there make the measurement meaningless. |
| 4. IBR current | Solid = total current / limit, dotted = reactive part. During a deep fault the IBR should push reactive current toward its limit. |

The header line names the fault, clearing time (ms and cycles), the IBRs
(generator, terminal bus → high-side bus), the load level and the run status.
Violated checks are listed at the bottom.

## What to record in review.csv

Open `review.csv` in Excel. For each row:

- `shai_agrees_Y_N`: **Y** if the label and reasons are right, **N** if not.
- `shai_notes`: why, in a few words. Especially note:
  - a RISKY label you consider a false alarm, or an OK you consider risky;
  - anything that looks non-physical (spikes, flat lines, odd recoveries);
  - IBR behavior you would not expect from a real plant.

Save the finished file as `docs/review-results.csv` and commit it; it becomes part
of the validation record.

## Known points to judge (found while preparing the review)

1. **Unsolvable bolted faults (milder-fault check).** ANDES 2.0.0 sometimes cannot solve a
   bolted fault close to large units, or "solves" it with buses stuck at 0 V
   after clearing (non-physical). Such a run is rerun at a milder fault
   (0.001, then 0.003 pu) and labeled **RISKY (confirmed)** if the milder fault
   is already RISKY, or **RISKY (unresolved)** if it is OK. Its figure shows the
   milder run under an orange banner. Check R07, R17 and R20 (and compare with
   R17z and R20z). Judge whether "confirmed" is convincing in each case.
2. **Post-fault overvoltage at IBR buses (R13, R15, R17z).** In R13, bus 6
   already sits at 1.08 pu before the fault (load scaling does not re-dispatch
   reactive power), and the post-fault peak of 1.109 pu stays above 1.10 pu for
   1.37 s, just over the 1.0 s allowed: a thin-margin case. The generic IBR
   parameters (copied from ANDES's example) keep reactive current high after
   the voltage recovers, so the high-side voltage overshoots above 1.1–1.2 pu.
   Under PRC-029 Table 2 that is ride-through exposure. Judge whether this is
   realistic for a modern plant or an artifact of the example parameters
   (open item: IBR parameter review, before the dataset in Week 3). Also note
   in R05 that during a bolted fault at its own high-side bus the IBR's
   reactive current reaches only about half its limit: the example converter
   time constant (0.1 s) is slow.
3. **Sampling.** Results are stored every cycle (about 17 ms; two cycles before
   2026-10-10). A single overvoltage sample above 1.2 pu counts as about 17 ms
   of exposure.
4. **Criteria values.** The PRC-029 bands were transcribed by Claude and must be
   checked against the PDF. Angle, voltage-recovery and frequency limits in
   `src/ibr_faultscreen/data/criteria_prc029.yaml` are illustrative, not from a standard.
