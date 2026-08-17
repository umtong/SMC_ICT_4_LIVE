from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace

from ml1_features import CausalFeatureBook, FEATURE_NAMES, build_plan_features


class Side(Enum):
    LONG = 1
    SHORT = -1


@dataclass
class Candle:
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 1.0
    quote_volume: float = 100.0


def _plan() -> SimpleNamespace:
    return SimpleNamespace(
        plan_id="p1",
        symbol="BTCUSDT",
        family="LOCAL_OB_FVG_CONTINUATION",
        side=Side.LONG,
        entry=100.0,
        stop=99.0,
        target=101.5,
        gross_rr=1.5,
        scenario_path="ACCEPTANCE",
        scale_name="15_5_1",
        higher_zone_kind="ORDER_BLOCK",
        lower_zone_kind="FVG",
        trigger_zone_kind="ORDER_BLOCK",
        target_zone_kind="SWING_HIGH",
        higher_zone_id="H",
        lower_zone_id="L",
        trigger_zone_id="H",
        target_zone_id="T",
        higher_strength_ratio=2.0,
        lower_strength_ratio=1.5,
        trigger_strength_ratio=1.0,
        source_rule_count=3,
        higher_timeframe_minutes=60,
        decision_timeframe_minutes=15,
        trigger_timeframe_minutes=1,
        overlap_lower=99.8,
        overlap_upper=100.1,
        setup_observed_time_ns=1_000_000_000,
        interaction_time_ns=61_000_000_000,
        trigger_time_ns=121_000_000_000,
        observed_time_ns=181_000_000_000,
        rule_provenance=("FLOW", "FIRST_RETURN"),
    )


def test_feature_schema_has_no_symbol_identity() -> None:
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
    assert not any("symbol" in name.lower() for name in FEATURE_NAMES)


def test_prior_only_feature_book_and_exact_schema() -> None:
    book = CausalFeatureBook()
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    for minute in range(1, 65):
        items = []
        for offset, symbol in enumerate(symbols):
            base = 100.0 + offset
            close = base * (1.0 + minute * 0.00001 * (1 if offset < 3 else -1))
            items.append(
                (
                    symbol,
                    1,
                    Candle(
                        ts_close_ns=minute * 60_000_000_000,
                        open=base,
                        high=max(base, close) + 0.1,
                        low=min(base, close) - 0.1,
                        close=close,
                        quote_volume=100.0 + minute,
                    ),
                )
            )
        # Include higher timeframes only at their closes, just as the composite
        # strategy does.  The latest observation remains available between them.
        if minute % 5 == 0:
            for offset, symbol in enumerate(symbols):
                base = 100.0 + offset
                items.append((symbol, 5, Candle(minute * 60_000_000_000, base, base + 0.2, base - 0.2, base + 0.05)))
        if minute % 15 == 0:
            for offset, symbol in enumerate(symbols):
                base = 100.0 + offset
                items.append((symbol, 15, Candle(minute * 60_000_000_000, base, base + 0.3, base - 0.3, base + 0.08)))
        if minute % 60 == 0:
            for offset, symbol in enumerate(symbols):
                base = 100.0 + offset
                items.append((symbol, 60, Candle(minute * 60_000_000_000, base, base + 0.5, base - 0.5, base + 0.1)))
        book.observe_bucket(items)

    features = build_plan_features(
        _plan(),
        feature_book=book,
        macro_side=Side.LONG,
        factor_state=SimpleNamespace(
            side=Side.LONG,
            event_time_ns=120_000_000_000,
            agreeing_symbols=symbols,
            sequence=2,
        ),
        flow_observation=SimpleNamespace(
            price_range=1.0,
            delta_share=0.2,
            body=0.3,
            close_location=0.8,
            active=True,
            directed=True,
            material_progress=True,
            activity_ratio=2.0,
            delta_ratio=1.5,
            body_ratio=1.2,
            range_ratio=1.1,
            trade_size_ratio=1.0,
            impact_per_activity=0.6,
        ),
    )
    assert tuple(features) == FEATURE_NAMES
    assert features["macro_aligned"] == 1.0
    assert features["factor_aligned"] == 1.0
    assert features["flow_aligned_initiative"] == 1.0
    assert features["cross_available"] == 1.0
    assert features["tf1_history_fraction"] == 1.0
    assert features["higher_strength"] == 2.0
    assert features["lower_strength"] == 1.5
    assert features["trigger_strength"] == 1.0
    assert features["confluence_strength"] >= 4.5
    assert features["mechanism_order_block"] == 1.0
    assert features["mechanism_fvg"] == 1.0
    assert features["mechanism_continuation"] == 1.0
    assert features["mechanism_flow"] == 0.0
    assert features["mechanism_channel"] == 0.0
    assert features["mechanism_wedge"] == 0.0
    assert features["mechanism_liquidity_sweep"] == 0.0
    assert abs(features["source_rule_count_log"] - 1.3862943611198906) < 1e-12
