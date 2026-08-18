# TCE LATAM v2.8.5.1 — dtype hotfix and checkpoint resume

## What failed

The frozen v2.8.4 primary analysis completed successfully. The failure occurred only when the
v2.8.5 secondary enhancement attempted to fit case-mix standardized mortality. Patsy/statsmodels
received a pandas nullable `Int64Dtype` column and raised:

```text
TypeError: Cannot interpret 'Int64Dtype()' as a data type
```

## What this release changes

The fixed patch:

1. converts formula outcomes and continuous variables to NumPy `float64`;
2. converts complete year fields to NumPy `int64`;
3. converts formula categorical fields to plain object/string values;
4. applies the same protection to every v2.8.5 formula model, not only the model that failed;
5. resumes enhancement tasks from saved checkpoints;
6. does **not** rerun the completed v2.8.4 primary models by default;
7. does not change the frozen primary outcome, exposure, cohort, or model.

## Correct execution

Restart the Colab runtime to remove stale function definitions. Do not run `prepare_data_v284()`.
The prepared data and completed v2.8.4 outputs already remain on Google Drive.

Upload these two Python files to `/content`:

```text
tce_master_v2_8_4_source_locked_final.py
tce_v2_8_5_1_methodological_enhancement_fixed.py
```

Load them:

```python
%run /content/tce_master_v2_8_4_source_locked_final.py
%run /content/tce_v2_8_5_1_methodological_enhancement_fixed.py
```

Verify the hotfix:

```python
check = verify_tce_enhancement_v285()
check
```

Required output fields:

```text
all_prepared: true
dtype_hotfix_passed: true
primary_models_will_rerun_by_default: false
```

Resume only the methodological enhancements:

```python
enhancements = run_methodological_enhancements_v285(
    skip_existing=True,
)

enhancements
```

`skip_existing=True` reuses the hospital-continuity and coding-drift outputs if the failed run
already saved them. It starts at the first incomplete enhancement.

An equivalent wrapper is available, but direct execution above is clearer:

```python
manifest = resume_final_analysis_v285(
    rerun_primary=False,
    run_enhancements=True,
    skip_existing_enhancements=True,
)
```

## Do not run

Do not run any of the following now:

```python
prepare_data_v284(clean_output=True)
resume_final_analysis_v285(rerun_primary=True)
resume_final_analysis_v284(run_models=True)
```

They would unnecessarily rebuild or rerun already completed work.

## Expected completion

The log should reuse or complete these domains:

```text
hospital_id_continuity
coding_drift
standardized_mortality
absolute_volume_effects
volume_continuity_boundary
temporal_holdout_validation
geriatric_shift
sap_amendment
```

The final manifest should be written to:

```text
analysis_v284_final/08_manuscript_support/
methodological_enhancement_manifest_v2851.json
```

After completion, zip and send the updated complete folder:

```text
analysis_v284_final
```
