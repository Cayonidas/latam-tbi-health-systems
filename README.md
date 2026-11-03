# Reproduction from national administrative sources

Study: **Hospital volume, mortality and concentration of traumatic brain injury care in Brazil and Mexico: a longitudinal administrative cohort study**

This pipeline downloads national public source files, verifies their bytes,
harmonises records, constructs administrative cohorts and lagged hospital
volumes, and calculates the study models, sensitivity analyses and figures.
Brazil uses SIH/SUS reduced AIH files (RD), by state and monthly processing
competence, 2015–2023. Mexico uses the **Secretaría de Salud (SSA)** discharge
series, RIISP catalogue `MEX-INSP-SAEH-SSA-2008-2024`, restricted to 2015–2023.
The broader Mexican sectorial discharge series is not an input.

## Installation and paths

The numerical environment was verified on Python 3.12.14. Create an isolated
environment and install the pinned versions in `requirements.txt`:

```text
python -m venv .venv
python -m pip install -r requirements.txt
python pipeline/run_pipeline.py --root /path/to/work --verify-dgis-annual
```

Activate the environment using the command appropriate to your operating
system before running Python. Replace `/path/to/work` with any writable data
directory. The script locations do not depend on a personal drive or account.
The analysis modules use `TBI_ROOT`, `TBI_DATA_ROOT` and `TBI_RESULTS_ROOT`;
the runner sets these from `--root`, `--data-root` and `--results-root`.

The default root is the parent of `pipeline/`. Raw sources, checkpoint records
and analytical cohorts are local working files, not public supplementary data.
Review the source access terms and cite DATASUS, DGIS and INSP. The source
manifest identifies the exact URLs and acquisition dates used for this study.
The INSP download page requests acknowledgement of its usage conditions.

## Stages and resumption

| Stage | Task |
|---|---|
| 00_download_sources | Download 2,916 RD files and the consolidated SSA archive; optional annual DGIS sources and dictionaries. |
| 01_validate_sources | Verify sizes and SHA-256 against acquisition metadata; create the source manifest. |
| 02_harmonise_brazil | Decompress each DBC, validate the DBF layout and count all source records; retain principal-S06 records. |
| 03_harmonise_mexico | Read the complete nested SSA CSV in bounded chunks, verify byte count and ZIP CRC, retain 2015–2023 S06 records; optionally compare DGIS releases. |
| 04_build_cohorts | Apply documented age, diagnosis and administrative-unit rules independently of reference cohorts. |
| 05_construct_exposure | Count hospital-years, construct contiguous prior-year volume and define longitudinal eligibility. |
| 06_run_models | Fit conventional and hospital fixed-effect linear models and the linear sensitivity specifications. |
| 07_run_sensitivities | Fit complementary logistic models, paired contrasts, influence deletions, workload and independent design diagnostics. |
| 08_generate_outputs | Generate Figures 1, 2 and supplementary Figure S1 as PDF and PNG. |
| 09_validate_publication | Run independent numerical tests and cross-check publication outputs. |

Execute or resume a stage with:

```text
python pipeline/run_pipeline.py --root /path/to/work --stage 02
python pipeline/run_pipeline.py --root /path/to/work --from-stage 04 --to-stage 09
python pipeline/run_pipeline.py --root /path/to/work --stage 03 --verify-dgis-annual
```

Source downloads use partial files, byte-range/FTP resumption, integrity checks
and immutable cache metadata. Monthly Brazilian checkpoints are independently
reusable. The nested Mexican Deflate64 CSV is sequential: after an interrupted
CSV pass, rerun stage 03 from the beginning of that member; downloaded archives
remain cached. A partial CSV pass is never accepted as complete. The annual
DGIS audit caches each fully read and CRC-validated annual file separately.

The published source manifest is also retained in `publication_reference/`.
Stage 01 stops if newly obtained source bytes differ from that reference.
After reviewing the documented differences, an explicitly new-source analysis
can use `--allow-source-version-change`; this never certifies agreement with
the published cohorts. Cross-platform execution has not been claimed: the
recorded numerical validation used Windows x64 and the pinned Python runtime.

`--verify-dgis-annual` requests all nine annual SSA releases and their official
catalogues. The comparison accounts for the 2020 age-unit change in the annual
files. It does not assume that annual release keys or profiles identify the
same patient. See `DATA_DICTIONARY.md` and the aggregate source comparison.

## Scientific rules

Include age at least 20 and principal ICD-10 S06. The Brazilian unit is an AIH
administrative authorisation; the Mexican unit is a recorded discharge. Neither
is a unique person. Repeated Brazilian AIH numbers follow the audited
authorisation grouping. Equal patient profiles are never a deletion criterion.
Brazilian code-5 ages are 100 plus the recorded value; the audit also retains a
separate compatibility reconstruction of the historical code-4-only rule.

