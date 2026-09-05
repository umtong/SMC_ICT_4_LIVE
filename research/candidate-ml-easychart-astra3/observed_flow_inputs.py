"""Small public-data reader for an unresolved economic question.

CryptoHFTData documents anonymous downloads at 60 requests/minute/IP. No account
credentials or paid endpoint is used. Binance bookDepth supplies aggregate depth
inside percentage bands, not order-level OFI. Liquidation broadcasts are sampled
by the exchange and are a lower bound, not all liquidations.
"""
from __future__ import annotations
from pathlib import Path
import io,json,time,urllib.request,urllib.parse,urllib.error
import pandas as pd
import pyarrow as pa

HERE=Path(__file__).resolve().parent
CACHE=Path('astra3_cache/observed_flow');CACHE.mkdir(parents=True,exist_ok=True)
OUT=Path('research_results/candidate_ml_easychart_astra3')
LAST_REQUEST=0.


def public_download(url,path,spacing=1.1):
    global LAST_REQUEST
    if path.exists():return path.read_bytes()
    delay=spacing-(time.monotonic()-LAST_REQUEST)
    if delay>0:time.sleep(delay)
    LAST_REQUEST=time.monotonic()
    req=urllib.request.Request(url,headers={'User-Agent':'SMC-ICT-public-research/1.0'})
    with urllib.request.urlopen(req,timeout=45) as response:
        raw=response.read()
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(raw)
    return raw


def liquidation_hour(symbol,date,hour):
    key=f'binance_futures/{date}/{hour:02d}/{symbol}_liquidations.parquet.zst'
    url='https://api.cryptohftdata.com/download?'+urllib.parse.urlencode({'file':key})
    path=CACHE/'liquidations'/date/f'{hour:02d}-{symbol}.parquet.zst'
    raw=public_download(url,path)
    # Zstd can be Parquet's internal column codec or an outer stream wrapper.
    # Identify the bytes, never silently interpret an HTTP error as market data.
    if raw[:4]==b'PAR1':return pd.read_parquet(io.BytesIO(raw))
    if raw[:4]==bytes.fromhex('28b52ffd'):
        with pa.CompressedInputStream(pa.BufferReader(raw),'zstd') as stream:
            decoded=stream.read()
        if decoded[:4]!=b'PAR1':raise ValueError('decompressed input is not Parquet')
        return pd.read_parquet(io.BytesIO(decoded))
    raise ValueError(f'unrecognized liquidation response {raw[:100]!r}')


def book_day(symbol,date):
    filename=f'{symbol}-bookDepth-{date}.zip'
    url=f'https://data.binance.vision/data/futures/um/daily/bookDepth/{symbol}/{filename}'
    path=CACHE/'bookDepth'/symbol/filename
    raw=public_download(url,path,spacing=.1)
    if raw[:2]!=b'PK':raise ValueError('depth response is not a ZIP')
    return pd.read_csv(io.BytesIO(raw),compression='zip')


def run():
    request=json.loads((HERE/'request.json').read_text())
    result={}
    for symbol,date,hour in request['liquidation_hours']:
        key=f'{symbol}:{date}:{hour}'
        try:
            frame=liquidation_hour(symbol,date,hour)
            result[key]={'rows':len(frame),'columns':{k:str(v) for k,v in frame.dtypes.items()},
                         'sample':json.loads(frame.head(5).to_json(orient='records',date_format='iso'))}
        except urllib.error.HTTPError as error:
            result[key]={'http_status':error.code,'body':error.read(500).decode(errors='replace')}
        except Exception as error:result[key]={'error':repr(error)}
    for symbol,date in request.get('book_days',[]):
        key=f'BOOK:{symbol}:{date}'
        try:
            frame=book_day(symbol,date)
            result[key]={'rows':len(frame),'columns':{k:str(v) for k,v in frame.dtypes.items()},
                         'sample':json.loads(frame.head(12).to_json(orient='records',date_format='iso')),
                         'tail':json.loads(frame.tail(2).to_json(orient='records',date_format='iso'))}
        except Exception as error:result[key]={'error':repr(error)}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'observed_flow_sample.json').write_text(json.dumps(result,indent=2))
    print('OBSERVED_FLOW_INPUT',json.dumps(result),flush=True)
