from pathlib import Path
import sys,os,json,gc,ast,hashlib
OUT=Path(__file__).resolve().parent; AUD=OUT.parent/'auditoria_20261028'
sys.path.insert(0,str(AUD/'dependencias'))
os.environ['OPENBLAS_NUM_THREADS']='2'; os.environ['OMP_NUM_THREADS']='2'
os.environ['MPLCONFIGDIR']=str(OUT/'cache_matplotlib')
import numpy as np,pandas as pd,scipy.stats as st
from patsy import dmatrix
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
DATA=AUD/'base_final'/'analysis_v284_final'/'01_data'; RES=OUT/'resultados';RES.mkdir(exist_ok=True)
BASE='bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype)'
tree=ast.parse((AUD/'reanalisar.py').read_text(encoding='utf-8'))
f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='residualize')
ns={'np':np};exec(compile(ast.Module(body=[f],type_ignores=[]),'residualize','exec'),ns)
residualize=ns['residualize']; results=[];diags=[];flow=[];profiles=[];concentration=[];support=[]

def save():
    pd.DataFrame(results).to_csv(RES/'efeitos_fixos_sensibilidades.csv',index=False,encoding='utf-8-sig')
    (RES/'diagnosticos.json').write_text(json.dumps(diags,ensure_ascii=False,indent=2),encoding='utf-8')

def eligible(d):
    d=d.dropna(subset=['death_in_hospital','log_lag_volume','age','sex','trauma_subtype','year','hospital_id']).copy()
    n=d.groupby('hospital_id').year.nunique()
    return d[d.hospital_id.isin(n[n.ge(2)].index)].copy()

def fit_fe(d,country,name,region_year=False):
    d=eligible(d)
    if region_year:d=d.dropna(subset=['hospital_region'])
    x=dmatrix('log_lag_volume + '+BASE,d,return_type='dataframe').drop(columns='Intercept')
    g1=pd.factorize(d.hospital_id)[0]
    g2=pd.factorize(d.hospital_region.astype(str)+'_'+d.year.astype(str) if region_year else d.year)[0]
    raw=np.column_stack([d.death_in_hospital.to_numpy(),x.to_numpy()])
    a,it=residualize(raw,g1,g2); y=a[:,0]; xx=a[:,1:]
    # Drop columns annihilated by the within transformation, never the exposure.
    varying=np.max(np.abs(xx),axis=0)>1e-9;cols=x.columns[varying];xx=xx[:,varying]
    rank=np.linalg.matrix_rank(xx.T@xx);assert rank==xx.shape[1]
    bread=np.linalg.inv(xx.T@xx);beta=bread@(xx.T@y);err=y-xx@beta
    G=int(g1.max()+1);T=int(g2.max()+1);n=len(d)
    # Fixed-effect rank via connected components of hospital/time-effect graph.
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    rows=np.r_[g1,G+g2];cc=np.r_[G+g2,g1]
    graph=coo_matrix((np.ones(len(rows)),(rows,cc)),shape=(G+T,G+T)).tocsr()
    components=connected_components(graph,directed=False,return_labels=False)
    k=rank+G+T-components; correction=(G/(G-1))*((n-1)/(n-k))
    scores=np.zeros((G,rank));np.add.at(scores,g1,xx*err[:,None]);cov=bread@(scores.T@scores)@bread*correction
    pos=list(cols).index('log_lag_volume');b=beta[pos];se=np.sqrt(cov[pos,pos]);crit=st.t.ppf(.975,G-1);unit=100*np.log(2)
    r={'country':country,'analysis':name,'estimate_pp':unit*b,'low_pp':unit*(b-crit*se),'high_pp':unit*(b+crit*se),'n':n,'deaths':int(d.death_in_hospital.sum()),'hospitals':G,'hospital_years':d[['hospital_id','year']].drop_duplicates().shape[0],'beta_log':b,'se_log':se}
    results.append(r)
    diag={'country':country,'analysis':name,'iterations':it,'rank':int(rank),'covariates':list(cols),'fixed_effect_connected_components':int(components),'df_parameters':int(k)}
    if name=='Principal hospital and year FE':
        fitted=d.death_in_hospital.to_numpy()-err
        diag.update({'fitted_below_zero':int((fitted<0).sum()),'fitted_above_one':int((fitted>1).sum()),'fitted_min':float(fitted.min()),'fitted_max':float(fitted.max())})
        rng=np.random.default_rng(20261028); influence=scores@bread[:,pos]*np.sqrt(correction)
        draws=np.array([np.dot(rng.choice([-1.,1.],size=G),influence) for _ in range(1999)])
        r['wild_one_step_low_pp'],r['wild_one_step_high_pp']=unit*np.quantile(b+draws,[.025,.975])
        # Conventional LPM on exactly the same admissions and covariate definitions.
        xc=dmatrix('log_lag_volume + '+BASE+' + C(year)',d,return_type='dataframe')
        import statsmodels.api as sm
        m=sm.OLS(d.death_in_hospital.to_numpy(),xc).fit(cov_type='cluster',cov_kwds={'groups':g1},use_t=True)
        lo,hi=m.conf_int().loc['log_lag_volume']
        results.append({'country':country,'analysis':'Conventional LPM same sample','estimate_pp':unit*m.params['log_lag_volume'],'low_pp':unit*lo,'high_pp':unit*hi,'n':n,'deaths':int(d.death_in_hospital.sum()),'hospitals':G,'hospital_years':r['hospital_years']})
        del m,xc
    diags.append(diag);print(country,name,round(r['estimate_pp'],4),n,G,flush=True);save();gc.collect()

