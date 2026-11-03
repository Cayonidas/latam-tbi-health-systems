"""Retrieve the DGIS annual SSA releases for comparison with INSP consolidation."""
from pathlib import Path
import argparse,urllib.request,urllib.parse,re,json,concurrent.futures,time
from html.parser import HTMLParser
from raw_io import download_http,sha256,write_json,utc
PAGE='http://www.dgis.salud.gob.mx/contenidos/basesdedatos/da_egresoshosp_gobmx.html'
class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            link=dict(attrs).get('href')
            if link:self.links.append(link)
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);p.add_argument('--workers',type=int,default=3);a=p.parse_args()
    with urllib.request.urlopen(PAGE,timeout=90) as response:body=response.read()
    (a.root/'docs/source_pages/dgis_annual_index.html').write_bytes(body)
    parser=Links();parser.feed(body.decode('utf-8',errors='replace'))
    found={}
    for link in parser.links:
        absolute=urllib.parse.urljoin(PAGE,link)
        match=re.search(r'ssa_egresos_(20\d\d)\.zip',absolute,re.I)
        if match and 2015<=int(match[1])<=2023:found[int(match[1])]=absolute
    write_json(a.root/'logs/dgis_annual_discovery.json',dict(page=PAGE,page_sha256=sha256(a.root/'docs/source_pages/dgis_annual_index.html'),at=utc(),links=found))
    print('Official annual links:',json.dumps(found),flush=True)
    def get(pair):
        year,url=pair
        # The official page is HTTP; use the exact linked address, including query.
        dest=a.root/'source_cache/mexico_dgis'/f'ssa_egresos_{year}.zip'
        for attempt in range(4):
            try:
                meta=download_http(url,dest);meta.update(country='mexico',series='DGIS annual SSA',year=year)
                print(json.dumps(dict(year=year,bytes=meta['bytes'],status='downloaded')),flush=True);return meta
            except Exception as exc:
                write_json(a.root/'logs'/f'dgis_{year}_download_error.json',dict(url=url,error=repr(exc),attempt=attempt,at=utc()))
                if attempt==3:return dict(year=year,url=url,status='failed',error=repr(exc))
                time.sleep(2**attempt)
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:results=list(pool.map(get,sorted(found.items())))
    write_json(a.root/'logs/dgis_annual_downloads.json',results)
    print(json.dumps(dict(found_years=sorted(found),successful=sum(x.get('status')=='downloaded' for x in results))),flush=True)
if __name__=='__main__':main()
