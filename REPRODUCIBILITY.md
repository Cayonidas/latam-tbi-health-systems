# Reproducibility guide

## Purpose

This document describes the reproducibility boundaries and execution sequence for the manuscript-associated **LATAM TBI health-systems analysis v1.0.0**.

The repository preserves the publication-analysis lineage but does not redistribute patient-level national discharge datasets. Reproduction from raw sources therefore requires obtaining the original national files independently and recreating the expected project directory structure.

## 1. Obtain the source data

Use the official portals listed in [`source_manifest.csv`](source_manifest.csv). The primary common period is 2015–2023.

Raw files should not be committed to this Git repository. The included `.gitignore` excludes common raw/intermediate formats and project output directories to reduce the risk of accidental redistribution.

## 2. Project-root convention

The original analysis was developed in Google Colab using a project root equivalent to:

```text
/content/drive/MyDrive/Projeto_TCE_Multinacional
```

Several historical/upstream scripts preserve this path internally. The source-locked primary-analysis script supports the environment variable `TCE_BASE_DIR` and otherwise defaults to the original Colab path.

For local reproduction, either:

- reproduce the original directory convention; or
- update the relevant `base_dir`/path constants before execution.

Do not alter analytic definitions while adapting filesystem paths.

## 3. Execution order

The publication lineage is intentionally sequential. Run the following scripts in order.

### Stage 0 — upstream ingestion / checkpoint generation

```text
code/00_upstream_ingestion/tce_master_v2_5.py
```

This integrated upstream master contains the ingestion and checkpoint-generation logic used in the project, including the Brazil and later-year Mexico inputs subsequently consumed by the source-locked analysis. It also preserves historical country-specific ingestion logic. See `docs/version_notes/README_TCE_MASTER_v2_5.md` for the original Colab execution notes.

Representative verification call:

```python
verify_tce_master_v250()
```

### Stage 1 — source super-audit

```text
code/01_source_validation/tce_latam_source_super_audit_v282.py
```

Representative calls:

```python
verify_latam_source_super_audit_v282()
result = run_latam_source_super_audit_v282(
    clean_output=True,
    build_mexico_recovered=True,
    build_ecuador_recovered=True,
    force_rescan_mexico=False,
)
```

This stage audits the available sources and establishes source usability; it must not be bypassed when rebuilding from raw files.

### Stage 2 — preflight repair

```text
code/02_preflight_repair/tce_latam_preflight_repair_v283.py
```

Representative calls:

```python
verify_latam_preflight_repair_v283()
repair = run_latam_preflight_repair_v283(
    base_dir=BASE_DIR,
    clean_output=True,
    repair_mexico=True,
    repair_ecuador_sources=True,
)
```

The final analysis uses the repaired Mexico 2015–2017 and Ecuador 2015–2023 sources produced/validated at this stage.

### Stage 3 — source-locked primary analysis

```text
code/03_primary_analysis/tce_master_v2_8_4_source_locked_final.py
```

Run the source gate first:

```python
gate = verify_tce_master_v284(base_dir=BASE_DIR)
```

Then prepare the lean country-level analytic inputs:

```python
prep = prepare_data_v284(
    base_dir=BASE_DIR,
    clean_output=True,
    rebuild_chile=False,
)
```

The original workflow then restarted the runtime before running the final models/tables/figures:

```python
manifest = resume_final_analysis_v284(
    base_dir=BASE_DIR,
    run_models=True,
    regenerate_tables=True,
    regenerate_figures=True,
)
```

The primary output root is `analysis_v284_final/` under the selected project root.

### Stage 4 — methodological enhancements

```text
code/04_methodological_enhancements/tce_v2_8_5_1_methodological_enhancement_fixed.py
```

This patch depends on the v2.8.4 runtime/environment. Load the v2.8.4 master first, then this script.

Representative calls:

```python
verify_tce_enhancement_v285(base_dir=BASE_DIR)
run_methodological_enhancements_v285(
    base_dir=BASE_DIR,
    skip_existing=True,
)
```

These analyses add hospital-ID continuity, coding-drift checks, standardised mortality, absolute-volume contrasts, temporal validation, and related robustness analyses without changing the frozen primary analysis.

### Stage 5 — final robustness analyses

```text
code/05_final_robustness/tce_v2_8_6_1_final_robustness_paths_fixed.py
```

Load the v2.8.4 master and v2.8.5.1 enhancement script in the same runtime before this script.

Representative calls:

```python
verify_tce_final_robustness_v286(base_dir=BASE_DIR)
run_tce_final_robustness_v286(
    base_dir=BASE_DIR,
    skip_existing=True,
)
```

This stage adds the formal between-/within-hospital decomposition and other final robustness analyses.

### Stage 6 — publication freeze

```text
code/06_publication_freeze/tce_v2_8_7_publication_freeze.py
```

Representative calls:

```python
verify_tce_publication_freeze_v287(base_dir=BASE_DIR)
run_tce_publication_freeze_v287(base_dir=BASE_DIR)
```

This final stage verifies required outputs, records the publication freeze, and retires non-informative spline/funnel outputs as documented in the freeze note.

## 4. Country-specific inference boundaries

- **Brazil:** admission-level and longitudinal hospital-level inference supported.
- **Mexico:** admission-level and longitudinal hospital-level inference supported; 2015–2017 use the validated repaired source path.
- **Chile:** admission-level temporal/outcome analyses supported; no validated longitudinal hospital identifier for the primary volume analysis.
- **Ecuador:** admission-level temporal/outcome analyses supported; no validated longitudinal hospital identifier for the primary volume analysis.

## 5. Frozen verification outputs

Selected aggregate tables are under:

```text
outputs/aggregate_tables/
```

Selected source-lock, cohort, continuity, diagnostic, and model-validation outputs are under:

```text
outputs/qc/
```

These files are provided to permit verification of manuscript-facing claims without redistributing person-level records.

## 6. Supplementary hospital-level output

```text
supplementary/Supplementary_Table_7_Hospital_risk_standardization.xlsx
```

contains the manuscript's hospital-level supplementary output using pseudonymised analytic hospital identifiers. It contains no patient-level records.

## 7. Integrity verification

To verify the release on Linux/macOS from the repository root:

```bash
sha256sum -c checksums/SHA256SUMS.txt
```

On Windows PowerShell, checksums can be independently calculated with `Get-FileHash -Algorithm SHA256` and compared with the manifest.

## 8. Environment note

`requirements.txt` records the project environment specification. The upstream ingestion scripts use source-specific readers/converters that may require packages described in the script headers/version notes. Because the original workflow evolved through multiple national data formats and Colab sessions, the repository is a **publication reproducibility bundle**, not a one-command packaged application.

## 9. Reproducibility claims

This repository supports inspection of the full analytic lineage and recreation of the reported analyses when the required public raw inputs are independently obtained and placed in the expected project structure. It does not claim that raw national files are legally or practically redistributable by the manuscript authors.

## 10. Analytic stopping rule

The v2.8.7 freeze records that no additional hypothesis-generating analyses should be added before manuscript submission. Post-freeze changes should be limited to identified corrections, reproducibility maintenance, or reviewer-requested analyses and should be documented in a new tagged release.
