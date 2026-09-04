"""Reproduce public archives used by the Astra2 liquidity-control research.
No API key, account access, orders or live trading. Spot/derivatives observations
help distinguish forced futures flow from accepted cash-market repricing.
"""
from __future__ import annotations
import argparse
import calendar
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT')
MONTHS = ('2024-03', '2024-08', '2025-02', '2025-08', '2025-11', '2026-01', '2026-04', '2026-07')
BASE = 'https://data.binance.vision/data'


def jobs(root: Path, extended_only: bool):
    for symbol in SYMBOLS:
        for month in MONTHS:
            kinds = ['indexPriceKlines', 'premiumIndexKlines']
            if not extended_only:
                kinds += ['klines', 'markPriceKlines', 'fundingRate']
            for kind in kinds:
                name = f'{symbol}-fundingRate-{month}.zip' if kind == 'fundingRate' else f'{symbol}-1m-{month}.zip'
                interval = '' if kind == 'fundingRate' else '/1m'
                yield f'{BASE}/futures/um/monthly/{kind}/{symbol}{interval}/{name}', root/kind/symbol/name
            name = f'{symbol}-1m-{month}.zip'
            yield f'{BASE}/spot/monthly/klines/{symbol}/1m/{name}', root/'spot'/symbol/name
            year, mon = map(int, month.split('-'))
            for day in range(1, calendar.monthrange(year, mon)[1] + 1):
                date = f'{month}-{day:02d}'
                name = f'{symbol}-metrics-{date}.zip'
                yield f'{BASE}/futures/um/daily/metrics/{symbol}/{name}', root/'metrics'/symbol/name


def download(job):
    url, path = job
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            if path.exists():
                content = path.read_bytes()
            else:
                with urllib.request.urlopen(url, timeout=90) as response:
                    content = response.read()
                if content[:2] != b'PK':
                    raise ValueError(f'not a ZIP: {url}')
                path.write_bytes(content)
            return {'url': url, 'path': str(path), 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        except Exception as error:
            if attempt == 2:
                return {'url': url, 'path': str(path), 'error': str(error)}
            time.sleep(1 + attempt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('market'))
    parser.add_argument('--extended-only', action='store_true')
    args = parser.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(download, jobs(args.output, args.extended_only)))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'archives.json').write_text(json.dumps(results, indent=2) + '\n')
    missing = [item for item in results if 'error' in item]
    print(f'{len(results) - len(missing)}/{len(results)} actual archives downloaded')
    for item in missing:
        print(item['url'], item['error'])
    # Missing observations are never silently synthesized or forward-filled
    # across an absent archive. The research reader explicitly decides which
    # causal observations exist and otherwise leaves the observation missing.


if __name__ == '__main__':
    main()
