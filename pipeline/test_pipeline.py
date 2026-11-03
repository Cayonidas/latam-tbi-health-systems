"""Independent numerical and source-rule tests; no historical-country inputs."""
from pathlib import Path
import sys,json,tempfile,importlib.util,os
import numpy as np,pandas as pd,scipy.stats as st,statsmodels.api as sm
from patsy import dmatrix
from scipy.linalg import qr
sys.path.insert(0,str(Path(__file__).parent/'analysis'))
import linear_models as lm
from paired_and_workload import validation
from cohort_rules import exposure,brazil_age,mexico_age,mexico_annual_age,eligible

def main():
    checks=validation()
    def ok(label,condition):
        if not bool(condition):raise AssertionError(label)
        checks[label]=True
    rng=np.random.default_rng(284);rows=[]
    for hospital in range(20):
        for year in range(6):
            if rng.uniform()<.15:continue
            for _ in range(4):rows.append([str(hospital),year,'A' if hospital<10 else 'B',rng.normal(),rng.uniform(20,99),rng.choice(['Male','Female']),rng.choice(['a','b']),rng.binomial(1,.15)])
    d=pd.DataFrame(rows,columns=['hospital_id','year','hospital_region','log_lag_volume','age','sex','trauma_subtype','death_in_hospital'])
    d['ry']=d.hospital_region+'_'+d.year.astype(str)
    lm.save=lambda:None
    for region in [False,True]:
        lm.fit_fe(d,'control','synthetic',region)
        z=lm.results[-1]
        x=dmatrix('log_lag_volume + '+lm.BASE+' + C(hospital_id) + C('+('ry' if region else 'year')+')',d,return_type='dataframe')
        rank=np.linalg.matrix_rank(x.to_numpy());_,_,piv=qr(x.to_numpy(),mode='economic',pivoting=True)
        x=x.iloc[:,sorted(piv[:rank])]
        model=sm.OLS(d.death_in_hospital,x).fit(cov_type='cluster',cov_kwds={'groups':d.hospital_id})
        ok('FE coefficient vs independent explicit dummies region='+str(region),np.isclose(z['beta_log'],model.params['log_lag_volume'],atol=1e-9))
        ok('CR1 rank-adjusted SE vs independent explicit dummies region='+str(region),np.isclose(z['se_log'],model.bse['log_lag_volume'],atol=1e-9))
    demo=pd.DataFrame({'hospital_id':['a']*7+['b']*3,'year':[2015,2015,2016,2018,2018,2019,2019,2015,2016,2017]})
    rebuilt,hy=exposure(demo)
    a=hy[hy.hospital_id.eq('a')].set_index('year')
    ok('Gap in observed years does not borrow a noncontiguous volume',pd.isna(a.loc[2018,'lag_volume']))
    ok('Prior count computed before model missingness selection',a.loc[2016,'lag_volume']==2 and a.loc[2019,'lag_volume']==2)
    ok('Exposure uses natural logarithm of prior count plus one',np.isclose(a.loc[2016,'log_lag_volume'],np.log(3)))
    ok('Exposure merge preserves admissions',len(rebuilt)==len(demo))
    ba=brazil_age(pd.Series(['20','00','09','20']),pd.Series(['4','5','5','3']))
    ok('Brazil year and centenarian age units',ba.iloc[:3].tolist()==[20,100,109] and pd.isna(ba.iloc[3]))
    ma=mexico_age(pd.Series([20,999,25,110]),pd.Series([3,3,2,3]))
    ok('Mexico year units and documented unknown-age code',ma.iloc[0]==20 and ma.iloc[3]==110 and ma.iloc[1:3].isna().all())
    before=mexico_annual_age(pd.Series([30,30]),pd.Series([3,5]),2019)
    after=mexico_annual_age(pd.Series([30,30]),pd.Series([3,5]),2020)
    ok('DGIS annual age coding changes in 2020',before.iloc[0]==30 and pd.isna(before.iloc[1]) and pd.isna(after.iloc[0]) and after.iloc[1]==30)
    from compare_mexico_sources import multiset
    ok('Equivalent source values compare equally across numeric storage types',multiset(pd.DataFrame({'unit':[3,3]}),pd.DataFrame({'unit':[3.,3.]}),['unit'])==dict(shared=2,annual_only=0,consolidated_only=0))
    from compare_source_cohorts import canonical
    ok('Frozen nullable integers and reconstructed floats compare equally',canonical(pd.DataFrame({'year':pd.Series([2015,None],dtype='Int64')}),['year']).equals(canonical(pd.DataFrame({'year':[2015.,np.nan]}),['year'])))
    from logistic_models import fit as fit_logit,LogisticEstimabilityError
    groups=np.repeat(np.arange(4),20);ys=np.tile([0.,1.],40);xs=np.column_stack([rng.normal(size=80),np.zeros(80)]);xs[[0,20],1]=1
    detected=False
    try:fit_logit(xs,ys,groups)
    except LogisticEstimabilityError as exc:detected=exc.diagnostics['separating_cells']==[dict(column=1,n=2,deaths=0)]
    ok('Separated covariate cell cannot be reported as a finite logistic fit',detected)
    # Test the custom DBF view independently against dbfread on an actual source,
    # when a locally downloaded file exists. This is not a synthetic source claim.
    root=Path(os.environ.get('TBI_ROOT',Path(__file__).resolve().parent.parent));raw=root/'source_cache/brazil/RDAC1501.dbc'
    if raw.exists():
        import datasus_dbc
        from dbfread import DBF
        path=Path(__file__).with_name('02_harmonise_brazil.py');spec=importlib.util.spec_from_file_location('br_reader',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        blob=datasus_dbc.decompress_bytes(raw.read_bytes());arr,_=module.parse_dbf(blob)
        with tempfile.TemporaryDirectory(dir=root/'checkpoints') as temp:
            dbf=Path(temp)/'sample.dbf';dbf.write_bytes(blob)
            records=list(DBF(str(dbf),encoding='latin1',char_decode_errors='strict'))
            ok('Native DBF row count vs independent dbfread',len(records)==len(arr))
            for name in ['N_AIH','CNES','DIAG_PRINC','COD_IDADE','IDADE','MORTE']:
                left=np.char.strip(np.char.decode(arr[name],'latin1')).tolist()
                right=[str(row[name]).strip() for row in records]
                ok('DBF field vs independent parser '+name,left==right)
    out=root/'execution';out.mkdir(exist_ok=True)
    (out/'automated_tests.json').write_text(json.dumps({'all_passed':True,'checks':checks},indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(checks),'checks':checks}),flush=True)
if __name__=='__main__':main()
