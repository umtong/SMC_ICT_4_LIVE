#!/usr/bin/env python3
"""Permit explicit bookDepth source gaps without inventing liquidity.

This is an ingestion-contract fix only. Missing official archives produce depth=None,
which makes the unchanged v29 engine reject entries. The continuous kline account and
all predeclared dates remain unchanged. Evaluation is invalid when causal depth coverage
falls below 95 percent.
"""
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data_loader.py"
text = PATH.read_text(encoding="utf-8")

if "MINIMUM_DEPTH_COVERAGE = 0.95" in text:
    raise SystemExit(0)

text = text.replace(
    "import urllib.request\n",
    "import urllib.error\nimport urllib.request\n",
    1,
)
text = text.replace(
    "MAX_DEPTH_AGE_NS = 180 * 1_000_000_000\n",
    "MAX_DEPTH_AGE_NS = 180 * 1_000_000_000\nMINIMUM_DEPTH_COVERAGE = 0.95\n",
    1,
)
text = text.replace(
    "    missing_depth_bars: int\n",
    "    missing_depth_bars: int\n    depth_coverage: float\n",
    1,
)

needle = "\n\ndef _csv_rows(path: Path):\n"
helper = '''

def _download_optional(url: str, destination: Path) -> bool:
    try:
        _download(url, destination, attempts=1)
        return True
    except RuntimeError as exc:
        cause = exc.__cause__
        if isinstance(cause, urllib.error.HTTPError) and cause.code == 404:
            destination.unlink(missing_ok=True)
            return False
        raise


def _csv_rows(path: Path):
'''
if needle not in text:
    raise RuntimeError("optional-download insertion point not found")
text = text.replace(needle, helper, 1)

needle = "        return url, path\n\n    def month(\n"
helper = '''        return url, path

    def _archive_optional(
        self,
        kind: str,
        label: str,
        *,
        monthly: bool,
        interval: str | None = None,
    ) -> tuple[str, Path, bool]:
        scope = "monthly" if monthly else "daily"
        if kind == "klines":
            assert interval
            filename = f"{self.symbol}-{interval}-{label}.zip"
            url = f"{BASE}/{scope}/klines/{self.symbol}/{interval}/{filename}"
            path = self.root / self.symbol / "klines" / interval / scope / filename
        else:
            filename = f"{self.symbol}-bookDepth-{label}.zip"
            url = f"{BASE}/{scope}/bookDepth/{self.symbol}/{filename}"
            path = self.root / self.symbol / "bookDepth" / scope / filename
        return url, path, _download_optional(url, path)

    def month(
'''
if needle not in text:
    raise RuntimeError("archive helper insertion point not found")
text = text.replace(needle, helper, 1)

start = text.index("    def month(\n")
end = text.index("\n\ndef validate_coverage", start)
month = '''    def month(
        self,
        year: int,
        month: int,
    ) -> tuple[list[FlowBar], list[DataFileRecord], list[str]]:
        label = f"{year:04d}-{month:02d}"
        kline_url, kline_path = self._archive(
            "klines",
            label,
            monthly=True,
            interval=self.interval,
        )
        depth_url, depth_path, depth_available = self._archive_optional(
            "bookDepth",
            label,
            monthly=True,
        )
        depth_sources: list[tuple[str, Path]] = []
        missing_depth_sources: list[str] = []
        if depth_available:
            depth_sources.append((depth_url, depth_path))
        else:
            start = date(year, month, 1)
            end = date(
                year + (month == 12),
                1 if month == 12 else month + 1,
                1,
            )
            current = start
            while current < end:
                url, path, available = self._archive_optional(
                    "bookDepth",
                    current.isoformat(),
                    monthly=False,
                )
                if available:
                    depth_sources.append((url, path))
                else:
                    missing_depth_sources.append(url)
                current += timedelta(days=1)

        parsed_depth_by_path: dict[Path, list[DepthSnapshot]] = {}
        depth_snapshots: list[DepthSnapshot] = []
        for _, path in depth_sources:
            parsed = parse_depth_archive(path)
            parsed_depth_by_path[path] = parsed
            depth_snapshots.extend(parsed)
        depth_snapshots.sort(key=lambda item: item.available_ns)

        klines = parse_kline_archive(kline_path)
        bars: list[FlowBar] = []
        index = 0
        active: DepthSnapshot | None = None
        for ts_ns, o, h, l, c, volume, taker_buy, count in klines:
            while (
                index < len(depth_snapshots)
                and depth_snapshots[index].available_ns <= ts_ns
            ):
                active = depth_snapshots[index]
                index += 1
            if active is None or ts_ns - active.observed_ns > MAX_DEPTH_AGE_NS:
                values = (None, None, None, None, None)
            else:
                values = (
                    active.bid_depth,
                    active.ask_depth,
                    active.bid_notional,
                    active.ask_notional,
                    active.observed_ns,
                )
            bars.append(
                FlowBar(
                    ts_ns,
                    o,
                    h,
                    l,
                    c,
                    volume,
                    taker_buy,
                    count,
                    *values,
                )
            )

        records = [
            DataFileRecord(
                kline_url,
                str(kline_path),
                _sha256(kline_path),
                kline_path.stat().st_size,
                len(klines),
                klines[0][0],
                klines[-1][0],
            )
        ]
        for url, path in depth_sources:
            parsed = parsed_depth_by_path[path]
            records.append(
                DataFileRecord(
                    url,
                    str(path),
                    _sha256(path),
                    path.stat().st_size,
                    len(parsed),
                    parsed[0].observed_ns if parsed else 0,
                    parsed[-1].observed_ns if parsed else 0,
                )
            )
        return bars, records, missing_depth_sources
'''
text = text[:start] + month + text[end:]

