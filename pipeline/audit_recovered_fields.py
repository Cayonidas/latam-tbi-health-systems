"""Aggregate audit of restored source fields; no new clinical adjustment."""
from pathlib import Path
import argparse,json
import numpy as np,pandas as pd
from raw_io import write_json
from cohort_rules import numeric

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);a=p.parse_args();rows=[];dates=[];institutions=[]
    for country in ['brasil','mexico']:
        d=pd.read_parquet(a.root/'checkpoints/cohorts'/f'cohort_{country}.parquet')
        def metric(name,value):rows.append(dict(country=country,metric=name,value=float(value)))
        metric('source_cohort_records',len(d));metric('age_above_110',d.age.gt(110).sum());metric('maximum_age',d.age.max())
        metric('missing_stay',d.los_days.isna().sum());metric('negative_stay',d.los_days.lt(0).sum());metric('zero_stay',d.los_days.eq(0).sum());metric('stay_above_90',d.los_days.gt(90).sum());metric('maximum_stay',d.los_days.max())
        region=numeric(d.hospital_region);metric('out_of_range_hospital_region',(~region.isin(range(1,33))).sum() if country=='mexico' else 0)
        if country=='brasil':
            year=numeric(d.admission_date.str[:4]);secondary=pd.DataFrame({f'diagsec{i}':d[f'diagsec{i}'].astype('string').str.match(r'^[A-Z][0-9]{2}',na=False) for i in range(1,10)})
            metric('at_least_one_recorded_secondary_ICD',secondary.any(axis=1).sum())
            metric('at_least_one_secondary_ICD_in_slots_2_to_9',secondary.iloc[:,1:].any(axis=1).sum())
            metric('secondary_slot_1_external_cause_code',d.diagsec1.astype('string').str.match(r'^[VWXY][0-9]{2}',na=False).sum())
            metric('legacy_DIAG_SECUN_valid_ICD',d.diag_secun.astype('string').str.match(r'^[A-Z][0-9]{2}',na=False).sum())
            for col in ['icu_days_official','intermediate_care_days_official','cost_local_currency_official']:
                metric(col+'_nonmissing',d[col].notna().sum());metric(col+'_positive',d[col].gt(0).sum());metric(col+'_max',d[col].max())
            metric('recorded_ICU_days_above_recorded_stay',d.icu_days_official.gt(d.los_days).sum())
            metric('eligible_type_5_authorisations',d.aih_type.eq('5').sum())
            date_label='clinical admission year minus processing year'
        else:
            year=numeric(d.discharge_date.str.extract(r'(?:^|/)([12][0-9]{3})(?:$|[ T-])',expand=False));date_label='clinical discharge year minus source-series year'
            metric('recorded_transfer_out',d.discharge_reason.eq(4).sum())
            for col in ['intensive_care_hours','intermediate_care_hours']:
                metric(col+'_nonmissing',d[col].notna().sum());metric(col+'_positive',d[col].gt(0).sum());metric(col+'_max',d[col].max())
            for col in ['referring_hospital','referred_hospital']:
                metric(col+'_valid_CLUES_format',d[col].astype('string').str.fullmatch(r'[A-Z]{5}[0-9]{6}',na=False).sum())
            for (year_value,inst),g in d.groupby(['year','institution_code']):institutions.append(dict(year=int(year_value),institution_code=inst,n=len(g),hospitals=g.hospital_id.nunique()))
        for delta,n in (year-d.year).value_counts(dropna=False).items():dates.append(dict(country=country,comparison=date_label,year_difference=str(delta),n=int(n)))
    pd.DataFrame(rows).to_csv(a.root/'reports/recovered_source_fields_audit.csv',index=False)
    pd.DataFrame(dates).to_csv(a.root/'reports/clinical_vs_source_year.csv',index=False)
    pd.DataFrame(institutions).to_csv(a.root/'reports/mexico_institution_codes_by_year.csv',index=False)
    print(json.dumps(rows),flush=True)
if __name__=='__main__':main()
