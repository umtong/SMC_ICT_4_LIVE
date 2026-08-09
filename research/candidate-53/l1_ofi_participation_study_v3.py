#!/usr/bin/env python3
"""Bounded-reorder compatibility layer for the frozen L1 OFI study.

Some preserved Binance USD-M bookTicker daily files contain rows which are not
strictly ordered by observed timestamp.  Candidate16's reusable parser correctly
rejected those files, but the archive itself remains usable if records are
ordered by their exchange/observed timestamps before OFI is accumulated.

This wrapper changes no economic rule.  It installs a 120-second min-heap reorder
buffer around each daily archive.  Records may arrive out of file order, but no
record is emitted until its timestamp is at least two minutes behind the maximum
timestamp seen in that same archive.  A record arriving more than that behind an
already emitted record raises instead of being silently accepted.  Daily files
are still processed in filename/date order and the final emitted stream remains
strictly non-decreasing.
"""
from __future__ import annotations

import heapq
import runpy
from pathlib import Path

import bookticker_source_v3 as source
import topbook_features

REORDER_NS = 120 * 1_000_000_000


def iter_book_ticker_paths_reordered(paths):
    previous_emitted_ns = -1
    for path in sorted(paths, key=lambda item: item.name):
        archive, reader = source.one_csv_reader(path)
        heap = []
        sequence = 0
        max_seen_ns = -1
        try:
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue
                if len(row) < 7:
                    raise ValueError(f"bookTicker row too short in {path}")
                transaction_ns = source.normalize_timestamp_ns(int(row[5]))
                observed_ns = max(source.normalize_timestamp_ns(int(row[6])), transaction_ns)
                max_seen_ns = max(max_seen_ns, observed_ns)
                record = (
                    int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                    transaction_ns, observed_ns,
                )
                heapq.heappush(heap, (observed_ns, sequence, record))
                sequence += 1
                cutoff = max_seen_ns - REORDER_NS
                while heap and heap[0][0] <= cutoff:
                    ts, _, item = heapq.heappop(heap)
                    if ts < previous_emitted_ns:
                        raise ValueError(
                            f"bookTicker lateness exceeded {REORDER_NS/1e9:.0f}s in {path}: "
                            f"{ts} < {previous_emitted_ns}"
                        )
                    previous_emitted_ns = ts
                    yield item
            while heap:
                ts, _, item = heapq.heappop(heap)
                if ts < previous_emitted_ns:
                    raise ValueError(f"bookTicker daily flush moved backwards in {path}")
                previous_emitted_ns = ts
                yield item
        finally:
            archive.close()


topbook_features.iter_book_ticker_paths = iter_book_ticker_paths_reordered

# SourceFile slots compatibility used by v2.
_original_download = source.download_verified

class _SourceProxy:
    def __init__(self, record):
        self.kind = record.kind
        self.source_url = record.source_url
        self.local_path = record.local_path
        self.sha256 = record.sha256
        self.size_bytes = record.size_bytes
        self.__dict__ = {
            "kind": record.kind,
            "source_url": record.source_url,
            "local_path": record.local_path,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
        }

def _download_verified(*args, **kwargs):
    return _SourceProxy(_original_download(*args, **kwargs))

source.download_verified = _download_verified

runpy.run_path(str(Path(__file__).with_name("l1_ofi_participation_study.py")), run_name="__main__")
