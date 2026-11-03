# Independent reconstruction from national sources

Study: **Hospital volume, mortality and concentration of traumatic brain injury care in Brazil and Mexico: a longitudinal administrative cohort study**

Report assembled from executed source metadata at 2026-11-03T22:20:55.363608+00:00.

## What was actually reconstructed

All 2,916 Brazilian SIH/SUS RD DBC files for 27 states, 12 monthly processing
competencies and 2015–2023 were downloaded from DATASUS, hashed and parsed.
The complete INSP RIISP SSA archive was downloaded and its nested CSV read to
EOF, with ZIP CRC, uncompressed byte count and SHA-256 verification. All nine
annual DGIS **SSA** releases for 2015–2023 were independently downloaded and
read to EOF; the sectorial release was not substituted. Frozen Parquets supplied
no records or replacement fields to either reconstructed cohort.

Brazilian source records read: **107,392,891**. Principal S06 source records:
**943,449**. The Mexican consolidation contains **44,844,719**
records for 2008–2024; **23,004,770** belong to 2015–2023 and **171,305** have
principal S06 in those years. All source years in the consolidated member were
read, although only the study years contributed selected records.

| country | series | files | bytes |
| --- | --- | --- | --- |
| brasil | SIH-SUS RD | 2916 | 7982881105 |
| mexico | DGIS annual SSA | 9 | 893343043 |
| mexico | DGIS official dictionary/descriptor | 18 | 34985286 |
| mexico | MEX-INSP-SAEH-SSA-2008-2024 | 1 | 1633412960 |

Exact addresses, acquisition times, remote metadata, sizes and SHA-256 hashes
are in `SOURCE_FILES_MANIFEST.csv` and `SOURCE_CHECKSUMS.sha256`. Metadata
files and source pages are retained internally. The first discovery RD file's
timestamp uses its local acquisition modification time; this exception is
labelled in the manifest, rather than assigned an invented downloader time.

The consolidated CSV SHA-256 is
`15f4d0bb947412f2d139a2fa155d74fb85c481a1941b5f7606c23ecb95653510` and its uncompressed
length is **11,033,796,740 bytes**. It was processed in 250,000-record chunks.
The successful complete pass is distinguished in the logs from two earlier
interrupted attempts (encoding detection and a transient Windows file lock).
Neither partial pass supplied a final cohort.

## Reconstructed cohorts and complete selection flow

| country | source_cohort_n | source_cohort_hospitals | longitudinal_n | longitudinal_hospitals | longitudinal_deaths |
| --- | --- | --- | --- | --- | --- |
| brasil | 755313 | 3211 | 664508 | 2079 | 74279 |
| mexico | 107686 | 700 | 92853 | 437 | 11352 |

| country | stage | n | unit | removed_or_grouped_since_previous | transition |
| --- | --- | --- | --- | --- | --- |
| brasil | All records in 2015-2023 competency files | 107392891 | RD records |  | source population |
| brasil | Active DBF records | 107392891 | RD records | 0.0 | selection |
| brasil | Principal S06 records before AIH aggregation | 943449 | RD records | 106449442.0 | selection |
| brasil | Distinct administrative AIH authorisations | 943383 | AIH authorisations | 66.0 | AIH grouping, not deletion of episodes |
| brasil | After excluding documented age below 20 | 755313 | AIH authorisations | 188070.0 | selection |
| brasil | Known age at least 20 | 755313 | AIH authorisations | 0.0 | selection |
| brasil | Processing year 2015-2023 | 755313 | AIH authorisations | 0.0 | selection |
| brasil | Hospital identifier available | 755313 | AIH authorisations | 0.0 | selection |
| brasil | Contiguous prior-year exposure | 666057 | AIH authorisations | 89256.0 | selection |
| brasil | Complete model variables | 666057 | AIH authorisations | 0.0 | selection |
| brasil | At least two eligible hospital years | 664508 | AIH authorisations | 1549.0 | selection |
| mexico | All records in consolidated source 2008-2024 | 44844719 | discharge records |  | source population |
| mexico | Discharge/source years 2015-2023 | 23004770 | discharge records | 21839949.0 | selection |
| mexico | Principal S06 in 2015-2023 | 171305 | discharge records | 22833465.0 | selection |
| mexico | After excluding documented age below 20 | 107792 | discharge records | 63513.0 | selection |
| mexico | Known age at least 20 | 107686 | discharge records | 106.0 | selection |
| mexico | Hospital identifier available | 107686 | discharge records | 0.0 | selection |
| mexico | Contiguous prior-year exposure | 93855 | discharge records | 13831.0 | selection |
| mexico | Complete model variables | 93790 | discharge records | 65.0 | selection |
| mexico | At least two eligible hospital years | 92853 | discharge records | 937.0 | selection |

