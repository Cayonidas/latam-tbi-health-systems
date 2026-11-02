from pathlib import Path
import argparse,hashlib,json,ast,sys
R=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--synthetic',action='store_true');a=p.parse_args()
manifest=json.loads((R/'package_manifest.json').read_text(encoding='utf-8'))
for name,sha in manifest.items():
    f=R/name
    assert f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()==sha,name
    if f.suffix=='.py':ast.parse(f.read_text(encoding='utf-8'))
assert not any(f.suffix.lower() in ['.parquet','.feather','.pkl','.sav','.dta','.dbf','.dbc'] for f in R.rglob('*') if f.is_file())
import pandas as pd
tables=0
for f in R.rglob('*.csv'):
    d=pd.read_csv(f);cols=set(d.columns)
    assert not ({'record_id','age','death_in_hospital'} <= cols),str(f)
    assert not ({'hospital_id','age','death_in_hospital'} <= cols),str(f)
    tables+=1
print('Archive hashes and Python syntax passed:',len(manifest),'files; CSV schemas:',tables)
if a.synthetic:
    sys.path.insert(0,str(R/'artigo_final_20261028'))
    from fortalecer import validation
    checks=validation();assert all(checks.values());print('Synthetic checks:',checks)
