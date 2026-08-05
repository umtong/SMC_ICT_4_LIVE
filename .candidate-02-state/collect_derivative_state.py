"""Collect immutable USD-M positioning and basis data for candidate-02.

The six weeks were fixed before these files were downloaded.  The collector
uses Binance Vision only and records SHA-256 for every archive.
"""
from __future__ import annotations
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json, time, urllib.request, zipfile

SYMBOLS=("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT")
WEEKS=("2024-12-23","2022-04-25","2024-07-08","2025-08-25","2023-11-27","2021-04-19")
ROOT=Path('.cache/candidate-02/derivative-state')
BASE='https://data.binance.vision/data/futures/um'

def fetch(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.stat().st_size>50:return
    tmp=path.with_suffix(path.suffix+'.tmp')
    last=None
    for attempt in range(6):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'candidate-02-research/1.0'})
            with urllib.request.urlopen(req,timeout=90) as r,tmp.open('wb') as f:
                while chunk:=r.read(1<<20):f.write(chunk)
            with zipfile.ZipFile(tmp) as z:
                files=[x for x in z.namelist() if not x.endswith('/')]
                if len(files)!=1 or z.getinfo(files[0]).file_size<=0:
                    raise RuntimeError(f'invalid archive {url}')
            tmp.replace(path);return
        except Exception as exc:
            last=exc;tmp.unlink(missing_ok=True)
            if attempt==5:break
            time.sleep(min(20,2**attempt))
    raise RuntimeError(f'failed {url}: {last}')

def record(kind: str,symbol: str,key: str,path: Path,url: str):
    return {'kind':kind,'symbol':symbol,'key':key,'path':str(path),'size':path.stat().st_size,'sha256':sha256(path.read_bytes()).hexdigest(),'url':url}

def main() -> None:
    days=set();months=set()
    for w in WEEKS:
        start=date.fromisoformat(w)-timedelta(days=2);end=date.fromisoformat(w)+timedelta(days=7)
        d=start
        while d<=end:
            days.add(d);months.add((d.year,d.month));d+=timedelta(days=1)
    rows=[]
    for symbol in SYMBOLS:
        for d in sorted(days):
            ds=d.isoformat()
            name=f'{symbol}-metrics-{ds}.zip';url=f'{BASE}/daily/metrics/{symbol}/{name}';path=ROOT/'metrics'/symbol/name
            fetch(url,path);rows.append(record('metrics',symbol,ds,path,url));print('metrics',symbol,ds,flush=True)
            name=f'{symbol}-1m-{ds}.zip';url=f'{BASE}/daily/premiumIndexKlines/{symbol}/1m/{name}';path=ROOT/'premiumIndexKlines'/symbol/name
            fetch(url,path);rows.append(record('premiumIndexKlines',symbol,ds,path,url));print('premium',symbol,ds,flush=True)
        for y,m in sorted(months):
            ms=f'{y:04d}-{m:02d}';name=f'{symbol}-fundingRate-{ms}.zip';url=f'{BASE}/monthly/fundingRate/{symbol}/{name}';path=ROOT/'fundingRate'/symbol/name
            fetch(url,path);rows.append(record('fundingRate',symbol,ms,path,url));print('funding',symbol,ms,flush=True)
    out=Path('artifacts/candidate-02-state');out.mkdir(parents=True,exist_ok=True)
    manifest={'source':'Binance Vision USD-M','symbols':SYMBOLS,'weeks':WEEKS,'file_count':len(rows),'files':rows}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')

if __name__=='__main__':main()
