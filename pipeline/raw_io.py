"""Public-source downloads with resumable caches and per-file provenance."""
from pathlib import Path
import hashlib,json,datetime,time,urllib.request,urllib.error,ftplib
import concurrent.futures, shutil

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.writing');temp.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
    # On Windows, a simultaneous short-lived status reader or antivirus scan
    # may temporarily deny atomic replacement. Retry without losing the record.
    for attempt in range(40):
        try:temp.replace(path);return
        except PermissionError:
            if attempt==39:raise
            time.sleep(min(.05*(attempt+1),.5))
def ftp_connect():
    f=ftplib.FTP('ftp.datasus.gov.br',timeout=90);f.login();f.cwd('/dissemin/publicos/SIHSUS/200801_/dados');f.voidcmd('TYPE I');return f
def download_ftp(ftp,name,destination):
    dest=Path(destination);dest.parent.mkdir(parents=True,exist_ok=True);meta=dest.with_suffix(dest.suffix+'.json')
    expected=ftp.size(name);modified=ftp.sendcmd('MDTM '+name)
    url='ftp://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/dados/'+name
    if dest.exists() and meta.exists():
        old=json.loads(meta.read_text(encoding='utf-8'))
        if dest.stat().st_size==expected and old.get('mdtm')==modified and sha256(dest)==old.get('sha256'):
            return old
        raise RuntimeError('Cached source changed; retain it and select a new cache directory: '+name)
    part=dest.with_suffix(dest.suffix+'.part');offset=part.stat().st_size if part.exists() else 0
    if offset>expected:raise RuntimeError('Partial file exceeds remote size: '+name)
    if offset<expected:
        with part.open('ab' if offset else 'wb') as stream:ftp.retrbinary('RETR '+name,stream.write,blocksize=1024*1024,rest=offset or None)
    if part.stat().st_size!=expected:raise IOError('Incomplete FTP transfer: '+name)
    part.replace(dest)
    result={'country':'brasil','source_family':'SIH-SUS RD','filename':name,'url':url,'obtained_at_utc':utc(),'bytes':expected,'remote_size':expected,'mdtm':modified,'sha256':sha256(dest),'status':'downloaded'}
    write_json(meta,result);return result
def download_http(url,destination,timeout=120):
    dest=Path(destination);dest.parent.mkdir(parents=True,exist_ok=True);meta=dest.with_suffix(dest.suffix+'.json')
    if dest.exists() and meta.exists():
        old=json.loads(meta.read_text(encoding='utf-8'))
        if sha256(dest)!=old['sha256']:raise RuntimeError('Cached HTTP file hash mismatch')
        return old
    part=dest.with_suffix(dest.suffix+'.part');offset=part.stat().st_size if part.exists() else 0
    headers={'User-Agent':'TBI-source-reconstruction/1.0 (academic research)'}
    if offset:headers['Range']=f'bytes={offset}-'
    request=urllib.request.Request(url,headers=headers)
    with urllib.request.urlopen(request,timeout=timeout) as response:
        if offset and response.status!=206:offset=0
        if response.status==206:
            interval=response.headers.get('Content-Range','')
            if not interval.startswith(f'bytes {offset}-'):raise IOError('Unexpected byte range')
            total=int(interval.split('/')[-1])
        else:total=int(response.headers['Content-Length']) if response.headers.get('Content-Length') else None
        with part.open('ab' if offset else 'wb') as stream:
            while True:
                block=response.read1(4*1024*1024)
                if not block:break
                stream.write(block)
        result={'url':url,'resolved_url':response.url,'obtained_at_utc':utc(),'bytes':part.stat().st_size,'expected_bytes':total,'etag':response.headers.get('ETag'),'last_modified':response.headers.get('Last-Modified'),'status':'downloaded'}
    if total is not None and part.stat().st_size!=total:raise IOError('Incomplete HTTP transfer')
    part.replace(dest);result['sha256']=sha256(dest);write_json(meta,result);return result

