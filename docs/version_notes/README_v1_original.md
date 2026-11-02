# LATAM TBI health-systems analysis

[![Version](https://img.shields.io/badge/version-v1.0.0-blue.svg)](#version-and-freeze-status)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Reproducibility repository for the associated manuscript

**Referral concentration, hospital volume, and in-hospital mortality after traumatic brain injury in Latin America: a four-country health-systems analysis, 2015–2023**

This repository is the **versioned reproducibility bundle (v1.0.0)** prepared for the associated manuscript. It contains the code lineage used for source ingestion/audit, source repair, the source-locked primary analysis, methodological enhancements, robustness analyses, and the final publication freeze, together with selected non-identifiable aggregate outputs and quality-control artifacts.

The repository intentionally **does not redistribute patient-level national discharge data**. Raw source files must be obtained from the official national portals listed in [`source_manifest.csv`](source_manifest.csv).

## What is included

- **Analysis code:** seven ordered Python scripts spanning upstream ingestion through the final publication freeze.
- **Source documentation:** national portals, study years, source-access dates, and analytic roles.
- **Harmonisation documentation:** cohort definitions, variable harmonisation, country-specific availability, and analysis boundaries.
- **Frozen analysis documentation:** the prespecified statistical-analysis plan, amendments, version notes, and the final analytic freeze.
- **Aggregate outputs:** selected manuscript/supplementary tables and QC outputs used to verify the reported results.
- **Supplementary Data 1:** the hospital-level risk-standardisation output with pseudonymised analytic hospital identifiers and no patient-level records.
- **Integrity files:** SHA-256 checksums and a repository file inventory.

## Study scope

The common primary study window is **2015–2023** and includes adults aged 20 years or older with a principal ICD-10 S06 diagnosis in four Latin American national discharge systems:

- Brazil — SIH/SUS
- Mexico — Secretaría de Salud / DGIS hospital discharge data
- Chile — DEIS Egresos Hospitalarios
- Ecuador — INEC Registro Estadístico de Camas y Egresos Hospitalarios

### Important analytic boundary

Brazil and Mexico supplied validated longitudinal hospital identifiers and therefore support hospital-volume, concentration, heterogeneity, and between-/within-hospital analyses. Chile and Ecuador contribute admission-level temporal and outcome analyses but **do not support the primary longitudinal hospital-volume inference**.

The repository must not be used to rank hospitals or countries by quality, infer causal effects of referral concentration, or reinterpret the administrative cohort as clinically adjudicated severe TBI.

## Repository structure

```text
latam-tbi-health-systems/
├── README.md
├── REPRODUCIBILITY.md
├── CITATION.cff
├── LICENSE
├── DATA_NOTICE.md
├── requirements.txt
├── source_manifest.csv
├── FILE_INVENTORY.csv
├── .gitignore
│
├── code/
│   ├── 00_upstream_ingestion/
│   │   └── tce_master_v2_5.py
│   ├── 01_source_validation/
│   │   └── tce_latam_source_super_audit_v282.py
│   ├── 02_preflight_repair/
│   │   └── tce_latam_preflight_repair_v283.py
│   ├── 03_primary_analysis/
│   │   └── tce_master_v2_8_4_source_locked_final.py
│   ├── 04_methodological_enhancements/
│   │   └── tce_v2_8_5_1_methodological_enhancement_fixed.py
│   ├── 05_final_robustness/
│   │   └── tce_v2_8_6_1_final_robustness_paths_fixed.py
│   └── 06_publication_freeze/
│       └── tce_v2_8_7_publication_freeze.py
│
├── docs/
│   ├── HARMONISATION_AND_CODEBOOK.md
│   ├── analysis_plan/
│   └── version_notes/
│
├── outputs/
│   ├── aggregate_tables/
│   └── qc/
│
├── supplementary/
│   └── Supplementary_Table_7_Hospital_risk_standardization.xlsx
│
└── checksums/
    └── SHA256SUMS.txt
```

## Analysis lineage

The publication analysis was frozen sequentially as:

```text
public national source files
        ↓
v2.5   upstream ingestion / checkpoint generation
        ↓
v2.8.2 source super-audit
        ↓
v2.8.3 preflight repair and source recovery
        ↓
v2.8.4 source-locked primary analysis
        ↓
v2.8.5.1 methodological enhancements
        ↓
v2.8.6.1 final robustness analyses
        ↓
v2.8.7 publication freeze
```

The sequence is documented in [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) and the version notes under [`docs/version_notes/`](docs/version_notes/).

## Reproducing the analysis

Start with [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md). In brief:

1. Obtain the raw national files from the official portals in [`source_manifest.csv`](source_manifest.csv).
2. Recreate the project directory structure described in the reproducibility guide.
3. Run the scripts in numeric order, respecting the source-lock and validation gates.
4. Compare generated aggregate outputs with the frozen files in [`outputs/`](outputs/).
5. Verify repository integrity using [`checksums/SHA256SUMS.txt`](checksums/SHA256SUMS.txt).

The scripts were developed in a Python/Google Colab workflow. Several scripts preserve the original project-root convention `/content/drive/MyDrive/Projeto_TCE_Multinacional`; the primary-analysis script also supports `TCE_BASE_DIR`. Local reproduction therefore requires setting or adapting the project root as documented.

`requirements.txt` records the analysis environment specification used by the project. Source-specific readers/converters can require additional packages described in the upstream script and version notes; the repository should therefore be treated as a **research reproducibility package rather than a standalone installable Python library**.

## Data availability and redistribution

Raw person-level national discharge files are **not included** in this repository. The repository contains only code, documentation, quality-control summaries, and non-identifiable aggregate outputs. The original national data remain subject to the access and reuse conditions of their source providers.

See [`DATA_NOTICE.md`](DATA_NOTICE.md) and [`source_manifest.csv`](source_manifest.csv).

## Version and freeze status

**v1.0.0 — manuscript-submission reproducibility freeze**

This release corresponds to the analytic state used for the submitted manuscript. The v2.8.7 stopping rule prohibits additional hypothesis-generating analyses before submission; subsequent changes should be limited to correction of identified errors, reproducibility maintenance, or reviewer-requested analyses and should be released under a new version.

## Citation

Citation metadata are provided in [`CITATION.cff`](CITATION.cff). GitHub will expose a **Cite this repository** control when this file is present in the default branch.

If this repository is archived in Zenodo, cite the **version-specific Zenodo DOI** for the exact release used with the manuscript. After journal publication, the preferred citation should also include the final article citation/DOI.

## License

The analysis code and repository documentation are released under the [MIT License](LICENSE). This licence does **not** grant rights to redistribute the underlying national source datasets, which remain governed by their respective providers.

## Authors

- Caio Arruda Maciel — ORCID: 0009-0007-4514-4891
- Carlos Gilberto Carlotti Junior
- Daniel Agustín Godoy
- Andrés Mariano Rubiano
- Wellingson Silva Paiva

## Integrity

The repository includes SHA-256 checksums for all versioned files (excluding the checksum file itself). This permits verification that a downloaded or archived release is identical to the frozen repository content.
