ANALYSIS CODE AND AGGREGATE EVIDENCE — Additional file 2

STUDY
Hospital volume, mortality and concentration of traumatic brain injury care in Brazil and Mexico: a longitudinal administrative cohort study

CONTENTS AND SCOPE
The package contains analysis programs, aggregate results, input checksums,
inherited code mappings, post hoc analysis plans and computational validation.
No admission-level datasets, national raw files or individual profiles are
included. Aggregate outputs alone cannot refit the models. Access to the frozen
processed cohorts is subject to the source terms and institutional requirements
described in the manuscript's data availability statement.

The three dated folders preserve the scientific programs and aggregate evidence
from successive analysis stages. Historical Chile/Ecuador aggregates are retained
for provenance and a legacy validation dependency; they are not results of the
Brazil/Mexico longitudinal study. No sensitivity result has been removed.
cohort_diagnostics/ contains the post hoc plan, code and aggregate results for the
cohort, repeated-profile and residualised-exposure diagnostics in section S15.
All plans were recorded after earlier results were known, before execution of
their respective stage; they are not prospective registrations.

VERIFY WITHOUT ADMISSION DATA
Install requirements.txt in an isolated Python environment. It specifies the
versions used; compatibility with alternative environments has not been tested.
  python verify_archive.py
verifies SHA-256, Python syntax and aggregate CSV schemas.
  python verify_archive.py --synthetic
also checks paired covariance and hospital-intercept logistic calculations
against explicit-indicator models in synthetic data. These checks do not require
admission files and do not validate clinical definitions or source selection.

REPRODUCE WITH AUTHORISED LOCAL COHORT ACCESS
Use a separate working copy because the programs replace result files. Keep the
three dated folders as siblings and place the two frozen inputs in:
  auditoria_20261028/base_final/analysis_v284_final/01_data/
  cohort_main_brasil_v280.parquet
  cohort_main_mexico_v280.parquet
Input hashes are in artigo_final_20261028/resultados/input_hashes.json and
cohort_diagnostics/results/processed_audit_validation.json.
  python run_analysis.py --validate
checks inputs, formula implementations and reference results. A legacy assertion
uses the included Chile aggregate bounds; it does not need Chile microdata.
  python run_analysis.py --core
refits all 19 linear specifications, full and subset logistic models, paired
comparisons, every targeted hospital exclusion, coding and workload diagnostics,
and regenerates the figures. It requires the two frozen admission files above.
  python cohort_diagnostics/audit_processed_cohorts.py --data-root auditoria_20261028/base_final/analysis_v284_final/01_data --output cohort_diagnostics/results
reproduces the cohort, profile and residualised-information diagnostics.

The historical oportunidades_adicionais.py additionally requires Chile/Ecuador
cohorts and coorte_chile_causas_recuperadas.parquet; it is not needed for this
paper. reanalisar.py supplies the residualize function and historical provenance;
running it as a standalone program requires its historical inputs.
Reanalysis of processed cohorts is distinct from reconstruction of national raw
source selection. Raw acquisition, clinical admission-date reconstruction and
patient or transfer linkage were not independently reconstructed.

RESULTS MAP
artigo_final_20261028/resultados/efeitos_fixos_sensibilidades.csv: 19 linear fits.
In the same folder:
  contraste_pareado.csv: paired contrasts;
  exclusoes_hospitais.csv: every targeted hospital exclusion;
  logistica_FE.csv and logistica_complementar.csv: hospital-intercept logistic fits;
  comparadores_auditoria_anterior.csv: earlier, distinct association estimands;
  carga_concentrada_2023.csv and auditoria_permanencia.csv: workload, ties and stays;
  codificacao_anual.csv and codificacao_transicoes.csv: diagnostic documentation.
cohort_diagnostics/results/: annual counts, field missingness, repeated-profile
summaries, identifier diagnostics, analytic characteristics, exposure information
and independent estimates. exposure_support_*.csv contain hospital-year summaries
of volume without outcomes or individual profiles; they are not admission files.
Country labels in code are brasil, mexico, chile and equador.

INTERPRETATION
All hypotheses are post hoc, and confidence intervals are nominal. Odds ratios
and percentage-point slopes use different scales. Hospital fixed effects do not
resolve unmeasured time-varying severity, referral selection or coding changes.
See the manuscript and Additional file 1 for the full interpretation and limits.
