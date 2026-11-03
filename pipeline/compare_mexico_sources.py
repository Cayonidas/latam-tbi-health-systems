"""Audit annual SSA releases against the independent INSP consolidation.

Only aggregate comparisons leave this script. Matching profiles does not prove
patient identity. Source IDs are assessed separately and never assumed equal.
"""
from pathlib import Path
import argparse,json
import numpy as np,pandas as pd
from cohort_rules import numeric,diagnosis,mexico_age,mexico_annual_age
from raw_io import write_json

def harmonise(d,year,annual):
    out=pd.DataFrame(index=d.index)
    out['hospital']=d.clues.astype('string').str.strip().str.upper()
    out['source_id']=numeric(d.id)
    out['age_raw']=numeric(d.edad)
    unit=numeric(d.cveedad)
    out['unit']=unit.map({2:0,3:1,4:2,5:3,9:9}) if annual and year>=2020 else unit
    out['age']=mexico_annual_age(d.edad,d.cveedad,year) if annual else mexico_age(d.edad,d.cveedad)
    for source,name in [('sexo','sex'),('motegre','reason'),('dias_esta','stay')]:out[name]=numeric(d[source])
    out['sex_model']=out.sex.map({0:3,1:1,2:2,3:3,8:3,9:3}).astype(float)
    out['death']=out.reason.eq(5).astype(float).where(out.reason.isin([1,2,3,4,5,6,7]))
    out['diagnosis']=diagnosis(d.afecprin)
    return out

def multiset(a,b,fields):
    def canonical(d):
        z=d[fields].copy()
        for c in fields:
            if c not in ['hospital','diagnosis']:z[c]=pd.to_numeric(z[c],errors='raise').astype(float)
        return z.astype('string').fillna('<MISSING>')
    a=canonical(a).value_counts(dropna=False).rename('annual')
    b=canonical(b).value_counts(dropna=False).rename('consolidated')
    z=pd.concat([a,b],axis=1).fillna(0)
    return dict(shared=int(np.minimum(z.annual,z.consolidated).sum()),annual_only=int((z.annual-z.consolidated).clip(lower=0).sum()),consolidated_only=int((z.consolidated-z.annual).clip(lower=0).sum()))

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);a=p.parse_args();root=a.root
    status=json.loads((root/'logs/mexico_harmonisation_status.json').read_text())
    if not status.get('complete'):raise RuntimeError('Complete verified INSP source required')
    d=pd.concat([pd.read_parquet(root/'checkpoints/mexico_s06'/name) for name in status['checkpoint_hashes']],ignore_index=True)
    years=numeric(d.anio_base);rows=[];details=[]
    fields=['hospital','age_raw','unit','sex','reason','stay','diagnosis']
    for year in range(2015,2024):
        source=root/'checkpoints/mexico_dgis_s06'/f'dgis_{year}.parquet'
        meta=json.loads(source.with_suffix('.json').read_text())
        if not meta.get('complete'):raise RuntimeError('Annual source not fully read')
        annual=harmonise(pd.read_parquet(source),year,True);consolidated=harmonise(d[years.eq(year)].copy(),year,False)
        row=dict(year=year,annual_all_records=meta['source_records'],consolidated_all_records=status['records_by_year'][str(year)],annual_S06=len(annual),consolidated_S06=len(consolidated),annual_adult_S06=int(annual.age.ge(20).sum()),consolidated_adult_S06=int(consolidated.age.ge(20).sum()))
        for sample,x,y in [('all_s06',annual,consolidated),('adult_s06',annual[annual.age.ge(20)],consolidated[consolidated.age.ge(20)])]:
            for key,value in multiset(x,y,fields).items():row[sample+'_'+key]=value
            analysis_fields=['hospital','age','sex_model','death','diagnosis','stay']
            for key,value in multiset(x,y,analysis_fields).items():row[sample+'_model_fields_'+key]=value
        keys=['hospital','source_id'];row['annual_duplicate_keys']=int(annual.duplicated(keys).sum());row['consolidated_duplicate_keys']=int(consolidated.duplicated(keys).sum())
        x=annual[~annual.duplicated(keys,keep=False)];y=consolidated[~consolidated.duplicated(keys,keep=False)]
        j=x.merge(y,on=keys,how='inner',suffixes=('_annual','_consolidated'),validate='one_to_one');row['shared_unambiguous_candidate_source_keys']=len(j)
        for col in fields[1:]:
            v=j[col+'_annual'];w=j[col+'_consolidated'];different=~(v.eq(w)|(v.isna()&w.isna()))
            row['key_matched_differences_'+col]=int(different.sum())
            if different.any():
                counts=j.loc[different].groupby([col+'_annual',col+'_consolidated'],dropna=False).size()
                for (old,new),n in counts.items():details.append(dict(year=year,source='matched_source_keys',variable=col,value=f'{old} -> {new}',n=int(n)))
        for label,frame in [('annual',annual),('consolidated',consolidated)]:
            for unit,n in frame.unit.value_counts(dropna=False).items():details.append(dict(year=year,source=label,variable='harmonised_age_unit',value=str(unit),n=int(n)))
        rows.append(row);print(json.dumps(row),flush=True)
    pd.DataFrame(rows).to_csv(root/'reports/DGIS_VS_RIISP_COMPARISON.csv',index=False)
    pd.DataFrame(details).to_csv(root/'reports/DGIS_VS_RIISP_AGE_CODES.csv',index=False)
    write_json(root/'reports/dgis_vs_riisp_comparison.json',dict(all_years_processed=True,comparison_fields=fields,rows=rows,interpretation='Profile multisets and candidate source keys; not unique-patient linkage'))

if __name__=='__main__':main()
