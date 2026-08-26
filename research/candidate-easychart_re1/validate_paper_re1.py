#!/usr/bin/env python3
"""Static/runtime graph checks for the frozen paper transport."""
from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path

from execution_re1_venue_safe import VenueSafeStopReplacementMixin
from paper_re1_generic import WarmStartCoherentPaperMixin
import run_binance_demo_re1_frozen as demo
import run_mtf_backtest_re1_venue_variant as venue_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    variant = str(manifest["variant"])
    if variant not in demo.VARIANTS:
        raise RuntimeError(f"paper runner lacks frozen variant {variant}")
    if variant not in venue_backtest.VARIANTS:
        raise RuntimeError(f"venue-safe backtest lacks frozen variant {variant}")

    _, paper_strategy, paper_families = demo.VARIANTS[variant]
    _, venue_strategy, venue_families = venue_backtest.VARIANTS[variant]
    if paper_families != venue_families:
        raise RuntimeError("paper/backtest family routing mismatch")
    if not issubclass(paper_strategy, WarmStartCoherentPaperMixin):
        raise RuntimeError("paper strategy lacks coherent warm-start wrapper")
    if not issubclass(venue_strategy, VenueSafeStopReplacementMixin):
        raise RuntimeError("venue-safe backtest strategy lacks stop replacement")

    replacement_source = inspect.getsource(VenueSafeStopReplacementMixin.modify_order)
    required_replacement_terms = (
        "_start_stop_replacement",
        "active_stop_id",
        "trigger_price",
    )
    if not all(term in replacement_source for term in required_replacement_terms):
        raise RuntimeError("stop modification interception contract changed")
    paper_source = inspect.getsource(WarmStartCoherentPaperMixin)
    for term in (
        "_preload_warmup",
        "_drain_live_buckets",
        "_halt_for_incomplete_market_view",
        "_fail_closed_restart_reconciliation",
        "close_all_positions",
    ):
        if term not in paper_source:
            raise RuntimeError(f"paper operational contract lost {term}")

    os.environ["EASYCHART_RE1_VARIANT"] = variant
    result = {
        "variant": variant,
        "paper_strategy": paper_strategy.__name__,
        "venue_backtest_strategy": venue_strategy.__name__,
        "families": paper_families,
        "warm_start_coherent": True,
        "missing_bar_fail_closed": True,
        "restart_fail_closed": True,
        "venue_safe_stop_replacement": True,
        "same_variant_in_paper_and_venue_backtest": True,
        "manifest_commit": manifest.get("frozen_commit_sha"),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "paper_contract.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output / "PAPER_CODE_READY").write_text(
        f"{variant}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
