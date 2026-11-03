"""Download only Brazil RD and the INSP SSA discharge series, never sectorial data."""
from pathlib import Path
import argparse,json,re,concurrent.futures,sys,time,csv
from raw_io import download_ftp,download_http,ftp_connect,write_json,utc
MEXICO_URL='https://riisp.insp.mx/nada/files_nesstar/SAEH_SSA/base/Base_Saeh_Egresos_2008_2024.zip'
UFS='AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split()
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--country',choices=['brasil','mexico','both'],default='both');p.add_argument('--workers',type=int,default=4);p.add_argument('--years',nargs='+',type=int,default=list(range(2015,2024)));args=p.parse_args()
    root=args.root;cache=root/'source_cache';logs=root/'logs';logs.mkdir(parents=True,exist_ok=True)
    results=[];start=time.monotonic()
    if args.country in ['brasil','both']:
        expected=[f'RD{uf}{year%100:02d}{month:02d}.dbc' for year in args.years for uf in UFS for month in range(1,13)]
        ftp=ftp_connect();available=set(ftp.nlst('RD*'));ftp.quit()
        missing=sorted(set(expected)-available);write_json(logs/'brazil_expected_sources.json',{'expected':expected,'absent_from_listing':missing,'checked_at_utc':utc()})
        if missing:raise RuntimeError('Expected raw files absent from official listing: '+str(missing))
        batches=[expected[i::args.workers] for i in range(args.workers)]
        def batch(names):
            conn=None;out=[]
            for name in names:
                for attempt in range(4):
                    try:
                        if conn is None:conn=ftp_connect()
                        result=download_ftp(conn,name,cache/'brazil'/name);out.append(result);break
                    except Exception as exc:
                        if conn:
                            try:conn.close()
                            except Exception:pass
                        conn=None
                        if attempt==3:
                            result={'country':'brasil','filename':name,'status':'failed','error':repr(exc),'at':utc()};out.append(result);write_json(logs/(name+'.error.json'),result)
                        else:time.sleep(2**attempt)
                if len(out)%30==0:print(json.dumps({'worker_completed':len(out),'last':name,'elapsed_s':round(time.monotonic()-start)}),flush=True)
            if conn:conn.quit()
            return out
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for out in pool.map(batch,batches):results.extend(out)
    if args.country in ['mexico','both']:
        from raw_io import download_http_ranges
        result=download_http_ranges(MEXICO_URL,cache/'mexico/Base_Saeh_Egresos_2008_2024.zip',workers=args.workers);result.update(country='mexico',source_family='INSP SSA discharges',filename='Base_Saeh_Egresos_2008_2024.zip');results.append(result)
    write_json(logs/('download_'+args.country+'.json'),results)
    print(json.dumps({'files':len(results),'failed':sum(r.get('status','downloaded')=='failed' for r in results),'seconds':time.monotonic()-start}),flush=True)
if __name__=='__main__':main()