text = text.replace(
    "        invalid,\n        missing_depth,\n    )\n",
    "        invalid,\n        missing_depth,\n        1.0 - missing_depth / len(materialized),\n    )\n",
    1,
)

start = text.index("def _load_range(\n")
end = text.index("\n\ndef load_fixed_weeks", start)
load_range = '''def _load_range(
    start: date,
    end_exclusive: date,
    cache: BinanceVisionCache,
):
    lower = int(
        datetime.combine(
            start,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )
    upper = int(
        datetime.combine(
            end_exclusive,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )
    bars: list[FlowBar] = []
    records: list[DataFileRecord] = []
    missing_depth_sources: list[str] = []
    for year, month in _month_starts(start, end_exclusive):
        month_bars, month_records, month_missing = cache.month(year, month)
        bars.extend(bar for bar in month_bars if lower < bar.ts_ns <= upper)
        records.extend(month_records)
        missing_depth_sources.extend(month_missing)
    bars.sort(key=lambda item: item.ts_ns)
    coverage = validate_coverage(bars)
    if coverage.duplicate_timestamps or coverage.non_positive_prices:
        raise ValueError(f"invalid coverage: {coverage}")
    if coverage.depth_coverage < MINIMUM_DEPTH_COVERAGE:
        raise ValueError(
            "insufficient causal bookDepth coverage: "
            f"{coverage.depth_coverage:.6%} < {MINIMUM_DEPTH_COVERAGE:.2%}"
        )
    return bars, records, coverage, missing_depth_sources
'''
text = text[:start] + load_range + text[end:]

start = text.index("def load_fixed_weeks(\n")
end = text.index("\n\ndef load_monthly_range", start)
fixed = '''def load_fixed_weeks(
    config: Mapping[str, object],
    cache: BinanceVisionCache,
):
    output = {}
    files: list[DataFileRecord] = []
    coverage = {}
    missing_depth_sources: list[str] = []
    for raw in config["fixed_gate_weeks_utc"]:
        item = dict(raw)
        start = date.fromisoformat(str(item["start"]))
        end = start + timedelta(days=int(item["days"]))
        bars, records, report, missing = _load_range(start, end, cache)
        output[str(item["name"])] = bars
        files.extend(records)
        missing_depth_sources.extend(missing)
        coverage[str(item["name"])] = asdict(report)
    return output, {
        "source": (
            "Binance Vision USD-M monthly klines plus "
            "monthly/daily bookDepth"
        ),
        "files": [asdict(record) for record in files],
        "coverage": coverage,
        "missing_depth_sources": missing_depth_sources,
        "minimum_depth_coverage": MINIMUM_DEPTH_COVERAGE,
    }
'''
text = text[:start] + fixed + text[end:]

start = text.index("def load_monthly_range(\n")
end = text.index("\n\ndef write_manifest", start)
monthly = '''def load_monthly_range(
    *,
    start: date,
    end_exclusive: date,
    cache: BinanceVisionCache,
):
    bars, records, coverage, missing = _load_range(start, end_exclusive, cache)
    return bars, {
        "source": (
            "Binance Vision USD-M monthly klines plus "
            "monthly/daily bookDepth"
        ),
        "start": start.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "files": [asdict(record) for record in records],
        "coverage": asdict(coverage),
        "missing_depth_sources": missing,
        "minimum_depth_coverage": MINIMUM_DEPTH_COVERAGE,
    }
'''
text = text[:start] + monthly + text[end:]

required = [
    "MINIMUM_DEPTH_COVERAGE = 0.95",
    "def _download_optional",
    "missing_depth_sources",
    "coverage.depth_coverage < MINIMUM_DEPTH_COVERAGE",
]
for marker in required:
    if marker not in text:
        raise RuntimeError(f"missing patched contract: {marker}")
PATH.write_text(text, encoding="utf-8")
