from pathlib import Path
import argparse,json
import pandas as pd
from cohort_rules import exposure,eligible
from raw_io import sha256,write_json

def export_flow(root):
    files=sorted(f for f in (root/'reports').glob('flow_*.csv') if 'before' not in f.name)
    flow=pd.concat((pd.read_csv(f) for f in files),ignore_index=True)
    flow['removed_or_grouped_since_previous']=(-flow.groupby('country')['n'].diff()).astype('Int64')
    flow['transition']='selection'
    flow.loc[flow.groupby('country').head(1).index,'transition']='source population'
    flow.loc[flow.stage.eq('Distinct administrative AIH authorisations'),'transition']='AIH grouping, not deletion of episodes'
    flow.to_csv(root/'COHORT_FLOW_FROM_RAW.csv',index=False)

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--country',choices=['brasil','mexico','both'],default='both');a=p.parse_args()
    out=a.root/'checkpoints/cohorts';out.mkdir(exist_ok=True);rows=[]
    for country in (['brasil','mexico'] if a.country=='both' else [a.country]):
        source=a.root/'checkpoints/cohorts_without_exposure'/f'cohort_{country}.parquet'
        d,hy=exposure(pd.read_parquet(source));e=eligible(d)
        d.to_parquet(out/f'cohort_{country}.parquet',index=False)
        hy.to_parquet(a.root/'checkpoints'/f'hospital_year_{country}.parquet',index=False)
        prior=pd.read_csv(a.root/'reports'/f'flow_{country}_before_exposure.csv').to_dict('records')
        fields=['hospital_id','sex','trauma_subtype','age','year','death_in_hospital','log_lag_volume']
        for label,n in [('Contiguous prior-year exposure',d.log_lag_volume.notna().sum()),('Complete model variables',len(d.dropna(subset=fields))),('At least two eligible hospital years',len(e))]:
            prior.append(dict(country=country,stage=label,n=int(n),unit='AIH authorisations' if country=='brasil' else 'discharge records'))
        pd.DataFrame(prior).to_csv(a.root/'reports'/f'flow_{country}.csv',index=False)
        stats=dict(country=country,cohort_n=len(d),cohort_hospitals=d.hospital_id.nunique(),analytic_n=len(e),analytic_hospitals=e.hospital_id.nunique(),analytic_deaths=int(e.death_in_hospital.sum()),source_sha256=sha256(source),cohort_sha256=sha256(out/f'cohort_{country}.parquet'))
        write_json(a.root/'reports'/f'cohort_{country}_summary.json',stats);print(json.dumps(stats),flush=True)
    export_flow(a.root)
if __name__=='__main__':main()
