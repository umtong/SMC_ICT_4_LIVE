"""Collect only the prospectively locked v66 first BTC week direct data.

This script downloads immutable public archives only. It does not generate
signals, fills, positions, PnL or NAV.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json, time, urllib.request, zipfile

SYMBOL="BTCUSDT"
EVALUATION_START=date(2024,10,28)
DATA_START=EVALUATION_START-timedelta(days=2)
DATA_END=EVALUATION_START+timedelta(days=7)
ROOT=Path("inputs/v66-first-week")
OUT=ROOT/"artifacts/candidate-02-v66-first-week-data"
SOURCES={
    "aggTrades": "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT",
    "bookDepth": "https://data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT",
    "klines": "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m",
}

def target(kind:str, day:date) -> tuple[str,Path]:
    ds=day.isoformat()
    if kind=="klines":
        name=f"{SYMBOL}-1m-{ds}.zip"
        path=ROOT/".cache/candidate-02/v66-first-week/binance_1m"/name
    else:
        name=f"{SYMBOL}-{kind}-{ds}.zip"
        path=ROOT/"direct"/kind/name
    return f"{SOURCES[kind]}/{name}", path

def fetch(kind:str, day:date) -> dict[str,object]:
    url,path=target(kind,day)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    last=None
    for attempt in range(6):
        try:
            request=urllib.request.Request(url,headers={"User-Agent":"candidate-02-research/1.0"})
            with urllib.request.urlopen(request,timeout=180) as response, tmp.open("wb") as stream:
                while chunk:=response.read(1<<20): stream.write(chunk)
            with zipfile.ZipFile(tmp) as archive:
                members=[n for n in archive.namelist() if not n.endswith("/")]
                if len(members)!=1 or archive.getinfo(members[0]).file_size<=0:
                    raise RuntimeError(f"invalid archive {url}")
            tmp.replace(path)
            return {"kind":kind,"date":day.isoformat(),"url":url,"path":str(path),
                    "size":path.stat().st_size,"sha256":sha256(path.read_bytes()).hexdigest()}
        except Exception as exc:
            last=exc; tmp.unlink(missing_ok=True)
            if attempt<5: time.sleep(min(20,2**attempt))
    raise RuntimeError(f"failed {url}: {last}")

def main() -> None:
    days=[]; day=DATA_START
    while day<=DATA_END: days.append(day); day+=timedelta(days=1)
    jobs=[(kind,day) for kind in SOURCES for day in days]
    records=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(fetch,*job):job for job in jobs}
        for future in as_completed(futures):
            record=future.result(); records.append(record)
            print(record["kind"],record["date"],flush=True)
    records.sort(key=lambda x:(str(x["kind"]),str(x["date"])))
    counts={kind:sum(r["kind"]==kind for r in records) for kind in SOURCES}
    if any(counts[kind]!=len(days) for kind in SOURCES):
        raise RuntimeError(f"incomplete direct data: {counts}")
    OUT.mkdir(parents=True,exist_ok=True)
    manifest={"family":"candidate-02-v66-direct-depth-flow-auction-state",
              "evaluation_start":EVALUATION_START.isoformat(),"data_start":DATA_START.isoformat(),
              "data_end_inclusive":DATA_END.isoformat(),"counts":counts,"files":records}
    (OUT/"raw_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    (OUT/"STATUS_DIRECT_DATA_READY").write_text(EVALUATION_START.isoformat()+"\n",encoding="utf-8")
if __name__=="__main__": main()
