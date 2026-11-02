from pathlib import Path
import sys,ast,json,hashlib,gc
OUT=Path(__file__).resolve().parent;AUD=OUT.parent/'auditoria_20261028';sys.path.insert(0,str(AUD/'dependencias'))
import numpy as np,pandas as pd,scipy.stats as st,statsmodels.api as sm
from patsy import dmatrix
checks=[]
def ok(name,flag):
    assert flag,name
    checks.append({'check':name,'passed':True})
tree=ast.parse((OUT/'analise_expandida.py').read_text(encoding='utf-8'))
old=ast.parse((AUD/'reanalisar.py').read_text(encoding='utf-8'))
funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['eligible','fit_fe']]
funcs+=[n for n in old.body if isinstance(n,ast.FunctionDef) and n.name=='residualize']
ns={'np':np,'pd':pd,'st':st,'dmatrix':dmatrix,'gc':gc,'results':[],'diags':[],'save':lambda:None,'BASE':'bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype)'}
exec(compile(ast.Module(body=funcs,type_ignores=[]),'actual_analysis_functions','exec'),ns)
rng=np.random.default_rng(284)
rows=[]
for h in range(20):
    for y in range(6):
        if rng.uniform()<.15:continue
        for j in range(4):rows.append([str(h),y,'A' if h<10 else 'B',rng.normal(),rng.uniform(20,99),rng.choice(['Male','Female']),rng.choice(['a','b']),rng.binomial(1,.15)])
d=pd.DataFrame(rows,columns=['hospital_id','year','hospital_region','log_lag_volume','age','sex','trauma_subtype','death_in_hospital'])
d['ry']=d.hospital_region+'_'+d.year.astype(str)
for region in [False,True]:
    ns['fit_fe'](d,'control','validation region='+str(region),region)
    z=ns['results'][-1]
    x=dmatrix('log_lag_volume + '+ns['BASE']+' + C(hospital_id) + C('+('ry' if region else 'year')+')',d,return_type='dataframe')
    # Region-year designs contain redundant dummies; retain an independent basis.
    from scipy.linalg import qr
    k_rank=np.linalg.matrix_rank(x.to_numpy())
    _,_,piv=qr(x.to_numpy(),mode='economic',pivoting=True)
    x=x.iloc[:,sorted(piv[:k_rank])]
    assert 'log_lag_volume' in x
    model=sm.OLS(d.death_in_hospital,x).fit(cov_type='cluster',cov_kwds={'groups':d.hospital_id})
    corrected_se=model.bse['log_lag_volume']
    ok('FE coefficient equals explicit dummy model region_year='+str(region),np.isclose(z['beta_log'],model.params['log_lag_volume'],atol=1e-9))
    ok('Cluster SE equals explicit dummy model with rank correction region_year='+str(region),np.isclose(z['se_log'],corrected_se,atol=1e-9))
expected=json.loads((AUD/'conferencia_coortes.json').read_text(encoding='utf-8'))['cohorts']
for c in ['brasil','mexico']:
    path=AUD/'base_final'/'analysis_v284_final'/'01_data'/f'cohort_main_{c}_v280.parquet'
    ok('Input unchanged '+c,hashlib.sha256(path.read_bytes()).hexdigest()==next(i['sha256'] for i in expected if i['country']==c))
    p=pd.read_parquet(path);p['year']=p.year.astype(int)
    hy=p.groupby(['hospital_id','year'],observed=True).agg(n=('year','size'),volume=('hospital_volume_year','first'),n_vol=('hospital_volume_year','nunique'),lag=('lag_volume','first'),loglag=('log_lag_volume','first')).reset_index().sort_values(['hospital_id','year'])
    ok('Annual volume matches cohort admission count '+c,np.array_equal(hy.n,hy.volume) and hy.n_vol.eq(1).all())
    prev=hy.groupby('hospital_id').volume.shift();con=hy.groupby('hospital_id').year.diff().eq(1);prev=prev.where(con)
    ok('Lag requires contiguous observed preceding year '+c,np.allclose(hy.lag.astype(float),prev.astype(float),equal_nan=True))
    ok('Exposure is log1p preceding volume '+c,np.allclose(hy.loglag.astype(float),np.log1p(prev.astype(float)),equal_nan=True))
z=pd.read_csv(OUT/'resultados'/'efeitos_fixos_sensibilidades.csv')
ok('No duplicated specifications',not z.duplicated(['country','analysis']).any())
ok('All estimates and intervals finite and ordered',np.isfinite(z[['estimate_pp','low_pp','high_pp']]).all().all() and z.low_pp.le(z.estimate_pp).all() and z.high_pp.ge(z.estimate_pp).all())
old=pd.read_csv(AUD/'reanalise'/'modelos_recalculados.csv')
for c in ['brasil','mexico']:
    a=z[z.country.eq(c)&z.analysis.eq('Principal hospital and year FE')].iloc[0]
    b=old[old.country.eq(c)&old.analysis.str.startswith('Individual admission')&old.term.eq('doubling_of_volume_plus_one')].iloc[0]
    ok('Independent rerun reproduces audited FE result '+c,abs(a.estimate_pp-b.estimate)<1e-9 and abs(a.low_pp-b.low)<1e-9)
q=pd.read_csv(OUT/'resultados'/'chile_quedas_limites.csv')
ok('Chile identification intervals bounded',q.observed_lower.ge(0).all() and q.upper_if_all_indeterminate_falls.le(1).all() and q.observed_lower.le(q.upper_if_all_indeterminate_falls).all())
(OUT/'resultados'/'validacao.json').write_text(json.dumps(checks,indent=2),encoding='utf-8');print(json.dumps(checks,indent=2))
