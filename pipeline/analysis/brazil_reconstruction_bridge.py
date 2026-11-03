"""Validation of the old age-unit rule using only newly acquired source records."""
from settings import ROOT,RESULTS
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from patsy import dmatrix
from cohort_rules import exposure,eligible
import linear_models as linear
from paired_and_workload import paired,FORM

def main():
    d=pd.read_parquet(ROOT/'checkpoints/brazil_legacy_rule_reconstruction.parquet');d,_=exposure(d);e=eligible(d)
    for col in ['hospital_id','sex','trauma_subtype','hospital_region']:e[col]=e[col].astype(object)
    out=RESULTS/'brazil_bridge';out.mkdir(exist_ok=True)
    linear.RES=out;linear.results=[];linear.diags=[]
    linear.fit_fe(e,'brasil','Principal hospital and year FE')
    design=dmatrix(FORM,e,return_type='dataframe');pos=list(design.columns).index('log_lag_volume')
    contrast,_,_=paired(design.to_numpy(),e.death_in_hospital.to_numpy(),e.hospital_id.to_numpy(),pos)
    contrast['country']='brasil';pd.DataFrame([contrast]).to_csv(out/'contraste_pareado.csv',index=False)
    print(json.dumps(contrast),flush=True)
if __name__=='__main__':main()
