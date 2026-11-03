"""Retain the earlier mean-centred logistic comparison on reconstructed data.

This is a contextual specification, not a hospital fixed-effect model.
"""
from settings import DATA,RESULTS
import numpy as np,pandas as pd,statsmodels.api as sm,json,gc
from patsy import dmatrix

def main():
    out=RESULTS/'contextual';out.mkdir(exist_ok=True);rows=[];diagnostics=[]
    for country in ['brasil','mexico']:
        d=pd.read_parquet(DATA/f'cohort_{country}.parquet')
        for col in ['hospital_id','hospital_region','sex','trauma_subtype']:d[col]=d[col].astype(object)
        d=d.dropna(subset=['death_in_hospital','log_lag_volume','age','sex','trauma_subtype','year','hospital_id'])
        hy=d[['hospital_id','year','log_lag_volume']].drop_duplicates(['hospital_id','year'])
        h=hy.groupby('hospital_id').agg(mean_log_volume=('log_lag_volume','mean'),years=('year','nunique'))
        h=h[h.years.ge(2)]
        d=d.merge(h,on='hospital_id',how='inner',validate='many_to_one').dropna(subset=['hospital_region'])
        d['within_log_volume']=d.log_lag_volume-d.mean_log_volume
        x=dmatrix('mean_log_volume + within_log_volume + bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year) + C(hospital_region)',d,return_type='dataframe')
        fit=sm.GLM(d.death_in_hospital.to_numpy(),x,family=sm.families.Binomial()).fit(maxiter=100,cov_type='cluster',cov_kwds={'groups':d.hospital_id.to_numpy()})
        if not fit.converged:raise RuntimeError('Mean-centred GLM did not converge')
        for term in ['mean_log_volume','within_log_volume']:
            b=float(fit.params[term])*np.log(2);se=float(fit.bse[term])*np.log(2)
            rows.append(dict(country=country,component=term,OR=np.exp(b),low=np.exp(b-1.96*se),high=np.exp(b+1.96*se),n=len(d),hospitals=d.hospital_id.nunique(),log_OR=b,se_log_OR=se))
        diagnostics.append(dict(country=country,converged=bool(fit.converged),design_columns=x.shape[1],rank=int(np.linalg.matrix_rank(x.to_numpy().T@x.to_numpy()))))
        pd.DataFrame(rows).to_csv(out/'mean_centered_logistic.csv',index=False)
        (out/'mean_centered_diagnostics.json').write_text(json.dumps(diagnostics,indent=2),encoding='utf-8')
        print(country,rows[-2:],flush=True);del d,x,fit;gc.collect()
if __name__=='__main__':main()
