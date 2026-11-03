"""Retrieve the versioned DGIS annual SSA catalogues and data descriptors."""
from pathlib import Path
import argparse,re,urllib.parse,urllib.request,concurrent.futures
from raw_io import download_http,write_json

PAGE='http://www.dgis.salud.gob.mx/contenidos/basesdedatos/da_egresoshosp_gobmx.html'
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent);a=p.parse_args();root=a.root
    with urllib.request.urlopen(PAGE,timeout=90) as response:body=response.read()
    pages=root/'docs/source_pages';pages.mkdir(parents=True,exist_ok=True)
    (pages/'dgis_annual_index.html').write_bytes(body)
    links=[]
    for link in re.findall(r'href="([^"]+)"',body.decode('utf-8',errors='replace')):
        if re.search(r'ssa_(Catalogos|Descriptores)',link,re.I) and re.search(r'201[4-9]|202[0-3]',link):links.append(urllib.parse.urljoin(PAGE,link))
    def get(url):
        target=pages/'dgis_dictionaries'/Path(urllib.parse.urlsplit(url).path).name
        meta=download_http(url,target);print(target.name,meta['bytes'],flush=True);return meta
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(get,sorted(set(links))))
    write_json(root/'reports/dgis_dictionary_downloads.json',results)
if __name__=='__main__':main()
