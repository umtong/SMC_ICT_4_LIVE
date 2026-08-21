#!/usr/bin/env python3
"""Validate the exact lazily loaded frozen paper/venue graph.

Only the variant named by the promoted manifest is imported.  This keeps an
abandoned research module from becoming an operational dependency of an
otherwise frozen candidate.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path

from execution_re1_venue_safe import VenueSafeStopReplacementMixin
from paper_re1_generic import WarmStartCoherentPaperMixin
from variant_loader import load_object
from variant_registry import VARIANTS


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
        raise RuntimeError(f"registry lacks frozen variant {variant!r}") from exc

    bundle = load_object(spec.bundle)
    venue_strategy = load_object(spec.venue_strategy)
    paper_strategy = load_object(spec.paper_strategy)
    if not isinstance(bundle, type):
        raise RuntimeError("bundle import did not resolve to a class")
    if not issubclass(venue_strategy, VenueSafeStopReplacementMixin):
        raise RuntimeError("venue backtest strategy lacks safe stop replacement")
    if not issubclass(paper_strategy, WarmStartCoherentPaperMixin):
        raise RuntimeError("paper strategy lacks coherent warm-start wrapper")
    if not issubclass(paper_strategy, VenueSafeStopReplacementMixin):
        raise RuntimeError("paper strategy lacks venue-safe conditional stop transport")

    replacement_source = inspect.getsource(VenueSafeStopReplacementMixin)
    for term in (
        "old reduce-only stop live",
        "pending_stop_id",
        "stop_replacement_submitted",
        "stop_replacement_accepted",
        "required_quantity_stop_replacement_failed",
    ):
        if term not in replacement_source:
            raise RuntimeError(f"venue stop lifecycle lost required element: {term}")

    paper_source = inspect.getsource(WarmStartCoherentPaperMixin)
    for term in (
        "_preload_warmup",
        "_drain_live_buckets",
        "_halt_for_incomplete_market_view",
        "_fail_closed_restart_reconciliation",
        "close_all_positions",
        "reduce_only=True",
    ):
        if term not in paper_source:
            raise RuntimeError(f"paper operational contract lost {term}")

    contract = manifest.get("contract") or {}
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
    mismatches = {
        key: {"expected": expected, "actual": contract.get(key)}
        for key, expected in expected_contract.items()
        if contract.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(f"promoted manifest changed the fixed account contract: {mismatches}")

    os.environ["EASYCHART_RE1_VARIANT"] = variant
    if spec.families is None:
        os.environ.pop("EASYCHART_RE1_FAMILIES", None)
    else:
        os.environ["EASYCHART_RE1_FAMILIES"] = spec.families

    result = {
        "variant": variant,
        "bundle": f"{bundle.__module__}:{bundle.__name__}",
        "venue_strategy": f"{venue_strategy.__module__}:{venue_strategy.__name__}",
        "paper_strategy": f"{paper_strategy.__module__}:{paper_strategy.__name__}",
        "families": spec.families,
        "lazy_selected_import": True,
        "warm_start_coherent": True,
        "missing_bar_fail_closed": True,
        "late_or_duplicate_bar_fail_closed": True,
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
