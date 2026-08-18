# TCE MASTER v2.5.0

## What v2.5 fixes

1. Chile grouped ages are no longer forced into nullable integer age.
2. `90 y más` is correctly retained as an adult group (midpoint proxy 95 only for storage/descriptive calculations).
3. Chile 2021 can be parsed when `GRUPO_EDAD` uses text variants or ordinal codes 0–11 / 1–12.
4. Integer fields are converted without silently rounding non-integral values.
5. All output functions recreate missing parent directories, including `03_qc`.
6. Existing Brazil, Mexico, Chile v2.4, and Ecuador v2.4 checkpoints can be reused.

## Recommended Colab sequence

Restart the runtime, mount Drive, and install dependencies:

```python
from google.colab import drive
drive.mount('/content/drive')

!pip -q install pyarrow openpyxl xlsxwriter pyreadstat statsmodels scipy scikit-learn matplotlib seaborn chardet
```

Load only v2.5:

```python
%run /content/tce_master_v2_5.py
verify_tce_master_v250()
```

Expected active functions:

- `run_pipeline_complete_v250`
- `finalize_country_df_v250`
- `harmonize_all_v250`
- age schema `('REQUIRED', 'float64')`

Inspect the raw 2021 Chile age groups before reprocessing:

```python
age_2021 = diagnose_chile_year_age_groups_v250(2021)
display(age_2021)
```

The diagnostic is saved at:

`03_qc/chile_age_group_raw_diagnostic_2021_v250.csv`

Reprocess only Chile 2021 and rebuild the Chile aggregate checkpoint:

```python
df_chile = refresh_chile_2021_v250()
print(df_chile.groupby('year').size())
```

This preserves all other yearly Chile checkpoints and all Brazil/Mexico/Ecuador checkpoints.

Then resume harmonization and analyses without rereading the large raw files:

```python
df_cdm, df_main, df_surg, df_dc, models, advanced = (
    resume_analysis_v250(run_models=True)
)
```

## Mandatory checks after completion

```python
print(df_main.groupby('country').size())
print(df_main.groupby(['country', 'year']).size())
print(df_main[['country','age','age_band_common','age_exact_available']].groupby('country').agg(
    n=('country','size'),
    age_missing=('age', lambda s: s.isna().sum()),
    age_exact=('age_exact_available', 'mean')
))
```

Chile must be modeled with `age_band_common`, not as exact age. Brazil, Mexico, and Ecuador may use exact age when validated.

## If Chile 2021 remains zero

Do not force it into the cohort. Open:

`03_qc/chile_age_group_raw_diagnostic_2021_v250.csv`

The file will show the raw age-group values and whether they were parsed. Unknown numeric coding is labeled `UNMAPPED_NUMERIC_GROUP_CODE` rather than being mistaken for exact age.
