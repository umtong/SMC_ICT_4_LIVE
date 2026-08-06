#!/usr/bin/env python3
"""Patch candidate-01 so Nautilus matches official Binance one-minute bars.

Equal-notional bars remain signal-generation data only.  The authoritative
execution adapter receives official USD-M one-minute klines, maps each completed
signal to the first observable execution bar, and submits through NautilusTrader
on the following completed bar.  This script is deterministic and refuses to
continue if an expected source block is absent.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "research" / "candidate-01"
TESTS = ROOT / "tests"


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}: {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def patch_adapter() -> None:
    path = CANDIDATE / "nautilus_plan_backtest.py"
    replace_once(
        path,
        "from __future__ import annotations\n\nfrom dataclasses",
        "from __future__ import annotations\n\nfrom bisect import bisect_left\nfrom dataclasses",
    )
    replace_once(
        path,
        '        "market_data_for_execution": "causal completed equal-notional event bars",\n'
        '        "entry_semantics": (\n'
        '            "signal on completed event; market bracket submitted on next "\n'
        '            "completed event; NautilusTrader owns fill and contingent orders"\n'
        '        ),',
        '        "market_data_for_execution": (\n'
        '            "official Binance Vision USD-M one-minute external bars"\n'
        '        ),\n'
        '        "entry_semantics": (\n'
        '            "equal-notional signal mapped to first completed one-minute bar; "\n'
        '            "market bracket evaluated on the following completed one-minute "\n'
        '            "bar; NautilusTrader owns fills and contingent orders"\n'
        '        ),',
    )
    replace_once(
        path,
        "    features: Sequence[EventFeature],\n    plans: Sequence[ScenarioPlan],",
        "    features: Sequence[EventFeature],\n    execution_frame: pd.DataFrame,\n    plans: Sequence[ScenarioPlan],",
    )
    replace_once(
        path,
        '    if not features:\n        raise ValueError("features cannot be empty")\n',
        '    if not features:\n        raise ValueError("features cannot be empty")\n'
        '    required_execution_columns = {\n'
        '        "close_dt", "open", "high", "low", "close", "base_volume"\n'
        '    }\n'
        '    missing_execution_columns = sorted(\n'
        '        required_execution_columns - set(execution_frame.columns)\n'
        '    )\n'
        '    if missing_execution_columns:\n'
        '        raise ValueError(\n'
        '            f"execution_frame missing columns: {missing_execution_columns}"\n'
        '        )\n'
        '    if execution_frame.empty:\n'
        '        raise ValueError("execution_frame cannot be empty")\n',
    )
    replace_once(
        path,
        '    evaluation_bar_times = [\n'
        '        int(item.bar.end_time_ns)\n'
        '        for item in features\n'
        '        if start_ns <= item.bar.end_time_ns < end_ns\n'
        '    ]\n'
        '    if not evaluation_bar_times:\n'
        '        raise ValueError("no completed event bars in evaluation interval")\n'
        '    force_exit_ts_ns = max(evaluation_bar_times)\n\n'
        '    plans_by_signal_time: dict[int, list[ScenarioPlan]] = {}\n'
        '    for plan in sorted(\n'
        '        plans,\n'
        '        key=lambda item: (item.signal_time_ns, item.scenario_id),\n'
        '    ):\n'
        '        if not start_ns <= plan.signal_time_ns < end_ns:\n'
        '            continue\n'
        '        plans_by_signal_time.setdefault(plan.signal_time_ns, []).append(plan)\n',
        '    ordered_execution = (\n'
        '        execution_frame.copy()\n'
        '        .sort_values("close_dt", kind="stable")\n'
        '        .drop_duplicates("close_dt", keep="last")\n'
        '        .reset_index(drop=True)\n'
        '    )\n'
        '    execution_bar_times = [\n'
        '        int(pd.Timestamp(value).as_unit("ns").value)\n'
        '        for value in ordered_execution["close_dt"]\n'
        '    ]\n'
        '    evaluation_bar_times = [\n'
        '        value for value in execution_bar_times if start_ns <= value < end_ns\n'
        '    ]\n'
        '    if not evaluation_bar_times:\n'
        '        raise ValueError("no completed one-minute execution bars in interval")\n'
        '    force_exit_ts_ns = max(evaluation_bar_times)\n\n'
        '    plans_by_signal_time: dict[int, list[ScenarioPlan]] = {}\n'
        '    for plan in sorted(\n'
        '        plans,\n'
        '        key=lambda item: (item.signal_time_ns, item.scenario_id),\n'
        '    ):\n'
        '        if not start_ns <= plan.signal_time_ns < end_ns:\n'
        '            continue\n'
        '        activation_index = bisect_left(\n'
        '            execution_bar_times,\n'
        '            int(plan.signal_time_ns),\n'
        '        )\n'
        '        if activation_index >= len(execution_bar_times):\n'
        '            continue\n'
        '        activation_time_ns = execution_bar_times[activation_index]\n'
        '        if not start_ns <= activation_time_ns < end_ns:\n'
        '            continue\n'
        '        plans_by_signal_time.setdefault(activation_time_ns, []).append(plan)\n',
    )
    old_bars = '''    bar_type = BarType.from_str(
        "BTCUSDT-PERP.BINANCE-1-TICK-LAST-EXTERNAL",
    )

    bars: list[Bar] = []
    for feature in features:
        source = feature.bar
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(float(source.open), instrument.price_precision),
                high=Price(float(source.high), instrument.price_precision),
                low=Price(float(source.low), instrument.price_precision),
                close=Price(float(source.close), instrument.price_precision),
                volume=Quantity(
                    float(source.base_quantity),
                    instrument.size_precision,
                ),
                ts_event=int(source.end_time_ns),
                ts_init=int(source.end_time_ns),
            ),
        )
'''
    new_bars = '''    # NautilusTrader's OHLC matching path requires a time-based BarType.
    # Equal-notional bars are therefore never registered as execution bars.
    # Official Binance Vision one-minute bars carry all matching events.
    bar_type = BarType.from_str(
        "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
    )

    bars: list[Bar] = []
    for row in ordered_execution.itertuples(index=False):
        ts_event = int(pd.Timestamp(row.close_dt).as_unit("ns").value)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(float(row.open), instrument.price_precision),
                high=Price(float(row.high), instrument.price_precision),
                low=Price(float(row.low), instrument.price_precision),
                close=Price(float(row.close), instrument.price_precision),
                volume=Quantity(
                    float(row.base_volume),
                    instrument.size_precision,
                ),
                ts_event=ts_event,
                ts_init=ts_event,
            ),
        )
'''
    replace_once(path, old_bars, new_bars)
    replace_once(
        path,
        '                "entry_delay": "one completed equal-notional event",',
        '                "entry_delay": (\n'
        '                    "signal observation mapped to one-minute execution clock; "\n'
        '                    "submission on following completed one-minute bar"\n'
        '                ),\n'
        '                "execution_market_data": (\n'
        '                    "official Binance Vision USD-M one-minute klines"\n'
        '                ),',
    )


def patch_runner(filename: str, *, period: bool) -> None:
    path = CANDIDATE / filename
    replace_once(
        path,
        "from data import parse_utc_date  # noqa: E402",
        "from data import load_interval, parse_utc_date  # noqa: E402",
    )
    marker = '''    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=DAILY_CANDIDATE_MINUTES,
    )
'''
    addition = marker + '''    execution_frame, execution_records = load_interval(
        symbol="BTCUSDT",
        start=evaluation_start,
        end=evaluation_end,
        cache_dir=args.cache / "execution-klines",
        warmup_minutes=2,
    )
'''
    replace_once(path, marker, addition)
    replace_once(
        path,
        "        features=features,\n        plans=plans,",
        "        features=features,\n        execution_frame=execution_frame,\n        plans=plans,",
    )
    replace_once(
        path,
        '        "custom_pnl_or_nav_ledger": False,',
        '        "custom_pnl_or_nav_ledger": False,\n'
        '        "execution_market_data": (\n'
        '            "official Binance Vision USD-M one-minute klines"\n'
        '        ),',
    )
    replace_once(
        path,
        '        "downloads": [record.to_dict() for record in records],',
        '        "signal_downloads": [record.to_dict() for record in records],\n'
        '        "execution_downloads": [\n'
        '            asdict(record) for record in execution_records\n'
        '        ],',
    )


def patch_tests() -> None:
    path = TESTS / "test_candidate_01_authoritative_nautilus.py"
    insertion = '''
    def test_execution_uses_official_time_based_bars(self) -> None:
        adapter = (CANDIDATE / "nautilus_plan_backtest.py").read_text(
            encoding="utf-8",
        )
        self.assertNotIn("1-TICK-LAST-EXTERNAL", adapter)
        self.assertIn("1-MINUTE-LAST-EXTERNAL", adapter)
        self.assertIn("execution_frame", adapter)
        for path in OFFICIAL:
            source = path.read_text(encoding="utf-8")
            self.assertIn("load_interval", source)
            self.assertIn("execution_frame=execution_frame", source)
'''
    replace_once(
        path,
        '\n\nif __name__ == "__main__":\n    unittest.main()\n',
        insertion + '\n\nif __name__ == "__main__":\n    unittest.main()\n',
    )


def main() -> None:
    patch_adapter()
    patch_runner("intrinsic_external_liquidity_v4_nautilus_week.py", period=False)
    patch_runner("intrinsic_external_liquidity_v4_nautilus_period.py", period=True)
    patch_tests()
    print("patched authoritative Nautilus execution to official one-minute bars")


if __name__ == "__main__":
    main()
