#!/usr/bin/env python3
"""Materialize the Nautilus runner for Candidate 11 positioning auctions."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "run_leadership_scdam.py"
DESTINATION = ROOT / "run_positioning_auction.py"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


AUXILIARY_LOADER = r'''

def _optional_float(value: Any) -> float | None:
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    result = float(text)
    return result if result == result else None


def load_positioning_observations(
    symbol: str,
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[list[PositioningObs], list[dict[str, Any]]]:
    """Load metrics and premium with a conservative causal visibility clock."""
    metric_rows: list[dict[str, Any]] = []
    premiums: list[tuple[int, float]] = []
    manifest: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end_inclusive:
        iso = cursor.isoformat()
        sources = {
            "metrics": (
                f"{symbol}-metrics-{iso}.zip",
                f"https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{iso}.zip",
            ),
            "premium_index_1m": (
                f"{symbol}-1m-{iso}.zip",
                f"https://data.binance.vision/data/futures/um/daily/premiumIndexKlines/{symbol}/1m/{symbol}-1m-{iso}.zip",
            ),
        }
        for kind, (filename, url) in sources.items():
            path = data_dir / kind / symbol / filename
            _download(url, path)
            digest = sha256(path.read_bytes()).hexdigest()
            with ZipFile(path) as archive:
                members = archive.namelist()
                if len(members) != 1:
                    raise RuntimeError(f"unexpected positioning archive members: {filename}: {members}")
                member_bytes = archive.read(members[0])
            if kind == "metrics":
                frame = pd.read_csv(BytesIO(member_bytes), dtype=str, keep_default_na=False)
                expected = (
                    "create_time", "symbol", "sum_open_interest",
                    "sum_open_interest_value", "count_toptrader_long_short_ratio",
                    "sum_toptrader_long_short_ratio", "count_long_short_ratio",
                    "sum_taker_long_short_vol_ratio",
                )
                if tuple(frame.columns) != expected:
                    raise RuntimeError(f"unexpected metrics schema for {filename}: {tuple(frame.columns)}")
                for row in frame.to_dict(orient="records"):
                    created = pd.Timestamp(str(row["create_time"]), tz="UTC")
                    metric_rows.append({
                        "ts_ns": int((created + pd.Timedelta(minutes=5)).value),
                        "open_interest": float(row["sum_open_interest"]),
                        "open_interest_value": float(row["sum_open_interest_value"]),
                        "taker_ratio": _optional_float(row["sum_taker_long_short_vol_ratio"]),
                        "account_ratio": _optional_float(row["count_long_short_ratio"]),
                        "top_position_ratio": _optional_float(row["sum_toptrader_long_short_ratio"]),
                    })
                rows = len(frame.index)
            else:
                frame = pd.read_csv(BytesIO(member_bytes), dtype=str, keep_default_na=False)
                if not set(COLUMNS).issubset(frame.columns):
                    frame = pd.read_csv(
                        BytesIO(member_bytes),
                        header=None,
                        names=COLUMNS,
                        dtype=str,
                        keep_default_na=False,
                    )
                frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()
                for open_time, close in zip(frame["open_time"], frame["close"], strict=True):
                    value = int(open_time)
                    if value < 10_000_000_000_000:
                        close_ts_ns = (value + 60_000) * 1_000_000
                    else:
                        close_ts_ns = (value + 60_000_000) * 1_000
                    premiums.append((close_ts_ns, float(close)))
                rows = len(frame.index)
            manifest.append({
                "kind": kind,
                "symbol": symbol,
                "date": iso,
                "url": url,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": digest,
                "rows": rows,
            })
        cursor += timedelta(days=1)

    metric_rows.sort(key=lambda row: int(row["ts_ns"]))
    premiums = sorted({ts_ns: close for ts_ns, close in premiums}.items())
    premium_times = [ts_ns for ts_ns, _ in premiums]
    observations: list[PositioningObs] = []
    import bisect
    for row in metric_rows:
        ts_ns = int(row["ts_ns"])
        index = bisect.bisect_right(premium_times, ts_ns) - 1
        premium_close = None if index < 0 else premiums[index][1]
        observations.append(PositioningObs(
            ts_ns=ts_ns,
            open_interest=float(row["open_interest"]),
            open_interest_value=float(row["open_interest_value"]),
            taker_ratio=row["taker_ratio"],
            account_ratio=row["account_ratio"],
            top_position_ratio=row["top_position_ratio"],
            premium_close=premium_close,
        ))
    unique = {observation.ts_ns: observation for observation in observations}
    return [unique[key] for key in sorted(unique)], manifest


def build_positioning_lookup(
    frame: pd.DataFrame,
    observations: list[PositioningObs],
) -> dict[int, PositioningObs | None]:
    """Forward-fill only already-visible snapshots onto completed bars."""
    import bisect
    times = [observation.ts_ns for observation in observations]
    lookup: dict[int, PositioningObs | None] = {}
    for timestamp in frame.index:
        ts_ns = int(timestamp.value)
        index = bisect.bisect_right(times, ts_ns) - 1
        lookup[ts_ns] = None if index < 0 else observations[index]
    return lookup
'''


def build_runner() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    source = replace_once(
        source,
        '''"""Dynamic price-discovery leadership evaluation of Candidate 11 SCDAM.

Each allowed market owns an independent RegionalHandoffAuctionEngine.  The
strategy logic is identical across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT; only
Nautilus instrument metadata differs.  Plans emitted at the same completed bar
are deterministically arbitrated, while one global pending-entry/position slot
is enforced across all instruments.  NautilusTrader exclusively owns clocks,
orders, fills, fees, margin, positions and account NAV.
"""''',
        '''"""NautilusTrader evaluation of Candidate 11 positioning-unwind auctions.

