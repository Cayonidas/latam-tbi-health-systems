# Current cohort definitions and limitations

The current longitudinal study uses administrative admissions aged at least 20
years with a principal ICD-10 S06 diagnosis in Brazil and Mexico, 2015–2023.
An admission record is not necessarily a unique person or complete episode.
Brazilian years are processing years; the retained files do not provide the
clinical dates needed to verify exposure–outcome ordering.

The outcome is recorded in-hospital death. Missing Mexican outcomes remain
missing. Volume counts all eligible admissions per hospital and reporting year;
the lag requires the immediately preceding year without bridging gaps. Exposure
is the natural logarithm of volume plus one. Longitudinal models require complete
variables and at least two eligible exposure years per hospital, covering
2016–2023. Adjustment includes age using a four-degree cubic B-spline, recorded
sex, diagnosis subtype and year, plus hospital intercepts in the fixed-effect
specification. Conventional and fixed-effect estimates use identical samples.

The diagnosis and procedure mappings are supplied in `reanalysis_2026/`. S06.1–
S06.7 is a specified-diagnosis sensitivity, not a validated severe-TBI phenotype.
Procedure counts describe admissions carrying mapped codes, not operation counts.
Lengths of stay are workload measures, not calendar bed occupancy.

External hospital-identifier stability, source episode uniqueness and transfers
were not validated. Repeated Brazilian profiles were retained because equality
does not identify duplicated episodes. Mexican full-profile uniqueness is
partly determined by a populated record_id and is not proof of unique episodes.
GCS, pupils, CT findings, physiology and post-discharge outcomes are unavailable.
Brazilian inherited secondary diagnoses, intensive/intermediate care,
reimbursement and urgency fields were unsuitable and excluded from interpretation.

All analyses are post hoc; source-level selection could not be independently
reconstructed. Chile/Ecuador belong to the historical analysis, not the current
longitudinal models. Full definitions and diagnostics are in the bundle README,
code and aggregate results. The previous codebook is preserved as historical
documentation in `docs/version_notes/`.
