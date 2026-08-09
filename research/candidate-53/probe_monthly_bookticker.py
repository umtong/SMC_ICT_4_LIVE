#!/usr/bin/env python3
from __future__ import annotations
import json, urllib.request, urllib.error
symbols=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT']
months=['2024-01','2024-03','2024-04','2024-06','2025-01','2025-06','2026-01','2026-06']
base='https://data.binance.vision/data/futures/um/monthly/bookTicker'
out={}
for s in symbols:
    out[s]={}
    for m in months:
        u=f'{base}/{s}/{s}-bookTicker-{m}.zip.CHECKSUM'
        req=urllib.request.Request(u,headers={'User-Agent':'SMC-ICT-4-research'})
        try:
            with urllib.request.urlopen(req,timeout=20) as r:
                out[s][m]={'status':int(r.status),'text':r.read(100).decode('utf-8','replace')}
        except urllib.error.HTTPError as e:
            out[s][m]={'status':int(e.code)}
        except Exception as e:
            out[s][m]={'status':'ERROR','error':repr(e)}
print(json.dumps(out,indent=2,sort_keys=True))
