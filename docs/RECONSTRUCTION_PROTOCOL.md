# Source reconstruction protocol — 3 November 2026

This is a post hoc audit. Published/submission estimates and the frozen cohort
counts were known before this protocol. It is not prospective registration.

## Independent inputs and scope

Download all 27 states × 12 competencies × 9 years (2015–2023) of SIH/SUS RD
from DATASUS. Parse the official files in bounded monthly chunks. Download the
INSP RIISP **SSA** discharge series, catalogue MEX-INSP-SAEH-SSA-2008-2024.
Select 2015–2023 from that series; do not substitute the broader sectorial file.
Frozen Parquets are comparison references only and cannot supply reconstructed
records or repair missing source fields. Cache sources with acquisition times,
remote metadata and SHA-256 checksums. Preserve all original packages.

## Selection and unit

The scientific inclusion criteria remain age >=20 and a principal ICD-10 S06
diagnosis. Brazil uses administrative processing year; Mexico uses discharge
year. Verify age units using source documentation. Values coded as unknown are
missing, not zero. Preserve valid recorded extremes and quantify them. Do not
infer clinical severity from absence of administrative codes.

For Brazil, first recreate the old authorisation-level rule on the independently
downloaded records, documenting its first-value/max-value aggregation. Audit
AIH type, continuation sequences, repeated authorisation numbers, admission and
discharge dates, hospital changes, source competency and clinical year. An AIH is
an administrative authorisation, not a proven unique patient or clinical episode.
Never remove records merely because their clinical profiles are equal. Any
correction to the old algorithm must be reported separately with its affected
count and effect; do not choose a rule to match the frozen total.

For Mexico, retain the SSA records as supplied and audit institution codes,
year-specific schemas, record keys and repeated profiles. Do not use a sectorial
reference cohort as evidence that SSA counts should equal it. Do not equate
source row identifiers across different releases without verification.

## Exposure and estimators

Count eligible admissions by hospital and administrative year before dropping
missing outcomes or covariates. Use log(prior-year count + 1) only when the prior
observed year is contiguous. Complete-case models require at least two eligible
years per hospital. The analysis window is 2016–2023.

Preserve the submitted linear-model specification, spline age adjustment, sex,
S06 subtype, year effects, clustered inference and hospital fixed effects. Retain
paired-estimator covariance, all planned sensitivity specifications, influential
hospital exclusions, complementary fixed-effect logistic models, design
information diagnostics and 2023 workload concentration. Preserve every result,
including null, adverse and unstable findings. Revalidate implementations against
explicit dummy-variable models on synthetic data. A successful rerun from old
Parquets is only a frozen-input benchmark, never source reconstruction.

## Reporting and decision rules

Report sequential source-to-cohort losses and compare full precision results.
Distinguish source-release differences, programming corrections and intentional
changes of population. If historical hashes or original microdata are absent,
do not assert that release drift caused a discrepancy. State that attribution
cannot be resolved from retained evidence.

Only aggregate results and necessary Brazil/Mexico code enter the public
supplement. Historical analyses are archived internally. Admission-level sources,
checkpoints and AIH identifiers remain outside the public package.
