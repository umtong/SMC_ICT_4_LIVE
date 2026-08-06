"""Causal contracts for the candidate-08 flow-response auction successor detector."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from aggtrade_flow_response import FlowResponseConfig, FlowResponseState
from aggtrade_flow_response_auction_signals import (
    ABSORPTION_FAMILY,
    INITIATIVE_FAMILY,
    FlowResponseAuctionConfig,
    build_flow_response_auction_signals,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource


def _level(level_id: str, kind: LevelKind, value: float) -> ExternalLevel:
    return ExternalLevel(
        level_id=level_id,
        kind=kind,
        source=LevelSource.FOUR_HOUR,
        level=value,
        formed_index=0,
        formed_time_ns=1,
        period_key="p0",
    )


def _context_bar(ts_event_ns: int) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=0,
        ts_event_ns=ts_event_ns,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
        trade_count=1000.0,
        taker_buy_volume=500.0,
        imbalance=0.0,
        atr=1.0,
        volume_ratio=1.0,
        trade_ratio=1.0,
        efficiency_60m=0.1,
        direction_60m=1.0,
        session_key="s0",
        day_key="d0",
        week_key="w0",
    )


def _bar(
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> dict[str, float]:
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
        "signed_volume": 0.0,
        "trade_count": 100.0,
    }


def _feature(
    *,
    state: FlowResponseState = FlowResponseState.BALANCED_OR_UNRESOLVED,
    direction: int = 1,
    noise: float = 0.2,
) -> dict[str, float | str]:
    if state is FlowResponseState.INITIATIVE_RESPONSE:
        progress = 1.4
        excursion = 1.6
        retention = 0.8
        surprise = 0.3
    elif state is FlowResponseState.ABSORBED_RESPONSE:
        progress = 0.1
        excursion = 0.9
        retention = 0.2
        surprise = -0.7
    else:
        progress = 0.2
        excursion = 0.4
        retention = 0.5
        surprise = 0.0
    return {
        "flow_response_state": state.value,
        "flow_direction": float(direction),
        "flow_consistency": 0.9,
        "window_pressure_ratio": 1.2,
        "progress_noise": progress,
        "excursion_noise": excursion,
        "retention": retention,
        "response_surprise": surprise,
        "causal_noise_reserve": noise,
    }


def _inputs(
    bars: list[dict[str, float]],
    features: list[dict[str, float | str]],
):
    index = pd.date_range("2024-01-01T00:00:10Z", periods=len(bars), freq="10s")
    data = pd.DataFrame(bars, index=index)
    response = pd.DataFrame(features, index=index)
    before = int((index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
    after = int((index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
    levels = (
        _level("completed-high-boundary", LevelKind.HIGH, 100.0),
        _level("completed-high-target", LevelKind.HIGH, 105.0),
        _level("completed-low-target", LevelKind.LOW, 95.0),
    )
    return {
        "data": data,
        "context_times": np.asarray([before, after], dtype=np.int64),
        "context_bars": (_context_bar(before), _context_bar(after)),
        "snapshots": (levels, levels),
        "symbol": "BTCUSDT",
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "tick": 0.1,
        "fee_rate": 0.0006,
        "minimum_net_reward_risk": 1.2,
        "flow_response_features": response,
    }


def _signals(bundle):
    return [signal for values in bundle.signals_by_time_ns.values() for signal in values]


class FlowResponseInitiativeContracts(unittest.TestCase):
    def test_initiative_requires_a_complete_post_interaction_response_window(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1),
            _bar(open_=100.1, high=100.7, low=100.0, close=100.5),
            _bar(open_=100.5, high=101.2, low=100.4, close=101.0),
        ]
        features = [
            _feature(),
            _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=1),
            _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=1),
            _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=1),
        ]
        inputs = _inputs(bars, features)
        bundle = build_flow_response_auction_signals(**inputs)
        signals = _signals(bundle)

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.signal_index, 3)
        self.assertEqual(signal.details["scenario_family"], INITIATIVE_FAMILY)
        self.assertEqual(signal.direction_name, "LONG")
        self.assertEqual(signal.target_id, "completed-high-target")
        self.assertEqual(signal.details["response_window_start_position"], 1)
        self.assertEqual(signal.details["response_window_bars"], 3)
        self.assertLess(signal.structural_stop, signal.details["response_window_low"])
        self.assertGreaterEqual(signal.net_reward_risk, 1.2)
        self.assertEqual(len(signal.events), 3)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "EXTERNAL_LIQUIDITY_INTERACTION_ARMED",
                "PERSISTENT_AGGRESSIVE_FLOW_RESPONSE_OBSERVED",
                "INITIATIVE_PRICE_RESPONSE_CONFIRMED",
            ],
        )
        self.assertEqual(bundle.diagnostics["FLOW_RESPONSE_INTERACTION_ARMED"], 1)
        self.assertEqual(bundle.diagnostics["TRADEABLE_FLOW_RESPONSE_INITIATIVE"], 1)

    def test_response_window_configuration_is_not_hardcoded(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1),
            _bar(open_=100.1, high=100.5, low=100.0, close=100.3),
            _bar(open_=100.3, high=100.8, low=100.2, close=100.6),
            _bar(open_=100.6, high=101.3, low=100.5, close=101.1),
        ]
        features = [_feature() for _ in bars]
        features[4] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=1)
        inputs = _inputs(bars, features)
        config = FlowResponseAuctionConfig(
            response=replace(FlowResponseConfig(), response_window_bars=4)
        )
        bundle = build_flow_response_auction_signals(
            **inputs,
            auction_config=config,
        )
        signal = _signals(bundle)[0]

        self.assertEqual(signal.signal_index, 4)
        self.assertEqual(signal.details["response_window_bars"], 4)
        self.assertEqual(signal.details["response_window_start_position"], 1)
        self.assertEqual(signal.retest_time_ns, int(inputs["data"].index[1].value))

    def test_future_rows_do_not_change_an_already_emitted_initiative_signal(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1),
            _bar(open_=100.1, high=100.7, low=100.0, close=100.5),
            _bar(open_=100.5, high=101.2, low=100.4, close=101.0),
        ]
        features = [_feature(), _feature(), _feature(), _feature(
            state=FlowResponseState.INITIATIVE_RESPONSE,
            direction=1,
        )]
        first_inputs = _inputs(bars, features)
        first = build_flow_response_auction_signals(**first_inputs)
        first_signal = _signals(first)[0]

        extended_bars = bars + [
            _bar(open_=101.0, high=120.0, low=80.0, close=85.0),
            _bar(open_=85.0, high=140.0, low=70.0, close=130.0),
        ]
        extended_features = features + [
            _feature(state=FlowResponseState.ABSORBED_RESPONSE, direction=-1),
            _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=1),
        ]
        second = build_flow_response_auction_signals(
            **_inputs(extended_bars, extended_features)
        )
        second_signal = second.signals_by_time_ns[first_signal.signal_time_ns][0]
        self.assertEqual(first_signal, second_signal)


class FlowResponseAbsorptionContracts(unittest.TestCase):
    def test_absorption_reversal_requires_a_separate_opposite_response_window(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.6, low=99.5, close=100.2),
            _bar(open_=100.2, high=101.0, low=99.9, close=100.1),
            _bar(open_=100.1, high=100.8, low=99.6, close=99.8),
            _bar(open_=99.8, high=101.4, low=99.7, close=99.9),
            _bar(open_=99.9, high=100.2, low=99.4, close=99.5),
            _bar(open_=99.5, high=99.6, low=98.6, close=98.8),
        ]
        features = [_feature() for _ in bars]
        features[3] = _feature(state=FlowResponseState.ABSORBED_RESPONSE, direction=1)
        # This same-direction separation is intentionally insufficient at position 5.
        features[5] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=-1)
        features[6] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=-1)
        inputs = _inputs(bars, features)
        bundle = build_flow_response_auction_signals(**inputs)
        signals = _signals(bundle)

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.signal_index, 6)
        self.assertEqual(signal.details["scenario_family"], ABSORPTION_FAMILY)
        self.assertEqual(signal.direction_name, "SHORT")
        self.assertEqual(signal.target_id, "completed-low-target")
        self.assertGreater(signal.structural_stop, 101.4)
        self.assertEqual(signal.details["sweep_high_at_absorption"], 101.0)
        self.assertEqual(signal.details["sweep_high_through_confirmation"], 101.4)
        self.assertEqual(
            signal.events[1].details["sweep_high_at_absorption"],
            101.0,
        )
        self.assertNotIn(
            "sweep_high_through_confirmation",
            signal.events[1].details,
        )
        self.assertEqual(signal.events[1].event_time_ns, signal.retest_time_ns)
        self.assertLess(signal.events[1].event_time_ns, signal.signal_time_ns)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "EXTERNAL_LIQUIDITY_INTERACTION_ARMED",
                "OUTWARD_FLOW_ABSORBED_AND_RECLAIMED",
                "INWARD_INITIATIVE_RESPONSE_CONFIRMED",
            ],
        )
        self.assertEqual(bundle.diagnostics["OUTWARD_FLOW_RESPONSE_ABSORBED"], 1)
        self.assertEqual(
            bundle.diagnostics["TRADEABLE_FLOW_RESPONSE_ABSORPTION_REVERSAL"],
            1,
        )

    def test_opposite_state_before_a_fully_separate_window_cannot_trade(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.6, low=99.5, close=100.2),
            _bar(open_=100.2, high=101.0, low=99.9, close=100.1),
            _bar(open_=100.1, high=100.8, low=99.6, close=99.8),
            _bar(open_=99.8, high=100.0, low=99.2, close=99.3),
            _bar(open_=99.3, high=99.5, low=98.7, close=98.9),
        ]
        features = [_feature() for _ in bars]
        features[3] = _feature(state=FlowResponseState.ABSORBED_RESPONSE, direction=1)
        features[4] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=-1)
        features[5] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=-1)
        bundle = build_flow_response_auction_signals(**_inputs(bars, features))

        self.assertEqual(_signals(bundle), [])
        self.assertEqual(
            bundle.diagnostics["FLOW_RESPONSE_INTERACTION_UNRESOLVED_AT_DATA_END"],
            1,
        )


class FlowResponseDetectorInfrastructureContracts(unittest.TestCase):
    def test_feature_index_and_columns_must_match_exactly(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1),
        ]
        inputs = _inputs(bars, [_feature(), _feature()])
        shifted = inputs["flow_response_features"].copy()
        shifted.index = shifted.index + pd.Timedelta(seconds=1)
        with self.assertRaises(ValueError):
            build_flow_response_auction_signals(
                **{**inputs, "flow_response_features": shifted}
            )
        missing = inputs["flow_response_features"].drop(columns=["retention"])
        with self.assertRaises(KeyError):
            build_flow_response_auction_signals(
                **{**inputs, "flow_response_features": missing}
            )

    def test_no_target_is_a_rejection_not_a_synthetic_projection(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1),
            _bar(open_=100.1, high=100.7, low=100.0, close=100.5),
            _bar(open_=100.5, high=101.2, low=100.4, close=101.0),
        ]
        features = [_feature(), _feature(), _feature(), _feature(
            state=FlowResponseState.INITIATIVE_RESPONSE,
            direction=1,
        )]
        inputs = _inputs(bars, features)
        boundary_only = (_level("completed-high-boundary", LevelKind.HIGH, 100.0),)
        inputs["snapshots"] = (boundary_only, boundary_only)
        bundle = build_flow_response_auction_signals(**inputs)

        self.assertEqual(_signals(bundle), [])
        self.assertEqual(bundle.diagnostics["NO_ACTIVE_COMPLETED_EXTERNAL_TARGET"], 1)
        self.assertEqual(
            bundle.rejected_scenarios[-1]["reason"],
            "NO_ACTIVE_COMPLETED_EXTERNAL_TARGET",
        )

    def test_source_has_no_outcome_model_score_or_fixed_r_target(self) -> None:
        source = (
            Path(__file__).resolve().parent
            / "aggtrade_flow_response_auction_signals.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "realized_pnl",
            "future_high",
            "future_low",
            "target_hit",
            "stop_hit",
            "win_rate",
            "risk_multiplier",
            "model_score",
            "fixed_r",
            "r_projection",
        ):
            self.assertNotIn(forbidden, source)

    def test_config_expiry_is_derived_from_response_windows(self) -> None:
        config = FlowResponseAuctionConfig(
            response=replace(FlowResponseConfig(), response_window_bars=4),
            interaction_response_windows=3,
            reversal_confirmation_windows=2,
        )
        self.assertEqual(config.interaction_expiry_bars, 12)
        self.assertEqual(config.reversal_expiry_bars, 8)
        with self.assertRaises(ValueError):
            replace(config, interaction_response_windows=0).validate()
        with self.assertRaises(ValueError):
            replace(config, reversal_confirmation_windows=0).validate()


if __name__ == "__main__":
    unittest.main(verbosity=2)
