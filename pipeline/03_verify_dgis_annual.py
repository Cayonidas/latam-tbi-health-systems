"""Independently parse all annual DGIS SSA discharge records for source audit."""
from pathlib import Path
import argparse,json,time,io
import pandas as pd
import zipfile64.zipfile as zipfile
from raw_io import sha256,write_json,utc
from cohort_rules import diagnosis,numeric,mexico_annual_age

KEEP={'id','clues','egreso','ingre','dias_esta','cveedad','edad','sexo','motegre','afecprin','diag_ini','proced','cluesproced','cluesreferido','sa_inten','sa_interm','entidad'}
def clean_name(s):return s.removeprefix('\ufeff').removeprefix('ï»¿').strip().lower()

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);a=p.parse_args()
    out=a.root/'checkpoints/mexico_dgis_s06';out.mkdir(parents=True,exist_ok=True);reports=[]
    for year in range(2015,2024):
        source=a.root/'source_cache/mexico_dgis'/f'ssa_egresos_{year}.zip';expected=json.loads(source.with_suffix('.zip.json').read_text())
        digest=sha256(source)
        if digest!=expected['sha256']:raise ValueError('DGIS annual source checksum mismatch')
        target=out/f'dgis_{year}.parquet';status=out/f'dgis_{year}.json'
        if status.exists() and target.exists():
            previous=json.loads(status.read_text())
            if previous.get('complete') and previous['source_sha256']==digest and previous['output_sha256']==sha256(target):
                d=pd.read_parquet(target)
                previous['adult_s06']=int(mexico_annual_age(d.edad,d.cveedad,year).ge(20).sum())
                previous['age_rule']='Official annual CATTIPOEDAD: years=3 through 2019; years=5 from 2020'
                write_json(status,previous);reports.append(previous);print(json.dumps({k:v for k,v in previous.items() if k!='columns'}),flush=True);continue
        total=0;parts=[];started=time.monotonic()
        with zipfile.ZipFile(source) as archive:
            candidates=[x for x in archive.infolist() if x.filename.lower().endswith(('.csv','.txt')) and 'egreso' in Path(x.filename).name.lower()]
            if len(candidates)!=1:raise ValueError('Ambiguous DGIS discharge member')
            info=candidates[0]
            with archive.open(info) as stream:header=stream.readline().decode('latin1').strip()
            separator='|' if header.count('|')>header.count(',') else ','
            columns=[clean_name(s) for s in header.split(separator)]
            with archive.open(info) as stream:
                for chunk in pd.read_csv(stream,sep=separator,encoding='latin-1',dtype='string',keep_default_na=False,chunksize=250000,usecols=lambda c:clean_name(c) in KEEP,on_bad_lines='error'):
                    chunk.columns=[clean_name(c) for c in chunk.columns]
                    mask=diagnosis(chunk.afecprin).str.startswith('S06',na=False)
                    selected=chunk.loc[mask].copy();selected['source_record_number']=chunk.index[mask]+1;selected['year']=year
                    parts.append(selected);total+=len(chunk)
                if stream.read(1):raise ValueError('Unread DGIS bytes after complete CSV pass')
            d=pd.concat(parts,ignore_index=True)
            d.to_parquet(target,index=False)
            report=dict(year=year,complete=True,source_records=total,principal_s06=len(d),adult_s06=int(mexico_annual_age(d.edad,d.cveedad,year).ge(20).sum()),
                        source_sha256=digest,source_member=info.filename,source_member_bytes=info.file_size,source_member_crc32=info.CRC,crc_verified=True,
                        delimiter=separator,encoding='latin-1; header BOM removed explicitly',columns=columns,output_sha256=sha256(target),seconds=time.monotonic()-started,at_utc=utc())
            write_json(status,report);reports.append(report);print(json.dumps({k:v for k,v in report.items() if k!='columns'}),flush=True)
    write_json(a.root/'reports/dgis_annual_complete_audit.json',reports)
    pd.DataFrame([{k:v for k,v in row.items() if k!='columns'} for row in reports]).to_csv(a.root/'reports/dgis_annual_flow.csv',index=False)
if __name__=='__main__':main()
