"""Download immutable Binance USD-M 1-minute klines for the locked cross-asset screen."""
from __future__ import annotations
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json, time, urllib.request, zipfile

SYMBOLS=("ETHUSDT","SOLUSDT","XRPUSDT")
WEEKS=("2024-12-23","2022-04-25","2024-07-08","2025-08-25","2023-11-27","2021-04-19")
ROOT=Path('.cache/candidate-02/cross-asset')
BASE='https://data.binance.vision/data/futures/um/daily/klines'

def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size>100:
        return
    tmp=path.with_suffix(path.suffix+'.tmp')
    for attempt in range(5):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'candidate-02-research/1.0'})
            with urllib.request.urlopen(req,timeout=60) as r, tmp.open('wb') as f:
                while chunk:=r.read(1<<20):f.write(chunk)
            tmp.replace(path);return
        except Exception:
            tmp.unlink(missing_ok=True)
            if attempt==4:raise
            time.sleep(2**attempt)

def main() -> None:
    records=[]
    for symbol in SYMBOLS:
        days=set()
        for value in WEEKS:
            start=date.fromisoformat(value)-timedelta(days=2)
            end=date.fromisoformat(value)+timedelta(days=7)
            d=start
            while d<=end:days.add(d);d+=timedelta(days=1)
        for d in sorted(days):
            name=f'{symbol}-1m-{d.isoformat()}.zip'
            url=f'{BASE}/{symbol}/1m/{name}'
            path=ROOT/symbol/name
            download(url,path)
            with zipfile.ZipFile(path) as z:
                members=[x for x in z.namelist() if not x.endswith('/')]
                if len(members)!=1 or z.getinfo(members[0]).file_size<=0:
                    raise RuntimeError(f'invalid archive {path}')
            digest=sha256(path.read_bytes()).hexdigest()
            records.append({'symbol':symbol,'date':d.isoformat(),'path':str(path),'size':path.stat().st_size,'sha256':digest,'url':url})
            print(symbol,d,flush=True)
    manifest={'symbols':SYMBOLS,'weeks':WEEKS,'files':records,'file_count':len(records)}
    Path('artifacts/candidate-02-cross').mkdir(parents=True,exist_ok=True)
    Path('artifacts/candidate-02-cross/manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')

if __name__=='__main__':main()
