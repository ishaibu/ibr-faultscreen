# Glossary

Abbreviations and terms used in IBR-FaultScreen, its reports and its documents.

## Power systems

| Term | Stands for | Meaning here |
|---|---|---|
| IBR | Inverter-based resource | Solar, wind (type 3/4) or battery storage connected through power electronics |
| BESS | Battery energy storage system | A battery plant; in v0.1 it must be discharging or idle |
| POI | Point of interconnection | Where a plant connects to the grid |
| SCR | Short-circuit ratio | Short-circuit MVA at the IBR's connection bus divided by the IBR's MVA rating; low SCR means a weak grid |
| CCT | Critical clearing time | The longest fault duration after which the machines stay in step |
| RMS simulation | Root mean square | Phasor (positive-sequence) dynamic simulation, as in PSS/E or PSLF |
| EMT | Electromagnetic transient | Detailed waveform simulation (PSCAD-type); not done by this tool |
| SMIB | Single machine, infinite bus | The textbook one-generator test case used to check the CCT method |
| SLG | Single line to ground | An unbalanced fault type; not in v0.1 |
| pu | Per unit | A value divided by its base (for example 1.05 pu voltage = 105% of nominal) |
| RoCoF | Rate of change of frequency | How fast frequency moves after a disturbance (Hz/s) |
| MVA, MW | Megavolt-ampere, megawatt | Apparent and active power |
| X'd | d-axis transient reactance | Machine reactance used for the short-circuit features |
| Bolted fault | — | A fault with (nearly) zero impedance; here 1e-4 pu |
| Milder-fault check | — | When a bolted fault cannot be solved, the tool reruns it at 0.001 then 0.003 pu: "confirmed" if the milder fault is already RISKY, "unresolved" if it is OK |
| Solver-hard fault bus | — | A fault location where the solver often fails; `screen` always simulates scenarios there |
| Ride-through | — | An IBR staying connected and controlled through a voltage or frequency disturbance |

## Standards and organizations

| Term | Stands for |
|---|---|
| NERC | North American Electric Reliability Corporation |
| PRC-029-1 | NERC standard "Frequency and Voltage Ride-through Requirements for Inverter-based Resources" (PRC = protection and control) |
| TPL-001 | NERC transmission system planning performance standard |
| FERC | Federal Energy Regulatory Commission |
| IEEE | Institute of Electrical and Electronics Engineers |
| IEEE 2800-2022 | IEEE standard for interconnection of IBRs to transmission systems |
| WECC | Western Electricity Coordinating Council |
| MVWG | WECC Modeling and Validation Work Group |

## Models and software

| Term | Stands for / is |
|---|---|
| ANDES | The open-source Python power-system simulator this tool runs on |
| REGCA1, REECA1, REPCA1 | WECC generic IBR models: generator/converter, electrical control, plant control |
| GENROU | A round-rotor synchronous machine model |
| PSS/E | Siemens Power System Simulator for Engineering; `.raw` holds the network, `.dyr` the dynamic models |
| PSLF | GE Positive Sequence Load Flow program |
| OEM | Original equipment manufacturer (for example, an inverter maker's own model) |
| CLI | Command-line interface: the `ibr-faultscreen ...` commands typed in a terminal |
| pip | Python's package installer (`pip install ibr-faultscreen`) |
| PyPI | Python Package Index, where `pip` downloads packages from |
| venv / .venv | A virtual environment: a private Python installation for one project |
| pytest | The tool that runs the automated tests |
| CI | Continuous integration: tests run automatically on GitHub for every push |
| YAML, CSV, JSON, HTML, PNG | File formats: settings (YAML), tables (comma-separated values), structured data (JavaScript Object Notation), web pages (HyperText Markup Language), images (Portable Network Graphics) |
| SHA-256 | A 256-bit fingerprint of a file (Secure Hash Algorithm); the same file always gives the same value |
| DOI | Digital Object Identifier: a permanent link for citing the software or dataset |
| ORCID | Open Researcher and Contributor ID: a researcher's permanent ID |
| GPL | GNU General Public License, the open-source license of this tool |

## Machine learning

| Term | Stands for | Meaning here |
|---|---|---|
| ML | Machine learning | Models that learn from labeled examples |
| GBM | Gradient boosting machine | Many small decision trees added one after another; the main model (`gbm`) |
| Logistic regression | — | A simple straight-line probability model; a baseline |
| Rule baseline | — | "RISKY if clearing time is long or SCR is low"; the bar the learned model must beat |
| Recall | — | Share of truly RISKY scenarios the screen catches (the tool aims for 0.95) |
| Precision | — | Share of flagged scenarios that are truly RISKY |
| AUC | Area under the ROC curve (ROC = receiver operating characteristic) | Chance that a random RISKY scenario is ranked above a random OK one: 0.5 = guessing, 1.0 = perfect |
| AP | Average precision | Summary of precision across all recall levels; guessing scores the share of RISKY scenarios |
| Brier score | — | Mean squared error of the predicted probabilities; lower is better |
| TP / FP / FN / TN | True positive / false positive / false negative / true negative | Counts of correct and wrong RISKY / OK calls |
| Threshold | — | The P(RISKY) value above which a scenario is flagged |
| GroupKFold | — | Splitting the data so a test fold only has fault buses never seen in training |
| Out-of-fold | — | Predictions made by a model that did not train on those rows |
| Latin hypercube | — | A sampling method that spreads scenarios evenly over each input range |
| Permutation importance | — | How much the score drops when one feature's values are shuffled |
| Model check | — | The release test: recall on target, and better ranking (AUC and AP) than the rule baseline on unseen fault locations |
| Study effort | — | Share of scenarios the screen sends to simulation for a given recall |
