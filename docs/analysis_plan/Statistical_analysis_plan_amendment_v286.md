# Statistical analysis plan amendment — 2.8.6.1-final-robustness-paths-hotfix

Date generated: 2026-07-31 02:43:15

## Status
This final amendment does not alter the frozen v2.8.4 primary cohort, outcome,
exposure, or primary hospital-volume model. It is restricted to diagnostic,
interpretive, geographic-confounding, coding-robustness, and figure-repair analyses.

## Added analyses
1. Repaired spline confidence intervals after numerical non-positive-semidefiniteness
   of the cluster-robust covariance matrix produced zero-width intervals in v2.8.4.
2. Hybrid decomposition of prior-year volume into between-hospital mean volume and
   within-hospital deviations from each hospital's own mean.
3. Country-specific region-adjusted prior-year volume sensitivity.
4. Case-mix characteristics across country-year prior-volume quartiles.
5. Age-sex-only direct mortality standardization to test dependence on administrative
   phenotype coding.
6. Overdispersion-adjusted funnel plots restricted to hospitals with at least five
   expected deaths; these remain descriptive and are not quality rankings.

## Interpretation safeguards
- Between-hospital volume coefficients can reflect referral, selective triage,
  unmeasured injury severity, and hospital role.
- Within-hospital estimates are less vulnerable to stable hospital confounding but
  remain observational and noncausal.
- Funnel plots and risk-standardized mortality must not be used to publicly rank
  individual hospitals because physiologic and imaging severity are unavailable.
- No additional subgroup fishing is planned after this amendment.
