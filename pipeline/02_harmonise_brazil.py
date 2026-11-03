"""Stream monthly official RD files; retain S06 records in private checkpoints.

No patient names, birth dates, postal codes or professional CPF fields are read.
AIH numbers are retained locally to audit administrative authorisation repeats.
"""
from pathlib import Path
import argparse, struct, json, time, hashlib
import numpy as np
import pandas as pd
import datasus_dbc
from raw_io import sha256, write_json, utc

FIELDS = ['N_AIH','IDENT','SEQ_AIH5','CNES','UF_ZI','MUNIC_MOV','MUNIC_RES',
          'ANO_CMPT','MES_CMPT','DT_INTER','DT_SAIDA','COD_IDADE','IDADE','SEXO',
          'DIAG_PRINC','DIAG_SECUN','MORTE','DIAS_PERM','PROC_REA','PROC_SOLIC',
          'CAR_INT','REGCT','UTI_MES_TO','UTI_INT_TO','VAL_TOT','VAL_UTI']
FIELDS += ['DIAGSEC'+str(i) for i in range(1,10)]

def parse_dbf(blob):
    n = struct.unpack_from('<I',blob,4)[0]
    header, length = struct.unpack_from('<HH',blob,8)
    if len(blob) < header+n*length:
        raise ValueError('Truncated decompressed DBF')
    fields=[]; offset=1
    for pos in range(32,header-1,32):
        if blob[pos]==13: break
        name=blob[pos:pos+11].split(b'\0')[0].decode('ascii')
        size=blob[pos+16]
        fields.append(dict(name=name,type=chr(blob[pos+11]),length=size,offset=offset))
        offset+=size
    if offset!=length: raise ValueError('DBF record layout mismatch')
    dtype=np.dtype(dict(names=['_delete']+[x['name'] for x in fields],
                        formats=['S1']+['S'+str(x['length']) for x in fields],
                        offsets=[0]+[x['offset'] for x in fields],itemsize=length))
    return np.frombuffer(blob,dtype=dtype,count=n,offset=header), fields

def process(source, output):
    digest=sha256(source); target=output/(source.stem+'.parquet'); meta=output/(source.stem+'.json')
    if meta.exists() and target.exists():
        old=json.loads(meta.read_text(encoding='utf-8'))
        if old['source_sha256']==digest and old['output_sha256']==sha256(target):return old,False
        raise ValueError('Changed source/checkpoint: '+source.name)
    blob=datasus_dbc.decompress_bytes(source.read_bytes())
    arr,schema=parse_dbf(blob)
    for required in ['DIAG_PRINC','N_AIH','CNES','ANO_CMPT','COD_IDADE','IDADE','MORTE']:
        if required not in arr.dtype.names:raise ValueError('Missing required field: '+required)
    active=arr['_delete']!=b'*'
    mask=active & np.char.startswith(np.char.strip(arr['DIAG_PRINC']),b'S06')
    data={name:np.char.strip(np.char.decode(arr[name][mask],'latin-1'))
          for name in FIELDS if name in arr.dtype.names}
    frame=pd.DataFrame(data)
    frame['source_file']=source.name
    frame['source_record_number']=np.flatnonzero(mask)+1
    frame['source_uf']=source.stem[2:4]
    frame['source_competence_year']=2000+int(source.stem[4:6])
    frame['source_competence_month']=int(source.stem[6:8])
    frame.to_parquet(target,index=False)
    age=pd.to_numeric(frame.IDADE,errors='coerce')
    result=dict(source_file=source.name,source_sha256=digest,processed_at_utc=utc(),
                source_records=len(arr),deleted_records=int((~active).sum()),
                active_records=int(active.sum()),principal_s06=len(frame),
                adult_year_code_4=int((frame.COD_IDADE.eq('4') & age.ge(20)).sum()),
                hundred_year_code_5=int(frame.COD_IDADE.eq('5').sum()),
                age_unit_counts=frame.COD_IDADE.value_counts().to_dict(),
                ident_counts=frame.IDENT.value_counts().to_dict(),
                missing_aih=int(frame.N_AIH.eq('').sum()),
                schema=schema,output_sha256=sha256(target))
    write_json(meta,result)
    return result,True

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    p.add_argument('--watch',action='store_true');p.add_argument('--expected',type=int,default=2916)
    args=p.parse_args();cache=args.root/'source_cache/brazil';out=args.root/'checkpoints/brazil_s06';out.mkdir(parents=True,exist_ok=True)
    done=set();start=time.monotonic();total=0;s06=0
    while True:
        candidates=sorted(x for x in cache.glob('RD*.dbc') if x.name not in done)
        for source in candidates:
            result,new=process(source,out);done.add(source.name)
            total+=result['source_records'];s06+=result['principal_s06']
            if len(done)%100==0:print(json.dumps(dict(files=len(done),source_records=total,s06=s06,seconds=round(time.monotonic()-start))),flush=True)
        write_json(args.root/'logs/brazil_harmonisation_status.json',dict(files=len(done),expected=args.expected,source_records=total,s06=s06,updated_at=utc(),complete=len(done)==args.expected))
        if not args.watch or len(done)>=args.expected:break
        time.sleep(5)
    print(json.dumps(dict(files=len(done),source_records=total,s06=s06,complete=len(done)==args.expected)),flush=True)

if __name__=='__main__':main()