for country in ['mexico','brasil']:
    d=pd.read_parquet(DATA/f'cohort_main_{country}_v280.parquet')
    for c in ['hospital_id','sex','trauma_subtype','hospital_region']:d[c]=d[c].astype(object)
    for c in ['year','age','death_in_hospital','log_lag_volume','lag_volume','hospital_volume_year']:d[c]=pd.to_numeric(d[c],errors='raise').astype(float)
    d['specified']=d.dx_main.astype(str).str.replace('.','',regex=False).str[:4].isin(['S061','S062','S063','S064','S065','S066','S067'])
    profiles.append({'country':country,'admissions':len(d),'deaths':int(d.death_in_hospital.sum()),'known_outcome':int(d.death_in_hospital.notna().sum()),'female':int(d.sex.eq('Female').sum()),'sex_counts':d.sex.value_counts().to_json(),'age_median':d.age.median(),'age_q1':d.age.quantile(.25),'age_q3':d.age.quantile(.75),'age70plus':int(d.age.ge(70).sum()),'specified_lesions':int(d.specified.sum()),'S069':int(d.dx_main.astype(str).str.replace('.','',regex=False).str.startswith('S069').sum()),'hospitals':d.hospital_id.nunique()})
    for year,g in d.groupby('year'):
        h=g.groupby('hospital_id').size().sort_values(ascending=False);toph=int(np.ceil(.1*len(h)))
        concentration.append({'country':country,'year':int(year),'hospitals':len(h),'admissions':len(g),'top10_n_hospitals':toph,'top10_admissions':int(h.iloc[:toph].sum()),'top10_share':h.iloc[:toph].sum()/len(g),'deaths':int(g.death_in_hospital.sum()),'specified_share':g.specified.mean(),'median_hospital_volume':h.median()})
    e=eligible(d)
    no_lag=d.log_lag_volume.isna();no_death=d.death_in_hospital.isna()
    flow.append({'country':country,'start':len(d),'missing_lag':int(no_lag.sum()),'missing_death_all':int(no_death.sum()),'missing_death_with_lag':int((no_death&~no_lag).sum()),'complete_case_before_repeated_hospital':len(d.dropna(subset=['death_in_hospital','log_lag_volume','age','sex','trauma_subtype','year','hospital_id'])),'FE_sample':len(e),'FE_hospitals':e.hospital_id.nunique()})
    hy=e[['hospital_id','year','log_lag_volume','lag_volume']].drop_duplicates().sort_values(['hospital_id','year'])
    zz=hy.groupby('hospital_id').agg(years=('year','nunique'),within_sd=('log_lag_volume','std'),minlag=('lag_volume','min'),maxlag=('lag_volume','max'))
    zz['log_range']=np.log1p(zz.maxlag)-np.log1p(zz.minlag)
    support.append({'country':country,'hospitals':len(zz),'median_years':zz.years.median(),'hospitals_all8':int(zz.years.eq(8).sum()),'median_within_log_sd':zz.within_sd.median(),'fraction_ever_double_Vplus1':zz.log_range.ge(np.log(2)).mean(),'within_log_sd_p10':zz.within_sd.quantile(.1),'within_log_sd_p90':zz.within_sd.quantile(.9)})
    hy.to_csv(RES/f'exposure_support_{country}.csv',index=False)
    fit_fe(e,country,'Principal hospital and year FE')
    fit_fe(e[e.specified],country,'Specified lesions S061-S067')
    if country=='brasil':fit_fe(e[e.procedure_group.ne('CHRONIC_SDH_SURGERY')],country,'Exclude chronic subdural procedure')
    balanced=zz[zz.years.eq(8)].index
    fit_fe(e[e.hospital_id.isin(balanced)],country,'Hospitals observed in all 8 exposure years')
    fit_fe(e[e.hospital_volume_year.ge(10)&e.lag_volume.ge(10)],country,'Current and prior volume at least 10')
    fit_fe(e[e.year.le(2019)],country,'Pre-pandemic 2016-2019')
    fit_fe(e[~e.year.isin([2020,2021])],country,'Exclude 2020-2021')
    fit_fe(e,country,'Hospital and region-year FE',True)
    if country=='mexico':
        fit_fe(e[e.age.le(110)],country,'Age no more than 110')
        fit_fe(e[e.year.ge(2019)],country,'Mexico 2019-2023')
    del d,e;gc.collect()
for file,items in [('coorte',profiles),('fluxo',flow),('concentracao_anual',concentration),('suporte_exposicao',support)]:pd.DataFrame(items).to_csv(RES/f'{file}.csv',index=False,encoding='utf-8-sig')
(RES/'input_hashes.json').write_text(json.dumps({c:hashlib.sha256((DATA/f'cohort_main_{c}_v280.parquet').read_bytes()).hexdigest() for c in ['brasil','mexico']},indent=2),encoding='utf-8')
print('COMPLETE',flush=True)
