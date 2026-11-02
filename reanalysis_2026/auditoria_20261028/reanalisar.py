"""Independent, post hoc audit. Originals remain unchanged; no causal estimates."""
from pathlib import Path
import sys, os, json, gc, time
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'dependencias'))
os.environ['OPENBLAS_NUM_THREADS']='2'; os.environ['OMP_NUM_THREADS']='2'
os.environ['MPLCONFIGDIR']=str(OUT/'cache_matplotlib')
import numpy as np
import pandas as pd
import scipy.stats as st
import statsmodels.api as sm
from patsy import dmatrix, build_design_matrices
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
DATA=OUT/'base_final'/'analysis_v284_final'/'01_data'
RES=OUT/'reanalise'; RES.mkdir(exist_ok=True)
results=[]; diagnostics=[]
BASE='bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype)'
def read(country):
    d=pd.read_parquet(DATA/f'cohort_main_{country}_v280.parquet')
    for c in ['sex','trauma_subtype','hospital_id','hospital_region','country']:
        d[c]=d[c].astype(object)
    for c in ['age','year','death_in_hospital','lag_volume','log_lag_volume','lag_volume_z_country_year']:
        d[c]=pd.to_numeric(d[c],errors='coerce').astype(float)
    return d
def save():
    pd.DataFrame(results).to_csv(RES/'modelos_recalculados.csv',index=False,encoding='utf-8-sig')
    (RES/'diagnosticos.json').write_text(json.dumps(diagnostics,indent=2,ensure_ascii=False),encoding='utf-8')
def model_glm(d,rhs,name,terms,country):
    x=dmatrix(rhs,d,return_type='dataframe')
    fit=sm.GLM(d.death_in_hospital.to_numpy(),x,family=sm.families.Binomial()).fit(maxiter=100,cov_type='cluster',cov_kwds={'groups':d.hospital_id.to_numpy()})
    for term in terms:
        b=float(fit.params[term]); se=float(fit.bse[term])
        results.append({'country':country,'analysis':name,'term':term,'scale':'OR','estimate':np.exp(b),'low':np.exp(b-1.96*se),'high':np.exp(b+1.96*se),'n':len(d),'hospitals':d.hospital_id.nunique(),'beta':b,'se':se})
    print(name,country,[(t,float(np.exp(fit.params[t]))) for t in terms],flush=True)
    save(); return fit,x
def residualize(a,g1,g2):
    a=a.astype(float).copy()
    for iteration in range(200):
        prev=a.copy()
        for g in [g1,g2]:
            sums=np.zeros((int(g.max())+1,a.shape[1]))
            np.add.at(sums,g,a)
            a-= (sums/np.bincount(g)[:,None])[g]
        if np.max(np.abs(a-prev))<1e-10: return a,iteration+1
    raise RuntimeError('Fixed effects did not converge')
def fit_fe(d,country):
    x=dmatrix('log_lag_volume + '+BASE,d,return_type='dataframe').drop(columns='Intercept')
    g1=pd.factorize(d.hospital_id)[0]; g2=pd.factorize(d.year)[0]
    a,it=residualize(np.column_stack([d.death_in_hospital.to_numpy(),x.to_numpy()]),g1,g2)
    y=a[:,0]; xx=a[:,1:]; beta=np.linalg.lstsq(xx,y,rcond=None)[0]
    err=y-xx@beta; bread=np.linalg.inv(xx.T@xx)
    scores=np.zeros((g1.max()+1,xx.shape[1])); np.add.at(scores,g1,xx*err[:,None])
    k=xx.shape[1]+len(np.unique(g1))+len(np.unique(g2))-1; n=len(d); G=len(scores)
    cov=bread@(scores.T@scores)@bread*(G/(G-1))*((n-1)/(n-k))
    pos=list(x.columns).index('log_lag_volume'); b=beta[pos]; se=np.sqrt(cov[pos,pos]); crit=st.t.ppf(.975,G-1)
    for label,unit in [('doubling_of_volume_plus_one',np.log(2)),('published_within_SD',float(d.within_sd_log_volume.iloc[0]))]:
        results.append({'country':country,'analysis':'Individual admission LPM with hospital and year fixed effects','term':label,'scale':'percentage_points','estimate':100*b*unit,'low':100*(b-crit*se)*unit,'high':100*(b+crit*se)*unit,'n':n,'hospitals':G,'beta':b,'se':se})
    diagnostics.append({'country':country,'analysis':'individual_FE','iterations':it,'matrix_rank':int(np.linalg.matrix_rank(xx.T@xx)),'columns':xx.shape[1],'hospital_effects':G,'covariates':list(x.columns)})
    print('FE',country,results[-1],flush=True); save()
