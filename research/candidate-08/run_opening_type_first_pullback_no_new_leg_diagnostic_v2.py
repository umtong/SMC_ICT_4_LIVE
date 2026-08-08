"""Diagnostic NautilusTrader runner for removing only V2 separate M5 confirmation."""
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
from opening_type_first_pullback_no_new_leg_diagnostic_v2 import (
    DIAGNOSTIC_ABLATION,
    build_opening_type_first_pullback_direct_diagnostic,
    reprice_diagnostic_bundle,
)
from opening_type_first_pullback_signals_v2 import IMPLEMENTATION_REVISION
from session_raid_reversal_execution_v2 import BarMarketRiskCompleteStrategy

_ACTIVE: dict[str, Any] | None = None


def _build(**kwargs: Any):
    if _ACTIVE is None:
        raise RuntimeError("diagnostic config not initialized")
    raw = build_opening_type_first_pullback_direct_diagnostic(
        **kwargs,
        router_config=_ACTIVE,
    )
    return reprice_diagnostic_bundle(
        raw,
        tick=float(kwargs["tick"]),
        minimum_net_reward_risk=float(kwargs["minimum_net_reward_risk"]),
    )


def run_suite(*, config_path: Path, pattern_config_path: Path, output: Path, data_cache: Path):
    global _ACTIVE
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("implementation_revision") != IMPLEMENTATION_REVISION:
        raise ValueError("unexpected V2 implementation revision")
    if float(payload["risk_fraction"]) != 0.03:
        raise ValueError("risk must remain three percent of current shared NAV")
    if list(payload["assets"]) != ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]:
        raise ValueError("diagnostic must use the same four markets")
    _ACTIVE = dict(payload["opening_type_router_config"])
    base.build_acceptance_signals = _build
    base.AggTradeAcceptanceStrategy = BarMarketRiskCompleteStrategy
    return base.run_suite(
        config_path=config_path,
        pattern_config_path=pattern_config_path,
        suite="first",
        output=output,
        data_cache=data_cache,
        reuse_first_dir=None,
        ablation=DIAGNOSTIC_ABLATION,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=HERE / "config_opening_type_first_pullback_multiasset_v2.json",
    )
    parser.add_argument(
        "--pattern-config",
        type=Path,
        default=HERE / "config_range_fvg.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-cache",
        type=Path,
        default=Path.home() / ".cache/smc4/candidate-08-opening-type-first-pullback-v2",
    )
    args = parser.parse_args()
    run_suite(
        config_path=args.config.resolve(),
        pattern_config_path=args.pattern_config.resolve(),
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
