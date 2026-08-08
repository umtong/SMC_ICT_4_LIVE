#!/usr/bin/env python3
"""Diagnose parent initiative auction state before local reversal entries.

A local sweep/reclaim can be a genuine failed auction or merely a pullback inside
an already accepted higher-level auction.  This module uses only completed,
non-overlapping thirty-minute auctions built from the checksum-verified one-
minute Binance USD-M data.  A parent initiative state begins when a completed
auction closes beyond the previous auction's extreme while its volume-weighted
value migrates in the same direction.  The state persists until a completed
auction closes back through the accepted boundary or the opposite state is
established.

The current initiative-auction strategy is not changed here.  This script joins
that independent parent state to already completed NautilusTrader trades after
all causal features are computed.  It creates no signals, orders, fills, PnL or
NAV.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date
import json
from math import prod
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from data_flow import load_flow_bundle
from smc_ict_4.manifest import write_json_atomic


NS_PER_MINUTE = 60_000_000_000
AUCTION_MINUTES = 30
NS_PER_AUCTION = AUCTION_MINUTES * NS_PER_MINUTE


@dataclass(frozen=True, slots=True)
class ParentAuctionState:
    direction: str
    accepted_boundary: float
    activated_ns: int
    source_bucket_end_ns: int
    source_value: float
    current_value: float
    age_buckets: int

    def __post_init__(self) -> None:
        if self.direction not in {"BULLISH", "BEARISH"}:
            raise ValueError(f"unsupported direction: {self.direction}")
        if self.accepted_boundary <= 0.0:
            raise ValueError("accepted boundary must be positive")
        if self.activated_ns <= 0 or self.source_bucket_end_ns <= 0:
            raise ValueError("timestamps must be positive")
        if self.age_buckets < 0:
            raise ValueError("age_buckets must be non-negative")


def prepare_complete_auctions(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate exact, complete one-minute rows into non-overlapping auctions."""
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"auction frame missing columns: {missing}")
    if frame.empty:
        raise ValueError("auction frame must not be empty")

    work = frame.copy()
    work["timestamp_ns"] = [int(value.value) for value in work.index]
    work = work.sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    gaps = work["timestamp_ns"].astype("int64").diff().dropna()
    if bool((gaps != NS_PER_MINUTE).any()):
        raise RuntimeError("one-minute input is not contiguous")
    work["auction_id"] = work["timestamp_ns"].astype("int64") // NS_PER_AUCTION

    records: list[dict[str, Any]] = []
    for auction_id, part in work.groupby("auction_id", sort=True):
        if len(part.index) != AUCTION_MINUTES:
            continue
        timestamps = part["timestamp_ns"].astype("int64")
        local_gaps = timestamps.diff().dropna()
        if bool((local_gaps != NS_PER_MINUTE).any()):
            continue
        volume = float(part["volume"].sum())
        value = (
            float((part["close"].astype(float) * part["volume"].astype(float)).sum())
            / volume
            if volume > 0.0
            else float(part["close"].astype(float).mean())
        )
        records.append(
            {
                "auction_id": int(auction_id),
                "start_ns": int(timestamps.iloc[0]),
                "end_ns": int(timestamps.iloc[-1]),
                "open": float(part.iloc[0]["open"]),
                "high": float(part["high"].max()),
                "low": float(part["low"].min()),
                "close": float(part.iloc[-1]["close"]),
                "volume": volume,
                "value": value,
            }
        )
    if len(records) < 2:
        raise RuntimeError("fewer than two complete parent auctions")
    return pd.DataFrame.from_records(records).sort_values("end_ns").reset_index(drop=True)


