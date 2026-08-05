#!/usr/bin/env python3
"""Download and checksum one official Binance Vision USD-M monthly kline file."""
from __future__ import annotations
import argparse,hashlib,urllib.request,zipfile
from pathlib import Path
BASE='https://data.binance.vision/data/futures/um/monthly/klines'
def sha(path:Path)->str:
 d=hashlib.sha256();
 with path.open('rb') as f:
  while chunk:=f.read(1024*1024):d.update(chunk)
 return d.hexdigest()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--symbol',default='BTCUSDT');p.add_argument('--interval',default='1m')
 p.add_argument('--month',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 name=f'{a.symbol}-{a.interval}-{a.month}.zip';url=f'{BASE}/{a.symbol}/{a.interval}/{name}';zip_path=a.output/name
 urllib.request.urlretrieve(url,zip_path);checksum=urllib.request.urlopen(url+'.CHECKSUM').read().decode().strip().split()[0]
 actual=sha(zip_path)
 if actual.lower()!=checksum.lower():raise RuntimeError(f'checksum mismatch: {actual} != {checksum}')
 with zipfile.ZipFile(zip_path) as archive:archive.extractall(a.output)
 print(a.output/name.replace('.zip','.csv'));return 0
if __name__=='__main__':raise SystemExit(main())
