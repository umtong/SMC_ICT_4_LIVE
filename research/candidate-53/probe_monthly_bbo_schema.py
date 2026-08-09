#!/usr/bin/env python3
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import bookticker_source_v3 as source
URL='https://data.binance.vision/data/futures/um/monthly/bookTicker/SOLUSDT/SOLUSDT-bookTicker-2024-04.zip'
r=source.download_verified(URL,Path('.cache/c53-bbo-schema'),'book_ticker_monthly')
archive,reader=source.one_csv_reader(Path(r.local_path))
count=0; samples=[]; min5=None;max5=None;min6=None;max6=None; last_rows=[]
try:
    for i,row in enumerate(reader):
        if i<8:samples.append(row)
        if row and row[0] and row[0][0].isdigit() and len(row)>=7:
            count+=1
            try:
                a=int(row[5]); b=int(row[6]); min5=a if min5 is None else min(min5,a); max5=a if max5 is None else max(max5,a); min6=b if min6 is None else min(min6,b); max6=b if max6 is None else max(max6,b)
                last_rows=(last_rows+[row])[-5:]
            except Exception:pass
finally:archive.close()
def iso(x):
    if x is None:return None
    divisor=1000 if x<10**14 else 1_000_000
    return datetime.fromtimestamp(x/divisor,tz=timezone.utc).isoformat()
print(json.dumps({'source':asdict(r),'sample_rows':samples,'last_rows':last_rows,'numeric_rows':count,'min_col5':min5,'max_col5':max5,'min_col6':min6,'max_col6':max6,'min_col5_iso':iso(min5),'max_col5_iso':iso(max5),'min_col6_iso':iso(min6),'max_col6_iso':iso(max6)},indent=2))
