# Final analytic and publication freeze — 2.8.7-publication-freeze

Generated: 2026-07-31 14:28:10

## Analytic status
The primary v2.8.4 cohort, outcome, exposure, and models remain unchanged. The v2.8.5.1 and v2.8.6.1 analyses are retained as prespecified methodological and interpretive sensitivities.

## Main inferential message
The conventional between-center association links greater prior-year hospital TBI volume with higher in-hospital mortality. In the hybrid decomposition, the between-hospital component remains positive, whereas the within-hospital component is protective in Brazil and null in Mexico. This pattern is compatible with residual referral and severity selection and does not support a causal claim that increasing a hospital's TBI volume worsens mortality.

## Retired output
The following files must not be used in the manuscript, supplement, abstract, or presentations:
- `Table_7_Adjusted_volume_spline_predictions.csv`
- `Table_7_Adjusted_volume_spline_predictions_v286.csv`
- `Figure_3_Adjusted_volume_splines.png/pdf`
- `Figure_3_Adjusted_volume_splines_v286.png/pdf`

Reason: the v2.8.6 cluster-covariance repair produced non-informative 0%-100% confidence bands. The point estimates are not required for the central conclusions and the spline analysis is analytically retired rather than repeatedly repaired.

## Final main-figure roster
1. Cohort flow.
2. Annual mortality by country.
3. Between-hospital versus within-hospital volume decomposition (`Figure_3_Between_within_volume_decomposition_v287`).
4. Primary and sensitivity hospital-volume forest plot.
5. Centralization trends.

The original raw funnel plot should not be used. The overdispersion-adjusted funnel plot remains supplementary and descriptive only.

## Analytic stopping rule
No additional hypothesis-generating correlations should be added before manuscript submission. Further analyses should be limited to reviewer-requested checks, reproducibility verification, or correction of an identified error.

## Interpretation boundaries
- Do not describe the between-center volume association as causal.
- Do not rank hospitals or countries by quality.
- Do not label the administrative cohort as clinically confirmed severe TBI.
- Do not infer harm from decompressive surgery analyses.
- Chile and Ecuador contribute individual-level temporal analyses but not hospital-volume inference.
