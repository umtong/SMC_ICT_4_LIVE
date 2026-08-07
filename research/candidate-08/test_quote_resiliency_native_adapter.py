"""Static and import-time contracts for the native quote-resiliency adapter."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

import run_quote_resiliency_nautilus as adapter
from quote_resiliency_data_v2 import DATA_REVISION
from quote_resiliency_features_v3 import IMPLEMENTATION_REVISION as FEATURE_REVISION
from quote_resiliency_signals import (
    CONTINUATION_FAMILY,
    REVERSAL_FAMILY,
    SIGNAL_REVISION,
)
from quote_resiliency_strategy import (
    EXECUTION_ADAPTER_REVISION,
    QuoteResiliencyExecutionStrategy,
)


HERE = Path(__file__).resolve().parent


class NativeAdapterContracts(unittest.TestCase):
    def test_config_and_implementation_revisions_are_bound(self) -> None:
        payload = json.loads(
            (HERE / "config_quote_resiliency_btc_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload["implementation_revision"],
            adapter.CONFIG_IMPLEMENTATION_REVISION,
        )
        self.assertEqual(payload["risk_fraction"], 0.03)
        self.assertEqual(set(payload["assets"]), {"BTCUSDT"})
        self.assertEqual(adapter.DATA_REVISION, DATA_REVISION)
        self.assertEqual(adapter.FEATURE_REVISION, FEATURE_REVISION)
        self.assertEqual(adapter.SIGNAL_REVISION, SIGNAL_REVISION)
        self.assertEqual(
            adapter.EXECUTION_ADAPTER_REVISION,
            EXECUTION_ADAPTER_REVISION,
        )

    def test_only_predeclared_ablation_is_exposed(self) -> None:
        self.assertEqual(
            adapter.ABLATIONS,
            frozenset(
                {
                    "none",
                    "remove_confirmation_quote_ofi_direction_gate",
                }
            ),
        )
        payload = adapter._load_payload()
        declared = payload["single_ablation"]
        self.assertEqual(
            declared["name"],
            "REMOVE_CONFIRMATION_QUOTE_OFI_DIRECTION_GATE",
        )
        self.assertFalse(declared["promotion_permitted"])

    def test_family_resolution_prefers_immutable_signal_field(self) -> None:
        class Signal:
            scenario_family = REVERSAL_FAMILY
            details = {"scenario_family": CONTINUATION_FAMILY}

        self.assertEqual(adapter._signal_family(Signal()), REVERSAL_FAMILY)

    def test_verified_runner_boundaries_are_reused(self) -> None:
        base = adapter.execution.runner.base_runner
        self.assertIs(base.build_acceptance_signals, adapter._build_signals)
        self.assertIs(
            base.load_ten_second_aggtrades,
            adapter._load_trade_and_quote_features,
        )
        self.assertIs(base.AggTradeAcceptanceStrategy, QuoteResiliencyExecutionStrategy)
        self.assertIs(base._write_merged_events, adapter._write_merged_events)
        self.assertIs(base._global_signal_summary, adapter._global_signal_summary)
        self.assertIs(base._suite_summary, adapter._suite_summary)
        self.assertEqual(
            adapter.execution.runner.FAMILY_MODES["both"],
            frozenset({CONTINUATION_FAMILY, REVERSAL_FAMILY}),
        )

    def test_adapter_contains_no_new_backtest_or_sizing_engine(self) -> None:
        runner_source = (HERE / "run_quote_resiliency_nautilus.py").read_text(
            encoding="utf-8"
        )
        strategy_source = (HERE / "quote_resiliency_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("BacktestEngine(", runner_source)
        self.assertNotIn("class Backtest", runner_source)
        self.assertNotIn("def risk_sized_quantity", runner_source)
        self.assertNotIn("def risk_sized_quantity", strategy_source)
        self.assertIn("risk_sized_quantity(", strategy_source)
        self.assertIn("RiskCompleteAggTradeAcceptanceStrategy", strategy_source)

    def test_order_and_intent_labels_name_real_quote_scenarios(self) -> None:
        source = (HERE / "quote_resiliency_strategy.py").read_text(encoding="utf-8")
        self.assertIn("signal.scenario_family", source)
        self.assertIn("signal.stop_reference_source", source)
        self.assertNotIn('"BREAKOUT_ACCEPTANCE_CONTINUATION"', source)
        self.assertNotIn('"OBSERVED_RETEST_INVALIDATION"', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
