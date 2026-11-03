"""Read the official SSA consolidated source completely in bounded CSV chunks."""
from pathlib import Path
import argparse,json,time,shutil,io,hashlib
from collections import Counter
import pandas as pd
import zipfile64.zipfile as zipfile
from raw_io import sha256,write_json,utc
from cohort_rules import diagnosis,numeric

KEEP=['id_insp','id','anio_base','clues','egreso','ingre','dias_esta','cveedad','edad','sexo',
      'cedocve','entidad','motegre','afecprin','diag_ini','proced','cluesproce','cluesrefer',
      'sa_inten','sa_interm','tipserv','causaext','traumat','mes_estadi']

class HashingReader(io.RawIOBase):
    def __init__(self,source):self.source=source;self.digest=hashlib.sha256();self.bytes=0
    def readable(self):return True
    def readinto(self,buffer):
        block=self.source.read(len(buffer));n=len(block);buffer[:n]=block
        self.digest.update(block);self.bytes+=n;return n

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--wait',action='store_true');p.add_argument('--chunksize',type=int,default=250000);a=p.parse_args()
    source=a.root/'source_cache/mexico/Base_Saeh_Egresos_2008_2024.zip';metadata=source.with_suffix('.zip.json')
    if a.wait:
        while not source.exists() or not metadata.exists():time.sleep(5)
    digest=sha256(source)
    if digest!=json.loads(metadata.read_text())['sha256']:raise ValueError('Official archive checksum mismatch')
    status=a.root/'logs/mexico_harmonisation_status.json'
    if status.exists():
        old=json.loads(status.read_text())
        if old.get('complete') and old.get('source_sha256')==digest:
            for name,h in old['checkpoint_hashes'].items():
                if sha256(a.root/'checkpoints/mexico_s06'/name)!=h:raise ValueError('Mexico checkpoint changed')
            print('Verified completed Mexico source harmonisation');return
    inner=source.parent/'egresos2008_2024.zip';imeta=inner.with_suffix('.zip.json')
    with zipfile.ZipFile(source) as outer:
        info=outer.getinfo(inner.name)
        if not inner.exists() or not imeta.exists() or inner.stat().st_size!=info.file_size or sha256(inner)!=json.loads(imeta.read_text())['sha256']:
            partial=inner.with_suffix('.zip.extracting')
            with outer.open(info) as src,partial.open('wb') as dst:shutil.copyfileobj(src,dst,8*1024*1024)
            partial.replace(inner);write_json(imeta,dict(bytes=inner.stat().st_size,sha256=sha256(inner),parent_sha256=digest,parent_member=info.filename,crc32=info.CRC))
        # Read the dictionary fully as well; outer ZipExtFile verifies CRC.
        dictionary=outer.read('Catalogo_saeh_Egresos_2008_2024.xlsx')
        target=a.root/'docs/source_pages/Catalogo_saeh_Egresos_2008_2024.xlsx';target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(dictionary)
    out=a.root/'checkpoints/mexico_s06';out.mkdir(parents=True,exist_ok=True)
    total=0;selected=0;byyear=Counter();s06year=Counter();age_units=Counter();hashes={};started=time.monotonic()
    with zipfile.ZipFile(inner) as archive:
        members=[x for x in archive.infolist() if x.filename.lower().endswith('.csv')]
        if len(members)!=1:raise ValueError('Unexpected inner archive CSV membership')
        info=members[0]
        with archive.open(info) as stream:
            hashed=HashingReader(stream);buffered=io.BufferedReader(hashed,buffer_size=1024*1024)
            # The CSV contains non-UTF-8 bytes (observed 0xC7 in a later block).
            # Latin-1 preserves every source byte; no replacement/ignore decoding.
            reader=pd.read_csv(buffered,dtype='string',usecols=KEEP,chunksize=a.chunksize,encoding='latin-1',keep_default_na=False,on_bad_lines='error')
            for index,chunk in enumerate(reader):
                years=numeric(chunk.anio_base)
                if years.isna().any():raise ValueError('Unknown source year; cannot silently assign')
                byyear.update({str(int(k)):int(v) for k,v in years.value_counts().items()})
                s06=diagnosis(chunk.afecprin).str.startswith('S06',na=False)
                selected_mask=years.between(2015,2023)&s06
                sub=chunk.loc[selected_mask].copy();sub['source_file']=info.filename
                sub['source_record_number']=chunk.index[selected_mask]+1
                total+=len(chunk);selected+=len(sub)
                s06year.update({str(int(k)):int(v) for k,v in years[s06].value_counts().items()})
                age_units.update(sub.cveedad.value_counts().to_dict())
                path=out/f'part_{index:04d}.parquet';sub.to_parquet(path,index=False);hashes[path.name]=sha256(path)
                record=dict(complete=False,source_sha256=digest,source_records=total,principal_s06_2015_2023=selected,records_by_year=dict(byyear),s06_by_year=dict(s06year),chunks=index+1,updated_at=utc())
                write_json(status,record)
                if index%10==0:print(json.dumps(dict(records=total,s06_2015_2023=selected,chunks=index+1,seconds=round(time.monotonic()-started))),flush=True)
            # Ensure stream EOF/CRC reached even if CSV parser buffered its final row.
            if buffered.read(1):raise ValueError('Unread data after CSV parsing')
            if hashed.bytes!=info.file_size:raise ValueError('CSV uncompressed byte count mismatch')
        record.update(complete=True,csv_member=info.filename,csv_bytes=hashed.bytes,csv_sha256=hashed.digest.hexdigest(),csv_crc32=info.CRC,crc_verified=True,
                      encoding='latin-1 (lossless byte mapping)',chunksize=a.chunksize,checkpoint_hashes=hashes,s06_age_unit_counts=dict(age_units),seconds=time.monotonic()-started,updated_at=utc())
        write_json(status,record)
    print(json.dumps({k:v for k,v in record.items() if k!='checkpoint_hashes'}),flush=True)
if __name__=='__main__':main()
