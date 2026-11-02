"""Additional post hoc analyses; read PLANO_FINAL.txt before interpretation."""
from pathlib import Path
import os,sys,json,hashlib,gc
ROOT=Path(__file__).resolve().parent; PREV=ROOT.parent/'artigo_revisado_20261028'; AUD=ROOT.parent/'auditoria_20261028'
sys.path.insert(0,str(AUD/'dependencias'));sys.path.insert(0,str(PREV))
os.environ['OPENBLAS_NUM_THREADS']='2';os.environ['OMP_NUM_THREADS']='2'
import numpy as np,pandas as pd
from scipy.stats import t,spearmanr
from patsy import dmatrix
from logistica_efeitos_fixos import fit as fit_logit,grouped_sum,validate as validate_logit
R=ROOT/'resultados';R.mkdir(exist_ok=True)
DATA=AUD/'base_final'/'analysis_v284_final'/'01_data'
FORM='log_lag_volume + bs(age, df=4, degree=3, include_intercept=False) + C(sex) + C(trauma_subtype) + C(year)'
UNIT=100*np.log(2)

def paired(x,y,g,pos):
    """Full rank X includes intercept; hospital-demeaned X omits it."""
    g=pd.factorize(g)[0];G=g.max()+1;n=len(y);p=x.shape[1]
    bc=np.linalg.solve(x.T@x,x.T@y);ec=y-x@bc;breadc=np.linalg.inv(x.T@x)
    counts=np.bincount(g);xf=x[:,1:];xf=xf-grouped_sum(xf,g,G)[g]/counts[g,None]
    yf=y-grouped_sum(y,g,G)[g]/counts[g]
    assert np.linalg.matrix_rank(xf.T@xf)==p-1
    breadf=np.linalg.inv(xf.T@xf);bf=breadf@(xf.T@yf);ef=yf-xf@bf
    sc=grouped_sum(x*ec[:,None],g,G);sf=grouped_sum(xf*ef[:,None],g,G)
    ic=sc@breadc[:,pos];iff=sf@breadf[:,pos-1]
    cf=G/(G-1)*(n-1)/(n-(G+p-1))
    delta=bc[pos]-bf[pos-1];influence=ic-iff
    se=np.sqrt(cf*np.sum(influence**2));crit=t.ppf(.975,G-1)
    return {'conventional_pp':bc[pos]*UNIT,'FE_pp':bf[pos-1]*UNIT,'difference_pp':delta*UNIT,'difference_low':(delta-crit*se)*UNIT,'difference_high':(delta+crit*se)*UNIT,'difference_se':se*UNIT,'n':n,'hospitals':int(G),'common_CR1':cf},influence,iff

def validation():
    import statsmodels.api as sm
    rng=np.random.default_rng(823);g=np.repeat(np.arange(24),rng.integers(20,40,24));n=len(g)
    year=rng.integers(0,5,n);v=rng.normal(size=n)+rng.normal(size=24)[g]
    x=np.column_stack([np.ones(n),v,rng.normal(size=n),np.eye(5)[year,1:]])
    y=.1+.02*v+.03*x[:,2]+rng.normal(0,.2,24)[g]+rng.normal(0,.1,n)
    out,_,_=paired(x,y,g,1)
    xf=np.column_stack([x,np.eye(24)[g,1:]])
    a=sm.OLS(y,x).fit();b=sm.OLS(y,xf).fit()
    ga=grouped_sum(x*a.resid[:,None],g,24)@np.linalg.inv(x.T@x)[:,1]
    gb=grouped_sum(xf*b.resid[:,None],g,24)@np.linalg.inv(xf.T@xf)[:,1]
    se=np.sqrt(np.sum((ga-gb)**2)*24/23*(n-1)/(n-xf.shape[1]))*UNIT
    assert abs(out['difference_pp']-(a.params[1]-b.params[1])*UNIT)<1e-9
    assert abs(out['difference_se']-se)<1e-9
    return {'paired_difference_matches_explicit_dummies':True,'joint_cluster_covariance_matches_explicit_dummies':True,**validate_logit()}

def load(country):
    d=pd.read_parquet(DATA/f'cohort_main_{country}_v280.parquet')
    for c in ['hospital_id','sex','trauma_subtype']:d[c]=d[c].astype(object)
    for c in ['age','year','death_in_hospital','log_lag_volume','los_days']:d[c]=pd.to_numeric(d[c],errors='coerce').astype(float)
    code=d.dx_main.astype(str).str.replace('.','',regex=False).str[:4]
    d['specified']=code.isin([f'S06{i}' for i in range(1,8)]);d['unspecified']=code.eq('S069')
    return d

