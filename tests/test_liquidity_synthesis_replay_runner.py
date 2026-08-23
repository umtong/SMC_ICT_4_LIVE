from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import io
from pathlib import Path
import re
from types import SimpleNamespace
import zipfile

import pytest
import pandas as pd

import smc_ict_4.episode_policy_live.replay_runner as replay_runner
from smc_ict_4.episode_policy_live.domain import SYMBOLS
from smc_ict_4.episode_policy_live.replay_runner import (
    ReplaySourceError,
    _bounded_funding,
    _bounded_mark_minutes,
    _bounded_minutes,
    _git_source_provenance,
    _write_native_reports,
    configure_inventory_timelines,
    discover_inventory_sources,
    discover_replay_sources,
    load_inventory_replay_bundle,
    months_intersecting,
    verify_replay_sources,
)
from smc_ict_4.episode_policy_live.cli import parser


def _archive(root: Path, kind: str, symbol: str, month: str) -> Path:
    if kind == "trade":
        path = root / "klines" / symbol / "1m" / f"{symbol}-1m-{month}.zip"
    elif kind == "funding":
        path = root / "fundingRate" / symbol / f"{symbol}-fundingRate-{month}.zip"
    else:
        path = root / "markPriceKlines" / symbol / "1m" / f"{symbol}-1m-{month}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _complete_month(root: Path, month: str, *, trade_only: bool = False) -> None:
    for symbol in SYMBOLS:
        _archive(root, "trade", symbol, month)
        if not trade_only:
            _archive(root, "funding", symbol, month)
            _archive(root, "mark", symbol, month)


def _metrics_day(
    root: Path,
    symbol: str,
    day: date,
    *,
    rows: int = 288,
    extra_rows: tuple[str, ...] = (),
) -> Path:
    directory = root / symbol
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{symbol}-metrics-{day.isoformat()}.zip"
    start_ms = int(
        datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000,
    )
    buffer = io.StringIO()
    buffer.write(
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n",
    )
    for index in range(rows):
        buffer.write(
            f"{start_ms + index * 300_000},{symbol},{100000-index},10000000,"
            "1.1,1.2,1.0,0.9\n",
        )
    for row in extra_rows:
        buffer.write(row.rstrip("\n") + "\n")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"{symbol}-metrics-{day.isoformat()}.csv", buffer.getvalue())
    digest = sha256(archive.read_bytes()).hexdigest()
    Path(str(archive) + ".CHECKSUM").write_text(
        f"{digest}  {archive.name}\n",
        encoding="utf-8",
    )
    return archive


def _complete_metrics(root: Path, first: date, end_exclusive: date) -> None:
    day = first
    while day < end_exclusive:
        for symbol in SYMBOLS:
            _metrics_day(root, symbol, day)
        day += timedelta(days=1)


def test_months_intersect_half_open_range() -> None:
    assert months_intersecting(date(2024, 1, 31), date(2024, 3, 1)) == (
        "2024-01",
        "2024-02",
    )


def test_discovers_split_roots_and_warmup_trade_only_month(tmp_path: Path) -> None:
    older = tmp_path / "older" / "monthly"
    newer = tmp_path / "newer" / "monthly"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    _complete_month(older, "2023-12", trade_only=True)
    _complete_month(newer, "2024-01")

    found = discover_replay_sources(
        [older, newer],
        start=date(2024, 1, 1),
        end=date(2024, 2, 1),
        warmup_days=10,
    )

    assert found.trade_months == ("2023-12", "2024-01")
    assert found.funding_months == ("2024-01",)
    assert found.trade_klines["BTCUSDT"] == (
        older / "klines" / "BTCUSDT" / "1m" / "BTCUSDT-1m-2023-12.zip",
        newer / "klines" / "BTCUSDT" / "1m" / "BTCUSDT-1m-2024-01.zip",
    )


def test_ignores_noncanonical_repair_copy_and_requires_canonical(tmp_path: Path) -> None:
    root = tmp_path / "monthly"
    root.mkdir()
    _complete_month(root, "2024-01")
    canonical = root / "klines" / "BTCUSDT" / "1m" / "BTCUSDT-1m-2024-01.zip"
    canonical.unlink()
    (canonical.parent / "BTCUSDT-1m-2024-01-repaired.zip").touch()

    with pytest.raises(ReplaySourceError, match="missing canonical trade archive"):
        discover_replay_sources(
            [root],
            start=date(2024, 1, 1),
            end=date(2024, 2, 1),
            warmup_days=0,
        )


