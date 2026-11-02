# Frozen statistical analysis plan — TCE Multinational v2.8.4

## Study design
Retrospective multinational cohort of hospital discharge records with a principal S06.x diagnosis. The primary harmonized cohort includes patients aged 20 years or older in Brazil, Mexico, Chile, and Ecuador from 2015 through 2023. Chile is released in grouped ages, so 20 years was selected as the common lower bound. A sensitivity cohort includes patients aged 18–19 years where exact age is available. Ecuador 2024 is preserved as a source extension but is not mixed into the primary common-period cohort.

## Source lock
Mexico 2015–2017 is derived from the consolidated 2013–2020 SAEH/DGIS source after exact agreement with cached S06 and adult-age counts; age-unit code 5 denotes years and MOTEGRE code 5 denotes in-hospital death. Mexico 2018–2023 uses validated annual checkpoints. Ecuador 2015–2023 uses one selected annual source per year with at least 95% mortality completeness. Chile has no exact public establishment identifier and is excluded from hospital-volume analyses. Ecuador composite capacity linkage is ecological only and is not treated as hospital-level linkage.

## Primary descriptive objective
Describe admissions, in-hospital mortality, length of stay, age, sex, administrative TBI phenotype, and temporal trends across the four participating health systems. Country comparisons are descriptive and are not interpreted as rankings of quality.

## Primary hospital-volume objective
Evaluate the association between prior-year hospital TBI volume and in-hospital mortality in Brazil and Mexico, the countries with stable hospital identifiers. Prior-year volume is the primary exposure to reduce simultaneity. Same-year volume is a sensitivity exposure.

## Primary volume model
Patient-level logistic generalized linear model with hospital-clustered standard errors. Adjustment: age restricted spline, sex, administrative TBI subtype, and country-year fixed effects. Exposure: country-year standardized log prior-year hospital volume.

## Prespecified volume sensitivities
Same-year volume; country interaction; country-specific estimates; exclusion of hospital-years with fewer than five admissions; age >=18 sensitivity; structural intracranial injury subset; post-admission ICU/surgery sensitivity in Brazil; Gamma and negative-binomial survivor length-of-stay models.

## Nonlinear association
Country-specific restricted cubic spline models of absolute prior-year hospital volume. Standardized adjusted predictions are generated over the 5th to 95th percentiles. A likelihood-ratio test compares spline and linear specifications.

## Hospital variation and risk standardization
Country-specific mortality risk models excluding hospital volume are used to calculate expected deaths. Hospital-year observed and expected mortality are aggregated. Hospital estimates are stabilized with empirical-Bayes random-effects shrinkage of log observed-to-expected mortality. Funnel plots use Poisson 95% and 99.8% control limits.

## Within-hospital analyses
Risk-adjusted hospital-year mortality differences are analyzed using weighted two-way hospital and year fixed-effects residualization. A contiguous-year first-difference model is a supporting sensitivity. These analyses estimate whether changes in a hospital's own volume are associated with changes in its risk-adjusted mortality.

## Individual-level factors
Age band, sex, and administrative TBI phenotype are modeled in the four-country cohort with country-year adjustment. Country-specific models are supplementary.

## Pandemic analyses
Brazil, Mexico, Chile, and Ecuador are included because all four now have validated individual mortality through 2023. Period models classify 2015–2019 as pre-pandemic, 2020–2021 as pandemic, and 2022–2023 as recovery. Adjusted annual event studies use 2019 as reference.

## Country-specific exploratory analyses
Chile and Ecuador variables are analyzed only in years with at least 70% informative completeness and at least two levels. Missing values are not coded as absence. Rare levels are collapsed. Chile facility ownership is retained as a system-level attribute, not a hospital identifier. Ecuador numeric labels are mapped from official SAV metadata when available, while already textual labels are preserved.

## Multiplicity
The primary hospital-volume hypothesis is interpreted using its nominal two-sided p value and confidence interval. Benjamini-Hochberg correction is applied separately within prespecified secondary/exploratory families.

## Interpretation
All estimates are associations. Residual confounding by injury severity, referral, transfer, imaging findings, physiology, and indication for surgery is expected. The volume coefficient is not interpreted as a causal measure of hospital quality. Chile and Ecuador must not be used for hospital-volume inference.
