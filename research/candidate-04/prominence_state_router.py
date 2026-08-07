#!/usr/bin/env python3
"""V56 structural router for frozen causal rich-state signals.

The router does not create entries, stops or targets.  It receives the frozen
V44/V45 signal set and keeps only mechanisms for which the prior experiments
identified a coherent market cause:

* NORMAL_FAILED_AUCTION_RESUMPTION is valid only after an internal liquidity
  pool.  A pivot prominence below one contemporaneous ATR is the invariant
  internal/external boundary; larger pools are regime-scale events and must not
  be traded by the same immediate-reclaim scenario.
* TRAPPED_COUNTERTREND_INVENTORY_RETEST_RESUMPTION additionally requires the
  final completed 30 seconds to contain both price movement and executed flow
  in the intended direction.  A positive 60-second reading alone can be stale
  carry-over from the counter-auction.
* Four already independent liquidation/session failure mechanisms are retained
  unchanged.  Parent-auction continuation and impact-continuation families are
  rejected rather than re-tuned after their prospective failures.

Every input feature is known at the signal observation time.  The module emits
signals only; NautilusTrader remains sole owner of orders, fills, costs,
positions, PnL and NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


INTERNAL_POOL_MAX_PROMINENCE_ATR = 1.0
NORMAL_FAILED_AUCTION = "NORMAL_FAILED_AUCTION_RESUMPTION"
TRAPPED_COUNTERTREND = "TRAPPED_COUNTERTREND_INVENTORY_RETEST_RESUMPTION"
RETAINED_INDEPENDENT_SCENARIOS = frozenset(
    {
        "EXTERNAL_SETTLED_FAILED_DISCOVERY_REVERSAL",
        "DIRECTIONAL_SESSION_VWAP_LIQUIDATION_RECLAIM",
        "FAILED_EXTERNAL_BREAK_RETEST_LIQUIDATION_REVERSAL_NO_IMPACT_CAP_ABLATION",
        "STRESS_SETTLED_DELEVERAGING_REVERSAL",
    }
)


def finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def load_rich(directory: Path) -> pd.DataFrame:
    files = sorted(directory.glob("BTCUSDT-rich-*.csv.gz"))
    if not files:
        raise RuntimeError(f"no BTCUSDT rich features in {directory}")
    frame = pd.concat((pd.read_csv(path) for path in files), ignore_index=True)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame["observed_time"] = pd.to_datetime(frame["observed_time"], utc=True)
    frame = frame.sort_values("open_time").drop_duplicates("open_time")
    frame = frame.set_index("open_time")
    expected = frame.index + pd.Timedelta(minutes=1)
    if not (frame["observed_time"].array == expected.array).all():
        raise RuntimeError("rich features violate the close-observed contract")
    return frame


def aligned_terminal_auction(row: pd.Series, side: int) -> tuple[bool, float, float]:
    if side not in (-1, 1):
        return False, float("nan"), float("nan")
    directional_return = side * finite(row.get("ret_30s_bps"))
    directional_flow = side * finite(row.get("flow_30s"))
    passed = (
        math.isfinite(directional_return)
        and math.isfinite(directional_flow)
        and directional_return > 0.0
        and directional_flow > 0.0
    )
    return passed, directional_return, directional_flow


def state_boundary(signal: dict[str, Any]) -> tuple[float | None, str | None]:
    scenario = str(signal["scenario"])
    details = dict(signal.get("details") or {})
    candidates: tuple[tuple[str, str], ...]
    if scenario == NORMAL_FAILED_AUCTION:
        candidates = (("structure", "pre_sweep_structure_break"),)
    elif scenario == TRAPPED_COUNTERTREND:
        candidates = (("broken_pool_level", "accepted_external_break_level"),)
    elif scenario == "EXTERNAL_SETTLED_FAILED_DISCOVERY_REVERSAL":
        candidates = (("external_pool_level", "reclaimed_external_pool"),)
    elif scenario == "DIRECTIONAL_SESSION_VWAP_LIQUIDATION_RECLAIM":
        candidates = (("parent_session_vwap", "parent_session_vwap"),)
    elif scenario.startswith("FAILED_EXTERNAL_BREAK_RETEST_LIQUIDATION_REVERSAL"):
        candidates = (("broken_pool_level", "failed_external_break_level"),)
    else:
        candidates = ()
    for key, contract in candidates:
        value = finite(details.get(key))
        if math.isfinite(value):
            return value, contract
    return None, None


def route_signal(
    signal: dict[str, Any],
    rich: pd.DataFrame,
) -> tuple[dict[str, Any] | None, str]:
    scenario = str(signal["scenario"])
    side = int(signal["side"])
    details = dict(signal.get("details") or {})
    timestamp = pd.Timestamp(signal["signal_time"])
    if timestamp not in rich.index:
        return None, "missing_completed_rich_row"
    row = rich.loc[timestamp]

    route_reason: str
    if scenario == NORMAL_FAILED_AUCTION:
        prominence = finite(details.get("prominence_atr"))
        if not math.isfinite(prominence):
            return None, "normal_failed_auction_missing_prominence"
        if prominence >= INTERNAL_POOL_MAX_PROMINENCE_ATR:
            return None, "regime_scale_pool_not_immediate_failed_auction"
        route_reason = "internal_pool_failed_auction"
        details["internal_pool_prominence_atr"] = prominence
        details["internal_pool_prominence_ceiling_atr"] = (
            INTERNAL_POOL_MAX_PROMINENCE_ATR
        )
    elif scenario == TRAPPED_COUNTERTREND:
        passed, directional_return, directional_flow = aligned_terminal_auction(
            row, side
        )
        if not passed:
            return None, "terminal_30s_price_flow_not_realigned"
        route_reason = "accepted_break_inventory_trapped_with_fresh_resumption"
        details["terminal_directional_return_30s_bps"] = directional_return
        details["terminal_directional_flow_30s"] = directional_flow
    elif scenario in RETAINED_INDEPENDENT_SCENARIOS:
        route_reason = "independent_liquidation_or_session_failure_mechanism"
    else:
        return None, "discarded_nonportable_continuation_family"

    boundary, boundary_contract = state_boundary(signal)
    if boundary is not None:
        details["actual_fill_state_boundary"] = boundary
        details["actual_fill_state_contract"] = boundary_contract
        details["actual_fill_state_rule"] = "trade_side_must_remain_beyond_boundary"
    details["v56_router"] = "candidate-04-v56-prominence-state-v1"
    details["v56_route_reason"] = route_reason
    routed = dict(signal)
    routed["details"] = details
    return routed, route_reason


def route_signals(
    signals: list[dict[str, Any]],
    rich: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    routed: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    for signal in signals:
        counts["input_signals"] += 1
        selected, reason = route_signal(signal, rich)
        counts[reason] += 1
        if selected is None:
            counts["discarded"] += 1
            continue
        counts["routed"] += 1
        scenario_counts[str(selected["scenario"])] += 1
        routed.append(selected)
    routed.sort(key=lambda item: int(item["observe_time_ns"]))
    summary = {
        "candidate": "candidate-04-v56-prominence-state-router",
        "compiler": "candidate-04-v56-prominence-state-v1",
        "counts": dict(counts),
        "scenario_counts": dict(scenario_counts),
        "internal_pool_max_prominence_atr": INTERNAL_POOL_MAX_PROMINENCE_ATR,
        "market_logic_changed_after_results": False,
        "performance_calculated": False,
        "future_information_used": False,
        "written_signals": len(routed),
    }
    return routed, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.signals.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("signals must be a JSON list")
    rich = load_rich(args.rich_dir)
    routed, summary = route_signals(
        [dict(item) for item in raw if isinstance(item, dict)], rich
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "signals.json").write_text(
        json.dumps(routed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
