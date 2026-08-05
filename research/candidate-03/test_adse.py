from __future__ import annotations

from dataclasses import replace
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from adse_engine import (
    AdseReplay,
    expected_loss_budget_per_unit,
    target_trigger_price,
)
from adse_features import build_regime_ratios
from adse_model import (
    AdseConfig,
    FiveMinuteBar,
    FiveMinuteState,
    MinuteBar,
    ScenarioSignal,
)
from select_adse_weeks import FROZEN, select


def minute(start: int, close: float = 100.0, spread: float = 2.0) -> MinuteBar:
    return MinuteBar(
        start, close - 0.2, close + spread / 2, close - spread / 2, close,
        1.0, 100.0, 10.0, 1, 1, 1, start, start + 1,
    )


def five(boundary: int, close: float = 100.0) -> FiveMinuteBar:
    return FiveMinuteBar(boundary, close - 1, close + 1, close - 1, close, 5, 500, 50, 5)


class SelectionTests(unittest.TestCase):
    def test_validation_selection_is_frozen(self) -> None:
        self.assertEqual(select(), FROZEN)


class ConfigTests(unittest.TestCase):
    def test_overlap_band_is_deliberate_and_valid(self) -> None:
        config = AdseConfig(); config.validate()
        self.assertLess(config.tpr_regime_ratio_min, config.lcpt_regime_ratio_max)
        with self.assertRaises(ValueError):
            replace(config, tpr_regime_ratio_min=1.5, lcpt_regime_ratio_max=1.4).validate()


class RegimeTests(unittest.TestCase):
    def test_current_oi_change_and_current_minute_cannot_change_current_regime(self) -> None:
        config = replace(
            AdseConfig(),
            atr_minutes=2,
            regime_oi_lookback_states=4,
            regime_oi_min_states=2,
            regime_atr_lookback_minutes=10,
            regime_atr_min_minutes=5,
        )
        ns = 60_000_000_000
        minutes = {i * ns: minute(i * ns, 100 + (i % 3) * 0.1) for i in range(25)}
        states = []
        for i, boundary_minute in enumerate((5, 10, 15, 20)):
            boundary = boundary_minute * ns
            states.append(FiveMinuteState(
                boundary, five(boundary), five(boundary), 1000.0,
                1.0, None if i == 0 else float(i),
            ))
        before = build_regime_ratios(config, minutes, states)[20 * ns]
        changed_states = list(states)
        changed_states[-1] = replace(changed_states[-1], open_interest_change_bps=10_000.0)
        minutes[20 * ns] = minute(20 * ns, 100.0, spread=1000.0)
        after = build_regime_ratios(config, minutes, changed_states)[20 * ns]
        self.assertAlmostEqual(before, after)


class AccountingTests(unittest.TestCase):
    def test_quantity_uses_exact_three_percent_loss_budget(self) -> None:
        config = AdseConfig(); profile = config.lcpt_exit
        entry, _, max_funding, loss = expected_loss_budget_per_unit(config, profile, 100.0, 98.0, 1)
        planned = config.initial_nav * config.risk_fraction
        quantity = planned / loss
        self.assertAlmostEqual(quantity * loss, 3000.0)
        raw_target = target_trigger_price(config, profile, entry, loss, 1, max_funding)
        fee = config.taker_fee_bps / 10_000.0
        slip = config.slippage_impact_bps / 10_000.0
        exit_fill = raw_target * (1.0 - slip)
        net = exit_fill - entry - entry * fee - exit_fill * fee - max_funding
        self.assertAlmostEqual(net / loss, profile.target_net_r, places=9)

    def test_tpr_buffer_requires_directional_survival(self) -> None:
        config = replace(AdseConfig(), atr_minutes=2)
        ns = 60_000_000_000
        bad = minute(0, 99.0)
        bad.open = 100.0
        bars = {0: bad, ns: minute(ns, 99.0)}
        signal = ScenarioSignal(
            "test", "TPR", 1, -5 * ns, 0, 95.0, 1.0, 2.0, True,
            config.tpr_exit, {},
        )
        replay = AdseReplay(config, bars, [signal], lambda **_: None, 0, 10 * ns)
        self.assertFalse(replay._buffer_direction_valid(signal))


if __name__ == "__main__": unittest.main(verbosity=2)
