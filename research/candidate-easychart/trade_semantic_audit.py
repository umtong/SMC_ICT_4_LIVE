#!/usr/bin/env python3
"""Trade-by-trade semantic and intrabar-order audit for EasyChart diagnostics.

This is not another backtest engine. It consumes an existing diagnostic run and
asks whether each setup and recorded trade remains valid under both OHLC
extreme orderings unknowable from a one-minute bar. It also exposes which
source roles the experiment actually tested, so a result cannot be used to make
claims about absent components such as OB or FVG.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from data import load_range


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    open_time_ns: int
    close_time_ns: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class PathResult:
    path_name: str
    event: str
    entered: bool
    entry_mode: str | None = None
    entry_segment: int | None = None
    exit_segment: int | None = None


@dataclass(frozen=True, slots=True)
class LifecycleAudit:
    classification: str
    event: str
    event_open_time_ns: int | None
    event_close_time_ns: int | None
    path_results: tuple[PathResult, ...]


FIXED_STRUCTURE = "FIXED_STRUCTURE"
ENTRY_EVENTS = {"ENTRY_OPEN", "ENTRY_THEN_STOP", "ENTRY_THEN_TARGET"}


def _crosses(a: float, b: float, level: float) -> bool:
    return min(a, b) <= level <= max(a, b)


def _ordered_events(
    a: float,
    b: float,
    levels: Mapping[str, float],
) -> list[tuple[str, float]]:
    crossed = [
        (name, abs(float(level) - a))
        for name, level in levels.items()
        if _crosses(a, b, float(level))
    ]
    return sorted(crossed, key=lambda item: (item[1], item[0]))


def _paths(bar: Bar) -> tuple[tuple[str, tuple[float, ...]], ...]:
    candidates = (
        ("O-H-L-C", (bar.open, bar.high, bar.low, bar.close)),
        ("O-L-H-C", (bar.open, bar.low, bar.high, bar.close)),
    )
    unique: list[tuple[str, tuple[float, ...]]] = []
    seen: set[tuple[float, ...]] = set()
    for name, path in candidates:
        if path not in seen:
            unique.append((name, path))
            seen.add(path)
    return tuple(unique)


def _position_event(
    *,
    side: int,
    stop: float,
    target: float,
    path: Sequence[float],
) -> tuple[str, int | None]:
    if not path:
        return "OPEN", None
    open_price = float(path[0])
    if side == 1 and open_price <= stop:
        return "STOP", -1
    if side == -1 and open_price >= stop:
        return "STOP", -1
    if side == 1 and open_price >= target:
        return "TARGET", -1
    if side == -1 and open_price <= target:
        return "TARGET", -1
    for index in range(len(path) - 1):
        events = _ordered_events(
            float(path[index]),
            float(path[index + 1]),
            {"STOP": stop, "TARGET": target},
        )
        if events:
            return events[0][0], index
    return "OPEN", None


def scan_setup_path(
    setup: Mapping[str, object],
    bar: Bar,
    path_name: str,
    path: Sequence[float],
) -> PathResult:
    side = int(setup["side"])
    entry = float(setup["entry"])
    stop = float(setup["stop"])
    target = float(setup["initial_target"])
    target_mode = str(setup.get("target_mode", FIXED_STRUCTURE))
    if target_mode != FIXED_STRUCTURE:
        return PathResult(path_name, "UNSUPPORTED_DYNAMIC_TARGET", False)

    open_price = float(path[0])
    if side == 1:
        if open_price <= stop:
            return PathResult(path_name, "STOP_INVALID_AT_OPEN", False)
        if open_price >= target:
            return PathResult(path_name, "TARGET_CONSUMED_AT_OPEN", False)
        entered_at_open = open_price <= entry
    else:
        if open_price >= stop:
            return PathResult(path_name, "STOP_INVALID_AT_OPEN", False)
        if open_price <= target:
            return PathResult(path_name, "TARGET_CONSUMED_AT_OPEN", False)
        entered_at_open = open_price >= entry

    if entered_at_open:
        outcome, exit_segment = _position_event(
            side=side,
            stop=stop,
            target=target,
            path=path,
        )
        event = "ENTRY_OPEN" if outcome == "OPEN" else f"ENTRY_THEN_{outcome}"
        return PathResult(path_name, event, True, "OPEN_CROSS", -1, exit_segment)

    for index in range(len(path) - 1):
        events = _ordered_events(
            float(path[index]),
            float(path[index + 1]),
            {"ENTRY": entry, "STOP_INVALID": stop, "TARGET_CONSUMED": target},
        )
        if not events:
            continue
        name = events[0][0]
        if name != "ENTRY":
            return PathResult(path_name, name, False, entry_segment=index)
        remainder = (entry, *tuple(float(value) for value in path[index + 1 :]))
        outcome, exit_segment = _position_event(
            side=side,
            stop=stop,
            target=target,
            path=remainder,
        )
        event = "ENTRY_OPEN" if outcome == "OPEN" else f"ENTRY_THEN_{outcome}"
        return PathResult(path_name, event, True, "LIMIT_TOUCH", index, exit_segment)
    return PathResult(path_name, "NO_EVENT", False)


def audit_setup_lifecycle(
    setup: Mapping[str, object],
    bars: Sequence[Bar],
) -> LifecycleAudit:
    observed = int(setup["observed_time_ns"])
    valid_until_raw = setup.get("valid_until_ns")
    valid_until = (
        int(valid_until_raw)
        if valid_until_raw is not None and not pd.isna(valid_until_raw)
        else None
    )
    for bar in bars:
        if bar.open_time_ns <= observed:
            continue
        if valid_until is not None and valid_until <= bar.open_time_ns:
            return LifecycleAudit(
                "ROBUST",
                "EXPIRED",
                bar.open_time_ns,
                bar.close_time_ns,
                (),
            )
        results = tuple(
            scan_setup_path(setup, bar, name, path)
            for name, path in _paths(bar)
        )
        events = {result.event for result in results}
        if events == {"NO_EVENT"}:
            continue
        if len(events) == 1:
            return LifecycleAudit(
                "ROBUST",
                results[0].event,
                bar.open_time_ns,
                bar.close_time_ns,
                results,
            )
        return LifecycleAudit(
            "PATH_AMBIGUOUS",
            "|".join(sorted(events)),
            bar.open_time_ns,
            bar.close_time_ns,
            results,
        )
    return LifecycleAudit("ROBUST", "NO_EVENT_BEFORE_DATA_END", None, None, ())


def audit_open_position(
    *,
    side: int,
    stop: float,
    target: float,
    bars: Sequence[Bar],
    first_bar_open_ns: int,
    entry_bar_audit: LifecycleAudit,
) -> LifecycleAudit:
    if (
        entry_bar_audit.classification != "ROBUST"
        or entry_bar_audit.event not in ENTRY_EVENTS
    ):
        return LifecycleAudit("NOT_AUDITABLE", "ENTRY_NOT_ROBUST", None, None, ())
    if entry_bar_audit.event == "ENTRY_THEN_STOP":
        return LifecycleAudit(
            "ROBUST",
            "STOP",
            entry_bar_audit.event_open_time_ns,
            entry_bar_audit.event_close_time_ns,
            entry_bar_audit.path_results,
        )
    if entry_bar_audit.event == "ENTRY_THEN_TARGET":
        return LifecycleAudit(
            "ROBUST",
            "TARGET",
            entry_bar_audit.event_open_time_ns,
            entry_bar_audit.event_close_time_ns,
            entry_bar_audit.path_results,
        )

    for bar in bars:
        if bar.open_time_ns <= first_bar_open_ns:
            continue
        path_results: list[PathResult] = []
        for name, path in _paths(bar):
            event, segment = _position_event(
                side=side,
                stop=stop,
                target=target,
                path=path,
            )
            path_results.append(
                PathResult(
                    path_name=name,
                    event=event,
                    entered=True,
                    entry_mode="POSITION_ALREADY_OPEN",
                    exit_segment=segment,
                ),
            )
        events = {result.event for result in path_results}
        if events == {"OPEN"}:
            continue
        if len(events) == 1:
            return LifecycleAudit(
                "ROBUST",
                path_results[0].event,
                bar.open_time_ns,
                bar.close_time_ns,
                tuple(path_results),
            )
        return LifecycleAudit(
            "PATH_AMBIGUOUS",
            "|".join(sorted(events)),
            bar.open_time_ns,
            bar.close_time_ns,
            tuple(path_results),
        )
    return LifecycleAudit("ROBUST", "OPEN_AT_DATA_END", None, None, ())


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {str(key): value for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


def _load_bars(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> list[Bar]:
    frame = load_range(symbol, start, end, cache)
    return [
        Bar(
            symbol=symbol,
            open_time_ns=int(row.open_time_dt.value),
            close_time_ns=int(row.close_time_dt.value),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
        )
        for row in frame.itertuples(index=False)
    ]


def _gross_rr(setup: Mapping[str, object]) -> float:
    risk = abs(float(setup["entry"]) - float(setup["stop"]))
    reward = abs(float(setup["initial_target"]) - float(setup["entry"]))
    return reward / risk if risk > 0.0 else float("inf")


def _busy_trade(
    trades: Sequence[Mapping[str, object]],
    *,
    plan_id: str,
    bar_open_ns: int | None,
) -> str | None:
    if bar_open_ns is None:
        return None
    for trade in trades:
        other = str(trade["plan_id"])
        if other == plan_id:
            continue
        if int(trade["entry_time_ns"]) <= bar_open_ns <= int(trade["exit_time_ns"]):
            return other
    return None


def _infer_roles(setup: Mapping[str, object]) -> list[str]:
    family = str(setup.get("family", ""))
    roles: list[str] = []
    if family.startswith("SESSION_"):
        roles.extend(["TIME_STRUCTURED_REFERENCE", "LIQUIDITY_BOUNDARY"])
    if "IMMEDIATE_FAKEOUT" in family:
        roles.append("IMMEDIATE_SINGLE_EXTREME_FAKEOUT")
    if "WM_TRAP" in family:
        roles.append("DELAYED_WM_TRAP")
    elif "DELAYED_TRAP" in family:
        roles.append("DELAYED_RECLAIM_NOT_WM_PROVEN")
    if "SMT_ISOLATED" in family:
        roles.append("ADAPTED_CROSS_SECTIONAL_ISOLATION")
    if "BROAD_RECLAIM" in family:
        roles.append("ADAPTED_CROSS_SECTIONAL_BROAD_RECLAIM")
    if "BROKEN_TRENDLINE" in family:
        roles.append("BROKEN_TRENDLINE_ROLE_FLIP")
    if "FIB0618" in family:
        roles.append("ADAPTED_FIB_VALUE")
    if "FIRST_DC_OBJECTIVE" in family:
        roles.append("FIRST_DIRECTIONAL_CHANGE_OBJECTIVE")
    if float(setup.get("body_ratio", 0.0) or 0.0) > 0.0:
        roles.append("BODY_ENGULF_ORDER_BLOCK")
    if "FVG" in family:
        roles.append("STRICT_FVG")
    return sorted(set(roles))


def _window(
    bars: Sequence[Bar],
    center_ns: int,
    before: int,
    after: int,
) -> list[dict[str, object]]:
    if not bars:
        return []
    index = min(
        range(len(bars)),
        key=lambda item: abs(bars[item].open_time_ns - center_ns),
    )
    low = max(0, index - before)
    high = min(len(bars), index + after + 1)
    return [asdict(bar) for bar in bars[low:high]]


def run_audit(args: argparse.Namespace) -> dict[str, object]:
    run_dir = args.run_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    setups = _records(pd.read_csv(run_dir / "setups.csv"))
    trades = _records(pd.read_csv(run_dir / "trades.csv"))
    symbols = sorted(
        {str(row["symbol"]) for row in setups}
        | {str(row["symbol"]) for row in trades},
    )
    bars_by_symbol = {
        symbol: _load_bars(
            symbol,
            date.fromisoformat(args.start),
            date.fromisoformat(args.end),
            args.cache.resolve(),
        )
        for symbol in symbols
    }

    trade_by_plan = {str(trade["plan_id"]): trade for trade in trades}
    setup_rows: list[dict[str, object]] = []
    audits: dict[str, LifecycleAudit] = {}
    for setup in setups:
        plan_id = str(setup["setup_id"])
        lifecycle = audit_setup_lifecycle(
            setup,
            bars_by_symbol[str(setup["symbol"])],
        )
        audits[plan_id] = lifecycle
        recorded = trade_by_plan.get(plan_id)
        busy = _busy_trade(
            trades,
            plan_id=plan_id,
            bar_open_ns=lifecycle.event_open_time_ns,
        )
        setup_rows.append(
            {
                "plan_id": plan_id,
                "symbol": setup["symbol"],
                "family": setup["family"],
                "observed_time_ns": int(setup["observed_time_ns"]),
                "audit_classification": lifecycle.classification,
                "audit_event": lifecycle.event,
                "audit_event_open_time_ns": lifecycle.event_open_time_ns,
                "audit_event_close_time_ns": lifecycle.event_close_time_ns,
                "recorded_trade": recorded is not None,
                "recorded_entry_time_ns": (
                    None if recorded is None else int(recorded["entry_time_ns"])
                ),
                "busy_plan_id": busy,
                "gross_rr_geometry": _gross_rr(setup),
                "semantic_roles": ";".join(_infer_roles(setup)),
                "path_results": json.dumps(
                    [asdict(item) for item in lifecycle.path_results],
                    sort_keys=True,
                ),
            },
        )

    simultaneous: dict[int, list[dict[str, object]]] = defaultdict(list)
    for setup, row in zip(setups, setup_rows, strict=True):
        if (
            row["audit_classification"] == "ROBUST"
            and row["audit_event"] in ENTRY_EVENTS
        ):
            simultaneous[int(row["audit_event_open_time_ns"])].append(setup)
    winners: dict[str, str] = {}
    for group in simultaneous.values():
        ordered = sorted(
            group,
            key=lambda setup: (
                int(setup["observed_time_ns"]),
                -_gross_rr(setup),
                str(setup["symbol"]),
                str(setup["setup_id"]),
            ),
        )
        winner = str(ordered[0]["setup_id"])
        for setup in ordered:
            winners[str(setup["setup_id"])] = winner
    for row in setup_rows:
        winner = winners.get(str(row["plan_id"]))
        row["simultaneous_winner_plan_id"] = winner
        if row["recorded_trade"]:
            row["disposition"] = "RECORDED_TRADE"
        elif row["audit_classification"] == "PATH_AMBIGUOUS":
            row["disposition"] = "PATH_AMBIGUOUS_BEFORE_ENTRY"
        elif row["audit_event"] not in ENTRY_EVENTS:
            row["disposition"] = str(row["audit_event"])
        elif row["busy_plan_id"] is not None:
            row["disposition"] = "FIRST_RETEST_CONSUMED_GLOBAL_SLOT_BUSY"
        elif winner is not None and winner != row["plan_id"]:
            row["disposition"] = "SIMULTANEOUS_CONFLICT_LOST"
        else:
            row["disposition"] = "UNEXPLAINED_MISSING_TRADE"

    setup_by_plan = {str(setup["setup_id"]): setup for setup in setups}
    trade_rows: list[dict[str, object]] = []
    casebook: list[dict[str, object]] = []
    for trade in trades:
        plan_id = str(trade["plan_id"])
        setup = setup_by_plan.get(plan_id)
        if setup is None:
            trade_rows.append(
                {"plan_id": plan_id, "classification": "TRADE_WITHOUT_SETUP"},
            )
            continue
        entry_audit = audits[plan_id]
        bars = bars_by_symbol[str(trade["symbol"])]
        exit_audit = audit_open_position(
            side=int(trade["side"]),
            stop=float(trade["stop"]),
            target=float(trade["target"]),
            bars=bars,
            first_bar_open_ns=int(trade["entry_time_ns"]),
            entry_bar_audit=entry_audit,
        )
        entry_time_match = (
            entry_audit.event_open_time_ns == int(trade["entry_time_ns"])
        )
        exit_time_match = (
            exit_audit.event_close_time_ns == int(trade["exit_time_ns"])
        )
        outcome_match = exit_audit.event == str(trade["outcome"])
        if entry_audit.classification != "ROBUST":
            classification = "PATH_AMBIGUOUS_ENTRY"
        elif not entry_time_match:
            classification = "RECORDED_ENTRY_TIME_MISMATCH"
        elif exit_audit.classification == "PATH_AMBIGUOUS":
            classification = "PATH_AMBIGUOUS_EXIT"
        elif not outcome_match or not exit_time_match:
            classification = "RECORDED_EXIT_MISMATCH"
        else:
            classification = "ROBUST_REPLAY_MATCH"
        trade_rows.append(
            {
                "plan_id": plan_id,
                "symbol": trade["symbol"],
                "family": trade["family"],
                "recorded_outcome": trade["outcome"],
                "recorded_entry_time_ns": int(trade["entry_time_ns"]),
                "recorded_exit_time_ns": int(trade["exit_time_ns"]),
                "entry_audit_classification": entry_audit.classification,
                "entry_audit_event": entry_audit.event,
                "entry_time_match": entry_time_match,
                "exit_audit_classification": exit_audit.classification,
                "exit_audit_event": exit_audit.event,
                "exit_time_match": exit_time_match,
                "outcome_match": outcome_match,
                "classification": classification,
                "gross_rr": float(trade["gross_rr"]),
                "net_r": float(trade["net_r"]),
                "entry_notional_to_nav": float(trade["entry_notional_to_nav"]),
                "semantic_roles": ";".join(_infer_roles(setup)),
                "entry_path_results": json.dumps(
                    [asdict(item) for item in entry_audit.path_results],
                    sort_keys=True,
                ),
                "exit_path_results": json.dumps(
                    [asdict(item) for item in exit_audit.path_results],
                    sort_keys=True,
                ),
            },
        )
        casebook.append(
            {
                "plan_id": plan_id,
                "setup": setup,
                "trade": trade,
                "entry_audit": asdict(entry_audit),
                "exit_audit": asdict(exit_audit),
                "signal_window": _window(
                    bars,
                    int(setup["observed_time_ns"]),
                    5,
                    5,
                ),
                "entry_window": _window(
                    bars,
                    int(trade["entry_time_ns"]),
                    3,
                    3,
                ),
                "exit_window": _window(
                    bars,
                    int(trade["exit_time_ns"]),
                    3,
                    3,
                ),
            },
        )

    pd.DataFrame(setup_rows).to_csv(
        output / "setup_semantic_audit.csv",
        index=False,
    )
    pd.DataFrame(trade_rows).to_csv(
        output / "trade_path_audit.csv",
        index=False,
    )
    (output / "trade_casebook.jsonl").write_text(
        "".join(
            json.dumps(item, sort_keys=True, default=str) + "\n"
            for item in casebook
        ),
        encoding="utf-8",
    )

    coverage = None
    if args.coverage is not None:
        coverage_document = json.loads(
            args.coverage.read_text(encoding="utf-8"),
        )
        run_document = json.loads(
            (run_dir / "run.json").read_text(encoding="utf-8"),
        )
        candidate = str(run_document.get("candidate", ""))
        coverage = coverage_document.get("experiments", {}).get(candidate)

    summary = {
        "run_dir": str(run_dir),
        "evaluation_start": args.start,
        "evaluation_end": args.end,
        "setups": len(setup_rows),
        "trades": len(trade_rows),
        "setup_dispositions": dict(
            Counter(str(row["disposition"]) for row in setup_rows),
        ),
        "trade_classifications": dict(
            Counter(str(row["classification"]) for row in trade_rows),
        ),
        "recorded_trade_replay_matches": sum(
            row.get("classification") == "ROBUST_REPLAY_MATCH"
            for row in trade_rows
        ),
        "path_ambiguous_recorded_trades": sum(
            str(row.get("classification", "")).startswith("PATH_AMBIGUOUS")
            for row in trade_rows
        ),
        "unexplained_missing_trades": sum(
            row.get("disposition") == "UNEXPLAINED_MISSING_TRADE"
            for row in setup_rows
        ),
        "experiment_coverage": coverage,
        "interpretation": {
            "robust_replay_match": "Recorded sequence agrees under both OHLC extreme orderings.",
            "path_ambiguous": "One-minute OHLC cannot establish event order; finer data or Nautilus event replay is required.",
            "unexplained_missing_trade": "A free-slot executable setup was not explained by deterministic arbitration; inspect implementation."
        }
    }
    (output / "audit_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--coverage", type=Path)
    args = parser.parse_args()
    run_audit(args)


if __name__ == "__main__":
    main()