def build_parent_state_history(auctions: pd.DataFrame) -> list[dict[str, Any]]:
    """Return the parent state after each completed auction.

    Activation is price acceptance beyond the immediately preceding auction and
    same-direction value migration.  Persistence is owned by the accepted
    boundary, not by elapsed time, PnL, or a fitted score.
    """
    required = {"end_ns", "high", "low", "close", "value"}
    missing = sorted(required - set(auctions.columns))
    if missing:
        raise ValueError(f"parent auctions missing columns: {missing}")
    if len(auctions.index) < 2:
        raise ValueError("at least two auctions are required")

    current: ParentAuctionState | None = None
    history: list[dict[str, Any]] = []
    first = auctions.iloc[0]
    history.append(
        {
            "end_ns": int(first["end_ns"]),
            "state": None,
            "activation": None,
            "release": None,
        }
    )

    for index in range(1, len(auctions.index)):
        previous = auctions.iloc[index - 1]
        row = auctions.iloc[index]
        bullish = (
            float(row["close"]) > float(previous["high"])
            and float(row["value"]) > float(previous["value"])
        )
        bearish = (
            float(row["close"]) < float(previous["low"])
            and float(row["value"]) < float(previous["value"])
        )
        activation: str | None = None
        release: str | None = None

        if bullish:
            current = ParentAuctionState(
                direction="BULLISH",
                accepted_boundary=float(previous["high"]),
                activated_ns=int(row["end_ns"]),
                source_bucket_end_ns=int(previous["end_ns"]),
                source_value=float(previous["value"]),
                current_value=float(row["value"]),
                age_buckets=0,
            )
            activation = "BULLISH_OUTSIDE_ACCEPTANCE_WITH_VALUE_MIGRATION"
        elif bearish:
            current = ParentAuctionState(
                direction="BEARISH",
                accepted_boundary=float(previous["low"]),
                activated_ns=int(row["end_ns"]),
                source_bucket_end_ns=int(previous["end_ns"]),
                source_value=float(previous["value"]),
                current_value=float(row["value"]),
                age_buckets=0,
            )
            activation = "BEARISH_OUTSIDE_ACCEPTANCE_WITH_VALUE_MIGRATION"
        elif current is not None:
            reclaimed = (
                float(row["close"]) <= current.accepted_boundary
                if current.direction == "BULLISH"
                else float(row["close"]) >= current.accepted_boundary
            )
            if reclaimed:
                release = "ACCEPTED_BOUNDARY_RECLAIMED"
                current = None
            else:
                current = ParentAuctionState(
                    direction=current.direction,
                    accepted_boundary=current.accepted_boundary,
                    activated_ns=current.activated_ns,
                    source_bucket_end_ns=current.source_bucket_end_ns,
                    source_value=current.source_value,
                    current_value=float(row["value"]),
                    age_buckets=current.age_buckets + 1,
                )

        history.append(
            {
                "end_ns": int(row["end_ns"]),
                "state": None if current is None else asdict(current),
                "activation": activation,
                "release": release,
                "close": float(row["close"]),
                "value": float(row["value"]),
            }
        )
    return history


def parent_state_strictly_before(
    history: Iterable[Mapping[str, Any]],
    event_ns: int,
) -> ParentAuctionState | None:
    """Return only a state whose entire parent auction completed before event."""
    selected: Mapping[str, Any] | None = None
    for item in history:
        if int(item["end_ns"]) >= int(event_ns):
            break
        selected = item
    if selected is None or selected.get("state") is None:
        return None
    return ParentAuctionState(**dict(selected["state"]))