def spline(d,country):
    original=dmatrix('cr(log_lag_volume, df=4) + '+BASE+' + C(year)',d,return_type='dataframe')
    info=original.design_info
    spline_cols=[c for c in original.columns if c.startswith('cr(')]
    assert np.allclose(original[spline_cols].sum(axis=1),1,atol=1e-8)
    dropped=spline_cols[-1]; keep=[c for c in original.columns if c!=dropped]
    x=original[keep]
    gram=x.to_numpy().T@x.to_numpy(); rank=int(np.linalg.matrix_rank(gram))
    assert rank==len(keep),(rank,len(keep))
    fit=sm.GLM(d.death_in_hospital.to_numpy(),x,family=sm.families.Binomial()).fit(maxiter=100,cov_type='cluster',cov_kwds={'groups':d.hospital_id.to_numpy()})
    cov=np.asarray(fit.cov_params()); eig=np.linalg.eigvalsh((cov+cov.T)/2)
    assert eig.min()>-1e-8,eig.min()
    sample=d.sample(n=min(15000,len(d)),random_state=286).copy()
    grid=np.geomspace(max(1,d.lag_volume.quantile(.05)),d.lag_volume.quantile(.95),50)
    curves=[]
    for v in grid:
        sample['log_lag_volume']=np.log1p(v)
        nx=build_design_matrices([info],sample,return_type='dataframe')[0][keep].to_numpy()
        p=1/(1+np.exp(-np.clip(nx@np.asarray(fit.params),-35,35)))
        mu=p.mean(); grad=np.mean(nx*(p*(1-p))[:,None],axis=0); se=np.sqrt(grad@cov@grad)
        curves.append({'country':country,'volume':v,'mortality':mu,'low':max(0,mu-1.96*se),'high':min(1,mu+1.96*se),'standardization_n':len(sample)})
    pd.DataFrame(curves).to_csv(RES/f'spline_corrigida_{country}.csv',index=False)
    diagnostics.append({'country':country,'analysis':'spline','original_columns':original.shape[1],'original_rank':int(np.linalg.matrix_rank(original.to_numpy().T@original.to_numpy())),'corrected_columns':len(keep),'corrected_rank':rank,'removed_redundant_column':dropped,'minimum_cov_eigenvalue':float(eig.min()),'max_interval_width':max(c['high']-c['low'] for c in curves),'converged':bool(fit.converged),'llf':float(fit.llf),'note':'Same natural spline space and original knots; one redundant basis column omitted. No PSD repair. Pointwise CIs conditional on standardization sample.'})
    print('SPLINE',country,diagnostics[-1],flush=True); save()

if '--finish' in sys.argv:
    results=pd.read_csv(RES/'modelos_recalculados.csv').to_dict('records')
    results=[r for r in results if not str(r['analysis']).startswith('First differences')]
    diagnostics=json.loads((RES/'diagnosticos.json').read_text(encoding='utf-8'))