def download_http_ranges(url,destination,workers=4):
    """Resume a serial prefix and cache bounded HTTP ranges against one ETag."""
    dest=Path(destination);dest.parent.mkdir(parents=True,exist_ok=True)
    meta=dest.with_suffix(dest.suffix+'.json')
    if dest.exists() and meta.exists():
        previous=json.loads(meta.read_text(encoding='utf-8'))
        if sha256(dest)!=previous['sha256']:raise RuntimeError('Cached source hash mismatch')
        return previous
    with urllib.request.urlopen(urllib.request.Request(url,method='HEAD'),timeout=120) as response:
        headers=dict(response.headers);size=int(response.headers['Content-Length']);etag=response.headers.get('ETag')
        modified=response.headers.get('Last-Modified')
    if not etag:raise RuntimeError('Range retrieval requires a stable ETag')
    part=dest.with_suffix(dest.suffix+'.part');prefix=part.stat().st_size if part.exists() else 0
    ranges=dest.parent/(dest.name+'.ranges');ranges.mkdir(exist_ok=True)
    identity=ranges/'identity.json'
    spec=dict(url=url,bytes=size,etag=etag,prefix_bytes=prefix,workers=workers)
    if identity.exists() and json.loads(identity.read_text())!=spec:
        raise RuntimeError('Source/range plan changed; preserve cache and use a new directory')
    write_json(identity,spec)
    if prefix>size:raise ValueError('Partial prefix larger than remote file')
    width=(size-prefix+workers-1)//workers
    def retrieve(bounds):
        start,end=bounds;target=ranges/(str(start)+'-'+str(end)+'.part')
        for attempt in range(8):
            offset=target.stat().st_size if target.exists() else 0
            if offset==end-start+1:return target
            if offset>end-start+1:raise ValueError('Oversized range cache')
            req=urllib.request.Request(url,headers={'Range':f'bytes={start+offset}-{end}','If-Range':etag})
            try:
                with urllib.request.urlopen(req,timeout=120) as response:
                    if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start+offset}-{end}/{size}':
                        raise ValueError('Source changed or server ignored requested range')
                    if response.headers.get('ETag')!=etag:raise ValueError('Source ETag changed during download')
                    with target.open('ab') as stream:
                        first=True;started=time.monotonic()
                        while True:
                            block=response.read1(256*1024)
                            if not block:break
                            stream.write(block)
                            if first:
                                stream.flush();print(json.dumps(dict(range_start=start,first_block_bytes=len(block),seconds=time.monotonic()-started)),flush=True);first=False
                if target.stat().st_size!=end-start+1:raise IOError('Incomplete range')
                print(json.dumps(dict(range_start=start,range_end=end,downloaded=target.stat().st_size)),flush=True)
                return target
            except (OSError,urllib.error.URLError) as exc:
                write_json(ranges/(str(start)+'.retry.json'),dict(attempt=attempt,error=repr(exc),at=utc()))
                if attempt==7:raise
                time.sleep(min(2**attempt,30))
    bounds=[(start,min(start+width-1,size-1)) for start in range(prefix,size,width)] if width else []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:pieces=list(pool.map(retrieve,bounds))
    assembled=dest.with_suffix(dest.suffix+'.assembling')
    with assembled.open('wb') as stream:
        if prefix:
            with part.open('rb') as inp:shutil.copyfileobj(inp,stream,8*1024*1024)
        for piece in pieces:
            with piece.open('rb') as inp:shutil.copyfileobj(inp,stream,8*1024*1024)
    if assembled.stat().st_size!=size:raise IOError('Assembled size mismatch')
    assembled.replace(dest)
    result=dict(url=url,obtained_at_utc=utc(),bytes=size,expected_bytes=size,etag=etag,last_modified=modified,
                sha256=sha256(dest),status='downloaded',method='ETag-locked HTTP ranges',reused_prefix_bytes=prefix)
    write_json(meta,result);return result