Each allowed market owns an independent PositioningUnwindAuctionEngine. The
engine receives only completed one-minute bars and conservatively visible
Binance USD-M positioning snapshots. Plans emitted at the same completed bar
are deterministically arbitrated, while one global pending-entry/position slot
is enforced across all instruments. NautilusTrader exclusively owns clocks,
orders, fills, fees, margin, positions and account NAV.
"""''',
        "runner description",
    )
    source = replace_once(
        source,
        "from logic import BarObs, Direction, LogicConfig, RiskSizer, TradePlan\nfrom session_engine import RegionalHandoffAuctionEngine\n",
        "from logic import BarObs, Direction, RiskSizer, TradePlan\n"
        "from positioning_auction import (\n"
        "    PositioningAuctionConfig,\n"
        "    PositioningObs,\n"
        "    PositioningUnwindAuctionEngine,\n"
        ")\n",
        "positioning imports",
    )
    source = replace_once(
        source,
        '\n\ndef _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:\n',
        AUXILIARY_LOADER
        + '\n\ndef _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:\n',
        "positioning loader",
    )
    source = replace_once(
        source,
        "logics: dict[str, RegionalHandoffAuctionEngine],",
        "logics: dict[str, PositioningUnwindAuctionEngine],",
        "metric engine type",
    )
    source = replace_once(
        source,
        '"candidate": "candidate-11-market-leadership-scdam",',
        '"candidate": "candidate-11-positioning-unwind-auction",',
        "candidate identity",
    )
    source = replace_once(
        source,
        '''    frames: dict[str, pd.DataFrame] = {}
    manifest: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frames[symbol], files = load_symbol_bars(symbol, warmup_start, evaluation_end, output_dir / "data")
        manifest.extend(files)
    write_json_atomic(output_dir / "data_manifest.json", {
        "schema": "candidate-11-portfolio-source-manifest-v1",
        "dataset": "Binance USD-M one-minute daily klines",
        "symbols": list(SYMBOLS),
        "bar_visibility": "archive open_time plus one minute",
        "warmup_start": warmup_start.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "files": manifest,
    })

    account_config = config["account"]
    execution_config = config["execution"]
    logic_config = LogicConfig(**config["logic"])
''',
        '''    frames: dict[str, pd.DataFrame] = {}
    positioning_lookup: dict[str, dict[int, PositioningObs | None]] = {}
    manifest: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frames[symbol], files = load_symbol_bars(symbol, warmup_start, evaluation_end, output_dir / "data")
        manifest.extend({"kind": "trade_bar_1m", **record} for record in files)
        observations, positioning_files = load_positioning_observations(
            symbol, warmup_start, evaluation_end, output_dir / "positioning_data",
        )
        positioning_lookup[symbol] = build_positioning_lookup(frames[symbol], observations)
        manifest.extend(positioning_files)
    write_json_atomic(output_dir / "data_manifest.json", {
        "schema": "candidate-11-positioning-auction-source-manifest-v1",
        "dataset": "Binance USD-M one-minute klines, five-minute metrics, and one-minute premium index",
        "symbols": list(SYMBOLS),
        "bar_visibility": "archive open_time plus one minute",
        "metrics_visibility": "create_time plus five minutes",
        "premium_visibility": "archive open_time plus one minute on the metrics clock",
        "warmup_start": warmup_start.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "files": manifest,
    })

    account_config = config["account"]
    execution_config = config["execution"]
    logic_config = PositioningAuctionConfig(**config.get("positioning_logic", {}))
''',
        "positioning sources and config",
    )
    source = replace_once(
        source,
        '''            self.logic = {
                symbol: RegionalHandoffAuctionEngine(logic_config, str(instruments[symbol].id))
                for symbol in SYMBOLS
            }''',
        '''            self.logic = {
                symbol: PositioningUnwindAuctionEngine(logic_config, str(instruments[symbol].id))
                for symbol in SYMBOLS
            }''',
        "positioning engines",
    )
    source = replace_once(
        source,
        "                plan = self.logic[symbol].on_bar(observation)",
        "                plan = self.logic[symbol].on_bar(\n"
        "                    observation,\n"
        "                    positioning_lookup[symbol].get(ts_ns),\n"
        "                )",
        "positioning observation",
    )
    source = source.replace(
        "candidate-11-market-leadership-scdam",
        "candidate-11-positioning-unwind-auction",
    )
    source = replace_once(
        source,
        'run_id=f"candidate-11-leadership-{week_id.lower()}-',
        'run_id=f"candidate-11-positioning-{week_id.lower()}-',
        "positioning run id",
    )
    source = replace_once(
        source,
        '                "logic": config["logic"],',
        '                "positioning_logic": config.get("positioning_logic", {}),',
        "positioning manifest config",
    )
    source = source.replace(
        'parser.add_argument("--week", choices=("W1", "W2", "W3", "W4", "W5", "W6"), default="W1")',
        'parser.add_argument("--week", choices=("W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9"), default="W1")',
    )
    source = replace_once(
        source,
        'parser.add_argument("--output", type=Path, default=ROOT / "results" / "LEADERSHIP_W1")',
        'parser.add_argument("--output", type=Path, default=ROOT / "results" / "POSITIONING_W1")',
        "positioning default output",
    )
    previous = DESTINATION.read_text(encoding="utf-8") if DESTINATION.exists() else None
    if previous == source:
        return 0
    DESTINATION.write_text(source, encoding="utf-8")
    return 1


def main() -> None:
    missing = [path.name for path in (SOURCE, ROOT / "positioning_auction.py") if not path.is_file()]
    if missing:
        raise SystemExit(f"positioning runner inputs missing: {missing}")
    changed = build_runner()
    print(f"positioning-auction runner materialization applied: {changed}")


if __name__ == "__main__":
    main()