Brazil groups repeated records of the same administrative AIH number. The raw
S06 records contain 943,383 distinct AIHs, 24 repeated-number groups comprising
90 rows and 66 continuation records. No groups changed hospital or admission
date; all 24 changed discharge date and five crossed processing years. The
grouping follows the documented authorisation rule and does not prove that an
AIH equals a unique clinical episode. One eligible authorisation has type 5 as
its retained first record. No records are removed merely because patient
profiles are equal. No missing AIH numbers or identical source rows excluding
source coordinates were observed in this extraction.

Volumes are counted before outcome/covariate exclusions. A lag is available
only for contiguous reporting years. Complete cases must contribute at least
two eligible years per hospital. These rules do not use target sample sizes.

## Brazil versus the frozen reference

Reapplying the historical age-unit-4-only rule to independently downloaded data
gives **754,674** authorisations and the original **663,921 observations in
2,078 hospitals**. The independently rebuilt compatibility cohort reproduces
the original conventional and fixed-effect slopes and paired covariance; the
full-precision bridge is in `results/reconstructed/brazil_bridge/`.

Two corrections are separated from that compatibility exercise:

* The scientific criterion is age at least 20. Official code 5 denotes
  centenarian ages, represented as 100 plus the recorded value. Restoring it adds
  **639** source-cohort authorisations and **587** longitudinal observations.
  The corrected longitudinal sample includes one additional hospital. Recorded
  ages are not silently capped; the additional Brazilian age-at-most-110
  sensitivity was documented before inspecting corrected model coefficients.
* Six repeated AIH authorisations had a character rather than numeric maximum
  of length of stay in the old aggregation. All six old values equal the
  lexicographic maximum, and all six corrected values equal the numeric maximum
  of the actual source fields. Their recorded stay sum changes from **46** to
  **169** days. Outcomes, adjustment variables and procedure codes otherwise
  agree under the same age rule. This is a computational correction, not a
  source-record exclusion.

With hospital, year, age, sex, diagnosis, outcome, stay and procedure compared
as multisets, **754,668** original occurrences match. Under the old age rule,
the only six unmatched occurrences on each side are the stay corrections.
The corrected cohort additionally contains the 639 restored ages. Frozen
Brazilian AIH identifiers were absent, so characteristic concordance is not
asserted to be person-level linkage. Historical raw-file hashes were not
retained; present-day source identity with the original download cannot be
proven from that absence, even though the core analytical fields reproduce.

## Mexico versus annual DGIS sources

| year | annual_all_records | consolidated_all_records | annual_adult_S06 | consolidated_adult_S06 | adult_s06_model_fields_shared | adult_s06_model_fields_annual_only | adult_s06_model_fields_consolidated_only |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2015 | 2970812 | 2970812 | 11290 | 11290 | 11290 | 0 | 0 |
| 2016 | 2953109 | 2954506 | 11772 | 11772 | 11772 | 0 | 0 |
| 2017 | 2729341 | 2729341 | 11666 | 11666 | 11666 | 0 | 0 |
| 2018 | 2623379 | 2623379 | 11935 | 11935 | 11935 | 0 | 0 |
| 2019 | 2629434 | 2629434 | 12548 | 12548 | 12548 | 0 | 0 |
| 2020 | 1937344 | 1937344 | 9281 | 9281 | 9281 | 0 | 0 |
| 2021 | 2088780 | 2088779 | 11078 | 11078 | 11078 | 0 | 0 |
| 2022 | 2479897 | 2479897 | 13880 | 13880 | 13880 | 0 | 0 |
| 2023 | 2591278 | 2591278 | 14236 | 14236 | 14236 | 0 | 0 |

