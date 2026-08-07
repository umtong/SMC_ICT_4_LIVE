"""Recollect only revealed v66 one-minute bars for the locked v67 control."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from pathlib import Path
import urllib.request,zipfile,time
START=date(2024,10,26); END=date(2024,11,4)
ROOT=Path("inputs/v67-control/.cache/candidate-02/v67-control/binance_1m")
BASE="https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m"
def fetch(day:date)->None:
    ds=day.isoformat(); name=f"BTCUSDT-1m-{ds}.zip"; url=f"{BASE}/{name}"; path=ROOT/name
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(".zip.tmp"); last=None
    for attempt in range(6):
        try:
            request=urllib.request.Request(url,headers={"User-Agent":"candidate-02-research/1.0"})
            with urllib.request.urlopen(request,timeout=120) as response,tmp.open("wb") as stream:
                while chunk:=response.read(1<<20):stream.write(chunk)
            with zipfile.ZipFile(tmp) as archive:
                if len([n for n in archive.namelist() if not n.endswith("/")])!=1:raise RuntimeError(url)
            tmp.replace(path);print(ds,flush=True);return
        except Exception as exc:
            last=exc;tmp.unlink(missing_ok=True);time.sleep(min(20,2**attempt))
    raise RuntimeError(f"failed {url}: {last}")
def main()->None:
    days=[];d=START
    while d<=END:days.append(d);d+=timedelta(days=1)
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(fetch,days))
if __name__=="__main__":main()
