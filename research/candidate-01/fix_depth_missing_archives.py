#!/usr/bin/env python3
"""Make unavailable official book-depth days explicit, not fatal or imputed."""

from pathlib import Path

path = Path(__file__).with_name("depth_diagnostics.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one depth patch match, found {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)


replace_once(
    "import sys\nfrom typing import Any\nfrom zipfile import ZipFile\n",
    "import sys\nfrom typing import Any\nfrom urllib.error import HTTPError\nfrom zipfile import ZipFile\n",
)
replace_once(
    '''def _download_depth_days(
    days: list[date],
    *,
    cache_dir: Path,
    workers: int,
) -> list[AuxiliaryDownload]:
    records: list[AuxiliaryDownload] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                _download_one,
                data_type="bookDepth",
                symbol="BTCUSDT",
                day=day,
                cache_dir=cache_dir,
            ): day
            for day in days
        }
        for future in as_completed(futures):
            records.append(future.result())
    return sorted(records, key=lambda item: item.day)
''',
    '''def _download_depth_days(
    days: list[date],
    *,
    cache_dir: Path,
    workers: int,
) -> tuple[list[AuxiliaryDownload], list[dict[str, Any]]]:
    records: list[AuxiliaryDownload] = []
    missing: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                _download_one,
                data_type="bookDepth",
                symbol="BTCUSDT",
                day=day,
                cache_dir=cache_dir,
            ): day
            for day in days
        }
        for future in as_completed(futures):
            day = futures[future]
            try:
                records.append(future.result())
            except HTTPError as exc:
                if exc.code != 404:
                    raise
                missing.append(
                    {
                        "data_type": "bookDepth",
                        "symbol": "BTCUSDT",
                        "day": day.isoformat(),
                        "status": 404,
                        "reason": "official_archive_not_published",
                    },
                )
    return sorted(records, key=lambda item: item.day), sorted(missing, key=lambda item: item["day"])
''',
)
replace_once(
    '''    manifest: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
''',
    '''    manifest: list[dict[str, Any]] = []
    missing_archives: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
''',
)
replace_once(
    '''        records = _download_depth_days(
            days,
            cache_dir=args.cache / "depth",
            workers=args.workers,
        )
        manifest.extend(record.to_dict() for record in records)
        depth = _read_depth(records)
        joined = pd.concat(
            [
                trades.reset_index(drop=True),
                _asof(trades["probe_time_ns"], depth, "probe"),
                _asof(trades["displacement_time_ns"], depth, "displacement"),
                _asof(trades["entry_time_ns"], depth, "entry_depth"),
            ],
            axis=1,
        )
''',
    '''        records, missing = _download_depth_days(
            days,
            cache_dir=args.cache / "depth",
            workers=args.workers,
        )
        manifest.extend(record.to_dict() for record in records)
        missing_archives.extend({**item, "segment": label} for item in missing)
        if records:
            depth = _read_depth(records)
            joins = [
                _asof(trades["probe_time_ns"], depth, "probe"),
                _asof(trades["displacement_time_ns"], depth, "displacement"),
                _asof(trades["entry_time_ns"], depth, "entry_depth"),
            ]
        else:
            raw_columns = ["timestamp"]
            for level in LEVELS:
                raw_columns.extend(
                    [
                        f"bid_notional_{level}",
                        f"ask_notional_{level}",
                        f"log_bid_ask_{level}",
                        f"log_bid_ask_z_{level}",
                        f"bid_notional_z_{level}",
                        f"ask_notional_z_{level}",
                    ],
                )
            joins = [
                pd.DataFrame(
                    {
                        f"{prefix}_{column}": [np.nan] * len(trades)
                        for column in raw_columns
                    },
                )
                for prefix in ("probe", "displacement", "entry_depth")
            ]
        joined = pd.concat(
            [trades.reset_index(drop=True), *joins],
            axis=1,
        )
''',
)
replace_once(
    '''    _atomic_json(
        output / "depth_manifest.json",
        {"provider": "Binance Vision", "files": files.to_dict(orient="records")},
    )
''',
    '''    _atomic_json(
        output / "depth_manifest.json",
        {
            "provider": "Binance Vision",
            "files": files.to_dict(orient="records"),
            "missing_archives": missing_archives,
        },
    )
''',
)
path.write_text(text, encoding="utf-8")
