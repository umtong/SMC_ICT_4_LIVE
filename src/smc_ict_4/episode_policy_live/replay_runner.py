"""Production historical replay entrypoint backed only by NautilusTrader.

This integrates, rather than reinvents, the native one-account replay paths
already established by C29, C35 and Candidate05.  Binance Vision trade,
funding and mark-price archives are discovered with an exact monthly naming
contract before the shared live strategy is run by NautilusTrader.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Iterator, Mapping, Sequence

from .domain import SYMBOLS
from .inventory_ownership import InventoryTimeline, load_official_metrics_archives
from .nautilus_backtest import NativeBacktestResult, run_streaming_native_backtest
from .nautilus_data import BinanceKline1mLoader, SynchronizedMinute
from .nautilus_funding import (
    BinanceFundingPaymentSource,
    BinanceMarkPrice1mLoader,
    HistoricalFundingPayment,
    SynchronizedMarkPriceMinute,
)
from .replay_evidence import build_replay_evidence, write_replay_evidence


UTC = timezone.utc


class ReplaySourceError(ValueError):
    """Monthly roots cannot form one unambiguous official replay source."""


@dataclass(frozen=True, slots=True)
class ReplaySources:
    """Canonical Binance Vision archive sequences for one replay."""

    trade_klines: Mapping[str, tuple[Path, ...]]
    funding_rates: Mapping[str, tuple[Path, ...]]
    mark_price_klines: Mapping[str, tuple[Path, ...]]
    trade_months: tuple[str, ...]
    funding_months: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InventoryReplaySources:
    """Canonical daily metrics files required by one native replay."""

    root: Path
    start: date
    end_exclusive: date
    archives: Mapping[str, tuple[Path, ...]]
    checksums: Mapping[str, tuple[Path, ...]]


@dataclass(frozen=True, slots=True)
class InventoryReplayBundle:
    """Strictly validated timelines and compact run-manifest evidence."""

    timelines: Mapping[str, InventoryTimeline]
    manifest: Mapping[str, object]


def _utc_ns(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1_000_000_000)


def _month_floor(value: date) -> date:
    return date(value.year, value.month, 1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def months_intersecting(start: date, end: date) -> tuple[str, ...]:
    """Return UTC calendar months intersecting ``[start, end)``."""

    if end <= start:
        raise ValueError("end must be after start")
    month = _month_floor(start)
    result: list[str] = []
    while month < end:
        result.append(month.strftime("%Y-%m"))
        month = _next_month(month)
    return tuple(result)


def _canonical_relative(kind: str, symbol: str, month: str) -> Path:
    if kind == "trade":
        return Path("klines") / symbol / "1m" / f"{symbol}-1m-{month}.zip"
    if kind == "funding":
        return Path("fundingRate") / symbol / f"{symbol}-fundingRate-{month}.zip"
    if kind == "mark":
        return Path("markPriceKlines") / symbol / "1m" / f"{symbol}-1m-{month}.zip"
    raise AssertionError(f"unknown archive kind: {kind}")


def _normalized_roots(monthly_roots: Iterable[str | Path]) -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw in monthly_roots:
        root = Path(raw).expanduser().resolve()
        if root in seen:
            continue
        if not root.is_dir():
            raise ReplaySourceError(f"monthly root does not exist: {root}")
        seen.add(root)
        roots.append(root)
    if not roots:
        raise ReplaySourceError("at least one --monthly-root is required")
    return tuple(roots)


def _find_exactly_one(
    roots: Sequence[Path],
    *,
    kind: str,
    symbol: str,
    month: str,
) -> Path:
    relative = _canonical_relative(kind, symbol, month)
    matches = tuple(root / relative for root in roots if (root / relative).is_file())
    if not matches:
        raise ReplaySourceError(
            f"missing canonical {kind} archive for {symbol} {month}: {relative}",
        )
    if len(matches) > 1:
        joined = ", ".join(str(path) for path in matches)
        raise ReplaySourceError(
            f"ambiguous canonical {kind} archive for {symbol} {month}: {joined}",
        )
    return matches[0]


def discover_replay_sources(
    monthly_roots: Iterable[str | Path],
    *,
    start: date,
    end: date,
    warmup_days: int,
) -> ReplaySources:
    """Resolve every required canonical archive before starting Nautilus.

    ``monthly_roots`` must point at Binance Vision ``futures_um/monthly``
    directories.  Repair copies, suffix variants and nested cache artifacts
    are intentionally ignored.  Two canonical copies are an error rather than
    an implicit root-precedence rule.
    """

    if end <= start:
        raise ValueError("end must be after start")
    if warmup_days < 0:
        raise ValueError("warmup_days must be non-negative")
    roots = _normalized_roots(monthly_roots)
    warmup_start = start - timedelta(days=warmup_days)
    trade_months = months_intersecting(warmup_start, end)
    funding_months = months_intersecting(start, end)

    trades = {
        symbol: tuple(
            _find_exactly_one(roots, kind="trade", symbol=symbol, month=month)
            for month in trade_months
        )
        for symbol in SYMBOLS
    }
    funding = {
        symbol: tuple(
            _find_exactly_one(roots, kind="funding", symbol=symbol, month=month)
            for month in funding_months
        )
        for symbol in SYMBOLS
    }
    marks = {
        symbol: tuple(
            _find_exactly_one(roots, kind="mark", symbol=symbol, month=month)
            for month in funding_months
        )
        for symbol in SYMBOLS
    }
    return ReplaySources(
        trade_klines=trades,
        funding_rates=funding,
        mark_price_klines=marks,
        trade_months=trade_months,
        funding_months=funding_months,
    )


_METRICS_NAME = re.compile(
    r"^(BTCUSDT|ETHUSDT|SOLUSDT|XRPUSDT)-metrics-(\d{4}-\d{2}-\d{2})\.zip$",
)


def discover_inventory_sources(
    metrics_root: str | Path,
    *,
    start: date,
    end: date,
) -> InventoryReplaySources:
    """Resolve canonical official daily metrics over ``[start-1d, end)``.

    The preceding UTC day supplies the three prior five-minute observations
    needed at the evaluation boundary.  A missing archive/checksum or another
    copy of the same canonical archive anywhere below the root fails closed.
    """

    if end <= start:
        raise ValueError("end must be after start")
    root = Path(metrics_root).expanduser().resolve()
    if not root.is_dir():
        raise ReplaySourceError(f"metrics root does not exist: {root}")
    coverage_start = start - timedelta(days=1)
    requested_days = tuple(
        coverage_start + timedelta(days=offset)
        for offset in range((end - coverage_start).days)
    )
    requested = {(symbol, day) for symbol in SYMBOLS for day in requested_days}
    zip_matches: dict[tuple[str, date], list[Path]] = {}
    checksum_matches: dict[tuple[str, date], list[Path]] = {}
    for path in root.rglob("*-metrics-*.zip"):
        match = _METRICS_NAME.fullmatch(path.name)
        if match is None:
            continue
        key = (match.group(1), date.fromisoformat(match.group(2)))
        if key in requested:
            zip_matches.setdefault(key, []).append(path.resolve())
    for path in root.rglob("*-metrics-*.zip.CHECKSUM"):
        match = _METRICS_NAME.fullmatch(path.name.removesuffix(".CHECKSUM"))
        if match is None:
            continue
        key = (match.group(1), date.fromisoformat(match.group(2)))
        if key in requested:
            checksum_matches.setdefault(key, []).append(path.resolve())

    archives: dict[str, tuple[Path, ...]] = {}
    checksums: dict[str, tuple[Path, ...]] = {}
    for symbol in SYMBOLS:
        symbol_archives: list[Path] = []
        symbol_checksums: list[Path] = []
        for day in requested_days:
            filename = f"{symbol}-metrics-{day.isoformat()}.zip"
            expected_archive = (root / symbol / filename).resolve()
            expected_checksum = Path(str(expected_archive) + ".CHECKSUM")
            key = (symbol, day)
            found_archives = zip_matches.get(key, [])
            found_checksums = checksum_matches.get(key, [])
            if expected_archive not in found_archives:
                raise ReplaySourceError(
                    f"missing canonical metrics archive for {symbol} {day}: "
                    f"{expected_archive}",
                )
            if len(found_archives) != 1:
                raise ReplaySourceError(
                    f"duplicate canonical metrics archive for {symbol} {day}: "
                    + ", ".join(str(path) for path in found_archives),
                )
            if expected_checksum not in found_checksums:
                raise ReplaySourceError(
                    f"missing canonical metrics checksum for {symbol} {day}: "
                    f"{expected_checksum}",
                )
            if len(found_checksums) != 1:
                raise ReplaySourceError(
                    f"duplicate canonical metrics checksum for {symbol} {day}: "
                    + ", ".join(str(path) for path in found_checksums),
                )
            symbol_archives.append(expected_archive)
            symbol_checksums.append(expected_checksum)
        archives[symbol] = tuple(symbol_archives)
        checksums[symbol] = tuple(symbol_checksums)
    return InventoryReplaySources(
        root=root,
        start=coverage_start,
        end_exclusive=end,
        archives=archives,
        checksums=checksums,
    )


def load_inventory_replay_bundle(sources: InventoryReplaySources) -> InventoryReplayBundle:
    """Checksum-verify official coverage while preserving provider uncertainty.

    Archive/checksum/date coverage remains exact.  Missing rows stay as causal
    gaps and same-timestamp official disagreements are unknown observations;
    neither is synthesized into inventory evidence.
    """

    expected_days = (sources.end_exclusive - sources.start).days
    expected_points = expected_days * 24 * 12
    first_expected = _utc_ns(sources.start)
    last_expected = _utc_ns(sources.end_exclusive) - 5 * 60 * 1_000_000_000
    timelines: dict[str, InventoryTimeline] = {}
    coverage: dict[str, dict[str, object]] = {}
    for symbol in SYMBOLS:
        timeline = load_official_metrics_archives(
            symbol,
            sources.archives[symbol],
            verify_checksums=True,
        )
        points = timeline.points
        if not points:
            raise ReplaySourceError(f"metrics archives contain no observations for {symbol}")
        if (
            points[0].nominal_ts_ns < first_expected
            or points[-1].nominal_ts_ns > last_expected
        ):
            raise ReplaySourceError(
                f"metrics observation outside requested archive coverage for {symbol}",
            )
        interval_ns = 5 * 60 * 1_000_000_000
        gaps: list[dict[str, int]] = []
        cursor = first_expected
        for point in points:
            if point.nominal_ts_ns > cursor:
                gaps.append({
                    "start_nominal_ts_ns": cursor,
                    "end_exclusive_nominal_ts_ns": point.nominal_ts_ns,
                    "missing_observations": (point.nominal_ts_ns - cursor) // interval_ns,
                })
            elif point.nominal_ts_ns < cursor:
                raise ReplaySourceError(
                    f"metrics nominal order corruption for {symbol}: "
                    f"{point.nominal_ts_ns} < {cursor}",
                )
            cursor = point.nominal_ts_ns + interval_ns
        coverage_end_ns = _utc_ns(sources.end_exclusive)
        if cursor < coverage_end_ns:
            gaps.append({
                "start_nominal_ts_ns": cursor,
                "end_exclusive_nominal_ts_ns": coverage_end_ns,
                "missing_observations": (coverage_end_ns - cursor) // interval_ns,
            })
        missing_observations = sum(item["missing_observations"] for item in gaps)
        if len(points) + missing_observations != expected_points:
            raise ReplaySourceError(
                f"metrics coverage accounting mismatch for {symbol}: "
                f"expected={expected_points}, observations={len(points)}, "
                f"missing={missing_observations}",
            )
        invalid_fields = Counter(
            field
            for point in points
            for field in point.invalid_fields
        )
        timelines[symbol] = timeline
        coverage[symbol] = {
            "archives": len(sources.archives[symbol]),
            "checksums": len(sources.checksums[symbol]),
            "expected_observations": expected_points,
            "observations": len(points),
            "first_nominal_ts_ns": points[0].nominal_ts_ns,
            "last_nominal_ts_ns": points[-1].nominal_ts_ns,
            "first_observed_ts_ns": points[0].observed_ts_ns,
            "last_observed_ts_ns": points[-1].observed_ts_ns,
            "invalid_observations": sum(bool(point.invalid_fields) for point in points),
            "invalid_fields": dict(sorted(invalid_fields.items())),
            "gap_count": len(gaps),
            "missing_observations": missing_observations,
            "gaps": gaps,
            "identical_duplicates_collapsed": len(timeline.duplicate_observed_ts_ns),
            "identical_duplicate_observed_ts_ns": list(
                timeline.duplicate_observed_ts_ns,
            ),
            "conflicting_duplicates_merged_unknown": len(
                timeline.conflicting_duplicates
            ),
            "conflicting_duplicate_evidence": [
                {
                    "source_ts_ns": item.source_ts_ns,
                    "source_timestamps_ns": list(item.source_timestamps_ns),
                    "nominal_ts_ns": item.nominal_ts_ns,
                    "observed_ts_ns": item.observed_ts_ns,
                    "conflicting_fields": list(item.conflicting_fields),
                    "source_archives": list(item.source_archives),
                    "source_archive_sha256": list(item.source_archive_sha256),
                }
                for item in timeline.conflicting_duplicates
            ],
        }
    return InventoryReplayBundle(
        timelines=timelines,
        manifest={
            "status": "LOADED",
            "provider": "Binance public data / USD-M futures daily metrics",
            "root": str(sources.root),
            "coverage_start": sources.start.isoformat(),
            "coverage_end_exclusive": sources.end_exclusive.isoformat(),
            "checksum_source": "adjacent official .zip.CHECKSUM",
            "checksum_algorithm": "SHA-256",
            "archive_count": sum(len(items) for items in sources.archives.values()),
            "checksum_count": sum(len(items) for items in sources.checksums.values()),
            "identical_duplicates_collapsed": sum(
                len(timeline.duplicate_observed_ts_ns)
                for timeline in timelines.values()
            ),
            "conflicting_duplicates_merged_unknown": sum(
                len(timeline.conflicting_duplicates)
                for timeline in timelines.values()
            ),
            "gap_count": sum(int(item["gap_count"]) for item in coverage.values()),
            "missing_observations": sum(
                int(item["missing_observations"])
                for item in coverage.values()
            ),
            "symbols": coverage,
        },
    )


def configure_inventory_timelines(
    strategy: object,
    timelines: Mapping[str, InventoryTimeline],
) -> None:
    """Inject prevalidated timelines into the native strategy policies."""

    coordinator = getattr(strategy, "coordinator", None)
    policies = getattr(coordinator, "policies", None)
    if not isinstance(policies, Mapping) or set(policies) != set(SYMBOLS):
        raise ReplaySourceError("native strategy coordinator does not expose four policies")
    if set(timelines) != set(SYMBOLS):
        raise ReplaySourceError("inventory timelines must contain exactly four symbols")
    for symbol in SYMBOLS:
        policy = policies[symbol]
        if not hasattr(policy, "inventory_timeline"):
            raise ReplaySourceError(f"policy cannot accept inventory timeline: {symbol}")
        policy.inventory_timeline = timelines[symbol]


def _bounded_minutes(
    minutes: Iterable[SynchronizedMinute],
    *,
    warmup_start_ns: int,
    end_ns: int,
) -> Iterator[SynchronizedMinute]:
    # A bar stamped at T closes the [T-1m, T) candle.  Thus the first included
    # candle has ts_event > warmup_start and the last has ts_event == end.
    for minute in minutes:
        if minute.ts_event <= warmup_start_ns:
            continue
        if minute.ts_event > end_ns:
            break
        yield minute


def _bounded_mark_minutes(
    minutes: Iterable[SynchronizedMarkPriceMinute],
    *,
    start_ns: int,
    end_ns: int,
    coverage: dict[str, object],
) -> Iterator[SynchronizedMarkPriceMinute]:
    """Yield exact completed mark minutes for ``(start, end]`` and prove coverage."""

    minute_ns = 60 * 1_000_000_000
    expected_first = start_ns + minute_ns
    expected_count = (end_ns - start_ns) // minute_ns
    first: int | None = None
    last: int | None = None
    count = 0
    for minute in minutes:
        if minute.ts_event <= start_ns:
            continue
        if minute.ts_event > end_ns:
            break
        first = minute.ts_event if first is None else first
        last = minute.ts_event
        count += 1
        yield minute
    if first != expected_first or last != end_ns or count != expected_count:
        raise ReplaySourceError(
            "official mark-price coverage mismatch: "
            f"expected_first={expected_first}, actual_first={first}, "
            f"expected_last={end_ns}, actual_last={last}, "
            f"expected_count={expected_count}, actual_count={count}",
        )
    coverage.update(
        {
            "status": "COMPLETE",
            "range": "(start, end] completed one-minute observations",
            "start_exclusive_ns": start_ns,
            "first_observation_ns": first,
            "last_observation_ns": last,
            "end_inclusive_ns": end_ns,
            "observations": count,
            "symbols": list(SYMBOLS),
            "price_field": "official completed 1m mark-price kline close",
        },
    )


def _bounded_funding(
    payments: Iterable[HistoricalFundingPayment],
    *,
    start_ns: int,
    end_ns: int,
) -> tuple[HistoricalFundingPayment, ...]:
    selected: list[HistoricalFundingPayment] = []
    for payment in payments:
        if payment.ts_event < start_ns:
            continue
        if payment.ts_event >= end_ns:
            break
        selected.append(payment)
    return tuple(selected)


def _prepare_output(output: str | Path) -> Path:
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        if not destination.is_dir():
            raise FileExistsError(f"output exists and is not a directory: {destination}")
        if any(destination.iterdir()):
            raise FileExistsError(f"refusing to overwrite nonempty output: {destination}")
    else:
        destination.mkdir(parents=True)
    return destination


def _path_map(payload: Mapping[str, Sequence[Path]]) -> dict[str, list[str]]:
    return {symbol: [str(path) for path in payload[symbol]] for symbol in SYMBOLS}


def _verify_archive(path: Path) -> dict[str, object]:
    checksum_path = path.with_name(path.name + ".CHECKSUM")
    if not checksum_path.is_file():
        raise ReplaySourceError(f"missing official CHECKSUM companion: {checksum_path}")
    content = checksum_path.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", content)
    if match is None:
        raise ReplaySourceError(f"malformed CHECKSUM companion: {checksum_path}")
    expected = match.group(1).lower()
    declared_name = match.group(2).strip()
    if declared_name != path.name:
        raise ReplaySourceError(
            f"CHECKSUM filename mismatch for {path}: declared {declared_name!r}",
        )
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ReplaySourceError(
            f"SHA-256 mismatch for {path}: expected {expected}, calculated {actual}",
        )
    return {
        "path": str(path),
        "checksum_path": str(checksum_path),
        "sha256": actual,
        "checksum_verified": True,
        "bytes": path.stat().st_size,
    }


def verify_replay_sources(sources: ReplaySources) -> dict[str, object]:
    """Verify every selected archive against its official Binance checksum."""

    groups = {
        "trade_klines": sources.trade_klines,
        "funding_rates": sources.funding_rates,
        "mark_price_klines": sources.mark_price_klines,
    }
    archives = {
        kind: {
            symbol: [_verify_archive(path) for path in paths]
            for symbol, paths in mapping.items()
        }
        for kind, mapping in groups.items()
    }
    portable_records = [
        {
            "kind": kind,
            "symbol": symbol,
            "filename": Path(str(record["path"])).name,
            "sha256": record["sha256"],
            "bytes": record["bytes"],
        }
        for kind, mapping in sorted(archives.items())
        for symbol, records in sorted(mapping.items())
        for record in records
    ]
    portable_payload = json.dumps(
        portable_records,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "algorithm": "SHA-256",
        "official_checksum_companion_required": True,
        "all_verified": True,
        "archive_count": sum(
            len(records)
            for mapping in archives.values()
            for records in mapping.values()
        ),
        "portable_manifest_sha256": sha256(portable_payload).hexdigest(),
        "archives": archives,
    }


def _git_source_provenance() -> dict[str, object]:
    """Resolve the checked-out source revision and disclose dirty-tree state."""

    location = Path(__file__).resolve().parent
    project_root = next(
        (parent for parent in (location, *location.parents) if (parent / "pyproject.toml").is_file()),
        location,
    )
    source_files = sorted(location.rglob("*.py"))
    source_files.extend(
        path for path in (project_root / "pyproject.toml", project_root / "uv.lock")
        if path.is_file() and path not in source_files
    )
    source_records = [
        {
            "path": path.relative_to(project_root).as_posix(),
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in source_files
    ]
    source_payload = json.dumps(
        source_records,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    working_tree = {
        "working_tree_manifest_sha256": sha256(source_payload).hexdigest(),
        "working_tree_manifest_files": len(source_records),
        "working_tree_manifest": source_records,
    }
    environment_sha = os.environ.get("GITHUB_SHA", "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", environment_sha):
        return {
            "git_sha": environment_sha.lower(),
            "git_dirty": None,
            "resolution": "GITHUB_SHA",
            **working_tree,
        }
    try:
        revision = subprocess.run(
            ["git", "-C", str(location), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(location), "status", "--porcelain", "--untracked-files=all"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReplaySourceError("cannot resolve current Git source SHA") from exc
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ReplaySourceError(f"invalid Git source SHA: {revision!r}")
    return {
        "git_sha": revision.lower(),
        "git_dirty": dirty,
        "resolution": "git rev-parse HEAD",
        **working_tree,
    }


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _write_native_reports(result: NativeBacktestResult, destination: Path) -> None:
    # Nautilus report identities are named DataFrame indices: client_order_id,
    # position_id and ts_event respectively.  Dropping them makes the persisted
    # reports impossible to join or replay even though the in-memory evidence
    # build still succeeds.
    for filename, report, identity in (
        ("fills.csv", result.fills, "client_order_id"),
        ("positions.csv", result.positions, "position_id"),
        ("account.csv", result.account, "ts_event"),
    ):
        table = report
        index = getattr(table, "index", None)
        if index is not None and getattr(index, "name", None) is None:
            table = table.copy()
            table.index.name = identity
        table.to_csv(destination / filename, index=True)


def run_native_replay(
    *,
    monthly_roots: Iterable[str | Path],
    start: date,
    end: date,
    warmup_days: int,
    output: str | Path,
    initial_nav: float,
    metrics_root: str | Path | None = None,
) -> dict[str, object]:
    """Run one continuous four-market native account and persist evidence."""

    if not math.isfinite(initial_nav) or initial_nav <= 0:
        raise ValueError("initial_nav must be finite and positive")
    sources = discover_replay_sources(
        monthly_roots,
        start=start,
        end=end,
        warmup_days=warmup_days,
    )
    source_integrity = verify_replay_sources(sources)
    source_code = _git_source_provenance()
    inventory_bundle = (
        load_inventory_replay_bundle(
            discover_inventory_sources(metrics_root, start=start, end=end),
        )
        if metrics_root is not None
        else None
    )
    inventory_manifest: Mapping[str, object] = (
        inventory_bundle.manifest
        if inventory_bundle is not None
        else {
            "status": "NO_INVENTORY_TIMELINE",
            "provider": "Binance public data / USD-M futures daily metrics",
            "root": None,
            "coverage_start": None,
            "coverage_end_exclusive": None,
            "checksum_source": None,
            "checksum_algorithm": None,
            "archive_count": 0,
            "checksum_count": 0,
        }
    )
    destination = _prepare_output(output)
    start_ns = _utc_ns(start)
    end_ns = _utc_ns(end)
    warmup_start = start - timedelta(days=warmup_days)
    loader = BinanceKline1mLoader(sources.trade_klines)
    funding_source = BinanceFundingPaymentSource(
        funding_archives=sources.funding_rates,
        mark_price_archives=sources.mark_price_klines,
    )
    payments = _bounded_funding(funding_source, start_ns=start_ns, end_ns=end_ns)
    configure_strategy = (
        None
        if inventory_bundle is None
        else lambda strategy: configure_inventory_timelines(
            strategy,
            inventory_bundle.timelines,
        )
    )
    result = run_streaming_native_backtest(
        _bounded_minutes(
            loader,
            warmup_start_ns=_utc_ns(warmup_start),
            end_ns=end_ns,
        ),
        state_path=destination / "state.sqlite",
        initial_nav=initial_nav,
        funding_payments=payments,
        execution_start_ns=start_ns,
        execution_end_ns=end_ns,
        configure_strategy=configure_strategy,
    )
    _write_native_reports(result, destination)
    mark_coverage: dict[str, object] = {}
    evidence = build_replay_evidence(
        positions=result.positions,
        fills=result.fills,
        account=result.account,
        state_path=destination / "state.sqlite",
        start=start,
        end=end,
        initial_nav=initial_nav,
        # The native engine matches orders from trade OHLC.  Account cash is
        # native, while unrealized valuation below uses the official completed
        # Binance mark-price close rather than silently reusing trade close.
        final_nav=result.final_nav,
        equity_minutes=_bounded_mark_minutes(
            BinanceMarkPrice1mLoader(sources.mark_price_klines),
            start_ns=start_ns,
            end_ns=end_ns,
            coverage=mark_coverage,
        ),
        final_cash_balance=result.final_balance,
    )
    evidence_metrics = evidence["metrics"]
    if not isinstance(evidence_metrics, dict):
        raise RuntimeError("replay evidence metrics are not mutable")
    mark_final_nav = evidence_metrics.get("final_daily_equity")
    if not isinstance(mark_final_nav, (int, float)) or not math.isfinite(mark_final_nav):
        raise RuntimeError("official mark-price final NAV is unavailable")
    native_trade_close_nav = result.final_nav
    evidence_metrics.update(
        {
            "native_trade_close_final_nav": native_trade_close_nav,
            "final_nav": float(mark_final_nav),
            "final_unrealized_pnl": float(mark_final_nav) - result.final_balance,
            "valuation_basis": "OFFICIAL_BINANCE_COMPLETED_1M_MARK_PRICE_CLOSE",
            "mark_price_coverage": dict(mark_coverage),
            "mark_nav_difference_from_native_trade_close_nav": (
                float(mark_final_nav) - native_trade_close_nav
            ),
        },
    )
    for row in evidence.get("daily_equity", []):
        if isinstance(row, dict):
            row["equity_basis"] = (
                "NATIVE_ACCOUNT_TOTAL_PLUS_FILL_RECONSTRUCTED_"
                "OFFICIAL_BINANCE_MARK_1M_CLOSE"
            )
    evidence_metrics["drawdown_basis"] = (
        "CONTINUOUS_COMPLETED_1M_NATIVE_ACCOUNT_PLUS_OFFICIAL_BINANCE_MARK_MTM"
    )
    evidence_metrics["daily_equity_basis"] = (
        "NATIVE_ACCOUNT_TOTAL_PLUS_OFFICIAL_BINANCE_MARK_MTM_AT_UTC_DAY_END"
    )
    write_replay_evidence(destination, evidence)
    summary: dict[str, object] = {
        "engine": "NautilusTrader BacktestEngine MARGIN/NETTING",
        "provenance": "reuses C29/C35/Candidate05 native one-account paths",
        "source_sha": source_code["git_sha"],
        "source_working_tree_manifest_sha256": source_code[
            "working_tree_manifest_sha256"
        ],
        "policy_decision_fingerprints": (
            evidence_metrics.get("episode_decisions", {}).get(
                "policy_fingerprints",
                [],
            )
            if isinstance(evidence_metrics.get("episode_decisions"), Mapping)
            else []
        ),
        "data_source_manifest_sha256": source_integrity["portable_manifest_sha256"],
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "warmup_days": warmup_days,
        "initial_nav": initial_nav,
        "final_cash_balance": result.final_balance,
        "final_nav": float(mark_final_nav),
        "final_unrealized_pnl": float(mark_final_nav) - result.final_balance,
        "valuation_basis": "OFFICIAL_BINANCE_COMPLETED_1M_MARK_PRICE_CLOSE",
        "native_trade_close_final_nav": native_trade_close_nav,
        "mark_nav_difference_from_native_trade_close_nav": (
            float(mark_final_nav) - native_trade_close_nav
        ),
        "mark_price_coverage": mark_coverage,
        "symbols": list(SYMBOLS),
        "inventory_metrics": dict(inventory_manifest),
        "trade_months": list(sources.trade_months),
        "funding_months": list(sources.funding_months),
        "sources": {
            "trade_klines": _path_map(sources.trade_klines),
            "funding_rates": _path_map(sources.funding_rates),
            "mark_price_klines": _path_map(sources.mark_price_klines),
        },
        "source_integrity": source_integrity,
        "source_code": source_code,
        "native_reports": {
            "state": "state.sqlite",
            "fills": "fills.csv",
            "positions": "positions.csv",
            "account": "account.csv",
            "normalized_closed_trades": "trades.csv",
            "episode_decisions": "episode_decisions.csv",
            "daily_equity": "daily_equity.csv",
            "replay_evidence": "replay_evidence.json",
        },
        "fills_rows": int(len(result.fills)),
        "positions_rows": int(len(result.positions)),
        "account_rows": int(len(result.account)),
        "parent_orders_submitted": result.parent_orders_submitted,
        "protective_pairs_submitted": result.protective_pairs_submitted,
        "plans_blocked_by_global_slot": result.plans_blocked_by_global_slot,
        "max_active_instruments": result.max_active_instruments,
        "missing_flow_bars": result.missing_flow_bars,
        "funding_payments_loaded": len(payments),
        "funding_payments_applied": result.funding_payments_applied,
        "funding_totals": {
            currency: str(amount) for currency, amount in result.funding_totals.items()
        },
        "strategy_evidence": evidence["metrics"],
    }
    (destination / "run.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = [
    "InventoryReplayBundle",
    "InventoryReplaySources",
    "ReplaySourceError",
    "ReplaySources",
    "configure_inventory_timelines",
    "discover_inventory_sources",
    "discover_replay_sources",
    "load_inventory_replay_bundle",
    "months_intersecting",
    "run_native_replay",
    "verify_replay_sources",
]