def eligible(d):
    e=d.dropna(subset=['hospital_id','sex','trauma_subtype','age','year','death_in_hospital','log_lag_volume']).copy()
    ny=e.groupby('hospital_id').year.nunique()
    return e[e.hospital_id.isin(ny[ny.ge(2)].index)].copy()

def logistic_variant(e,country,variant):
    e=eligible(e);before=len(e);h_before=e.hospital_id.nunique()
    counts=e.groupby('hospital_id').death_in_hospital.agg(['sum','size'])
    keep=counts[(counts['sum']>0)&(counts['sum']<counts['size'])].index
    e=e[e.hospital_id.isin(keep)].copy();g=pd.factorize(e.hospital_id)[0]
    design=dmatrix(FORM,e,return_type='dataframe');pos=list(design.columns).index('log_lag_volume')
    xx=design.to_numpy();y=e.death_in_hospital.to_numpy()
    b,c,diag=fit_logit(xx[:,1:],y,g);se=np.sqrt(c[pos-1,pos-1]);crit=t.ppf(.975,g.max());u=np.log(2)
    linear,_,_=paired(xx,y,g,pos)
    row={'country':country,'variant':variant,'n':len(e),'hospitals':len(keep),'excluded_n':before-len(e),'excluded_hospitals':h_before-len(keep),'OR':np.exp(u*b[pos-1]),'OR_low':np.exp(u*(b[pos-1]-crit*se)),'OR_high':np.exp(u*(b[pos-1]+crit*se)),'LPM_same_sample_pp':linear['FE_pp']}
    return row,{'country':country,'variant':variant,**diag}

