# TCE LATAM Source Super-Audit v2.8.2

## Purpose

This corrected preflight addresses the v2.8.1 crash and expands the Ecuador audit to 2015–2024.

### Mexico

- Reuses the completed v2.8.1 scans when the source path and file size match.
- Does **not** rescan the 45-million-row consolidated file unless `force_rescan_mexico=True`.
- Uses fixed empty-table schemas, so an absent calibration candidate no longer raises `KeyError: calibration_pass`.
- Calibrates age units and in-hospital death codes against validated 2018–2023 checkpoints.
- Recovers 2015–2017 only when diagnosis, adult age, outcome and hospital fields pass strict validation.
- Prefers annual files over the 2013–2020 consolidated file and measures their overlap.

### Ecuador

- Inventories all files recursively.
- Separates patient microdata, capacity files, documentation, archives and 2014 aggregate tabulations.
- Audits 2015–2024.
- Selects one preferred patient source and one preferred capacity source per year.
- Prefers readable CSV/SAV files; RDS/RData remain fallbacks when no equivalent source exists.
- Builds adult S06 recovery Parquets for valid years.
- Tests exact establishment identifiers first.
- Otherwise tests a composite institutional key.
- Validates linkage using total discharges, deaths and stay-days when these capacity totals are available.

## Important

Do not delete `analysis_v281_preflight` before running this version. The v2.8.2 script can reuse the completed Mexico scan stored there.

`clean_output=True` removes only `analysis_v282_preflight`.

## Colab execution

Restart the runtime and run only this script.

```python
from google.colab import drive
drive.mount('/content/drive')
```

```python
!pip -q install pandas numpy pyarrow pyreadstat openpyxl xlsxwriter psutil
```

Upload `tce_latam_source_super_audit_v282.py` to `/content/`, then:

```python
%run /content/tce_latam_source_super_audit_v282.py
verify_latam_source_super_audit_v282()
```

Run the audit:

```python
result = run_latam_source_super_audit_v282(
    clean_output=True,
    build_mexico_recovered=True,
    build_ecuador_recovered=True,
    force_rescan_mexico=False,
)
result
```

Expected early log lines for Mexico:

```text
Imported completed v2.8.1 scan: consolidated_2013_2020
Imported completed v2.8.1 scan: annual_2015
...
```

If those lines appear, the large raw Mexico files are not being rescanned.

## Key outputs

```text
analysis_v282_preflight/
├── 01_mexico/
│   ├── Mexico_coding_consensus_v282.csv
│   ├── Mexico_2015_2017_recoverability_v282.csv
│   ├── Mexico_annual_vs_consolidated_overlap_v282.csv
│   ├── Mexico_preferred_recovered_sources_v282.csv
│   └── recovered/
├── 02_chile/
│   └── Chile_hospital_linkage_audit_v282.csv
├── 03_ecuador/
│   ├── Ecuador_source_inventory_v282.csv
│   ├── Ecuador_preferred_sources_v282.csv
│   ├── Ecuador_recovery_manifest_v282.csv
│   ├── Ecuador_hospital_linkage_audit_v282.csv
│   ├── Ecuador_linkage_count_validation_YEAR_v282.csv
│   └── recovered/
├── 04_summary/
└── 05_logs/
```

A ZIP is created automatically at:

```text
/content/drive/MyDrive/Projeto_TCE_Multinacional/analysis_v282_preflight.zip
```

## Interpretation rules

### Mexico

An early year is included only if it has a preferred recovered file with `selected=True`.

### Ecuador

- `EXACT_ID_LINKAGE_VALIDATED_COUNTS`: eligible for hospital/capacity analyses after final review.
- `EXACT_ID_LINKAGE_POSSIBLE`: exact ID matches, but count concordance requires review.
- `VALIDATED_COMPOSITE_LINKAGE_COUNTS`: useful for a capacity sensitivity analysis, not an official hospital identifier.
- `VALIDATED_COMPOSITE_LINKAGE_CANDIDATE`: linkage is plausible but requires count review.
- `AGGREGATED_CAPACITY_ONLY`: use capacity variables only at aggregated institutional/geographic levels.

Do not run the final analytic master until the preflight ZIP has been reviewed.