All **107,686** eligible adults match between the independent annual releases
and the INSP consolidation as multisets of hospital, source year, age, model
sex category, principal diagnosis, in-hospital outcome and recorded stay.
This comparison is stronger than equality of counts but does not identify
unique people. In 2016, source-key domains differ and two annual candidate keys
are repeated; profile multisets are therefore used rather than invented key
equivalence. In other years the candidate hospital/source-ID keys are unique
and the shared fields can also be compared directly.

The raw releases are not identical. The consolidated 2016 file contains 1,397
more source records and four more S06 records than the current annual release;
the four additional S06 records do not meet adult eligibility. The 2021 annual
file has one additional source record outside principal S06. These observations
establish release differences; retained evidence does not determine why those
source versions differ.

Annual age code 3 means years through 2019, whereas code 5 means years from
2020. INSP harmonises these to code 3. Official year-specific `CATTIPOEDAD`
catalogues verified this transition. Motive code 6 is recoded to 7 in 193 S06
records in 2015, without changing death status; the 2016 motive distributions
also differ but model-field adult profiles agree. In 2020 and 2023, raw sex
recodings affect seven and ten S06 records respectively (four and five adults),
while their combined other/unknown model category agrees. The 2020 annual sex
catalogue has an inconsistent label for code 9; this was not used to infer
female sex against the consolidated dictionary and observed harmonisation.

## Mexico versus the frozen reference

The frozen cohort contains the broader sectorial population, including 68,954
IMSS-coded and 7,827 ISSSTE-coded records. It is not a frozen SSA-only cohort.
The reconstruction follows the requested SSA series and never forces the old
162,904-observation longitudinal count. All 700 reconstructed hospitals occur
in the old cohort; the old cohort additionally contains 478 hospitals. At those
700 shared hospitals, the old sectorial series contains 109,755 records versus
107,686 in the reconstructed SSA series. Hospital labels alone therefore do
not isolate the correct source population.

The complete seven-field multiset comparison matches **107,642** occurrences.
The **44** newly unmatched occurrences comprise **36** unknown-outcome profiles previously coded as survivors, **five** source-sex categories previously missing under binary normalisation, and **three** 2016 deaths recorded at age 130 that the old early-year upper-age limit of 120 excluded. The extreme ages are not asserted to be clinically valid; the age-at-most-110 sensitivity remains. The detailed aggregate and
candidate-profile evidence is in `reports/mexico_frozen_difference_diagnosis.json`
and `reports/mexico_profile_change_categories.json`. No source identifiers from
the different series are assumed equivalent. The remaining old-only records
reflect the broader population; their source scope is reported, not silently
discarded to obtain a preferred coefficient.

