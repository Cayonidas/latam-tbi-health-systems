# Statistical analysis plan amendment — 2.8.5.1-methodological-enhancement-dtype-hotfix

Date generated: 2026-07-31 02:12:25

## Status
This amendment is executed before the v2.8.4 final models are interpreted for manuscript claims.
It does not replace the frozen primary exposure, outcome, or primary model. The prior-year
hospital-volume model remains primary. All analyses below are secondary, sensitivity, diagnostic,
or interpretability analyses.

## Rationale
The v2.8.3 source audit expanded Mexico to 2015–2017 and Ecuador to 2015–2023. The source
transition between Mexico 2017 and 2018 and country differences in ICD-10 coding practice require
explicit robustness checks. Odds ratios alone are also difficult to interpret clinically in a cohort
exceeding one million admissions.

## Added analyses
1. Hospital identifier continuity and Mexico 2017–2018 source-boundary audit.
2. Broad administrative TBI phenotype sensitivity and coding-drift assessment.
3. Country-specific case-mix standardized mortality using each country's 2019 age-sex-phenotype
   distribution, with absolute risk differences versus 2019.
4. Standardized absolute mortality contrast between the 10th and 90th percentiles of prior-year
   hospital volume using nonlinear models.
5. Volume sensitivities restricted to hospitals with longitudinal continuity and Mexico source-
   homogeneous windows.
6. Temporal holdout validation of mortality risk models (development 2015–2020; validation 2021–2023).
7. Geriatric case-mix shift by country and year.

## Interpretation safeguards
- Cross-country mortality levels are not treated as rankings of quality because database coverage,
  referral patterns, and coding differ.
- Broad phenotype analyses test robustness to granular coding differences; they do not create a
  clinical severity score.
- Mexico source-boundary analyses are sensitivity checks, not post hoc exclusions from the primary cohort.
- Volume effects remain associational and may reflect referral of more severe cases to high-volume centers.
- Standardized absolute risks complement, but do not replace, the frozen primary odds-ratio model.