def main():
    checks=validation();contrast=[];influence_summary=[];deletions=[];coding=[];coding_summary=[];burden=[];logits=[];logitdiag=[];hashes={}
    for country in ['mexico','brasil']:
        d=load(country);hashes[country]=hashlib.sha256((DATA/f'cohort_main_{country}_v280.parquet').read_bytes()).hexdigest()
        e=eligible(d);g,ids=pd.factorize(e.hospital_id);xx=dmatrix(FORM,e,return_type='dataframe');pos=list(xx.columns).index('log_lag_volume');x=xx.to_numpy();y=e.death_in_hospital.to_numpy()
        r,inf,inf_fe=paired(x,y,g,pos);r['country']=country;contrast.append(r)
        old=pd.read_csv(PREV/'resultados'/'efeitos_fixos_sensibilidades.csv');old=old[(old.country==country)&(old.analysis=='Principal hospital and year FE')].iloc[0]
        assert abs(r['FE_pp']-old.estimate_pp)<1e-8 and r['n']==old.n
        checks[country+'_main_estimate_reproduced']=True
        approx=(r['difference_pp']-UNIT*inf)
        influence_summary.append({'country':country,'max_abs_first_order_contrast_change_pp':float(np.max(np.abs(UNIT*inf))),'first_order_deletion_min_pp':float(approx.min()),'first_order_deletion_max_pp':float(approx.max()),'max_abs_FE_score_change_pp':float(np.max(np.abs(UNIT*inf_fe)))})
        top=np.argsort(-np.abs(inf))[:5];largest=int(np.argmax(np.bincount(g)));chosen=list(dict.fromkeys([*top,largest]))
        for j in chosen:
            mask=g!=j;rr,_,_=paired(x[mask],y[mask],g[mask],pos)
            rr.update({'country':country,'removed_hospital_token':hashlib.sha256(str(ids[j]).encode()).hexdigest()[:12],'removed_admissions':int((~mask).sum()),'selection':'top5 contrast influence'+('; largest admission count' if j==largest else '') if j in top else 'largest admission count'})
            deletions.append(rr)
        print('Paired contrast',country,r,flush=True)
        hy=d.groupby(['hospital_id','year']).agg(n=('specified','size'),s069=('unspecified','mean'),specified=('specified','mean'),age70=('age',lambda z:z.ge(70).mean())).reset_index().sort_values(['hospital_id','year'])
        hy['previous_year']=hy.groupby('hospital_id').year.shift();hy['previous_s069']=hy.groupby('hospital_id').s069.shift();hy['previous_n']=hy.groupby('hospital_id').n.shift()
        hy['change_pp']=100*(hy.s069-hy.previous_s069)
        transitions=hy[(hy.year-hy.previous_year).eq(1)&hy.n.ge(10)&hy.previous_n.ge(10)]
        coding_summary.append({'country':country,'eligible_consecutive_hospital_year_pairs':len(transitions),'median_abs_s069_change_pp':transitions.change_pp.abs().median(),'p90_abs_s069_change_pp':transitions.change_pp.abs().quantile(.9),'pairs_change_atleast20pp':int(transitions.change_pp.abs().ge(20).sum()),'fraction_change_atleast20pp':transitions.change_pp.abs().ge(20).mean()})
        for year,dd in d.groupby('year'):
            hh=hy[hy.year.eq(year)];hh10=hh[hh.n.ge(10)]
            coding.append({'country':country,'year':int(year),'n':len(dd),'S069_n':int(dd.unspecified.sum()),'S069_pct':100*dd.unspecified.mean(),'specified_pct':100*dd.specified.mean(),'hospitals':len(hh),'hospitals_atleast10':len(hh10),'hospital_median_S069_pct_n10':100*hh10.s069.median(),'hospital_p25_S069_pct_n10':100*hh10.s069.quantile(.25),'hospital_p75_S069_pct_n10':100*hh10.s069.quantile(.75),'spearman_volume_S069_n10':float(spearmanr(hh10.n,hh10.s069).statistic)})
        dd=d[d.year.eq(2023)].copy();hv=dd.groupby('hospital_id').size().rename('n').reset_index().sort_values(['n','hospital_id'],ascending=[False,True]);nh=int(np.ceil(.1*len(hv)));topids=hv.iloc[:nh].hospital_id;selected=dd.hospital_id.isin(topids)
        metric={'Admissions':np.ones(len(dd)),'Recorded deaths':dd.death_in_hospital.fillna(0).to_numpy(),'Recorded bed-days':dd.los_days.to_numpy(),'Bed-days capped at 90':dd.los_days.clip(0,90).to_numpy(),'Admissions age 70 or older':dd.age.ge(70).to_numpy(),'S061-S067 admissions':dd.specified.to_numpy()}
        if country=='brasil':metric['Coded acute cranial surgery']=dd.primary_acute_surgery.eq(1).to_numpy(dtype=float)
        cutoff=int(hv.iloc[nh-1].n);strict=dd.hospital_id.isin(hv.loc[hv.n.gt(cutoff),'hospital_id']).to_numpy();inclusive=dd.hospital_id.isin(hv.loc[hv.n.ge(cutoff),'hospital_id']).to_numpy()
        for name,values in metric.items():
            assert np.isfinite(values).all() and np.min(values)>=0
            total=float(np.sum(values));topn=float(np.sum(values[selected.to_numpy()]))
            burden.append({'country':country,'year':2023,'metric':name,'total':total,'top10_total':topn,'top10_share_pct':100*topn/total,'top10_hospitals':nh,'all_hospitals':len(hv),'cutoff_volume':cutoff,'ties_at_cutoff':int(hv.n.eq(cutoff).sum()),'share_excluding_all_cutoff_ties':100*np.sum(values[strict])/total,'share_including_all_cutoff_ties':100*np.sum(values[inclusive])/total})
        balanced=e.groupby('hospital_id').year.nunique();balanced=balanced[balanced.eq(8)].index
        for name,subset in [('S061-S067',e[e.specified]),('Balanced 8 years',e[e.hospital_id.isin(balanced)])]:
            row,diag=logistic_variant(subset,country,name);logits.append(row);logitdiag.append(diag);print('Logistic sensitivity',row,flush=True)
            pd.DataFrame(logits).to_csv(R/'logistica_complementar.csv',index=False)
        del d,e,x,xx;gc.collect()
    for name,rows in [('contraste_pareado',contrast),('influencia_resumo',influence_summary),('exclusoes_hospitais',deletions),('codificacao_anual',coding),('codificacao_transicoes',coding_summary),('carga_concentrada_2023',burden)]:pd.DataFrame(rows).to_csv(R/(name+'.csv'),index=False)
    (R/'diagnosticos_logistica.json').write_text(json.dumps(logitdiag,indent=2),encoding='utf-8')
    (R/'validacao.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    (R/'hashes_dados.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    print('ALL COMPLETE',checks,flush=True)

if __name__=='__main__':main()
