# Reproducing the current longitudinal analysis

The current executable bundle is `reanalysis_2026/`. It is identical to the
scientific Additional file 2 accompanying the manuscript. The earlier seven
scripts remain under `code/` for provenance. Their execution does not by itself
reproduce the current manuscript, and the original national extraction was not
independently reconstructed in the present audit.

## Environment and checks without admission data

The recorded environment is Python 3.12.14. Install the exact dependencies from
`requirements.txt` into an isolated environment. Compatibility with other
versions has not been established. The root requirements delegate to the frozen
bundle; they do not contain the invalid pip requirement `python>=3.10` that was
present in the historical file. Python itself must be installed separately.

```sh
python verify_repository.py
python -m pip install -r requirements.txt
python reanalysis_2026/verify_archive.py
python reanalysis_2026/verify_archive.py --synthetic
```

The synthetic option compares paired clustered covariance and unconditional
hospital-intercept logistic fits with explicit-indicator models. These checks
validate implementations, not clinical definitions or causal identification.

## Reanalysis requiring the frozen inputs

Use a separate working copy: the analysis programs overwrite generated outputs.
Provide the two authorised input files, with the recorded hashes, at:

```text
reanalysis_2026/auditoria_20261028/base_final/analysis_v284_final/01_data/
    cohort_main_brasil_v280.parquet
    cohort_main_mexico_v280.parquet
```

The hashes are recorded in
`reanalysis_2026/artigo_final_20261028/resultados/input_hashes.json` and
`reanalysis_2026/cohort_diagnostics/results/processed_audit_validation.json`.
Do not add the inputs to Git. From the repository root:

```sh
python reanalysis_2026/run_analysis.py --validate
python reanalysis_2026/run_analysis.py --core
python reanalysis_2026/cohort_diagnostics/audit_processed_cohorts.py --data-root reanalysis_2026/auditoria_20261028/base_final/analysis_v284_final/01_data --output reanalysis_2026/cohort_diagnostics/results
```

`--validate` includes input-dependent checks. `--core` reproduces linear models,
hospital-intercept logistic models, paired comparisons, all selected hospital
deletions, coding diagnostics and workload summaries. The last command reproduces
the additional cohort and residualised-exposure diagnostics. The scripts require
the dated subdirectories to remain siblings. The code's fixed relative layout
has been preserved to avoid changing analytic implementations.

`oportunidades_adicionais.py` is a historical extension requiring additional
Chile/Ecuador inputs; it is not needed for this paper. Historical Chile aggregate
bounds remain included for one validation assertion. See the bundle README for
the result-to-file map. Do not substitute the historical risk-standardisation
spreadsheet for the current fixed-effect estimates or use it to rank quality.

## Verification record and scope

The November editorial update did not rerun cohort models or change their
results. All 11 Python programs and 50 scientific CSVs from the final submission
bundle were preserved byte for byte. Repository checks and synthetic computation
checks were used to verify the new directory layout without admission records.
Full private-cohort reproduction had already been performed in the preceding
audit. Statistical plans were recorded by stage after earlier results were
known; no prospective registration is claimed. Severity, transfer chains,
clinical dates in Brazil and source-level episode validation remain unavailable.
