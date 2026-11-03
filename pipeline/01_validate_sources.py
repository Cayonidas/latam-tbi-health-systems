"""Verify acquired raw bytes and export a portable acquisition manifest."""
from pathlib import Path
import argparse,json,csv,datetime
from raw_io import sha256,write_json,utc

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--allow-partial',action='store_true');p.add_argument('--allow-source-version-change',action='store_true');p.add_argument('--annual-only',action='store_true',help='Add DGIS checks to the previously verified primary manifest without rereading unchanged primary sources');a=p.parse_args()
    entries=[];checks=[]
    if a.annual_only:
        with (a.root/'SOURCE_FILES_MANIFEST.csv').open(encoding='utf-8',newline='') as stream:entries=[x for x in csv.DictReader(stream) if x['series'] not in ['DGIS annual SSA','DGIS official dictionary/descriptor']]
        for entry in entries:entry['bytes']=int(entry['bytes'])
    sources=[('mexico','mexico_dgis','ssa_egresos_*.zip')]
    if not a.annual_only:sources=[('brasil','brazil','RD*.dbc'),('mexico','mexico','Base_Saeh_Egresos_2008_2024.zip')]+sources
    for country,folder,pattern in sources:
        files=sorted((a.root/'source_cache'/folder).glob(pattern))
        for path in files:
            meta=json.loads(path.with_suffix(path.suffix+'.json').read_text(encoding='utf-8'))
            digest=sha256(path);size=path.stat().st_size
            if digest!=meta['sha256'] or size!=meta['bytes']:raise ValueError('Source hash/size mismatch: '+path.name)
            obtained=meta.get('obtained_at_utc')
            basis='retrieval completion recorded by downloader'
            if obtained is None:
                obtained=datetime.datetime.fromtimestamp(path.stat().st_mtime,datetime.timezone.utc).isoformat()
                basis='local acquisition file modification time; initial discovery download'
            entries.append(dict(country=country,filename=path.name,relative_path=path.relative_to(a.root).as_posix(),
                url=meta['url'],obtained_at_utc=obtained,retrieval_timestamp_basis=basis,bytes=size,sha256=digest,
                remote_modified=meta.get('mdtm',meta.get('last_modified','')),etag=meta.get('etag',''),
                series='SIH-SUS RD' if country=='brasil' else ('DGIS annual SSA' if folder=='mexico_dgis' else 'MEX-INSP-SAEH-SSA-2008-2024'),status='verified'))
    for path in sorted((a.root/'docs/source_pages/dgis_dictionaries').glob('*')):
        if path.suffix.lower() not in ['.zip','.xlsx']:continue
        meta=json.loads(path.with_suffix(path.suffix+'.json').read_text(encoding='utf-8'));digest=sha256(path);size=path.stat().st_size
        if digest!=meta['sha256'] or size!=meta['bytes']:raise ValueError('Dictionary checksum mismatch')
        entries.append(dict(country='mexico',filename=path.name,relative_path=path.relative_to(a.root).as_posix(),url=meta['url'],obtained_at_utc=meta['obtained_at_utc'],retrieval_timestamp_basis='retrieval completion recorded by downloader',bytes=size,sha256=digest,remote_modified=meta.get('last_modified',''),etag=meta.get('etag',''),series='DGIS official dictionary/descriptor',status='verified'))
    counts={c:sum(x['country']==c for x in entries) for c in ['brasil','mexico']}
    complete=counts['brasil']==2916 and sum(x['series']=='MEX-INSP-SAEH-SSA-2008-2024' for x in entries)==1
    reference=Path(__file__).resolve().parent.parent/'publication_reference/SOURCE_FILES_MANIFEST.csv'
    if reference.exists():
        with reference.open(encoding='utf-8',newline='') as stream:expected={x['filename']:x for x in csv.DictReader(stream)}
        differences=[dict(filename=x['filename'],published_sha256=expected[x['filename']]['sha256'],downloaded_sha256=x['sha256']) for x in entries if x['filename'] in expected and x['sha256']!=expected[x['filename']]['sha256']]
        write_json(a.root/'execution/source_version_comparison.json',dict(compared=sum(x['filename'] in expected for x in entries),changed= differences,all_compared_bytes_identical=not differences))
        if differences and not a.allow_source_version_change:raise ValueError('Current official source bytes differ from the publication manifest; review source_version_comparison.json before explicitly allowing a new-source analysis')
    with (a.root/'SOURCE_FILES_MANIFEST.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(entries[0]));writer.writeheader();writer.writerows(entries)
    (a.root/'SOURCE_CHECKSUMS.sha256').write_text(''.join(x['sha256']+'  '+x['relative_path']+'\n' for x in entries),encoding='utf-8')
    report=dict(verified_at=utc(),counts=counts,complete_expected_acquisition=complete,dgis_annual_files=sum(x['series']=='DGIS annual SSA' for x in entries),primary_hashes_reused_from_completed_validation=a.annual_only,total_bytes=sum(x['bytes'] for x in entries))
    write_json(a.root/'execution/source_validation.json',report);print(json.dumps(report),flush=True)
    if not complete and not a.allow_partial:raise RuntimeError('Raw acquisition incomplete')
if __name__=='__main__':main()