Volume counts all eligible source-cohort records before model complete-case
selection. Exposure is `log(prior_year_volume + 1)` only with a contiguous prior
year. Models require complete covariates/outcome and at least two eligible
years per hospital. The resulting longitudinal window is 2016–2023. Effects in
percentage points are scaled by `100 * log(2)` and refer to doubling `V + 1`.
The countries are analysed separately. No coefficient is interpreted causally.

The protocol is a post hoc reconstruction audit, not prospective registration.
It preserves the submitted estimators and sensitivity specifications and adds
a documented Brazilian age-at-most-110 check after the age-field audit.
Reference counts never enter cohort selection. Unfavourable or unstable
results remain in the output tables.

## Output map

`results/reconstructed/linear/` contains the linear specifications, primary
logistic models, cohort profiles, model selection flow and exposure support.
`robustness/` contains paired contrasts, hospital deletions, coding,
workload summaries and complementary logistic variants. `diagnostics/` contains
an independently implemented FWL calculation, design-information concentration,
source-identifier summaries and profile repetition counts. `figures/` contains
the three figures. Contextual mean-centred logistic results are stored separately.

`COHORT_FLOW_FROM_RAW.csv` traces every selection stage. Source manifests,
dictionary and aggregate comparisons connect source records to cohorts. The
numerical validation checks cohort volumes and contiguous lags, identical
paired samples, confidence intervals, input hashes and independent estimates.
Synthetic tests compare fixed effects and clustered covariance with explicit
dummy-variable fits; logistic coefficients and covariance are independently
checked as well. Tests do not depend on frozen reference Parquets.

The optional `compare_source_cohorts.py --reference-root PATH` and
`compare_results.py --old-results-root PATH` audit retained historical files.
They are not dependencies of stages 00–09. Reference microdata are not
redistributed in the public supplement. Aggregate benchmark comparisons are
provided separately.

## Resources and remaining limitations

Allow at least 30 GB of writable space for downloaded sources, decompressed
streams/checkpoints and model outputs; 16 GB RAM is recommended. Processing
is bounded by a monthly Brazilian file or 250,000 Mexican CSV records; it never
loads all Brazilian hospitalisations at once. Models operate on the selected
adult S06 cohorts. The runner limits numerical libraries to two threads.
Network speed, decompression and fixed-effect logistic fitting dominate runtime.
Execution logs and verified dependency versions record the actual environment.

Current source hashes do not prove that the original historical download used
identical releases. Hospital IDs do not establish continuity across mergers or
renaming. Processing year in Brazil differs from clinical admission year.
Administrative subtypes, secondary diagnoses and ICU fields do not replace
validated severity measures, and referral fields do not establish complete
transfer chains. Recorded stay sums are not observed calendar occupancy.

## Distributed evidence and optional reference comparisons

The completed source reconstruction processed all 2,916 Brazilian monthly
files and the entire consolidated Mexican SSA CSV, plus all nine annual DGIS
files. Source and model validation reports document executed checks. The
public archive contains no raw admissions, authorisation numbers or discharge
keys. The two optional historical comparators require separately retained
reference files supplied by the researcher; stages 00–09 do not use them.

`MANUSCRIPT_VALUE_CONCORDANCE.csv` connects the numerical result cells in the
main and supplementary tables with full-precision aggregate outputs.
`OLD_VS_NEW_RESULTS.csv` retains changes and the nonestimable logistic attempt.
`PACKAGE_CHECKSUMS.sha256` hashes the public archive contents (excluding itself).
The complete source acquisition hashes are in `SOURCE_CHECKSUMS.sha256`; those
files must first be downloaded, because they are not redistributed here.

`--data-root` and `--results-root` override model inputs and analytical outputs
for stages 06–09. Acquisition and cohort construction stages 00–05 store their
cache and checkpoints beneath `--root`. The default end-to-end configuration
uses `ROOT/checkpoints/cohorts` throughout.

## Current reconstructed study

The longitudinal samples contain 664,508 Brazilian AIH authorisations in 2,079
hospitals and 92,853 Mexican SSA discharges in 437 hospitals. The previous
Mexican cohort covered a broader sectorial population and must not be treated
as SSA. Source-population differences and harmonisation corrections are
documented in the reconstruction report and old/new result tables.

This repository contains the current source-to-result implementation. Earlier
versions remain in Git history and in the locally preserved research archive.
The manuscript is prepared for BMC Health Services Research; this does not
claim acceptance or publication. To verify this code distribution without raw
data, run `python verify_repository.py`.
