from pathlib import Path
import os,sys,json,gc
OUT=Path(__file__).resolve().parent;AUD=OUT.parent/'auditoria_20261028';sys.path.insert(0,str(AUD/'dependencias'))
os.environ['OPENBLAS_NUM_THREADS']='2';os.environ['OMP_NUM_THREADS']='2'
import numpy as np,pandas as pd
from scipy.special import expit
from scipy.stats import t
from patsy import dmatrix
import statsmodels.api as sm
BASE='bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)'
def grouped_sum(a,g,G):
    if a.ndim==1:return np.bincount(g,weights=a,minlength=G)
    return np.column_stack([np.bincount(g,weights=a[:,j],minlength=G) for j in range(a.shape[1])])
def fit(x,y,g):
    G=g.max()+1;n,p=x.shape;counts=np.bincount(g);events=np.bincount(g,weights=y)
    assert ((events>0)&(events<counts)).all()
    alpha=np.log(events/(counts-events));beta=np.zeros(p)
    def ll(a,b):
        eta=a[g]+x@b
        return float(np.sum(y*eta-np.logaddexp(0,eta)))
    likelihood=ll(alpha,beta)
    for iteration in range(100):
        eta=alpha[g]+x@beta;mu=expit(eta);w=np.maximum(mu*(1-mu),1e-12);den=grouped_sum(w,g,G)
        mx=grouped_sum(x*w[:,None],g,G)/den[:,None]
        # Newton increments; no high-dimensional dummy matrix is materialised.
        v=y-mu;mv=grouped_sum(v,g,G)/den
        xx=x-mx[g];info=xx.T@(w[:,None]*xx)
        assert np.linalg.matrix_rank(info)==p
        db=np.linalg.solve(info,xx.T@v);da=mv-mx@db
        step=1.
        while step>1e-7:
            nl=ll(alpha+step*da,beta+step*db)
            if nl>=likelihood-1e-8:break
            step*=.5
        if step<=1e-7:raise RuntimeError('Newton line search failed')
        alpha+=step*da;beta+=step*db;likelihood=nl
        if max(np.max(np.abs(step*da)),np.max(np.abs(step*db)))<1e-8:break
    else:raise RuntimeError('Fixed-effect logistic model did not converge')
    mu=expit(alpha[g]+x@beta);w=np.maximum(mu*(1-mu),1e-12);mx=grouped_sum(x*w[:,None],g,G)/grouped_sum(w,g,G)[:,None];xx=x-mx[g]
    bread=np.linalg.inv(xx.T@(w[:,None]*xx));scores=grouped_sum(xx*(y-mu)[:,None],g,G)
    cov=bread@(scores.T@scores)@bread*(G/(G-1))*((n-1)/(n-p-G))
    assert np.max(np.abs(grouped_sum(y-mu,g,G)))<1e-5
    return beta,cov,{'iterations':iteration+1,'log_likelihood':likelihood,'hospital_score_max_abs':float(np.max(np.abs(grouped_sum(y-mu,g,G)))),'min_predicted':float(mu.min()),'max_predicted':float(mu.max()),'columns':p,'hospitals':int(G),'n':int(n)}
def validate():
    rng=np.random.default_rng(2026);g=np.repeat(np.arange(12),50);x=rng.normal(size=(len(g),3));a=rng.normal(0,.5,12);y=rng.binomial(1,expit(a[g]+x@np.array([.3,-.2,.1])))
    b,c,diag=fit(x,y,g);dummy=np.eye(12)[g];full=np.column_stack([x,dummy]);m=sm.GLM(y,full,family=sm.families.Binomial()).fit(cov_type='cluster',cov_kwds={'groups':g},maxiter=100)
    assert np.allclose(b,m.params[:3],atol=1e-8)
    assert np.allclose(c,m.cov_params()[:3,:3],atol=1e-8)
    print('Validated logistic coefficients and clustered covariance against full indicator GLM',flush=True)
    return {'dummy_glm_beta_agreement':True,'dummy_glm_cluster_covariance_agreement':True}
if __name__=='__main__':
    validation=validate();results=[];diagnostics=[]
    if '--test' in sys.argv:sys.exit(0)
    for country in ['mexico','brasil']:
        d=pd.read_parquet(AUD/'base_final'/'analysis_v284_final'/'01_data'/f'cohort_main_{country}_v280.parquet')
        for c in ['sex','hospital_id','trauma_subtype']:d[c]=d[c].astype(object)
        for c in ['age','year','death_in_hospital','log_lag_volume']:d[c]=pd.to_numeric(d[c],errors='coerce').astype(float)
        d=d.dropna(subset=['sex','hospital_id','trauma_subtype','age','year','death_in_hospital','log_lag_volume'])
        ny=d.groupby('hospital_id').year.nunique();d=d[d.hospital_id.isin(ny[ny.ge(2)].index)].copy();before=len(d);gh=d.hospital_id.nunique()
        stats=d.groupby('hospital_id').death_in_hospital.agg(['sum','size']);keep=stats[(stats['sum']>0)&(stats['sum']<stats['size'])].index
        d=d[d.hospital_id.isin(keep)].copy();g=pd.factorize(d.hospital_id)[0]
        xx=dmatrix('log_lag_volume + '+BASE,d,return_type='dataframe').drop(columns='Intercept');idx=list(xx.columns).index('log_lag_volume');x=xx.to_numpy();y=d.death_in_hospital.to_numpy()
        b,c,diag=fit(x,y,g);se=np.sqrt(c[idx,idx]);unit=np.log(2);crit=t.ppf(.975,g.max());diag.update({'country':country,'excluded_admissions_no_outcome_variation':before-len(d),'excluded_hospitals_no_outcome_variation':gh-len(keep)})
        # On the identical informative-hospital sample, isolate functional-form selection.
        # Year indicators already occur in X; remove hospital means alone.
        counts=np.bincount(g);means=grouped_sum(x,g,len(counts))/counts[:,None];xc=x-means[g];yc=y-(grouped_sum(y,g,len(counts))/counts)[g]
        gram=xc.T@xc;assert np.linalg.matrix_rank(gram)==x.shape[1];bread=np.linalg.inv(gram);bl=bread@(xc.T@yc);err=yc-xc@bl;sc=grouped_sum(xc*err[:,None],g,len(counts));cl=bread@(sc.T@sc)@bread*(len(counts)/(len(counts)-1))*((len(d)-1)/(len(d)-x.shape[1]-len(counts)))
        sl=np.sqrt(cl[idx,idx]);scale=100*unit
        r={'country':country,'n':len(d),'hospitals':len(counts),'excluded_n':before-len(d),'excluded_hospitals':gh-len(counts),'logistic_OR':float(np.exp(unit*b[idx])),'OR_low':float(np.exp(unit*(b[idx]-crit*se))),'OR_high':float(np.exp(unit*(b[idx]+crit*se))),'LPM_same_sample_pp':float(scale*bl[idx]),'LPM_low':float(scale*(bl[idx]-crit*sl)),'LPM_high':float(scale*(bl[idx]+crit*sl))}
        results.append(r);diagnostics.append(diag);pd.DataFrame(results).to_csv(OUT/'resultados'/'logistica_FE.csv',index=False,encoding='utf-8-sig');(OUT/'resultados'/'logistica_FE_diagnosticos.json').write_text(json.dumps({'validation':validation,'fits':diagnostics},indent=2),encoding='utf-8');print(r,flush=True);del d,x,xx,xc,gram;gc.collect()
