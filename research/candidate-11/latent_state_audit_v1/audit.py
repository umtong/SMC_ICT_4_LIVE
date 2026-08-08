#!/usr/bin/env python3
"""Audit whether an apparent FAR confirmation repaired the prior market state.

This is an opened-data diagnosis only.  It does not simulate fills, change risk,
or authorize a strategy.  The one tested state distinction is fixed before the
outcomes are inspected by this script:

* a broad adverse state exists when at least three of four completed-market
  directional trend scores oppose the proposed trade;
* that state is repaired only when the candidate's sweep-to-confirmation move
  recovers at least one half of its own adverse trailing directional return;
* otherwise the event is a counter-trend bounce, not a completed failed auction.

The 50% boundary is the midpoint of the already measured adverse displacement,
not a PnL-optimized parameter.  The audit applies the same definition to the
Candidate 11 continuous development account and Candidate 14's separate 84-day
continuous account.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from statistics import median
from typing import Any


@dataclass(frozen=True, slots=True)
class TradeState:
    source: str
    scenario_id: str
    symbol: str
    scenario: str
    direction: str
    observed_ts_ns: int
    pnl: float
    won: bool
    adverse_breadth: int
    adverse_trailing_return: float
    event_move: float
    repair_fraction: float
    event_path_efficiency: float
    event_standardized_displacement: float
    confirmation_impulse: float
    peer_event_median: float
    latent_state: str


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def decimal_number(value: Any) -> float:
    text = str(value).replace(",", "").strip()
    if not text:
        raise ValueError("empty decimal")
    return float(Decimal(text.split()[0]))


def timestamp_ns(value: Any) -> int:
    text = str(value).strip()
    try:
        number = Decimal(text)
    except Exception:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1_000_000_000)
    magnitude = abs(number)
    if magnitude >= Decimal("1e17"):
        return int(number)
    if magnitude >= Decimal("1e14"):
        return int(number * Decimal("1000"))
    if magnitude >= Decimal("1e11"):
        return int(number * Decimal("1000000"))
    return int(number * Decimal("1000000000"))


def plan_rows(path: Path) -> list[dict[str, Any]]:
    value = load_json(path)
    rows = value.get("plans") if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise TypeError(f"{path} does not contain plans")
    return [row for row in rows if isinstance(row, dict)]


def position_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def state_from_plan(source: str, plan: dict[str, Any], pnl: float) -> TradeState:
    details = plan.get("details")
    if not isinstance(details, dict):
        raise ValueError("plan details missing")
    leadership = details.get("market_leadership")
    if not isinstance(leadership, dict):
        raise ValueError("market leadership evidence missing")
    directional = leadership.get("directional_returns")
    trend_scores = leadership.get("directional_trend_scores")
    if not isinstance(directional, dict) or not isinstance(trend_scores, dict):
        raise ValueError("directional state missing")

    symbol = str(plan["symbol"])
    adverse_breadth = sum(float(value) < 0.0 for value in trend_scores.values())
    own_trailing = float(directional[symbol])
    adverse_trailing = max(0.0, -own_trailing)
    event_move = max(0.0, float(leadership.get("candidate_event_move") or 0.0))
    repair_fraction = 1.0 if adverse_trailing <= 1e-12 else event_move / adverse_trailing
    broad_adverse = adverse_breadth >= 3
    repaired = repair_fraction >= 0.50
    latent_state = (
        "COUNTERTREND_BOUNCE_UNRESOLVED"
        if broad_adverse and not repaired
        else "STATE_REPAIRED_OR_DIRECTIONALLY_ALIGNED"
    )
    return TradeState(
        source=source,
        scenario_id=str(plan["scenario_id"]),
        symbol=symbol,
        scenario=str(plan["scenario"]),
        direction=str(plan["direction"]),
        observed_ts_ns=int(plan["observed_ts_ns"]),
        pnl=float(pnl),
        won=pnl > 0.0,
        adverse_breadth=adverse_breadth,
        adverse_trailing_return=adverse_trailing,
        event_move=event_move,
        repair_fraction=repair_fraction,
        event_path_efficiency=float(leadership.get("event_path_efficiency") or 0.0),
        event_standardized_displacement=float(
            leadership.get("event_standardized_displacement") or 0.0
        ),
        confirmation_impulse=float(leadership.get("confirmation_impulse") or 0.0),
        peer_event_median=float(leadership.get("peer_event_median") or 0.0),
        latent_state=latent_state,
    )


def candidate11_states(root: Path) -> list[TradeState]:
    aggregate = load_json(root / "core_far_continuous_v1" / "aggregate.json")
    outcomes = {
        str(row["scenario_id"]): decimal_number(row["realized_pnl"])
        for row in aggregate["trades"]
    }
    records: list[TradeState] = []
    for block in ("D1", "D2", "D3"):
        path = root / "core_far_continuous_v1" / "results" / block / "submitted_plans.json"
        for plan in plan_rows(path):
            scenario_id = str(plan["scenario_id"])
            if scenario_id not in outcomes:
                raise ValueError(f"Candidate 11 plan lacks outcome: {scenario_id}")
            records.append(state_from_plan("candidate-11-development", plan, outcomes[scenario_id]))
    return records


def candidate14_states(plans_path: Path, positions_path: Path) -> list[TradeState]:
    plans = sorted(plan_rows(plans_path), key=lambda row: int(row["observed_ts_ns"]))
    positions = position_rows(positions_path)
    unused = set(range(len(positions)))
    records: list[TradeState] = []
    for plan in plans:
        symbol = str(plan["symbol"])
        observed = int(plan["observed_ts_ns"])
        candidates: list[tuple[int, int]] = []
        for index in unused:
            position = positions[index]
            instrument = str(position.get("instrument_id", ""))
            if not instrument.startswith(symbol):
                continue
            opened = timestamp_ns(position.get("ts_opened") or position.get("ts_init"))
            delta = opened - observed
            if 0 <= delta <= 24 * 60 * 60 * 1_000_000_000:
                candidates.append((delta, index))
        if not candidates:
            raise ValueError(f"Candidate 14 plan lacks position: {plan['scenario_id']}")
        _, index = min(candidates)
        unused.remove(index)
        pnl = decimal_number(positions[index].get("realized_pnl", "0"))
        records.append(state_from_plan("candidate-14-continuous-holdout", plan, pnl))
    if unused:
        raise ValueError(f"unmapped Candidate 14 positions: {sorted(unused)}")
    return records


def group_summary(records: list[TradeState], state: str | None = None) -> dict[str, Any]:
    selected = records if state is None else [row for row in records if row.latent_state == state]
    wins = [row for row in selected if row.won]
    losses = [row for row in selected if not row.won]
    return {
        "trades": len(selected),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(selected) if selected else None,
        "net_pnl": sum(row.pnl for row in selected),
        "median_repair_fraction": median(row.repair_fraction for row in selected) if selected else None,
        "sources": sorted({row.source for row in selected}),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    records = candidate11_states(args.candidate11_root)
    records.extend(candidate14_states(args.candidate14_plans, args.candidate14_positions))
    states = sorted({row.latent_state for row in records})
    result = {
        "schema": "candidate-11-latent-state-audit-v1",
        "research_stage": "OPENED_DATA_DIAGNOSIS",
        "can_claim_alpha": False,
        "can_advance_candidate": False,
        "tested_assumption": (
            "A broad adverse state is not a completed reversal until the local event repairs "
            "at least half of its own adverse trailing displacement."
        ),
        "threshold_search_performed": False,
        "all": group_summary(records),
        "by_state": {state: group_summary(records, state) for state in states},
        "by_source": {
            source: group_summary([row for row in records if row.source == source])
            for source in sorted({row.source for row in records})
        },
        "trades": [asdict(row) for row in records],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate11-root", type=Path, required=True)
    parser.add_argument("--candidate14-plans", type=Path, required=True)
    parser.add_argument("--candidate14-positions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
