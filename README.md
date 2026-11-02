# LATAM TBI health-systems analysis

Reproducibility materials for **Hospital volume, mortality and concentration of traumatic brain injury care in Brazil and Mexico: a longitudinal administrative cohort study**.

The current analysis is in [`reanalysis_2026/`](reanalysis_2026/README.txt). It
compares conventional and hospital fixed-effect volume–mortality associations
on identical samples in Brazil and Mexico and describes concentration of recorded
care. This update contains the code and aggregate evidence accompanying the
manuscript prepared for BMC Health Services Research; it does not imply journal
acceptance or publication.

## Current study

Adults aged at least 20 years with a principal S06 diagnosis were identified in
the available processed cohorts for 2015–2023. Longitudinal analyses cover
2016–2023: 663,921 Brazilian admissions in 2,078 hospitals and 162,904 Mexican
admissions in 842 hospitals. Exposure is log(1 + prior-year eligible volume).
Linear associations are expressed per doubling of **volume plus one**.

All analyses are post hoc. The package retains all 19 linear specifications,
hospital-intercept logistic checks, paired contrasts, every targeted hospital
exclusion, coding diagnostics and workload summaries. The Brazilian paired
contrast persisted across the targeted exclusions; the Mexican comparison was
less precise and sensitive to diagnosis and hospital participation. Missing
clinical severity, episode identifiers, transfers and uncertain source lineage
preclude causal performance rankings and volume thresholds.

## Run the checks

From the repository root, using Python 3.12:

```sh
python verify_repository.py
python -m pip install -r requirements.txt
python reanalysis_2026/verify_archive.py
python reanalysis_2026/verify_archive.py --synthetic
```

The first command uses only the standard library. The remaining commands check
the frozen bundle and synthetic formula implementations without admission data.
They do not rerun the patient-data models. See [REPRODUCIBILITY.md](REPRODUCIBILITY.md)
for reproduction with authorised access to the frozen cohorts.

## Repository contents

| Location | Role |
| --- | --- |
| `reanalysis_2026/` | Current manuscript code, all aggregate results, post hoc plans, input hashes and computational checks |
| `code/00_*` through `code/06_*` | Preserved historical ingestion and earlier analysis scripts |
| `docs/HARMONISATION_AND_CODEBOOK.md` | Current definitions and source limitations |
| `docs/analysis_plan/` | Historical plans; read its README for the boundary with the current post hoc analysis |
| `outputs/` and `supplementary/` | Earlier outputs, retained as historical evidence; not current hospital-performance estimates |
| `docs/version_notes/` | Historical documentation plus the November update record |
| `FILE_INVENTORY.csv`, `checksums/` | Repository integrity records |

The archived four-country analysis is preserved for provenance. Chile and Ecuador
are not part of the current longitudinal comparison. Historical claims that the
identifiers were validated or plans were prespecified do not establish external
identifier validity or prospective registration for the current analysis.

## Data availability

No admission-level records or national raw files are included. Aggregate outputs
alone cannot refit the models. The corresponding author retains the processed
cohorts; access is conditional on applicable source terms and institutional
requirements. Redistribution permission has not been established. Reanalysis
from frozen processed cohorts is separate from independent reconstruction of
national source-to-cohort selection, which was not completed. See
[DATA_NOTICE.md](DATA_NOTICE.md) and [source_manifest.csv](source_manifest.csv).

## Version and citation

The November 2026 update is the local **2.0.0** reproducibility package. A local
version label does not imply a published GitHub release, DOI or accepted article.
Citation metadata in [CITATION.cff](CITATION.cff) identify the five authors.
Use the specific commit or released version when citing the code.

## Integrity and licence

The current `reanalysis_2026` files match Additional file 2 byte for byte. Their
own manifest uses raw SHA-256. Repository-wide fingerprints normalise CRLF to LF
for other text files so checks remain valid across Windows and Unix checkouts;
binary files are hashed without conversion. The inventory excludes itself and
the repository checksum file to avoid circular hashes. `verify_repository.py`
checks both layers. The [MIT licence](LICENSE) applies to repository code and
documentation and does not grant rights to redistribute the source datasets.