def test_rejects_ambiguous_canonical_archive_across_roots(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _complete_month(first, "2024-01")
    _archive(second, "trade", "BTCUSDT", "2024-01")

    with pytest.raises(ReplaySourceError, match="ambiguous canonical trade archive"):
        discover_replay_sources(
            [first, second],
            start=date(2024, 1, 1),
            end=date(2024, 2, 1),
            warmup_days=0,
        )


def test_rejects_missing_or_nondirectory_root(tmp_path: Path) -> None:
    with pytest.raises(ReplaySourceError, match="does not exist"):
        discover_replay_sources(
            [tmp_path / "missing"],
            start=date(2024, 1, 1),
            end=date(2024, 2, 1),
            warmup_days=0,
        )


def test_verifies_every_selected_official_checksum(tmp_path: Path) -> None:
    root = tmp_path / "monthly"
    root.mkdir()
    _complete_month(root, "2024-01")
    selected = discover_replay_sources(
        [root], start=date(2024, 1, 1), end=date(2024, 2, 1), warmup_days=0,
    )
    for mapping in (
        selected.trade_klines,
        selected.funding_rates,
        selected.mark_price_klines,
    ):
        for paths in mapping.values():
            for path in paths:
                payload = f"official:{path.name}".encode()
                path.write_bytes(payload)
                digest = sha256(payload).hexdigest()
                path.with_name(path.name + ".CHECKSUM").write_text(
                    f"{digest}  {path.name}\n",
                    encoding="utf-8",
                )

    evidence = verify_replay_sources(selected)

    assert evidence["all_verified"] is True
    assert evidence["archive_count"] == 12
    assert len(evidence["portable_manifest_sha256"]) == 64
    assert evidence["archives"]["trade_klines"]["BTCUSDT"][0]["checksum_verified"] is True


def test_rejects_missing_or_mismatched_official_checksum(tmp_path: Path) -> None:
    root = tmp_path / "monthly"
    root.mkdir()
    _complete_month(root, "2024-01")
    selected = discover_replay_sources(
        [root], start=date(2024, 1, 1), end=date(2024, 2, 1), warmup_days=0,
    )
    with pytest.raises(ReplaySourceError, match="missing official CHECKSUM"):
        verify_replay_sources(selected)

    first = selected.trade_klines["BTCUSDT"][0]
    first.with_name(first.name + ".CHECKSUM").write_text(
        f"{'0' * 64}  {first.name}\n",
        encoding="utf-8",
    )
    with pytest.raises(ReplaySourceError, match="SHA-256 mismatch"):
        verify_replay_sources(selected)


def test_bounded_sorted_streams_stop_after_end_without_reading_tail() -> None:
    def stream():
        yield SimpleNamespace(ts_event=60)
        yield SimpleNamespace(ts_event=120)
        yield SimpleNamespace(ts_event=180)
        raise AssertionError("out-of-range tail was consumed")

    assert [item.ts_event for item in _bounded_minutes(
        stream(), warmup_start_ns=0, end_ns=120,
    )] == [60, 120]
    assert [item.ts_event for item in _bounded_funding(
        stream(), start_ns=0, end_ns=180,
    )] == [60, 120]


def test_bounded_mark_minutes_prove_exact_completed_coverage() -> None:
    minute_ns = 60 * 1_000_000_000
    coverage: dict[str, object] = {}
    values = [
        SimpleNamespace(ts_event=minute_ns),
        SimpleNamespace(ts_event=2 * minute_ns),
        SimpleNamespace(ts_event=3 * minute_ns),
    ]

    selected = list(_bounded_mark_minutes(
        values,
        start_ns=0,
        end_ns=2 * minute_ns,
        coverage=coverage,
    ))

    assert [item.ts_event for item in selected] == [minute_ns, 2 * minute_ns]
    assert coverage["status"] == "COMPLETE"
    assert coverage["observations"] == 2
    assert coverage["price_field"] == "official completed 1m mark-price kline close"


def test_git_source_provenance_contains_a_commit_sha() -> None:
    provenance = _git_source_provenance()
    assert re.fullmatch(r"[0-9a-f]{40}", str(provenance["git_sha"]))
    assert provenance["git_dirty"] in {True, False, None}
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        str(provenance["working_tree_manifest_sha256"]),
    )
    assert provenance["working_tree_manifest_files"] == len(
        provenance["working_tree_manifest"],
    )


