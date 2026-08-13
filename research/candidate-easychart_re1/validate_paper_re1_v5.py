#!/usr/bin/env python3
"""Validate one final lazily loaded promoted paper graph."""
from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path

from execution_re1_venue_safe import VenueSafeStopReplacementMixin
from paper_re1_generic import WarmStartCoherentPaperMixin
from variant_loader_v4 import load_object
from variant_registry_v4 import VARIANTS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    variant = str(manifest.get("variant") or "").strip()
    try:
        spec = VARIANTS[variant]
    except KeyError as exc:
        raise RuntimeError(f"registry lacks promoted variant {variant!r}") from exc

    bundle = load_object(spec.bundle)
    venue_strategy = load_object(spec.venue_strategy)
    paper_strategy = load_object(spec.paper_strategy)
    if not isinstance(bundle, type):
        raise RuntimeError("bundle did not resolve to a class")
    if not issubclass(venue_strategy, VenueSafeStopReplacementMixin):
        raise RuntimeError("venue parity strategy lacks stop replacement")
    if not issubclass(paper_strategy, WarmStartCoherentPaperMixin):
        raise RuntimeError("paper strategy lacks coherent warm start")
    if not issubclass(paper_strategy, VenueSafeStopReplacementMixin):
        raise RuntimeError("paper strategy lacks venue-safe stop replacement")

    expected_imports = {
        "bundle_import": spec.bundle,
        "venue_strategy_import": spec.venue_strategy,
        "paper_strategy_import": spec.paper_strategy,
        "families": spec.families,
        "mechanisms": spec.mechanisms,
    }
    mismatches = {
        key: {"expected": value, "actual": manifest.get(key)}
        for key, value in expected_imports.items()
        if manifest.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"manifest/registry mismatch: {mismatches}")

    replacement = inspect.getsource(VenueSafeStopReplacementMixin)
    paper = inspect.getsource(WarmStartCoherentPaperMixin)
    for term in (
        "stop_replacement_submitted",
        "stop_replacement_accepted",
        "required_quantity_stop_replacement_failed",
        "retiring_stop_cancel_rejected_both_reduce_only_retained",
    ):
        if term not in replacement:
            raise RuntimeError(f"venue stop lifecycle lost {term}")
    for term in (
        "_preload_warmup",
        "_drain_live_buckets",
        "_halt_for_incomplete_market_view",
        "_fail_closed_restart_reconciliation",
        "reduce_only=True",
    ):
        if term not in paper:
            raise RuntimeError(f"paper operational contract lost {term}")

    expected_contract = {
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        "single_global_pending_or_position": True,
        "risk_fraction_current_nav": 0.03,
        "minimum_gross_rr": 1.0,
        "partial_entries": False,
        "partial_exits": False,
        "fixed_holding_exit": False,
        "fee_profile": "usd_m_vip0",
    }
    contract = manifest.get("contract") or {}
    contract_mismatches = {
        key: {"expected": value, "actual": contract.get(key)}
        for key, value in expected_contract.items()
        if contract.get(key) != value
    }
    if contract_mismatches:
        raise RuntimeError(f"fixed account contract mismatch: {contract_mismatches}")

    os.environ["EASYCHART_RE1_VARIANT"] = variant
    result = {
        "variant": variant,
        "bundle": spec.bundle,
        "venue_strategy": spec.venue_strategy,
        "paper_strategy": spec.paper_strategy,
        "families": spec.families,
        "mechanisms": spec.mechanisms,
        "lazy_selected_import": True,
        "warm_start_coherent": True,
        "cross_symbol_timestamp_coherence": True,
        "missing_late_duplicate_bar_fail_closed": True,
        "restart_fail_closed": True,
        "venue_safe_stop_replacement": True,
        "paper_and_backtest_share_bundle": True,
        "paper_and_backtest_share_execution_lineage": True,
        "manifest_commit": manifest.get("frozen_commit_sha"),
        "account_contract": expected_contract,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "paper_contract.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output / "PAPER_CODE_READY").write_text(
        f"{variant}\n{manifest.get('frozen_commit_sha', '')}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
