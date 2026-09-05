"""Use already-acquired hourly force-order files for short account experiments.

Hour coverage is read from archive names or acquisition records, never from a
liquidation time series' first/last observations. Unknown formats fail explicitly.
This adapter does not download data, infer missing force orders, or trade live.
"""
from pathlib import Path
import json
import re
import sys
import pandas as pd
import pyarrow.parquet as pq

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from forced_inventory_experiment import run, SYMBOLS, MINUTE, timestamp


def named_hour(text):
    # Supported successful hourly archive conventions: YYYY-MM-DD/HH,
    # YYYY-MM-DD_HH, YYYY-MM-DDTHH, or YYYY/MM/DD/HH.
    m=re.search(r'(20\d{2})[-/](\d{2})[-/](\d{2})[T_/ -](\d{2})(?:[^0-9]|$)',text)
    if m is None:
        return None
    y,mo,d,h=m.groups()
    return f'{y}-{mo}-{d}T{h}:00:00Z'


def run_acquired():
    workspace=Path.cwd()
    manifest={'complete_hours':{s:[] for s in SYMBOLS},'files':[]}
    records={}
    # Existing acquisition records can name hashed local paths. Only records
    # with an actual local file and no reported error may supply coverage.
    def visit(value):
        if isinstance(value,list):
            for x in value:visit(x)
        elif isinstance(value,dict):
            path=value.get('path') or value.get('file') or value.get('local_path')
            url=str(value.get('url',''))
            if path and not value.get('error'):
                p=Path(path)
                if p.exists():
                    hour=named_hour(str(p)) or named_hour(url)
                    symbol=value.get('symbol') or next((s for s in SYMBOLS if s in str(p)+' '+url),None)
                    if hour and symbol in SYMBOLS:records[str(p.resolve())]=(symbol,hour)
            for x in value.values():
                if isinstance(x,(dict,list)):visit(x)
    roots=[workspace/'astra3_cache',workspace/'observed_market',workspace/'observed_flow',workspace/'received_flow']
    roots=[p for p in roots if p.exists()]
    for root in roots:
        for p in root.rglob('*.json'):
            if p.stat().st_size>10_000_000:continue
            try:visit(json.loads(p.read_text()))
            except (ValueError,UnicodeError):continue
    required={'received_time','event_time','symbol','side','average_price','last_filled_quantity'}
    for root in roots:
        for p in root.rglob('*.parquet'):
            if not required<=set(pq.read_schema(p).names):continue
            identity=records.get(str(p.resolve()))
            if identity is None:
                hour=named_hour(str(p))
                symbol=next((s for s in SYMBOLS if s in str(p)),None)
                if hour and symbol:identity=(symbol,hour)
            if identity is None:
                # A probe with sample rows is not a complete hourly archive.
                continue
            symbol,hour=identity
            manifest['complete_hours'][symbol].append(hour)
            manifest['files'].append(str(p))
    available=None
    for s in SYMBOLS:
        hours={timestamp(x) for x in manifest['complete_hours'][s]}
        available=hours if available is None else available & hours
    if not available:
        raise RuntimeError('No four-market acquisition coverage found. Supply the successful collector manifest explicitly to forced_inventory_experiment.py; do not synthesize coverage.')
    hours=sorted(available)
    segments=[];start=last=hours[0]
    for h in hours[1:]:
        if h!=last+60*MINUTE:
            segments.append((start,last+60*MINUTE));start=h
        last=h
    segments.append((start,last+60*MINUTE))
    output=workspace/'research_results/candidate_ml_easychart_astra3/forced_inventory_v24'
    output.mkdir(parents=True,exist_ok=True)
    mp=output/'acquired_hours.json';mp.write_text(json.dumps(manifest,indent=2))
    results=[]
    for a,z in segments:
        # Two warmup days and at least three diagnostic days, no profit-dependent
        # selection of dates and no concatenation of disjoint account returns.
        day=1440*MINUTE
        a=((a+day-1)//day)*day;z=(z//day)*day
        if z-a<5*day:continue
        b=a+2*day
        fmt=lambda t:pd.Timestamp(t,unit='ns',tz='UTC').strftime('%Y-%m-%d')
        destination=output/f'{fmt(b)}_{fmt(z)}'
        results.extend(run(mp,fmt(b),fmt(z),fmt(a),destination))
    if not results:
        raise RuntimeError('Acquired segments contain no complete short evaluation after warmup')
    (output/'all_short_results.json').write_text(json.dumps(results,indent=2,allow_nan=False))
    print(json.dumps(results,indent=2,allow_nan=False),flush=True)


if __name__=='__main__':run_acquired()
