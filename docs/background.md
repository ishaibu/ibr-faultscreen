# Background reading notes

Notes by Shaibu Ibrahim, P.E. Write in your own words: what failed, why, and
what an interconnection screening tool should therefore check.

## Sources

| # | Document | Link |
|---|---|---|
| 1 | NERC & Texas RE, *Odessa Disturbance* (May 9 and June 26, 2021 events), September 2021 | https://www.nerc.com/pa/rrm/ea/Documents/Odessa_Disturbance_Report.pdf |
| 2 | NERC & Texas RE, *2022 Odessa Disturbance* (June 4, 2022 event), December 2022 | https://www.nerc.com/comm/RSTC_Reliability_Guidelines/NERC_2022_Odessa_Disturbance_Report%20(1).pdf |
| 3 | NERC, *Reliability Guideline: EMT Modeling for BPS-Connected IBRs – Recommended Model Requirements and Verification Practices* | https://www.nerc.com/comm/RSTC_Reliability_Guidelines/Reliability_Guideline-EMT_Modeling_and_Simulations.pdf |
| 4 | NERC, *Reliability Guideline: Recommended Practices for EMT Studies for IBR* (part 2) | https://www.nerc.com/globalassets/who-we-are/standing-committees/rstc/irps/reliability_guideline_recommended_practices_for_emt_studies_for_ibr_approved.pdf |
| 5 | NERC PRC-029-1, *Frequency and Voltage Ride-through Requirements for IBRs* | https://www.nerc.com/globalassets/standards/reliability-standards/prc/prc-029-1.pdf |
| 6 | IEEE 2800-2022 (paywalled; IEEE Xplore or your IEEE SA access) | https://standards.ieee.org/project/2800.html |

NERC has been moving documents to new `globalassets` addresses. If a link
breaks, search nerc.com for the document title.

## Notes

### 1. Odessa 2021
- Causes of the MW reduction:
- What a screening study could have caught:

### 2. Odessa 2022
- What changed versus 2021:
- What a screening study could have caught:

### 3–4. EMT guidelines
- When RMS screening is not enough (to cite in the tool's limitations):

### 5. PRC-029-1
- Checked the transcribed Tables 2 and 3 in `src/ibr_faultscreen/data/criteria_prc029.yaml`: yes (every row), 2026-10-09. Attachment 1 notes 1-11 and Attachment 2 notes 1-5 checked.
- Interpretation questions: note 7, are the voltage rows separate bands (tool's choice) or nested thresholds (more conservative)?

### 6. IEEE 2800-2022
- Voltage ride-through table (clause and table number):
- Frequency ride-through table:
- Differences from PRC-029-1:
