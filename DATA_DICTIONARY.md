# Data dictionary and source semantics

The primary unit is a Brazilian administrative AIH authorisation or a Mexican
discharge record. Neither is a unique person. No patient linkage is performed.
All individual fields below remain local; public outputs contain aggregates.

## Brazilian SIH/SUS RD

Official source: [DATASUS SIH/SUS](https://datasus.saude.gov.br/acesso-a-informacao/producao-hospitalar-sih-sus/).
Field definitions: [DATASUS technical layout, 2015](https://ccvisat.ufba.br/wp-content/uploads/2019/10/Dicion%C3%A1rio_-SIH_SUS.pdf).

| Source field | Derived field or role | Rule and limitation |
|---|---|---|
| N_AIH | record_id | Administrative authorisation key; repeat records are audited and grouped by the historical authorisation rule. Not a person identifier. Missing keys receive source-file/row keys and remain separate. |
| IDENT, SEQ_AIH5 | aih_type, continuation_sequence | Authorisation type and long-stay sequence. Report type 5 and consistency; do not silently interpret every authorisation as a new clinical episode. |
| CNES | hospital_id | Preserve leading zeros; prefix `brasil:BR_`. Observed continuity does not verify institutional identity through mergers or code changes. |
| UF_ZI, MUNIC_MOV, MUNIC_RES | source geography; hospital_region, residence_region | Hospital-region code is the first two digits of occurrence municipality. |
| ANO_CMPT, MES_CMPT | year, month | Processing competence, not clinical admission year. Source filename competence is retained separately for validation. |
| DT_INTER, DT_SAIDA | admission_date, discharge_date | Local audit of clinical dates and processing-year mismatch; no automatic redefinition of the primary time axis. |
| COD_IDADE, IDADE | age | Code 4: completed years. Code 5: 100 + recorded value. Sub-year and unknown units cannot establish adult eligibility. The old code-4-only rule is retained as a separate compatibility benchmark. |
| SEXO | sex | 1 Male, 3 Female, 0 Other/unknown; otherwise missing under the inherited mapping. |
| DIAG_PRINC | dx_main, trauma_subtype | Principal S06 diagnosis. Normalise punctuation/case, retain source code locally. Four-character groups S060–S069 are administrative subtypes. |
| DIAG_SECUN | diag_secun | Obsolete secondary field, filled with zeros from January 2015; not a valid negative comorbidity indicator. |
| DIAGSEC1–DIAGSEC9 | diagsec1–diagsec9 | Actual secondary diagnosis slots. Recovered for source auditing, not automatically interpreted as present-on-admission comorbidities. |
| MORTE | death_in_hospital | 0/1 retained; invalid/unknown codes become missing and are counted. Maximum across repeated records of the same AIH follows the historical aggregation rule. |
| DIAS_PERM | los_days | Numeric maximum across repeated authorisation records. The historical character maximum understated six stays; this correction is reported separately. Recorded stay sum is not calendar occupancy. |
| PROC_REA, PROC_SOLIC | procedure_code_raw, procedure_code_norm | Unique realised codes are sorted and joined within AIH. The first realised code is mapped, preserving the submitted crosswalk. Requested codes are retained in raw checkpoints. |
| UTI_MES_TO | icu_days_official | ICU daily units; maximum within AIH. Source auditing only. |
| UTI_INT_TO | intermediate_care_days_official | Intermediate-care daily units, not additional ICU days. Source auditing only. |
| CAR_INT | admission_character | Correct admission-character field. REGCT is a contractual field and is not used as urgency. |
| VAL_TOT | cost_local_currency_official | Correct total reimbursement field. Not resource cost or hospital capacity; excluded from primary inference. |

The centenarian conversion is also documented by the maintained
[microdatasus implementation](https://github.com/rfsaldanha/microdatasus/blob/master/R/process_sih.R).
The raw age-unit values and affected counts are reported; no age is inferred
from a missing value or from a profile in the frozen cohort.

## Mexican SSA source

Series: [MEX-INSP-SAEH-SSA-2008-2024](https://riisp.insp.mx/nada/index.php/catalog/18).
The downloaded ZIP contains `Catalogo_saeh_Egresos_2008_2024.xlsx` and a nested
Deflate64 ZIP containing `base_egresos2008_2024.csv`. The accompanying spreadsheet
and [RIISP field dictionary](https://riisp.insp.mx/nada/index.php/catalog/18/data-dictionary/F1?file_name=Egresos)
describe the consolidated schema. Year-specific DGIS files are examined separately;
harmonisation in a consolidated file is not proof that annual releases are identical.

| Source field | Derived field or role | Rule and limitation |
|---|---|---|
| id_insp, id | record_id, source_original_id | Source keys; do not equate with keys in a different sectorial series. |
| anio_base | year | Source/discharge-series year; compare with EGRESO's calendar year and report disagreements. |
| clues | hospital_id, institution_code | Preserve uppercase CLUES; prefix `mexico:`. Characters 3–5 describe institution. The SSA catalogue defines the source population; do not silently trim institutional transitions to match old counts. |
| egreso, ingre | discharge_date, admission_date | Source dates for temporal audit; not person-level transfer linkage. |
| cveedad, edad | age | CVEEDAD 3 = years; 0 hours, 1 days, 2 months, 9 unknown. EDAD 999 is unknown. Keep observed adult ages rather than silently capping extremes. |
| sexo | sex | 1 Male, 2 Female; 3 intersex and 8/9 unknown are retained in the inherited combined Other/unknown category. Missing/invalid source codes remain missing. |
| afecprin | dx_main, trauma_subtype | Reselected principal condition, rather than initial diagnosis DIAG_INI. Principal S06 defines eligibility. |
| motegre | death_in_hospital, discharge_reason | 5 death; 1 cure, 2 improvement, 3 voluntary, 4 transfer, 6 escape and 7 other are non-death discharge reasons. 9 unknown and undocumented codes are missing outcomes. |
| dias_esta | los_days | Recorded duration. Preserve source extremes and separately report 90-day-capped workload. |
| cedocve | hospital_region | Occurrence state, not habitual residence state ENTIDAD. |
| proced | admission_source | Admission origin; this field means procedencia, not surgical procedure. |
| cluesproce, cluesrefer | referring_hospital, referred_hospital | Recorded referral facility fields. Availability is not proof of a complete linked transfer history. |
| sa_inten, sa_interm | intensive_care_hours, intermediate_care_hours | Separate recorded hours. Units differ from Brazilian daily units; no pooled interpretation or primary adjustment. |
| tipserv, causaext, traumat | source audit fields | Admission service, external cause and intent; retained in S06 checkpoints and not imputed when absent. |

No Mexican surgical procedure is inferred from PROCED. Separate procedure files
would require their own linkage and validation and are not used for the submitted
Brazil-only acute-procedure workload measure.

The consolidated Mexican dates are month/day/year strings. The year is extracted
explicitly; 399 eligible discharges have a clinical discharge year one year before
the source-series year. The original source-series time axis is retained and the
discordance is reported. It is not silently reassigned to clinical year.

In Brazilian S06 authorisations, secondary slot 1 predominantly records external
causes (V–Y codes). A nonempty secondary code does not establish comorbidity.
Slots 2–9 are audited separately. No admission-timing or severity interpretation
is inferred from administrative availability alone.

## Common analysis variables

| Variable | Definition |
|---|---|
| hospital_volume_year | Count of all eligible adult principal-S06 records in the hospital and reporting year, before model complete-case exclusions. |
| lag_volume | The previous observed hospital-year's count only if the year difference equals exactly one. |
| log_lag_volume | Natural logarithm of `lag_volume + 1`. |
| analytic sample | Known outcome, exposure, age, sex, subtype, year and hospital; at least two eligible years per hospital. |
| specified lesion | Principal code S061–S067. Subsets keep the original full eligible-cohort volume exposure, then reapply model eligibility. |
| primary_acute_surgery | Brazil-only inherited mapping of realised acute cranial/decompressive procedure codes; counts authorisations with a code, not operations. |

Age splines, subtype categories and sex are adjusted administrative characteristics,
not substitutes for GCS, pupils, CT findings or physiological severity. Unknown
outcome is never classified as survival. Identical profiles are never used alone
as a deduplication key. Code files specify all operational rules and thresholds.

## DGIS annual SSA schema changes

The official annual `CATTIPOEDAD.csv` catalogues define completed years as code
3 for 2015–2019 and code 5 for 2020–2023. Codes 0/1/2 (hours/days/months)
become 2/3/4 from 2020. The INSP consolidation harmonises them back to 0/1/2/3.
The comparison explicitly maps these units; applying the consolidated age rule
to unchanged 2020–2023 annual records would incorrectly select days as years.
The downloaded annual dictionaries and SHA-256 manifest document this change.

The 2020 annual sex catalogue contains an inconsistent label for code 9
("FEMENINO"). This label is not used to reinterpret the consolidated SSA data: its
own dictionary defines 1 male, 2 female, 3 intersex, 8 unknown and 9 unspecified.
Sex distributions and annual-versus-consolidated recoding are audited separately.

## Additional discrepancies with retained Mexican cohorts

The earlier 2015–2017 repair applied an undocumented-for-the-paper upper age
limit of 120; three 2016 SSA deaths recorded at age 130 are consequently absent
from the frozen file. The reconstruction preserves recorded ages under the
scientific age-at-least-20 rule and retains the planned age-at-most-110 sensitivity.
These extreme recorded ages are not asserted to be clinically correct.
Five earlier records with non-binary/unknown source sex were missing under the
old binary normalisation and are now explicitly other/unknown. Thirty-six
2018–2020 profiles previously coded as survivors have unknown discharge reason
in the reconstructed source and are now missing outcomes. The 44-profile
discrepancy is reported separately from the broader sectorial-population change.