for country in ([] if '--finish' in sys.argv else ['mexico','brasil']):
    d=read(country).dropna(subset=['death_in_hospital','lag_volume_z_country_year','age','sex','trauma_subtype','year','hospital_id'])
    d=d[d.death_in_hospital.isin([0,1])].copy()
    fit,x=model_glm(d,'lag_volume_z_country_year + '+BASE+' + C(year)','Reproduction country primary',['lag_volume_z_country_year'],country)
    del fit,x; gc.collect()
    # Structural phenotype: positive ICD set avoids retaining unspecified S06.9.
    s=d[d.dx_main.astype(str).str.replace('.','',regex=False).str[:4].isin(['S061','S062','S063','S064','S065','S066','S067'])].copy()
    fit,x=model_glm(s,'lag_volume_z_country_year + '+BASE+' + C(year)','Post hoc explicitly specified S061-S067 phenotype',['lag_volume_z_country_year'],country)
    del fit,x,s; gc.collect()
    spline(d,country); gc.collect()
    hy=d[['hospital_id','year','log_lag_volume']].drop_duplicates(['hospital_id','year'])
    h=hy.groupby('hospital_id',observed=True).agg(mean_volume=('log_lag_volume','mean'),n_years=('year','nunique'))
    h=h[h.n_years.ge(2)]
    d=d.merge(h,on='hospital_id',how='inner',validate='many_to_one').dropna(subset=['hospital_region'])
    wh=hy.merge(h,on='hospital_id',how='inner'); wh['within']=wh.log_lag_volume-wh.mean_volume
    bsd=h.mean_volume.std(ddof=0); wsd=wh.within.std(ddof=0)
    d['between_volume_z']=(d.mean_volume-h.mean_volume.mean())/bsd
    d['within_volume_z']=(d.log_lag_volume-d.mean_volume)/wsd
    d['within_sd_log_volume']=wsd
    fit,x=model_glm(d,'between_volume_z + within_volume_z + '+BASE+' + C(year) + C(hospital_region)','Reproduction published mean-centered logistic',['between_volume_z','within_volume_z'],country)
    for term,sd in [('between_volume_z',bsd),('within_volume_z',wsd)]:
        b=float(fit.params[term])*np.log(2)/sd; se=float(fit.bse[term])*np.log(2)/sd
        results.append({'country':country,'analysis':'Published model common scale','term':term,'scale':'OR per doubling of volume plus one','estimate':np.exp(b),'low':np.exp(b-1.96*se),'high':np.exp(b+1.96*se),'n':len(d),'hospitals':d.hospital_id.nunique(),'beta':b,'se':se})
    del fit,x; gc.collect()
    fit_fe(d,country); del d; gc.collect()

# First differences: compare the same panel and distinguish contemporaneous and lagged exposure.
for country in ['brasil','mexico']:
    p=pd.read_parquet(DATA/f'hospital_year_risk_{country}_v280.parquet').dropna(subset=['risk_difference','log_lag_volume','hospital_id','year','admissions'])
    p['year']=pd.to_numeric(p.year,errors='raise').astype(int)
    p['hospital_id']=p.hospital_id.astype(object)
    p=p[p.admissions.ge(5)].copy(); n=p.groupby('hospital_id').year.nunique(); p=p[p.hospital_id.isin(n[n.ge(2)].index)].sort_values(['hospital_id','year']).copy()
    con=p.groupby('hospital_id').year.diff().eq(1)
    for col in ['risk_difference','log_volume','log_lag_volume']:
        p['d_'+col]=p.groupby('hospital_id')[col].diff().where(con)
    p=p.dropna(subset=['d_risk_difference','d_log_volume','d_log_lag_volume']).copy()
    for exposure in ['d_log_volume','d_log_lag_volume']:
        for year_adjust in [False,True]:
            x=dmatrix(exposure+(' + C(year)' if year_adjust else ''),p,return_type='dataframe')
            fit=sm.WLS(p.d_risk_difference.astype(float),x,weights=p.admissions.astype(float)).fit(cov_type='cluster',cov_kwds={'groups':p.hospital_id.to_numpy()})
            b=float(fit.params[exposure]); se=float(fit.bse[exposure]); unit=np.log(2)
            results.append({'country':country,'analysis':f'First differences year_adjust={year_adjust}','term':exposure,'scale':'percentage_points per doubling V+1','estimate':100*b*unit,'low':100*(b-1.96*se)*unit,'high':100*(b+1.96*se)*unit,'n':len(p),'hospitals':p.hospital_id.nunique(),'beta':b,'se':se})
    save()

fig,axes=plt.subplots(1,2,figsize=(11,4.5),sharey=True)
for ax,country,label in zip(axes,['brasil','mexico'],['Brasil','México']):
    c=pd.read_csv(RES/f'spline_corrigida_{country}.csv')
    ax.plot(c.volume,c.mortality*100,color='#166277'); ax.fill_between(c.volume,c.low*100,c.high*100,color='#166277',alpha=.18)
    ax.set_xscale('log'); ax.set_title(label); ax.set_xlabel('Internações no ano anterior (escala log)'); ax.grid(alpha=.2)
axes[0].set_ylabel('Letalidade hospitalar ajustada (%)')
fig.suptitle('Spline reestimada com matriz de desenho identificável')
fig.tight_layout(); fig.savefig(RES/'curvas_corrigidas.png',dpi=180); plt.close(fig)
save(); print('COMPLETE',flush=True)