def reversal_context(direction: str, state: ParentAuctionState | None) -> str:
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported reversal direction: {direction}")
    if state is None:
        return "BALANCE_OR_UNRESOLVED"
    if (
        (direction == "SHORT" and state.direction == "BULLISH")
        or (direction == "LONG" and state.direction == "BEARISH")
    ):
        return "COUNTER_INITIATIVE"
    return "WITH_INITIATIVE"


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _contact_event(
    events: Iterable[Mapping[str, Any]],
    scenario_id: str,
) -> Mapping[str, Any]:
    reasons = {"UPPER_POOL_SWEEP_RECLAIM", "LOWER_POOL_SWEEP_RECLAIM"}
    for event in events:
        if event.get("scenario_id") == scenario_id and event.get("reason_code") in reasons:
            return event
    raise RuntimeError(f"missing source contact for {scenario_id}")


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for context in (
        "COUNTER_INITIATIVE",
        "WITH_INITIATIVE",
        "BALANCE_OR_UNRESOLVED",
    ):
        selected = [row for row in rows if row["parent_context"] == context]
        returns = [float(row["net_return_on_nav"]) for row in selected]
        output[context] = {
            "trades": len(selected),
            "wins": sum(bool(row["win"]) for row in selected),
            "losses": sum(not bool(row["win"]) for row in selected),
            "win_rate": (
                sum(bool(row["win"]) for row in selected) / len(selected)
                if selected
                else 0.0
            ),
            "diagnostic_compounded_recorded_returns": (
                prod(1.0 + value for value in returns) - 1.0
                if returns
                else 0.0
            ),
        }
    allowed = [row for row in rows if row["parent_context"] != "COUNTER_INITIATIVE"]
    returns = [float(row["net_return_on_nav"]) for row in allowed]
    output["PREDECLARED_REVERSAL_RULE"] = {
        "rule": "allow reversal only outside counter-initiative parent state",
        "selected": len(allowed),
        "wins": sum(bool(row["win"]) for row in allowed),
        "losses": sum(not bool(row["win"]) for row in allowed),
        "win_rate": (
            sum(bool(row["win"]) for row in allowed) / len(allowed)
            if allowed
            else 0.0
        ),
        "diagnostic_compounded_recorded_returns": (
            prod(1.0 + value for value in returns) - 1.0
            if returns
            else 0.0
        ),
        "note": "diagnostic only; exact counterfactual NAV requires fresh NautilusTrader replay",
    }
    return output


def diagnose(args: argparse.Namespace) -> int:
    run_root = args.run_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    bundle = load_flow_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output / "parent_data_manifest.json",
    )
    auctions = prepare_complete_auctions(bundle.frame)
    history = build_parent_state_history(auctions)
    events = _read_events(run_root / "events.jsonl")
    trades = pd.read_csv(run_root / "trades.csv")
    absorption = trades[trades["kind"] == "ABSORPTION_RECLAIM"].copy()

    rows: list[dict[str, Any]] = []
    for trade in absorption.itertuples(index=False):
        scenario_id = str(trade.scenario_id)
        contact = _contact_event(events, scenario_id)
        contact_ns = int(contact["event_time_ns"])
        state = parent_state_strictly_before(history, contact_ns)
        context = reversal_context(str(trade.direction), state)
        rows.append(
            {
                "stage": str(args.stage),
                "scenario_id": scenario_id,
                "direction": str(trade.direction),
                "contact_time_ns": contact_ns,
                "opened_ns": int(trade.opened_ns),
                "closed_ns": int(trade.closed_ns),
                "net_pnl": float(trade.net_pnl),
                "net_return_on_nav": float(trade.net_return_on_nav),
                "win": float(trade.net_pnl) > 0.0,
                "parent_context": context,
                "parent_direction": None if state is None else state.direction,
                "parent_accepted_boundary": (
                    None if state is None else state.accepted_boundary
                ),
                "parent_activated_ns": None if state is None else state.activated_ns,
                "parent_age_buckets": None if state is None else state.age_buckets,
                "parent_source_value": None if state is None else state.source_value,
                "parent_current_value": None if state is None else state.current_value,
            }
        )

    pd.DataFrame(rows).to_csv(output / "parent_auction_features.csv", index=False)
    write_json_atomic(
        output / "parent_auction_history.json",
        {"auction_minutes": AUCTION_MINUTES, "history": history},
    )
    summary = {
        "candidate": "candidate-07-parent-auction-state-diagnostic",
        "stage": str(args.stage),
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "absorption_trades": len(rows),
        "context_results": _summary(rows),
        "causal_contract": {
            "parent_auction": "non-overlapping complete 30-minute windows",
            "value": "one-minute close weighted by verified traded base volume",
            "activation": "close beyond prior auction extreme plus same-direction value migration",
            "persistence": "until completed 30-minute close reclaims accepted boundary or opposite activation",
            "event_lookup": "latest parent auction ending strictly before local sweep/reclaim",
            "outcome_joined_after_state_computation": True,
            "orders_or_pnl_created": False,
            "future_information_in_state": False,
        },
    }
    write_json_atomic(output / "parent_auction_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(diagnose(build_parser().parse_args()))
