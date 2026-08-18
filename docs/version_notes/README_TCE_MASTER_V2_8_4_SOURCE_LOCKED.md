# TCE Multinational — Master v2.8.4 source-locked final

This release is the analytic continuation of the completed v2.8.3 preflight repair.
It must be used instead of the v2.8.1 master because v2.8.1 does not consume the
repaired Ecuador annual files and expects the obsolete v2.8.1 preflight layout.

## Frozen source decisions

### Brazil
- Uses the validated SIH/SUS intermediate checkpoint already present on Drive.
- Primary period: 2015–2023.
- Eligible for individual and hospital-volume analyses.

### Mexico
- 2015–2017: strict v2.8.3 recovery from the consolidated 2013–2020 SAEH/DGIS file.
- Age unit `5`: years, validated against 2018–2023.
- `MOTEGRE=5`: in-hospital death, based on the official DGIS discharge coding.
- 2015–2017 all passed `PASS_STRICT` and provide 64,673 admissions aged ≥20 years.
- 2018–2023: validated annual checkpoints used by the v2.8 analytic engine.
- Eligible for individual and hospital-volume analyses.

### Chile
- Uses official annual discharge files for 2015–2023.
- Eligible for individual mortality, length of stay, diagnosis, demographics,
  insurance, residence geography, facility ownership, and general intervention analyses.
- Not eligible for hospital-volume analysis: no exact public establishment identifier.
- Procedure fields must not be interpreted as a validated decompressive-craniectomy
  versus craniotomy classification.

### Ecuador
- Uses the v2.8.3 selected annual sources with complete individual outcomes for 2015–2023.
- Expected primary sample: approximately 46,753 admissions aged ≥20 years.
- 2024 is preserved in the preflight evidence but excluded from the common-period
  primary cohort, which remains 2015–2023.
- Not eligible for hospital-volume analysis. Composite linkage to beds/capacity is
  ecological sensitivity only.

## What changed from v2.8.1

- Reads `analysis_v283_preflight_repair/` directly.
- Hard-stops unless Mexico 2015–2017 are `PASS_STRICT`.
- Hard-stops unless Ecuador 2015–2023 are `PASS_INDIVIDUAL_OUTCOMES` with ≥95% outcome completeness.
- Replaces the obsolete Ecuador `equador_clean_v240.parquet` source with the repaired annual files.
- Includes Ecuador in pandemic-period and annual event-study analyses.
- Preserves textual Ecuador labels while mapping older numeric labels from SAV metadata.
- Retains Chile facility ownership without treating it as a hospital identifier.
- Keeps hospital-volume inference restricted to Brazil and Mexico.
- Writes all final outputs under `analysis_v284_final/`.

---

# Session 1 — source gate and clean data preparation

Restart the Colab runtime first.

```python
from google.colab import drive
drive.mount('/content/drive')
```

Install dependencies:

```python
!pip -q install pandas numpy scipy statsmodels patsy matplotlib \
    pyarrow openpyxl xlsxwriter psutil pyreadstat
```

Upload `tce_master_v2_8_4_source_locked_final.py` to `/content/`, then run:

```python
%run /content/tce_master_v2_8_4_source_locked_final.py
```

Validate the source lock:

```python
gate = verify_tce_master_v284()
```

The expected result includes:

```text
source_lock_passed: true
source_lock_checks: 15
Mexico 2015-2017: PASS_STRICT
Ecuador 2015-2023: PASS_INDIVIDUAL_OUTCOMES
hospital_volume_countries: Brazil, Mexico
pandemic_countries: Brazil, Mexico, Chile, Ecuador
```

Then prepare the country-level lean files and final cohort partitions:

```python
prep = prepare_data_v284(
    clean_output=True,
    rebuild_chile=False,
)
prep
```

Do not assign the large country DataFrames manually in the notebook. The function
returns paths and small validation summaries.

Expected output root:

```text
/content/drive/MyDrive/Projeto_TCE_Multinacional/analysis_v284_final/
```

After Session 1 finishes, inspect `prep["validation"]`. Expected primary years:

- Brazil: 2015–2023
- Mexico: 2015–2023
- Chile: 2015–2023
- Ecuador: 2015–2023

Then restart the runtime.

---

# Session 2 — final models, tables, and figures

Mount Drive and install the same dependencies, then:

```python
%run /content/tce_master_v2_8_4_source_locked_final.py
```

Run the final analysis:

```python
manifest = resume_final_analysis_v284(
    run_models=True,
    regenerate_tables=True,
    regenerate_figures=True,
)
manifest
```

The master will generate:

```text
analysis_v284_final/
├── 01_data/
├── 02_qc/
├── 03_tables/
├── 04_figures_main/
├── 05_figures_supplement/
├── 06_models/
├── 07_logs/
└── 08_manuscript_support/
```

Important final files include:

```text
02_qc/Source_lock_gate_v284.csv
02_qc/Mexico_year_coverage_audit_v280.csv
02_qc/Ecuador_source_lock_by_year_v284.csv
02_qc/Final_cohort_validation_v280.csv
03_tables/Table_1_Cohort_characteristics.csv
03_tables/Table_2_Annual_outcomes.csv
03_tables/Table_6_Final_hospital_volume_models.csv
03_tables/Supplementary_Table_8_Pandemic_period_models.csv
03_tables/Supplementary_Table_9_Annual_event_study.csv
08_manuscript_support/Frozen_statistical_analysis_plan_v284.md
08_manuscript_support/analysis_manifest_v284.json
```

## Do not run

Do not run any of the following before or instead of this release:

```python
prepare_data_v281(...)
resume_final_analysis_v281(...)
run_pipeline_complete_v281(...)
```

Do not delete or rename:

```text
analysis_v283_preflight_repair/
01_intermediate/brasil/
01_intermediate/mexico/
01_intermediate/chile/
```

The raw and intermediate folders are not modified by the v2.8.4 output reset.
