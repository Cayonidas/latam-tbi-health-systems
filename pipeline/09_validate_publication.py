"""Cross-check numerical outputs against independently calculated quantities."""
from pathlib import Path
import argparse,json
import numpy as np,pandas as pd
from cohort_rules import exposure,eligible
from raw_io import sha256,write_json,utc

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--data-root',type=Path);p.add_argument('--results-root',type=Path);a=p.parse_args()
    data=a.data_root or a.root/'checkpoints/cohorts';res=a.results_root or a.root/'results/reconstructed'
    checks={}
    def ok(name,condition):
        checks[name]=bool(condition)
        if not checks[name]:raise AssertionError(name)
    linear=pd.read_csv(res/'linear/efeitos_fixos_sensibilidades.csv')
    paired=pd.read_csv(res/'robustness/contraste_pareado.csv')
    independent=pd.read_csv(res/'diagnostics/independent_estimates.csv')
    ok('Only Brazil and Mexico in linear models',set(linear.country)=={'brasil','mexico'})
    ok('All original linear specifications plus documented Brazilian age audit retained',len(linear)==20)
    ok('Specifications not duplicated',not linear.duplicated(['country','analysis']).any())
    ok('Finite ordered intervals',np.isfinite(linear[['estimate_pp','low_pp','high_pp']]).all().all() and linear.low_pp.le(linear.estimate_pp).all() and linear.high_pp.ge(linear.estimate_pp).all())
    for country in ['brasil','mexico']:
        d=pd.read_parquet(data/f'cohort_{country}.parquet');recount,hy=exposure(d);e=eligible(d)
        ok(country+' annual volume and lag independently recounted',all(np.allclose(d[c],recount[c],equal_nan=True) for c in ['hospital_volume_year','lag_volume','log_lag_volume']))
        ok(country+' exposure window',e.year.between(2016,2023).all())
        ok(country+' age and principal diagnosis',d.age.ge(20).all() and d.dx_main.str.startswith('S06').all())
        row=paired[paired.country.eq(country)].iloc[0]
        main=linear[linear.country.eq(country)&linear.analysis.eq('Principal hospital and year FE')].iloc[0]
        conventional=linear[linear.country.eq(country)&linear.analysis.eq('Conventional LPM same sample')].iloc[0]
        ok(country+' identical paired admission sample',len(e)==row.n==main.n==conventional.n and e.hospital_id.nunique()==row.hospitals==main.hospitals)
        ok(country+' paired difference identity',abs(row.difference_pp-(conventional.estimate_pp-main.estimate_pp))<1e-8)
        for model,estimate,low,high in [('FE',main.estimate_pp,main.low_pp,main.high_pp),('Conventional',conventional.estimate_pp,conventional.low_pp,conventional.high_pp),('Paired difference',row.difference_pp,row.difference_low,row.difference_high)]:
            other=independent[independent.country.eq(country)&independent.model.eq(model)].iloc[0]
            ok(country+' independent FWL '+model,np.allclose([estimate,low,high],[other.estimate_pp,other.low_pp,other.high_pp],atol=1e-8,rtol=0))
        hashes=json.loads((res/'linear/input_hashes.json').read_text())
        ok(country+' analysis input hash',hashes[country]==sha256(data/f'cohort_{country}.parquet'))
    logits=pd.read_csv(res/'linear/logistica_FE.csv');complementary=pd.read_csv(res/'robustness/logistica_complementar.csv')
    ok('All six planned logistic attempts plus explicit separation diagnostic retained',len(logits)==2 and len(complementary)==5)
    failed=complementary[complementary.status.eq('not_estimable')]
    ok('Nonestimable models have no fabricated odds ratios',failed[['OR','OR_low','OR_high']].isna().all().all())
    ok('Known separation result retained explicitly',((failed.country=='mexico')&(failed.variant=='S061-S067')).any())
    converged=complementary[complementary.status.eq('converged')]
    ok('Converged logistic intervals finite and ordered',np.isfinite(converged[['OR','OR_low','OR_high']]).all().all() and converged.OR_low.le(converged.OR).all() and converged.OR_high.ge(converged.OR).all())
    context=pd.read_csv(res/'contextual/mean_centered_logistic.csv')
    context_diagnostics=json.loads((res/'contextual/mean_centered_diagnostics.json').read_text())
    ok('All four contextual mean-centred logistic components retained',len(context)==4 and set(context.country)=={'brasil','mexico'} and context[['OR','low','high']].gt(0).all().all())
    ok('Contextual logistic models converged',len(context_diagnostics)==2 and all(x['converged'] for x in context_diagnostics))
    burden=pd.read_csv(res/'robustness/carga_concentrada_2023.csv')
    ok('Workload shares bounded by tie sensitivity',burden.top10_share_pct.between(0,100).all() and (burden.top10_share_pct>=burden.share_excluding_all_cutoff_ties-1e-9).all() and (burden.top10_share_pct<=burden.share_including_all_cutoff_ties+1e-9).all())
    write_json(a.root/'execution/publication_numerical_validation.json',dict(all_passed=True,checks=checks,verified_at_utc=utc()))
    print(json.dumps(dict(passed=len(checks),all_passed=True)),flush=True)
if __name__=='__main__':main()
