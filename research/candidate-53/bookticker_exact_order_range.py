"""Exact chronological BBO reconstruction restricted to a known timestamp range.

Monthly Binance bookTicker archives must still be read in full because their ZIP
CSV is monolithic, but events outside the requested observation interval are
never materialized or sorted. Original event timestamps, prices, sizes, update
IDs, and within-timestamp original sequence ordering are preserved exactly.
This is a storage/runtime optimization only.
"""
from __future__ import annotations

import heapq
from pathlib import Path
import tempfile

import bookticker_source_v3 as source

CHUNK_ROWS=200_000


def _write(path:Path,rows):
    rows.sort(key=lambda x:(x[0],x[1],x[2]))
    with path.open('w',encoding='ascii',newline='') as f:
        for obs,txn,seq,uid,b,bq,a,aq in rows:
            f.write(f"{obs}\t{txn}\t{seq}\t{uid}\t{b:.17g}\t{bq:.17g}\t{a:.17g}\t{aq:.17g}\n")


def _read(path:Path):
    with path.open('r',encoding='ascii') as f:
        for line in f:
            p=line.rstrip('\n').split('\t')
            yield (int(p[0]),int(p[1]),int(p[2]),int(p[3]),float(p[4]),float(p[5]),float(p[6]),float(p[7]))


def _merge(paths):
    its=[iter(_read(p)) for p in paths]; heap=[]
    for j,it in enumerate(its):
        try:r=next(it)
        except StopIteration:continue
        heapq.heappush(heap,((r[0],r[1],r[2]),j,r))
    while heap:
        _,j,r=heapq.heappop(heap); yield r
        try:n=next(its[j])
        except StopIteration:continue
        heapq.heappush(heap,((n[0],n[1],n[2]),j,n))


def iter_book_ticker_paths_exact_range(paths,start_ns:int,end_ns:int):
    previous=-1
    for path in sorted(paths,key=lambda x:x.name):
        with tempfile.TemporaryDirectory(prefix='c53-bbo-range-sort-') as td:
            root=Path(td); chunk=[]; chunks=[]; seq=0
            archive,reader=source.one_csv_reader(path)
            try:
                for row in reader:
                    original_seq=seq; seq+=1
                    if not row or not row[0] or not row[0][0].isdigit():continue
                    if len(row)<7:raise ValueError(f'bookTicker row too short in {path}')
                    txn=source.normalize_timestamp_ns(int(row[5])); obs=max(source.normalize_timestamp_ns(int(row[6])),txn)
                    if obs<start_ns or obs>=end_ns:continue
                    chunk.append((obs,txn,original_seq,int(row[0]),float(row[1]),float(row[2]),float(row[3]),float(row[4])))
                    if len(chunk)>=CHUNK_ROWS:
                        p=root/f'chunk-{len(chunks):05d}.tsv'; _write(p,chunk); chunks.append(p); chunk=[]
                if chunk:
                    p=root/f'chunk-{len(chunks):05d}.tsv'; _write(p,chunk); chunks.append(p)
            finally:archive.close()
            for obs,txn,_,uid,b,bq,a,aq in _merge(chunks):
                if obs<previous:raise ValueError(f'exact-range BBO moved backwards: {obs} < {previous}')
                previous=obs
                yield (uid,b,bq,a,aq,txn,obs)
