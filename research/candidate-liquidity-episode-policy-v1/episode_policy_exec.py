#!/usr/bin/env python3
"""Executable adapter for the restored liquidity episode policy.

A four-symbol harvest previously ran serially inside one Actions job and repeatedly
hit the job timeout.  Multi-symbol requests are now split into independent symbol
processes, capped at two workers per runner, and merged before the unchanged causal
router sees the data.  The trading policy itself is not changed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pandas as pd

import departure_first_return_harvest_fixed as fixed
import episode_policy as policy

core = fixed.core
core.generate_symbol = policy.generate_symbol
_BASE_RUN = core.run_research


def _decorate_summary(summary: dict, output: Path) -> dict:
    """Append the restored policy contract to one completed harvest."""
    action_path = output / "departure_actions.csv.gz"
    if not action_path.exists():
        return summary
    frame = pd.read_csv(action_path, low_memory=False)
    exists = (
        frame.get("order_exists", pd.Series(False, index=frame.index))
        .astype(str)
        .str.lower()
        .isin({"true", "1", "yes"})
    )
    summary.update(
        {
            "episode_rows": int(len(frame)),
            "plans": int(exists.sum()),
            "no_trade_episodes": int((~exists).sum()),
            "states": int(frame.state_id.nunique()) if "state_id" in frame else 0,
            "episodes": int(frame.episode_id.nunique()) if "episode_id" in frame else 0,
            "one_plan_per_episode": True,
            "fixed_rr_target_lattice": False,
            "target_selected_before_rr": True,
            "episode_policy_version": "liquidity-episode-policy-v1",
            "policy_model_inputs_are_causal": True,
        }
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return summary


def _single_symbol_run(*, start, end, warmup_days, symbols, cache, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summary = dict(
        _BASE_RUN(
            start=start,
            end=end,
            warmup_days=warmup_days,
            symbols=symbols,
            cache=cache,
            output=output,
        )
    )
    return _decorate_summary(summary, output)


def _run_symbol_process(
    *, symbol: str, start, end, warmup_days: int, cache: Path, output: Path
) -> dict:
    """Run one symbol in an isolated process and return its exact summary."""
    symbol_cache = cache / symbol
    symbol_output = output / symbol
    symbol_cache.mkdir(parents=True, exist_ok=True)
    symbol_output.mkdir(parents=True, exist_ok=True)
    log_path = symbol_output / "parallel_harvest.log"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--start",
        str(start),
        "--end",
        str(end),
        "--warmup-days",
        str(warmup_days),
        "--symbols",
        symbol,
        "--cache",
        str(symbol_cache),
        "--output",
        str(symbol_output),
    ]
    environment = os.environ.copy()
    environment["EPISODE_POLICY_PARALLEL_CHILD"] = "1"
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
        )
    if completed.returncode:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        raise RuntimeError(
            f"symbol harvest failed for {symbol} with {completed.returncode}:\n{tail}"
        )
    summary_path = symbol_output / "summary.json"
    action_path = symbol_output / "departure_actions.csv.gz"
    if not summary_path.exists() or not action_path.exists():
        raise RuntimeError(f"incomplete symbol harvest for {symbol}: {symbol_output}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _parallel_symbol_run(*, start, end, warmup_days, symbols, cache, output):
    """Execute independent symbol harvests concurrently, then restore one output."""
    symbols = tuple(str(symbol) for symbol in symbols)
    cache = Path(cache)
    output = Path(output)
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}-symbol-workers"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    configured = int(os.environ.get("EPISODE_POLICY_MAX_WORKERS", "2"))
    max_workers = max(1, min(configured, len(symbols)))
    summaries: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _run_symbol_process,
                symbol=symbol,
                start=start,
                end=end,
                warmup_days=warmup_days,
                cache=cache,
                output=staging,
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            summaries[symbol] = future.result()

    frames: list[pd.DataFrame] = []
    by_symbol: dict[str, dict] = {}
    for symbol in symbols:
        symbol_output = staging / symbol
        frame = pd.read_csv(symbol_output / "departure_actions.csv.gz", low_memory=False)
        observed = set(frame.get("symbol", pd.Series(dtype=str)).dropna().astype(str))
        if observed and observed != {symbol}:
            raise RuntimeError(f"symbol isolation failed for {symbol}: {sorted(observed)}")
        frames.append(frame)
        child = summaries[symbol]
        child_by_symbol = child.get("by_symbol", {})
        by_symbol[symbol] = child_by_symbol.get(symbol, child)
        candidate = symbol_output / f"{symbol}_departure_actions.csv.gz"
        shutil.copy2(
            candidate if candidate.exists() else symbol_output / "departure_actions.csv.gz",
            output / f"{symbol}_departure_actions.csv.gz",
        )

    combined = pd.concat(frames, ignore_index=True, sort=False)
    sort_columns = [
        name
        for name in ("order_time_ns", "symbol", "episode_id", "state_id", "action_id")
        if name in combined
    ]
    if sort_columns:
        combined = combined.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)
    combined.to_csv(output / "departure_actions.csv.gz", index=False, compression="gzip")

    first = summaries[symbols[0]] if symbols else {}
    summary = {
        "policy": first.get("policy", "LIQUIDITY_EPISODE_POLICY_V1"),
        "start": str(start),
        "end": str(end),
        "symbols": list(symbols),
        "by_symbol": by_symbol,
        "parallel_symbol_workers": max_workers,
        "parallel_execution_changes_policy": False,
    }
    if "outcome" in combined:
        summary["outcomes"] = (
            combined["outcome"].astype(str).value_counts().sort_index().to_dict()
        )
    summary = _decorate_summary(summary, output)
    shutil.rmtree(staging)
    return summary


def run_research(*, start, end, warmup_days, symbols, cache, output):
    symbols = tuple(symbols)
    if len(symbols) > 1 and os.environ.get("EPISODE_POLICY_PARALLEL_CHILD") != "1":
        return _parallel_symbol_run(
            start=start,
            end=end,
            warmup_days=warmup_days,
            symbols=symbols,
            cache=cache,
            output=output,
        )
    return _single_symbol_run(
        start=start,
        end=end,
        warmup_days=warmup_days,
        symbols=symbols,
        cache=cache,
        output=output,
    )


core.run_research = run_research


def main() -> None:
    """Expose the inherited CLI as an importable, testable entry point."""
    core.main()


if __name__ == "__main__":
    main()
