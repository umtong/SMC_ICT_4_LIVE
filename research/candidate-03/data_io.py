"""Causal Binance USD-M kline ingestion and integrity checks."""
from __future__ import annotations
import csv
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from model import Bar

@dataclass(frozen=True, slots=True)
class DataQuality:
    files:tuple[str,...]; sha256:tuple[str,...]; rows:int; first_open_time_ns:int; last_close_time_ns:int
    duplicate_rows:int; missing_minutes:int; non_monotonic_rows:int
    @property
    def valid(self)->bool:
        return self.rows>0 and self.duplicate_rows==0 and self.missing_minutes==0 and self.non_monotonic_rows==0

def _sha(path:Path)->str:
    digest=sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024): digest.update(chunk)
    return digest.hexdigest()

def _is_header(row:Sequence[str])->bool:
    return bool(row) and any(token.lower() in {'open_time','open time','opentime'} for token in row[:2])

def _parse(row:Sequence[str])->Bar:
    if len(row)<11: raise ValueError(f'expected >=11 columns, got {len(row)}')
    return Bar(
        open_time_ns=int(row[0])*1_000_000, close_time_ns=int(row[6])*1_000_000,
        open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]),
        volume=float(row[5]), quote_volume=float(row[7]), trade_count=int(row[8]), taker_buy_volume=float(row[9]),
    )

def iter_binance_klines(path:str|Path)->Iterator[Bar]:
    with Path(path).open('r',encoding='utf-8',newline='') as stream:
        reader=csv.reader(stream); first=next(reader,None)
        if first is None: return
        if not _is_header(first): yield _parse(first)
        for row in reader:
            if row: yield _parse(row)

def load_klines(paths:Iterable[str|Path])->tuple[list[Bar],DataQuality]:
    path_list=tuple(sorted((Path(p) for p in paths),key=lambda p:p.as_posix()))
    if not path_list: raise ValueError('at least one kline file is required')
    bars:list[Bar]=[]; hashes:list[str]=[]
    for path in path_list:
        if not path.is_file(): raise FileNotFoundError(path)
        hashes.append(_sha(path)); bars.extend(iter_binance_klines(path))
    bars.sort(key=lambda b:b.open_time_ns)
    output:list[Bar]=[]; duplicates=0; nonmono=0; missing=0; last:int|None=None
    for bar in bars:
        if last is not None:
            if bar.open_time_ns==last:
                duplicates+=1
                if bar!=output[-1]: raise ValueError(f'conflicting duplicate at {last}')
                continue
            if bar.open_time_ns<last: nonmono+=1
            gap=(bar.open_time_ns-last)//60_000_000_000
            if gap>1: missing+=int(gap-1)
        output.append(bar); last=bar.open_time_ns
    if not output: raise ValueError('no kline rows loaded')
    quality=DataQuality(tuple(p.as_posix() for p in path_list),tuple(hashes),len(output),output[0].open_time_ns,
                        output[-1].close_time_ns,duplicates,missing,nonmono)
    if not quality.valid: raise ValueError(f'kline data failed integrity checks: {quality}')
    return output,quality
