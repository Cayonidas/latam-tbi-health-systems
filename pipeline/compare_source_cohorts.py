"""Compare independently rebuilt records with frozen reference Parquets.

Profiles are compared as multisets, not asserted to be unique patient identities.
Only aggregate summaries are written for public sharing.
"""
from pathlib import Path
import argparse,json
import numpy as np,pandas as pd
from cohort_rules import exposure,eligible
from raw_io import sha256,write_json

NUMERIC=['year','age','death_in_hospital','los_days','hospital_volume_year','lag_volume','log_lag_volume']
TEXT=['hospital_id','sex','dx_main','trauma_subtype','hospital_region','procedure_code_norm','procedure_group']

def standard(d):
    d=d.copy()
    for col in NUMERIC:
        if col in d:d[col]=pd.to_numeric(d[col],errors='coerce').astype(float)
    for col in TEXT:
        if col in d:d[col]=d[col].astype('string').str.strip()
    return d

def canonical(d,fields):
    result=pd.DataFrame(index=d.index)
    for c in fields:
        if c in NUMERIC:result[c]=pd.to_numeric(d[c],errors='coerce').astype(float).round(10).astype('string').fillna('<MISSING>')
        else:result[c]=d[c].astype('string').str.strip().fillna('<MISSING>')
    return result

def compare_profiles(old,new,fields):
    a=canonical(old,fields).value_counts(dropna=False).rename('old')
    b=canonical(new,fields).value_counts(dropna=False).rename('new')
    joined=pd.concat([a,b],axis=1).fillna(0)
    shared=np.minimum(joined.old,joined.new).sum()
    return dict(fields=fields,matched_profile_occurrences=int(shared),old_only_occurrences=int(len(old)-shared),new_only_occurrences=int(len(new)-shared),
                distinct_shared_profiles=int((joined.old.gt(0)&joined.new.gt(0)).sum()),
                old_repeated_profile_excess=int((a-1).clip(lower=0).sum()),new_repeated_profile_excess=int((b-1).clip(lower=0).sum()),
                interpretation='Multiset concordance of recorded characteristics, not proof of identical patients or episodes')

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--reference-root',type=Path);a=p.parse_args()
    reference=a.reference_root or a.root/'reference';rows=[];marginals=[];profile={};identifiers=[]
    def row(c,sample,year,metric,old,new,origin):
        rows.append(dict(country=c,sample=sample,year=year,metric=metric,old_value=old,new_value=new,difference=new-old if pd.notna(old) and pd.notna(new) else np.nan,interpretation=origin))
    for c in ['brasil','mexico']:
        old=standard(pd.read_parquet(reference/f'cohort_main_{c}_v280.parquet'))
        new=standard(pd.read_parquet(a.root/'checkpoints/cohorts'/f'cohort_{c}.parquet'))
        origin='Independent raw-source reconstruction; source-release identity with original extraction unverified' if c=='brasil' else 'Original sectorial cohort vs independently downloaded SSA series; distinct source populations'
        for sample,od,nd in [('starting',old,new),('longitudinal',eligible(old),eligible(new))]:
            for year in ['all',*range(2015,2024)]:
                x=od if year=='all' else od[od.year.eq(year)];y=nd if year=='all' else nd[nd.year.eq(year)]
                if not len(x) and not len(y):continue
                metrics={'records':lambda z:len(z),'hospitals':lambda z:z.hospital_id.nunique(),'deaths':lambda z:z.death_in_hospital.sum(),
                         'known_outcome':lambda z:z.death_in_hospital.notna().sum(),'age_median':lambda z:z.age.median(),
                         'age_q1':lambda z:z.age.quantile(.25),'age_q3':lambda z:z.age.quantile(.75),'female':lambda z:z.sex.eq('Female').sum(),
                         'mortality_known_outcome':lambda z:z.death_in_hospital.mean(),'hospital_years':lambda z:len(z[['hospital_id','year']].drop_duplicates()),
                         'los_sum':lambda z:z.los_days.sum(),'los_max':lambda z:z.los_days.max(),
                         'lag_mean':lambda z:z.lag_volume.mean(),'log_lag_mean':lambda z:z.log_lag_volume.mean()}
                for metric,f in metrics.items():row(c,sample,year,metric,float(f(x)),float(f(y)),origin)
            for col in NUMERIC+TEXT:
                if col not in od or col not in nd:continue
                row(c,sample,'all','missing_'+col,int(od[col].isna().sum()),int(nd[col].isna().sum()),origin)
        fields=['hospital_id','year','age','sex','dx_main','death_in_hospital','los_days']
        profile[c]=compare_profiles(old,new,fields)
        if c=='brasil':
            profile[c+'_with_procedure']=compare_profiles(old,new,fields+['procedure_code_norm'])
            legacy=pd.read_parquet(a.root/'checkpoints/brazil_legacy_rule_reconstruction.parquet');legacy,_=exposure(legacy)
            profile[c+'_same_legacy_age_rule']=compare_profiles(old,standard(legacy),fields+['procedure_code_norm'])
            row(c,'starting','all','legacy_rule_records',len(old),len(legacy),'Original code age-unit rule reconstructed independently; corrected centenarians excluded only in this benchmark')
        else:
            for version,d in [('old',old),('new',new)]:
                inst=d.hospital_id.str.replace('mexico:','',regex=False).str[2:5]
                for category,n in inst.value_counts(dropna=False).items():marginals.append(dict(country=c,version=version,variable='institution_code_from_CLUES',category=str(category),n=int(n)))
        for version,d in [('old',old),('new',new)]:
            for col in ['year','age','sex','trauma_subtype','hospital_region','procedure_group']:
                if col not in d:continue
                for category,n in d[col].value_counts(dropna=False).items():marginals.append(dict(country=c,version=version,variable=col,category=str(category),n=int(n)))
        oldh=set(old.hospital_id.dropna());newh=set(new.hospital_id.dropna())
        identifiers.append(dict(country=c,shared_hospitals=len(oldh&newh),old_only_hospitals=len(oldh-newh),new_only_hospitals=len(newh-oldh),
            old_record_id_missing=int(old.record_id.isna().sum()),new_record_id_missing=int(new.record_id.isna().sum()),
            old_record_id_repetitions=int(old.record_id.dropna().duplicated().sum()),new_record_id_repetitions=int(new.record_id.dropna().duplicated().sum()),
            row_linkage='Unavailable: old Brazilian AIH absent; Mexican source keys from different series not assumed equivalent'))
    pd.DataFrame(rows).to_csv(a.root/'SOURCE_TO_COHORT_COMPARISON.csv',index=False)
    pd.DataFrame(marginals).to_csv(a.root/'reports/source_comparison_marginals.csv',index=False)
    pd.DataFrame(identifiers).to_csv(a.root/'reports/source_identifier_comparison.csv',index=False)
    write_json(a.root/'reports/source_profile_multiset_comparison.json',profile)
    print(json.dumps(profile),flush=True)
if __name__=='__main__':main()