| country | sample | metric | old_value | new_value | difference |
| --- | --- | --- | --- | --- | --- |
| brasil | starting | records | 754674.0 | 755313.0 | 639.0 |
| brasil | starting | hospitals | 3209.0 | 3211.0 | 2.0 |
| brasil | starting | deaths | 83485.0 | 83628.0 | 143.0 |
| brasil | starting | known_outcome | 754674.0 | 755313.0 | 639.0 |
| brasil | starting | age_median | 47.0 | 47.0 | 0.0 |
| brasil | starting | los_sum | 5160179.0 | 5163352.0 | 3173.0 |
| brasil | starting | missing_sex | 0.0 | 0.0 | 0.0 |
| brasil | longitudinal | records | 663921.0 | 664508.0 | 587.0 |
| brasil | longitudinal | hospitals | 2078.0 | 2079.0 | 1.0 |
| brasil | longitudinal | deaths | 74152.0 | 74279.0 | 127.0 |
| brasil | longitudinal | known_outcome | 663921.0 | 664508.0 | 587.0 |
| brasil | longitudinal | age_median | 47.0 | 47.0 | 0.0 |
| brasil | longitudinal | los_sum | 4555229.0 | 4558043.0 | 2814.0 |
| brasil | longitudinal | missing_sex | 0.0 | 0.0 | 0.0 |
| mexico | starting | records | 187744.0 | 107686.0 | -80058.0 |
| mexico | starting | hospitals | 1178.0 | 700.0 | -478.0 |
| mexico | starting | deaths | 20813.0 | 12942.0 | -7871.0 |
| mexico | starting | known_outcome | 187700.0 | 107606.0 | -80094.0 |
| mexico | starting | age_median | 46.0 | 40.0 | -6.0 |
| mexico | starting | los_sum | 1330802.0 | 746636.0 | -584166.0 |
| mexico | starting | missing_sex | 5.0 | 0.0 | -5.0 |
| mexico | longitudinal | records | 162904.0 | 92853.0 | -70051.0 |
| mexico | longitudinal | hospitals | 842.0 | 437.0 | -405.0 |
| mexico | longitudinal | deaths | 18232.0 | 11352.0 | -6880.0 |
| mexico | longitudinal | known_outcome | 162904.0 | 92853.0 | -70051.0 |
| mexico | longitudinal | age_median | 46.0 | 40.0 | -6.0 |
| mexico | longitudinal | los_sum | 1135559.0 | 626304.0 | -509255.0 |
| mexico | longitudinal | missing_sex | 0.0 | 0.0 | 0.0 |

## Fields recovered and limitations that remain

Brazilian AIH numbers, clinical dates, secondary diagnosis slots, ICU daily units,
intermediate-care units, admission character and reimbursement were recovered
from their actual fields. Secondary slot 1 commonly contains external causes,
not comorbidity. Slots 2–9 are much sparser. These fields do not establish
admission timing, physiological severity or chart-validated comorbidities.
ICU units exceeding recorded stay remain an administrative inconsistency;
they are not forced into a clinically convenient value or added to risk models.

Brazil has **65,798** eligible authorisations with admission year different
from processing year. Mexico has **399** discharges dated one year before the
source-series year. Both original reporting-year axes are retained explicitly.
Observed hospital-code continuity cannot establish identity through mergers,
renumbering or changes of administrative participation. Two Mexican records
have occurrence-state code 37; the recorded category is retained and disclosed,
not geographically imputed.

Mexican referral-facility and intensive-care-hour fields were recovered, but
they do not supply linked transfer episodes or baseline severity. PROCED means
admission origin, not a surgical procedure. Separate annual procedure files are
not substituted for a validated linked surgical measure. The supplementary
workload measure for coded acute cranial procedures remains Brazil-specific.

No unique-person linkage, post-discharge mortality, disability, GCS, pupil
reactivity, CT classification or complete referral trajectory was created.
Administrative coverage differs between the two systems. Hospital clustering
does not remove residual dependence between hospitals in referral networks.
Successful source reconstruction addresses traceability; it does not resolve
these substantive validity limitations or establish a causal volume effect.

## Models, reproducibility and public materials

The pipeline preserves all 19 original linear specifications and adds the
documented Brazilian extreme-age check (20 specifications), six hospital
intercept logistic attempts and a separately labelled separation diagnostic, the contextual mean-centred logistic comparison,
paired covariance, targeted hospital deletions, coding diagnostics, workload,
and independent FWL estimates and covariance. `OLD_VS_NEW_RESULTS.csv` reports
full-precision comparisons after execution; values are never selected by
favourability. Figures and tables use the reconstructed outputs.

Stages 00–09 are described in `README.md`; dependency versions, source hashes,
checkpoint hashes, execution logs and numerical validation reports accompany
the implementation. Historical analyses are preserved internally. The public
supplement contains only Brazil/Mexico code, necessary documentation and
aggregate outputs; admission-level records are not included.
