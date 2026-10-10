# Independent verification record

Hand checks by Shaibu Ibrahim, P.E., done without looking at the code's numbers
first. Record your own values, then compare.

## 1. Single-machine (SMIB) critical clearing time (equal-area criterion)

**Data** (ANDES `smib/SMIB.xlsx`, 100 MVA base, from Sauer, Pai & Chow):
generator terminal voltage 1.05 pu; infinite bus 1.0 pu ∠0°; mechanical power
0.9 pu; X'd = 0.245 pu; line bus 1–3 = 0.15 pu; two parallel lines bus 2–3 of
0.4 pu each; M = 5.7512 s, so H = 2.8756 s; 60 Hz; damping set to zero.
Fault: bolted three-phase at bus 3. During the fault the generator delivers no
power; clearing restores the original network.

**Steps**
1. Network reactance X = 0.15 + 0.4/2.
2. Terminal-voltage angle θ from P = V·1·sin θ / X.
3. Current I = (V∠θ − 1∠0) / jX.
4. E' = V∠θ + jX'd·I; record |E'| and δ₀ = angle of E'.
5. δ_cr from cos δ_cr = (π − 2δ₀) sin δ₀ − cos δ₀ (radians).
6. t_cr = √(4H(δ_cr − δ₀) / (ω_s·P_m)), with ω_s = 2π·60 rad/s.

| Quantity | Your value | Code value (tests/test_smib_clearing_time.py) | Match? |
|---|---|---|---|
| \|E'\| (pu) | | 1.1368 | |
| δ₀ (deg) | | 28.10 | |
| δ_cr (deg) | | 82.20 | |
| t_cr (s) | | 0.1789 | |
| Simulated CCT (s) | — | 0.177 (bisection) | within ±1 cycle? |

Date: ________  IBR-FaultScreen version: ______  Method: hand calculator / own spreadsheet

## 2. Ride-through boundary cases

Test envelope (made-up test values, **not** from any standard), voltage in pu:
[0, 0.5) for at most 0.15 s; [0.5, 0.9) for at most 2.0 s; [0.9, 1.1)
continuous; [1.1, 9.9) 0 s. Rule: time **equal** to the limit counts as inside;
a band includes its lower edge, not its upper edge.

For each case, decide **inside** (must ride through) or **outside** (may trip):

| Case | Trajectory | Your answer | Code (tests/test_criteria.py) |
|---|---|---|---|
| a | 0.30 pu for 0.149 s | | inside |
| b | 0.30 pu for 0.150 s | | inside |
| c | 0.30 pu for 0.151 s | | outside |
| d | 0.50 pu for 0.300 s | | inside |
| e | 0.4999 pu for 0.300 s | | outside |
| f | 1.15 pu for 0.010 s | | outside |
| g | two 0.1 s dips to 0.30 pu, 0.5 s apart: continuous / cumulative | | inside / outside |

Date: ________
