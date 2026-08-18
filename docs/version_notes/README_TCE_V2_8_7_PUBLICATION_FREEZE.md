# TCE LATAM v2.8.7 — Publication Freeze

## Purpose
This is not a new inferential analysis. It performs final publication curation after v2.8.6.1:

- retires the non-informative spline output whose confidence bands span 0%–100%;
- creates a main figure for the validated between-hospital versus within-hospital decomposition;
- creates a supplementary case-mix-by-volume figure;
- generates a core-findings table;
- freezes the final figure roster and analytic stopping rule.

The v2.8.4 primary cohort and models are not changed or rerun.

## Colab execution
Restart the runtime, upload `tce_v2_8_7_publication_freeze.py`, then run:

```python
%run /content/tce_v2_8_7_publication_freeze.py

check = verify_tce_publication_freeze_v287(
    base_dir="/content/drive/MyDrive/Projeto_TCE_Multinacional"
)
check
```

Expected:

```text
all_required: True
spline_retirement_required: True
```

Then:

```python
freeze = run_tce_publication_freeze_v287(
    base_dir="/content/drive/MyDrive/Projeto_TCE_Multinacional"
)
freeze
```

## New outputs

```text
analysis_v284_final/
├── 03_tables/
│   └── Table_13_Core_findings_for_manuscript_v287.csv/.xlsx
├── 04_figures_main/
│   └── Figure_3_Between_within_volume_decomposition_v287.png/.pdf
├── 05_figures_supplement/
│   └── Supplementary_Figure_5_Case_mix_by_volume_v287.png/.pdf
└── 08_manuscript_support/
    ├── Final_analytic_and_publication_freeze_v287.md
    ├── Final_figure_roster_v287.csv/.xlsx
    └── publication_freeze_manifest_v287.json
```

## Outputs retired from publication
Do not use either v2.8.4 or v2.8.6 spline tables/figures. Do not use the original unadjusted funnel plot. The overdispersion-adjusted funnel remains supplementary and descriptive only.

## What not to rerun
Do not rerun data preparation, primary models, v2.8.5.1, or v2.8.6.1.
