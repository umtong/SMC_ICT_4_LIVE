#!/usr/bin/env python3
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
import bookticker_source_v3 as source
URL='https://data.binance.vision/data/futures/um/monthly/bookTicker/SOLUSDT/SOLUSDT-bookTicker-2024-04.zip'
r=source.download_verified(URL,Path('.cache/c53-bbo-schema'),'book_ticker_monthly')
archive,reader=source.one_csv_reader(Path(r.local_path))
rows=[]; numeric=0; samples=[]; min5=None;max5=None;min6=None;max6=None
try:
    for i,row in enumerate(reader):
        if i<8: samples.append(row)
        if row and row[0] and row[0][0].isdigit():
            numeric+=1
            if len(row)>=7:
                try:
                    a=int(row[5]); b=int(row[6]); min5=a if min5 is None else min(min5,a); max5=a if max5 is None else max(max5,a); min6=b if min6 is None else min(min6,b); max6=b if max6 is None else max(max6,b)
                except Exception: pass
        if i>=500000: break
finally: archive.close()
print(json.dumps({'source':asdict(r),'sample_rows':samples,'numeric_first500k':numeric,'min_col5':min5,'max_col5':max5,'min_col6':min6,'max_col6':max6},indent=2))