def test_replay_promotes_official_mark_nav_and_keeps_native_trade_nav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    minute_ns = 60 * 1_000_000_000
    start_ns = 1_704_067_200_000_000_000
    empty_paths = {symbol: () for symbol in SYMBOLS}
    sources = SimpleNamespace(
        trade_klines=empty_paths,
        funding_rates=empty_paths,
        mark_price_klines=empty_paths,
        trade_months=(),
        funding_months=(),
    )
    mark_minutes = [
        SimpleNamespace(
            ts_event=start_ns + index * minute_ns,
            bars={symbol: SimpleNamespace(close=95.0) for symbol in SYMBOLS},
        )
        for index in range(1, 1_441)
    ]
    result = SimpleNamespace(
        fills=[], positions=[], account=[], final_balance=100.0, final_nav=90.0,
        parent_orders_submitted=0, protective_pairs_submitted=0,
        plans_blocked_by_global_slot=0, max_active_instruments=0,
        missing_flow_bars=0, funding_payments_applied=0, funding_totals={},
    )

    monkeypatch.setattr(replay_runner, "discover_replay_sources", lambda *args, **kwargs: sources)
    monkeypatch.setattr(
        replay_runner,
        "verify_replay_sources",
        lambda value: {"all_verified": True, "portable_manifest_sha256": "b" * 64},
    )
    monkeypatch.setattr(
        replay_runner,
        "_git_source_provenance",
        lambda: {
            "git_sha": "a" * 40,
            "git_dirty": False,
            "working_tree_manifest_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(replay_runner, "BinanceKline1mLoader", lambda value: ())
    monkeypatch.setattr(replay_runner, "BinanceFundingPaymentSource", lambda **kwargs: ())
    monkeypatch.setattr(replay_runner, "BinanceMarkPrice1mLoader", lambda value: mark_minutes)
    monkeypatch.setattr(replay_runner, "run_streaming_native_backtest", lambda *args, **kwargs: result)
    monkeypatch.setattr(replay_runner, "_write_native_reports", lambda *args, **kwargs: None)

    def evidence_builder(**kwargs):
        observed = list(kwargs["equity_minutes"])
        assert len(observed) == 1_440
        assert observed[-1].bars["BTCUSDT"].close == 95.0
        return {
            "metrics": {"final_daily_equity": 95.0},
            "trades": [],
            "daily_equity": [{"equity": 95.0, "equity_basis": "old"}],
        }

    monkeypatch.setattr(replay_runner, "build_replay_evidence", evidence_builder)
    monkeypatch.setattr(replay_runner, "write_replay_evidence", lambda *args, **kwargs: None)

    summary = replay_runner.run_native_replay(
        monthly_roots=[tmp_path],
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        warmup_days=0,
        output=tmp_path / "out",
        initial_nav=100.0,
    )

    assert summary["final_nav"] == 95.0
    assert summary["native_trade_close_final_nav"] == 90.0
    assert summary["mark_nav_difference_from_native_trade_close_nav"] == 5.0
    assert summary["mark_price_coverage"]["observations"] == 1_440
    assert summary["valuation_basis"] == "OFFICIAL_BINANCE_COMPLETED_1M_MARK_PRICE_CLOSE"
    assert summary["source_sha"] == "a" * 40
    assert summary["data_source_manifest_sha256"] == "b" * 64


def test_native_csv_reports_preserve_named_identity_indices(tmp_path: Path) -> None:
    result = SimpleNamespace(
        fills=pd.DataFrame(
            [{"side": "BUY"}],
            index=pd.Index(["ORDER-1"], name="client_order_id"),
        ),
        positions=pd.DataFrame(
            [{"opening_order_id": "ORDER-1"}],
            index=pd.Index(["POSITION-1"], name="position_id"),
        ),
        account=pd.DataFrame(
            [{"total": 100}],
            # Nautilus 1.230 account reports currently carry an unnamed
            # timestamp index; persistence gives it the explicit contract.
            index=pd.Index([123]),
        ),
    )

    _write_native_reports(result, tmp_path)

    assert (tmp_path / "fills.csv").read_text(encoding="utf-8").startswith(
        "client_order_id,",
    )
    assert (tmp_path / "positions.csv").read_text(encoding="utf-8").startswith(
        "position_id,",
    )
    assert (tmp_path / "account.csv").read_text(encoding="utf-8").startswith(
        "ts_event,",
    )


def test_inventory_sources_load_exact_start_minus_one_day_coverage(tmp_path: Path) -> None:
    root = tmp_path / "daily" / "metrics"
    _complete_metrics(root, date(2024, 1, 1), date(2024, 1, 3))
    sources = discover_inventory_sources(
        root,
        start=date(2024, 1, 2),
        end=date(2024, 1, 3),
    )
    assert sources.start == date(2024, 1, 1)
    assert all(len(sources.archives[symbol]) == 2 for symbol in SYMBOLS)
    bundle = load_inventory_replay_bundle(sources)
    assert set(bundle.timelines) == set(SYMBOLS)
    assert bundle.manifest["status"] == "LOADED"
    assert bundle.manifest["archive_count"] == 8
    assert bundle.manifest["checksum_count"] == 8
    for symbol in SYMBOLS:
        assert len(bundle.timelines[symbol].points) == 576


def test_inventory_discovery_fails_on_missing_checksum_or_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "metrics"
    _complete_metrics(root, date(2024, 1, 1), date(2024, 1, 3))
    checksum = root / "BTCUSDT" / "BTCUSDT-metrics-2024-01-01.zip.CHECKSUM"
    checksum.unlink()
    with pytest.raises(ReplaySourceError, match="missing canonical metrics checksum"):
        discover_inventory_sources(
            root,
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
        )
    checksum.write_text("0" * 64 + "\n", encoding="utf-8")
    duplicate = root / "repair" / "BTCUSDT-metrics-2024-01-01.zip"
    duplicate.parent.mkdir()
    duplicate.touch()
    with pytest.raises(ReplaySourceError, match="duplicate canonical metrics archive"):
        discover_inventory_sources(
            root,
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
        )


def test_inventory_loader_preserves_missing_intraday_observation_as_gap(tmp_path: Path) -> None:
    root = tmp_path / "metrics"
    for symbol in SYMBOLS:
        _metrics_day(root, symbol, date(2024, 1, 1))
        _metrics_day(
            root,
            symbol,
            date(2024, 1, 2),
            rows=287 if symbol == "ETHUSDT" else 288,
        )
    sources = discover_inventory_sources(
        root,
        start=date(2024, 1, 2),
        end=date(2024, 1, 3),
    )
    bundle = load_inventory_replay_bundle(sources)
    coverage = bundle.manifest["symbols"]["ETHUSDT"]
    assert coverage["observations"] == 575
    assert coverage["gap_count"] == 1
    assert coverage["missing_observations"] == 1


def test_inventory_loader_merges_official_conflict_and_records_both_sources(
    tmp_path: Path,
) -> None:
    root = tmp_path / "metrics"
    first = date(2024, 1, 1)
    second = date(2024, 1, 2)
    second_start_ms = int(
        datetime(second.year, second.month, second.day, tzinfo=timezone.utc).timestamp()
        * 1000,
    )
    for symbol in SYMBOLS:
        extra = (
            f"{second_start_ms},{symbol},90000,9000000,1.3,1.4,1.2,1.1",
        ) if symbol == "BTCUSDT" else ()
        _metrics_day(root, symbol, first, extra_rows=extra)
        _metrics_day(root, symbol, second)
    sources = discover_inventory_sources(root, start=second, end=date(2024, 1, 3))
    bundle = load_inventory_replay_bundle(sources)
    coverage = bundle.manifest["symbols"]["BTCUSDT"]
    assert coverage["conflicting_duplicates_merged_unknown"] == 1
    assert coverage["gap_count"] == 0
    evidence = coverage["conflicting_duplicate_evidence"][0]
    assert evidence["source_archives"] == [
        "BTCUSDT-metrics-2024-01-01.zip",
        "BTCUSDT-metrics-2024-01-02.zip",
    ]
    assert len(evidence["source_archive_sha256"]) == 2

    timeline = bundle.timelines["BTCUSDT"]
    conflict = next(
        point for point in timeline.points if point.source_ts_ns == second_start_ms * 1_000_000
    )
    assert conflict.open_interest is None
    blocked = timeline.evaluate(
        shock_side="SELL",
        episode_start_ns=conflict.source_ts_ns,
        decision_ts_ns=conflict.observed_ts_ns + 10 * 60 * 1_000_000_000,
        price_move=-1.0,
        signed_taker_flow=-0.1,
    )
    assert blocked.regime.value == "UNKNOWN"
    resumed = timeline.evaluate(
        shock_side="SELL",
        episode_start_ns=conflict.source_ts_ns,
        decision_ts_ns=conflict.observed_ts_ns + 20 * 60 * 1_000_000_000,
        price_move=-1.0,
        signed_taker_flow=-0.1,
    )
    assert resumed.regime.value == "POSITION_RESET"


def test_inventory_loader_excludes_one_last_archive_end_boundary_overlap(
    tmp_path: Path,
) -> None:
    root = tmp_path / "metrics"
    first = date(2024, 1, 1)
    second = date(2024, 1, 2)
    end = date(2024, 1, 3)
    end_ms = int(datetime(2024, 1, 3, tzinfo=timezone.utc).timestamp() * 1000)
    for symbol in SYMBOLS:
        _metrics_day(root, symbol, first)
        extra = (
            f"{end_ms},{symbol},90000,9000000,1.3,1.4,1.2,1.1",
        ) if symbol == "BTCUSDT" else ()
        _metrics_day(root, symbol, second, extra_rows=extra)

    bundle = load_inventory_replay_bundle(
        discover_inventory_sources(root, start=second, end=end),
    )
    btc = bundle.manifest["symbols"]["BTCUSDT"]
    assert btc["observations"] == 576
    assert btc["end_boundary_overlap_observations_excluded"] == 1
    assert btc["end_boundary_overlap_evidence"][0]["source_archive"] == (
        "BTCUSDT-metrics-2024-01-02.zip"
    )
    assert bundle.manifest["end_boundary_overlap_observations_excluded"] == 1
    assert bundle.timelines["BTCUSDT"].points[-1].nominal_ts_ns < end_ms * 1_000_000


def test_inventory_loader_rejects_observation_beyond_single_end_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "metrics"
    first = date(2024, 1, 1)
    second = date(2024, 1, 2)
    end = date(2024, 1, 3)
    beyond_ms = int(datetime(2024, 1, 3, tzinfo=timezone.utc).timestamp() * 1000) + 300_000
    for symbol in SYMBOLS:
        _metrics_day(root, symbol, first)
        extra = (
            f"{beyond_ms},{symbol},90000,9000000,1.3,1.4,1.2,1.1",
        ) if symbol == "BTCUSDT" else ()
        _metrics_day(root, symbol, second, extra_rows=extra)

    with pytest.raises(ReplaySourceError, match="outside requested archive coverage"):
        load_inventory_replay_bundle(
            discover_inventory_sources(root, start=second, end=end),
        )


def test_native_strategy_policies_receive_each_timeline() -> None:
    policies = {
        symbol: SimpleNamespace(inventory_timeline=None)
        for symbol in SYMBOLS
    }
    strategy = SimpleNamespace(coordinator=SimpleNamespace(policies=policies))
    timelines = {symbol: object() for symbol in SYMBOLS}
    configure_inventory_timelines(strategy, timelines)  # type: ignore[arg-type]
    assert all(policies[symbol].inventory_timeline is timelines[symbol] for symbol in SYMBOLS)


def test_replay_cli_accepts_optional_metrics_root(tmp_path: Path) -> None:
    parsed = parser().parse_args([
        "replay",
        "--start", "2024-01-02",
        "--end", "2024-01-03",
        "--monthly-root", str(tmp_path / "monthly"),
        "--metrics-root", str(tmp_path / "daily" / "metrics"),
        "--output", str(tmp_path / "out"),
    ])
    assert parsed.metrics_root == tmp_path / "daily" / "metrics"
