"""NautilusTrader runner for opening-type first-pullback state router V2."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_aggtrade_acceptance_nautilus as base
from opening_type_first_pullback_signals_v2 import (
    IMPLEMENTATION_REVISION,
    build_opening_type_first_pullback_signals,
    reprice_bundle_for_bar_market_preserving_events,
)
from session_raid_reversal_execution_v2 import BarMarketRiskCompleteStrategy

_ACTIVE: dict[str, Any] | None = None


def _build(**kwargs: Any):
    if _ACTIVE is None:
        raise RuntimeError("opening-type V2 config not initialized")
    raw = build_opening_type_first_pullback_signals(**kwargs, router_config=_ACTIVE)
    return reprice_bundle_for_bar_market_preserving_events(
        raw,
        tick=float(kwargs["tick"]),
        minimum_net_reward_risk=float(kwargs["minimum_net_reward_risk"]),
    )


def run_suite(
    *, config_path: Path, pattern_config_path: Path, suite: str,
    output: Path, data_cache: Path, reuse_first_dir: Path | None = None,
):
    global _ACTIVE
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("implementation_revision") != IMPLEMENTATION_REVISION:
        raise ValueError("unexpected opening-type V2 implementation revision")
    if float(payload["risk_fraction"]) != 0.03:
        raise ValueError("risk must remain three percent of current shared NAV")
    if float(payload["cost_assumptions"]["bar_market_entry_reserve_ticks"]) != 2.0:
        raise ValueError("verified bar-market reserve is two ticks")
    expected_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    if list(payload["assets"]) != expected_assets:
        raise ValueError("V2 fixed evaluation uses all four liquid test markets")
    if bool(payload["scenario_contract"].get("ten_second_alpha_inputs", True)):
        raise ValueError("ten-second data must remain execution-only")
    _ACTIVE = dict(payload["opening_type_router_config"])
    base.build_acceptance_signals = _build
    base.AggTradeAcceptanceStrategy = BarMarketRiskCompleteStrategy
    return base.run_suite(
        config_path=config_path,
        pattern_config_path=pattern_config_path,
        suite=suite,
        output=output,
        data_cache=data_cache,
        reuse_first_dir=reuse_first_dir,
        ablation="none",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("first", "screen"), default="first")
    parser.add_argument(
        "--config", type=Path,
        default=HERE / "config_opening_type_first_pullback_multiasset_v2.json",
    )
    parser.add_argument(
        "--pattern-config", type=Path, default=HERE / "config_range_fvg.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-first-dir", type=Path, default=None)
    parser.add_argument(
        "--data-cache", type=Path,
        default=Path.home() / ".cache/smc4/candidate-08-opening-type-first-pullback-v2",
    )
    args = parser.parse_args()
    run_suite(
        config_path=args.config.resolve(),
        pattern_config_path=args.pattern_config.resolve(),
        suite=args.suite,
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
        reuse_first_dir=args.reuse_first_dir.resolve() if args.reuse_first_dir else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
