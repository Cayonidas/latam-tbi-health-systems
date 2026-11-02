"""Post-hoc diagnostics; read POST_HOC_AUDIT_PLAN.md. Aggregate output only.
Run: python audit_processed_cohorts.py --data-root PATH --output PATH
PATH contains cohort_main_{brasil,mexico}_v280.parquet.
"""
from pathlib import Path
import argparse, os, sys, json, time, hashlib
os.environ.update(OPENBLAS_NUM_THREADS='2', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
LOCAL=Path(__file__).resolve().parents[2]/'auditoria_20261028/dependencias'
if LOCAL.exists(): sys.path.insert(0,str(LOCAL))
import numpy as np
import pandas as pd
from patsy import dmatrix
from scipy.stats import t

def sums(a,g,G):
    out=np.zeros((G,)+a.shape[1:]); np.add.at(out,g,a); return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data-root',type=Path,required=True); ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);start=time.perf_counter()
    tables={k:[] for k in ['column_inventory','cohort_by_year','analytic_characteristics','repeated_profiles','repeat_by_year','identifier_diagnostics','design_information','independent_estimates','cohort_flow']};checks={}; hashes={}
    for country in ['brasil','mexico']:
        path=args.data_root/f'cohort_main_{country}_v280.parquet';hashes[country]=hashlib.sha256(path.read_bytes()).hexdigest()
        d=pd.read_parquet(path);n=len(d)
        for col in d:
            tables['column_inventory'].append(dict(country=country,column=col,dtype=str(d[col].dtype),missing=int(d[col].isna().sum()),unique_nonmissing=int(d[col].nunique())))
        excess=d.duplicated();rep=d.duplicated(keep=False)
        rh=pd.util.hash_pandas_object(d.loc[rep],index=False);sizes=rh.value_counts()
        assert len(sizes)==len(d.loc[rep].drop_duplicates())
        assert int((sizes-1).sum())==int(excess.sum())
        original_rep=rep.copy();original_excess=excess.copy()
        for col in ['hospital_id','sex','trauma_subtype','hospital_region']: d[col]=d[col].astype(object)
        for col in ['year','age','death_in_hospital','log_lag_volume','lag_volume','hospital_volume_year','los_days']: d[col]=pd.to_numeric(d[col],errors='raise').astype(float)
        fields=['hospital_id','sex','trauma_subtype','age','year','death_in_hospital','log_lag_volume']
        cc=d.dropna(subset=fields);ny=cc.groupby('hospital_id').year.nunique();e=cc[cc.hospital_id.isin(ny[ny.ge(2)].index)].copy()
        checks[country+'_age20plus']=bool(d.age.ge(20).all());checks[country+'_principal_S06']=bool(d.dx_main.astype(str).str.startswith('S06').all())
        checks[country+'_binary_known_outcome']=bool(d.death_in_hospital.dropna().isin([0,1]).all())
        counts=d.groupby(['hospital_id','year'],dropna=False).size().rename('recount').reset_index()
        prev=counts.rename(columns={'recount':'recount_previous'}).assign(year=lambda z:z.year+1)
        v=d[['hospital_id','year','hospital_volume_year','lag_volume','log_lag_volume']].merge(counts,on=['hospital_id','year'],how='left',validate='many_to_one').merge(prev,on=['hospital_id','year'],how='left',validate='many_to_one')
        checks[country+'_annual_volume']=bool(np.allclose(v.hospital_volume_year,v.recount,equal_nan=True))
        checks[country+'_contiguous_lag']=bool(np.allclose(v.lag_volume,v.recount_previous,equal_nan=True))
        checks[country+'_log_lag']=bool(np.allclose(v.log_lag_volume,np.log1p(v.recount_previous),equal_nan=True))
        checks[country+'_principal_n']=len(e)=={'brasil':663921,'mexico':162904}[country]
        tables['cohort_flow'].append(dict(country=country,starting=n,missing_lag=int(d.log_lag_volume.isna().sum()),missing_outcome_after_lag=int((d.log_lag_volume.notna()&d.death_in_hospital.isna()).sum()),complete_before_hospital_requirement=len(cc),removed_single_eligible_year=len(cc)-len(e),analytic_n=len(e),analytic_hospitals=e.hospital_id.nunique()))
        for sample,dd in [('starting',d),('analytic',e)]:
            code=dd.dx_main.astype(str).str.replace('.','',regex=False).str[:4]
            tables['analytic_characteristics'].append(dict(country=country,sample=sample,n=len(dd),deaths=int(dd.death_in_hospital.sum()),known_outcome=int(dd.death_in_hospital.notna().sum()),hospitals=dd.hospital_id.nunique(),hospital_years=len(dd[['hospital_id','year']].drop_duplicates()),age_median=dd.age.median(),age_q1=dd.age.quantile(.25),age_q3=dd.age.quantile(.75),female=int(dd.sex.eq('Female').sum()),age70=int(dd.age.ge(70).sum()),specified=int(code.isin([f'S06{i}' for i in range(1,8)]).sum()),S069=int(code.eq('S069').sum())))
            for year,z in dd.groupby('year'):
                tables['cohort_by_year'].append(dict(country=country,sample=sample,year=int(year),n=len(z),deaths=int(z.death_in_hospital.sum()),known_outcome=int(z.death_in_hospital.notna().sum()),hospitals=z.hospital_id.nunique()))
        repeated_per_hospital=d.loc[rep].groupby('hospital_id').size().sort_values(ascending=False)
        tables['repeated_profiles'].append(dict(country=country,columns_compared=len(d.columns),excess_after_first=int(excess.sum()),records_in_repeated_groups=int(rep.sum()),distinct_repeated_profiles=len(sizes),max_multiplicity=int(sizes.max()) if len(sizes) else 0,excess_pct=100*excess.mean(),repeated_records_pct=100*rep.mean(),analytic_excess=int(excess.loc[e.index].sum()),analytic_repeated_records=int(rep.loc[e.index].sum()),deaths_in_repeated_groups=int(d.loc[rep,'death_in_hospital'].sum()),hospitals_with_repeats=len(repeated_per_hospital),largest_hospital_repeat_records=int(repeated_per_hospital.iloc[0]) if len(repeated_per_hospital) else 0,top5_repeat_records=int(repeated_per_hospital.iloc[:5].sum())))
        for year,z in d.groupby('year'):
            tables['repeat_by_year'].append(dict(country=country,year=int(year),n=len(z),excess=int(excess.loc[z.index].sum()),records_in_repeated_groups=int(rep.loc[z.index].sum()),deaths_in_repeated_groups=int(z.loc[rep.loc[z.index],'death_in_hospital'].sum())))
        hy=d[['hospital_id','year']].drop_duplicates();yr=hy.groupby('hospital_id').year.agg(['min','max','nunique'])
        region=d.groupby('hospital_id').hospital_region.nunique()
        tables['identifier_diagnostics'].append(dict(country=country,missing_hospital_id=int(d.hospital_id.isna().sum()),hospitals=d.hospital_id.nunique(),hospital_ids_multiple_regions=int(region.gt(1).sum()),hospital_ids_with_internal_year_gaps=int((yr['max']-yr['min']+1>yr['nunique']).sum()),all9_years_hospitals=int(yr['nunique'].eq(9).sum()),original_record_id_missing=int(d.record_id.isna().sum()) if 'record_id' in d else n,source_file_missing=int(d.source_file.isna().sum()) if 'source_file' in d else n))
        # Independent FWL implementation of the exact primary specification.
        g,ids=pd.factorize(e.hospital_id);G=len(ids);N=len(e);ng=np.bincount(g)
        X=dmatrix('log_lag_volume + bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)',e,return_type='dataframe');p=X.shape[1];pos=list(X.columns).index('log_lag_volume')
        A=X.to_numpy();y=e.death_in_hospital.to_numpy();W=A[:,1:];W=W-sums(W,g,G)[g]/ng[g,None];yw=y-sums(y,g,G)[g]/ng[g]
        qpos=pos-1;other=np.delete(W,qpos,axis=1);v=W[:,qpos]
        z=v-other@np.linalg.lstsq(other,v,rcond=None)[0]
        denom=z@z;bf=(z@yw)/denom;coef=np.linalg.lstsq(W,yw,rcond=None)[0];err=yw-W@coef;fitted=y-err
        score_f=sums(z*err,g,G)/denom;cf=G/(G-1)*(N-1)/(N-(G+p-1));se_f=np.sqrt(cf*np.sum(score_f**2))
        bc=np.linalg.lstsq(A,y,rcond=None)[0];ec=y-A@bc
        zc=A[:,pos]-np.delete(A,pos,axis=1)@np.linalg.lstsq(np.delete(A,pos,axis=1),A[:,pos],rcond=None)[0]
        score_c=sums(zc*ec,g,G)/(zc@zc);cconv=G/(G-1)*(N-1)/(N-p)
        sec=np.sqrt(cconv*np.sum(score_c**2));sed=np.sqrt(cf*np.sum((score_c-score_f)**2));unit=100*np.log(2);crit=t.ppf(.975,G-1)
        for model,b,se in [('FE',bf,se_f),('Conventional',bc[pos],sec),('Paired difference',bc[pos]-bf,sed)]:
            tables['independent_estimates'].append(dict(country=country,model=model,n=N,hospitals=G,estimate_pp=unit*b,low_pp=unit*(b-crit*se),high_pp=unit*(b+crit*se),se_pp=unit*se))
        info=sums(z*z,g,G)/denom;info=np.sort(info)[::-1];raw_ranges=e.groupby('hospital_id').log_lag_volume.agg(lambda s:s.max()-s.min())
        tables['design_information'].append(dict(country=country,n=N,hospitals=G,design_columns=p,FE_parameter_count=G+p-1,CR1_FE=cf,CR1_conventional=cconv,residualized_exposure_sumsquares=denom,max_information_pct=100*info[0],top5_information_pct=100*info[:5].sum(),inverse_information_concentration=1/np.sum(info**2),hospitals_zero_raw_within_variation=int(raw_ranges.lt(1e-12).sum()),below_zero=int((fitted<0).sum()),above_one=int((fitted>1).sum()),fitted_min=float(fitted.min()),fitted_max=float(fitted.max()),log_lag_min=float(e.log_lag_volume.min()),log_lag_max=float(e.log_lag_volume.max())))
        checks[country+'_FWL_matches_joint_least_squares']=abs(bf-coef[qpos])<1e-10
        checks[country+'_FWL_residual_orthogonality']=np.max(np.abs(other.T@z))<1e-6
        print(country,tables['repeated_profiles'][-1],tables['design_information'][-1],flush=True)
    for name,rows in tables.items(): pd.DataFrame(rows).to_csv(args.output/(name+'.csv'),index=False)
    checks={k:bool(v) for k,v in checks.items()}
    report=dict(checks=checks,all_passed=all(checks.values()),seconds=time.perf_counter()-start,input_hashes=hashes,plan_sha256=hashlib.sha256(Path(__file__).with_name('POST_HOC_AUDIT_PLAN.md').read_bytes()).hexdigest())
    (args.output/'processed_audit_validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8');assert report['all_passed']; print('PASS',report,flush=True)
if __name__=='__main__': main()
