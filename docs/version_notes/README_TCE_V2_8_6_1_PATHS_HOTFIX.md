# TCE LATAM v2.8.6.1 — Paths compatibility hotfix

## What failed

The v2.8.6 patch used three path attribute names that do not exist in the
`Paths` dataclass loaded by the v2.8.4 master:

- `PATHS.manuscript_support` (correct: `PATHS.manuscript`)
- `PATHS.figures_supplement` (correct: `PATHS.figures_supp`)
- `PATHS.root` (correct: `PATHS.output`)

The first mismatch caused the reported `AttributeError` inside
`verify_tce_final_robustness_v286()`.

## Scientific impact

None. The error occurred in the readiness verifier before any v2.8.6 analysis
ran. The prepared cohort, v2.8.4 models, and v2.8.5.1 enhancements remain intact.
The hotfix changes only filesystem attribute names and the version label.

## Files

Upload this corrected file to Colab:

`tce_v2_8_6_1_final_robustness_paths_fixed.py`

## Continue in the same Colab runtime

If the v2.8.4 master and v2.8.5.1 patch are still loaded, do not restart and do
not rerun previous analyses. Run:

```python
%run /content/tce_v2_8_6_1_final_robustness_paths_fixed.py

check = verify_tce_final_robustness_v286()
check

final_robustness = run_tce_final_robustness_v286(
    skip_existing=True,
)
final_robustness
```

Expected verifier result:

```text
"all_ready": true
```

## After a restarted runtime

Load the three files in this order:

```python
%run /content/tce_master_v2_8_4_source_locked_final.py
%run /content/tce_v2_8_5_1_methodological_enhancement_fixed.py
%run /content/tce_v2_8_6_1_final_robustness_paths_fixed.py

check = verify_tce_final_robustness_v286()

final_robustness = run_tce_final_robustness_v286(
    skip_existing=True,
)
```

Do not run `prepare_data_v284()` and do not delete `analysis_v284_final`.

## Expected final manifest

`analysis_v284_final/08_manuscript_support/final_robustness_manifest_v286.json`
