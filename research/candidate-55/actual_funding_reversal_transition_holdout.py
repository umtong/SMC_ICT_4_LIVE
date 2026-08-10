#!/usr/bin/env python3
"""Untouched holdout for funding-crowding context -> recovery transition.

Development evidence from the first three post-source windows falsified the
external fixed-horizon policy under the four-symbol, one-global-slot project
contract.  It also exposed one result-blind structural distinction before this
holdout was opened:

* actual negative funding plus raw relative downside is context, not entry;
* episodes still falling over the completed prior hour were the loss engine;
* the first completed prior-hour return above zero inside the same context was
  the only natural state transition with pooled positive cost-after expectancy.

This file freezes that interpretation and tests it once on 2026-07-07 through
2026-07-30, a period untouched by the development audit and fully contained in
the completed checksum-verifiable July Binance Vision funding archive.

Policy under test
-----------------
context:
    five settled funding events sum to <= -1 bp, 120-minute return <= 0, and
    raw four-symbol idiosyncratic downside >= 2.5 bp.
transition:
    the first decision in the still-active context whose completed 60-minute
    return is > 0.  Zero is an economic boundary; it is not optimized.
entry:
    next-minute open after the fixed information and decision lag.
invalidation:
    the lowest completed one-minute low of the 120-minute downside leg through
    confirmation.  It belongs to the same causal episode and permits exact
    current-NAV 3% planned-loss sizing if the holdout supports implementation.
management:
    source 12-hour exit unless the structural low is breached first.

The external source baseline is replayed beside the transition policy with the
same score, symbols, cadence, hold and 20/30/40 bp costs.  No value is tuned in
this holdout.  Failure rejects this family as a standalone project scenario;
symbol deletion, threshold movement or hold optimization is forbidden.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from actual_funding_crowding_forensics import (
    DECISION_INTERVAL_MINUTES,
    DECISION_LAG_MINUTES,
    FUNDING_LOOKBACK_EVENTS,
    HOLD_MINUTES,
    MAX_RECENT_SAME_DIRECTION_RETURN_BPS,
    MIN_ABS_FUNDING_BPS,
    MIN_IDIOSYNCRATIC_RETURN_BPS,
    OUTCOME_DAYS,
    PRICE_WARMUP_DAYS,
    PROJECT_ROUND_TRIP_COST_BPS,
    RECENT_RETURN_LOOKBACK_MINUTES,
    RETURN_LOOKBACK_MINUTES,
    SOURCE_COMMIT,
    SOURCE_DATA_END,
    SOURCE_REPOSITORY,
    SOURCE_STRATEGY,
    STRESS_ROUND_TRIP_COST_BPS,
    SYMBOLS,
    SYMBOL_PRIORITY,
    FundingEvent,
    load_funding,
)
from kline_only_inputs import load_range


HOLDOUT_START = date(2026, 7, 7)
HOLDOUT_END = date(2026, 7, 30)
DEVELOPMENT_WINDOWS = (
    ("2026-04-14", "2026-05-11"),
    ("2026-05-12", "2026-06-08"),
    ("2026-06-09", "2026-07-06"),
)
DEVELOPMENT_CONFIRMATION_PRIOR = {
    "confirmed_trades": 26,
    "confirmed_mean_after_20bps": 0.0037954867481960505,
    "confirmed_profit_factor_after_20bps": 1.8431994148784896,
    "unconfirmed_trades": 46,
    "unconfirmed_mean_after_20bps": -0.003668588771862437,
    "unconfirmed_profit_factor_after_20bps": 0.6068839313713509,
}


def _load_inputs(
    *, start: date, end: date, cache: Path, output: Path
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, list[FundingEvent]],
    dict[str, Any],
]:
    load_start = start - timedelta(days=PRICE_WARMUP_DAYS)
    load_end = end + timedelta(days=OUTCOME_DAYS)
    prices: dict[str, pd.DataFrame] = {}
    funding: dict[str, list[FundingEvent]] = {}
    evidence: dict[str, Any] = {}
    reference_clock: pd.DatetimeIndex | None = None

    for symbol in SYMBOLS:
        frame, _feature_path, raw_files, source_evidence = load_range(
            symbol=symbol,
            start=load_start,
            end=load_end,
            cache=cache / "klines",
            output=output / "source" / symbol,
        )
        frame = frame.copy()
        frame["open_time_dt"] = pd.to_datetime(frame["open_time_dt"], utc=True)
        frame["close_time_dt"] = pd.to_datetime(frame["close_time_dt"], utc=True)
        frame = frame.sort_values("open_time_dt").reset_index(drop=True)
        clock = pd.DatetimeIndex(frame["open_time_dt"])
        if reference_clock is None:
            reference_clock = clock
        elif not clock.equals(reference_clock):
            raise RuntimeError(f"cross-symbol minute clock mismatch for {symbol}")
        prices[symbol] = frame

        settled, funding_evidence = load_funding(
            symbol=symbol,
            start=load_start,
            end=load_end,
            cache=cache,
        )
        funding[symbol] = settled
        evidence[symbol] = {
            "kline_rows": len(frame),
            "kline_raw_files": len(raw_files),
            "kline_evidence_files": len(source_evidence),
            "first_open": clock[0].isoformat(),
            "last_open": clock[-1].isoformat(),
            "funding_events": len(settled),
            "funding_first": settled[0].funding_time.isoformat(),
            "funding_last": settled[-1].funding_time.isoformat(),
            "funding_sources": sorted({item.source for item in funding_evidence}),
            "funding_evidence_items": len(funding_evidence),
        }
    return prices, funding, evidence


def _feature_rows(
    *,
    prices: dict[str, pd.DataFrame],
    funding: dict[str, list[FundingEvent]],
    start: date,
    end: date,
) -> pd.DataFrame:
    reference = prices[SYMBOLS[0]]
    clock = pd.DatetimeIndex(reference["open_time_dt"])
    start_ts = pd.Timestamp(start, tz="UTC")
    end_exclusive = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    decision_indices = [
        index
        for index, timestamp in enumerate(clock)
        if (
            start_ts <= timestamp < end_exclusive
            and (timestamp.hour * 60 + timestamp.minute)
            % DECISION_INTERVAL_MINUTES
            == 0
        )
    ]

    active_episode_start: dict[str, int | None] = {
        symbol: None for symbol in SYMBOLS
    }
    previous_context: dict[str, bool] = {symbol: False for symbol in SYMBOLS}
    rows: list[dict[str, Any]] = []

    for index in decision_indices:
        if index < RETURN_LOOKBACK_MINUTES:
            continue
        signal_close_time = pd.Timestamp(reference.iloc[index].close_time_dt)
        information_available = signal_close_time.ceil("min")
        decision_time = information_available + pd.Timedelta(
            minutes=DECISION_LAG_MINUTES
        )
        entry_index = index + 1 + DECISION_LAG_MINUTES
        if entry_index + HOLD_MINUTES >= len(reference):
            continue

        returns_120: dict[str, float] = {}
        returns_60: dict[str, float] = {}
        for symbol in SYMBOLS:
            frame = prices[symbol]
            current_close = float(frame.iloc[index].close)
            returns_120[symbol] = (
                current_close / float(frame.iloc[index - RETURN_LOOKBACK_MINUTES].close)
                - 1.0
            ) * 10_000.0
            returns_60[symbol] = (
                current_close
                / float(frame.iloc[index - RECENT_RETURN_LOOKBACK_MINUTES].close)
                - 1.0
            ) * 10_000.0
        market_return = sum(returns_120.values()) / len(returns_120)

        for symbol in SYMBOLS:
            settled = [
                item for item in funding[symbol] if item.available_at <= decision_time
            ]
            ready = len(settled) >= FUNDING_LOOKBACK_EVENTS
            recent_funding = settled[-FUNDING_LOOKBACK_EVENTS:] if ready else []
            pressure = (
                sum(item.rate for item in recent_funding) * 10_000.0
                if ready
                else math.nan
            )
            idiosyncratic_down = market_return - returns_120[symbol]
            context = bool(
                ready
                and pressure <= -MIN_ABS_FUNDING_BPS
                and returns_120[symbol] <= 0.0
                and idiosyncratic_down >= MIN_IDIOSYNCRATIC_RETURN_BPS
                and returns_60[symbol]
                >= -MAX_RECENT_SAME_DIRECTION_RETURN_BPS
            )
            if context and not previous_context[symbol]:
                active_episode_start[symbol] = index
            if not context:
                active_episode_start[symbol] = None
            previous_context[symbol] = context
            episode_start_index = active_episode_start[symbol]
            episode_key = (
                f"{symbol}:ACTUAL_FUNDING_RECOVERY:{clock[episode_start_index].isoformat()}"
                if episode_start_index is not None
                else ""
            )

            frame = prices[symbol]
            leg_start_index = (
                max(0, episode_start_index - RETURN_LOOKBACK_MINUTES)
                if episode_start_index is not None
                else max(0, index - RETURN_LOOKBACK_MINUTES)
            )
            structure_stop = (
                float(
                    frame.iloc[leg_start_index : index + 1]["low"]
                    .astype(float)
                    .min()
                )
                if context
                else math.nan
            )
            row: dict[str, Any] = {
                "symbol": symbol,
                "signal_index": index,
                "entry_index": entry_index,
                "signal_time": signal_close_time.isoformat(),
                "decision_time": decision_time.isoformat(),
                "context": int(context),
                "confirmation_60m_above_zero": int(context and returns_60[symbol] > 0.0),
                "episode_start_index": episode_start_index,
                "episode_leg_start_index": leg_start_index,
                "episode_key": episode_key,
                "funding_pressure_bps_5_events": pressure if ready else None,
                "latest_funding_bps": (
                    recent_funding[-1].rate * 10_000.0 if ready else None
                ),
                "market_return_bps_120m": market_return,
                "symbol_return_bps_120m": returns_120[symbol],
                "recent_return_bps_60m": returns_60[symbol],
                "idiosyncratic_down_bps": idiosyncratic_down,
                "source_score": (
                    abs(pressure) + idiosyncratic_down if context else None
                ),
                "structure_stop_price": structure_stop if context else None,
            }
            rows.append(row)
    return pd.DataFrame(rows)


def _funding_cash_return(
    *,
    events: list[FundingEvent],
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
) -> tuple[float, int]:
    held = [
        item for item in events if entry_time < item.funding_time <= exit_time
    ]
    return -sum(item.rate for item in held), len(held)


def _fixed_outcome(
    *,
    row: pd.Series,
    frame: pd.DataFrame,
    funding: list[FundingEvent],
) -> dict[str, Any]:
    entry_index = int(row.entry_index)
    exit_index = entry_index + HOLD_MINUTES
    entry = frame.iloc[entry_index]
    exit_row = frame.iloc[exit_index]
    entry_time = pd.Timestamp(entry.open_time_dt)
    exit_time = pd.Timestamp(exit_row.open_time_dt)
    entry_price = float(entry.open)
    exit_price = float(exit_row.open)
    funding_cash, held_events = _funding_cash_return(
        events=funding, entry_time=entry_time, exit_time=exit_time
    )
    gross = exit_price / entry_price - 1.0 + funding_cash
    window = frame.iloc[entry_index:exit_index]
    result: dict[str, Any] = {
        "entry_time": entry_time.isoformat(),
        "fixed_exit_time": exit_time.isoformat(),
        "entry_price": entry_price,
        "fixed_exit_price": exit_price,
        "fixed_gross_with_funding": gross,
        "fixed_funding_cash_return": funding_cash,
        "fixed_held_funding_events": held_events,
        "fixed_mfe_return": float(
            window["high"].astype(float).max() / entry_price - 1.0
        ),
        "fixed_mae_return": float(
            window["low"].astype(float).min() / entry_price - 1.0
        ),
    }
    for cost in (PROJECT_ROUND_TRIP_COST_BPS, *STRESS_ROUND_TRIP_COST_BPS):
        result[f"fixed_net_{int(cost)}bps"] = gross - cost / 10_000.0
    return result


def _structural_outcome(
    *,
    row: pd.Series,
    frame: pd.DataFrame,
    funding: list[FundingEvent],
) -> dict[str, Any]:
    entry_index = int(row.entry_index)
    fixed_exit_index = entry_index + HOLD_MINUTES
    entry = frame.iloc[entry_index]
    entry_time = pd.Timestamp(entry.open_time_dt)
    entry_price = float(entry.open)
    stop = float(row.structure_stop_price)
    price_risk_fraction = (entry_price - stop) / entry_price
    planned_loss_fraction = (
        price_risk_fraction + PROJECT_ROUND_TRIP_COST_BPS / 10_000.0
    )
    valid = bool(
        math.isfinite(stop)
        and stop > 0.0
        and entry_price > stop
        and planned_loss_fraction > 0.0
    )
    if not valid:
        return {
            "structural_geometry_valid": 0,
            "structural_stop_price": stop,
            "structural_price_risk_fraction": price_risk_fraction,
            "structural_planned_loss_fraction": planned_loss_fraction,
        }

    exit_index = fixed_exit_index
    exit_price = float(frame.iloc[fixed_exit_index].open)
    exit_reason = "FIXED_12H"
    # Conservative ordering: a completed entry minute which also touches the
    # structural low is stopped.  No same-minute favorable path is credited.
    for index in range(entry_index, fixed_exit_index):
        if float(frame.iloc[index].low) <= stop:
            exit_index = index
            exit_price = stop
            exit_reason = "STRUCTURAL_INVALIDATION"
            break
    exit_time = pd.Timestamp(frame.iloc[exit_index].open_time_dt)
    funding_cash, held_events = _funding_cash_return(
        events=funding, entry_time=entry_time, exit_time=exit_time
    )
    gross = exit_price / entry_price - 1.0 + funding_cash
    net = gross - PROJECT_ROUND_TRIP_COST_BPS / 10_000.0
    path = frame.iloc[entry_index : max(entry_index + 1, exit_index + 1)]
    mfe = float(path["high"].astype(float).max() / entry_price - 1.0)
    mae = float(path["low"].astype(float).min() / entry_price - 1.0)
    return {
        "structural_geometry_valid": 1,
        "structural_stop_price": stop,
        "structural_price_risk_fraction": price_risk_fraction,
        "structural_planned_loss_fraction": planned_loss_fraction,
        "structural_cost_share_of_planned_loss": (
            PROJECT_ROUND_TRIP_COST_BPS / 10_000.0 / planned_loss_fraction
        ),
        "structural_exit_time": exit_time.isoformat(),
        "structural_exit_price": exit_price,
        "structural_exit_reason": exit_reason,
        "structural_funding_cash_return": funding_cash,
        "structural_held_funding_events": held_events,
        "structural_gross_return": gross,
        "structural_net_20bps": net,
        "structural_r_multiple": net / planned_loss_fraction,
        "structural_mfe_return": mfe,
        "structural_mae_return": mae,
        "structural_max_favorable_r": (
            (mfe - PROJECT_ROUND_TRIP_COST_BPS / 10_000.0)
            / planned_loss_fraction
        ),
    }


def _select_variant(
    *,
    rows: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    funding: dict[str, list[FundingEvent]],
    confirmation_required: bool,
) -> pd.DataFrame:
    used_episodes: set[str] = set()
    slot_free_time = pd.Timestamp.min.tz_localize("UTC")
    selected: list[dict[str, Any]] = []
    variant = (
        "RECOVERY_TRANSITION" if confirmation_required else "SOURCE_CONTEXT"
    )

    for decision_time_text, group in rows.groupby("decision_time", sort=True):
        decision_time = pd.Timestamp(decision_time_text)
        if decision_time < slot_free_time:
            continue
        eligible = group[group["context"] == 1]
        if confirmation_required:
            eligible = eligible[
                eligible["confirmation_60m_above_zero"] == 1
            ]
        eligible = eligible[
            ~eligible["episode_key"].astype(str).isin(used_episodes)
        ]
        if eligible.empty:
            continue
        winner = eligible.sort_values(
            ["source_score", "funding_pressure_bps_5_events", "symbol"],
            ascending=[False, True, True],
            key=None,
        ).iloc[0]
        episode_key = str(winner.episode_key)
        if not episode_key:
            continue
        symbol = str(winner.symbol)
        fixed = _fixed_outcome(
            row=winner, frame=prices[symbol], funding=funding[symbol]
        )
        item = winner.to_dict()
        item.update(fixed)
        item["variant"] = variant
        if confirmation_required:
            structural = _structural_outcome(
                row=winner, frame=prices[symbol], funding=funding[symbol]
            )
            item.update(structural)
            slot_free_time = pd.Timestamp(
                structural.get("structural_exit_time", fixed["fixed_exit_time"])
            )
        else:
            slot_free_time = pd.Timestamp(fixed["fixed_exit_time"])
        used_episodes.add(episode_key)
        selected.append(item)
    return pd.DataFrame(selected)


def _summary(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {"trades": 0}
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return {"trades": 0}
    positive = values[values > 0.0]
    negative = values[values < 0.0]
    gross_profit = float(positive.sum())
    gross_loss = float(-negative.sum())
    by_symbol: dict[str, Any] = {}
    for symbol, group in frame.assign(
        _value=pd.to_numeric(frame[column], errors="coerce")
    ).groupby("symbol", sort=True):
        symbol_values = group["_value"].dropna()
        by_symbol[str(symbol)] = {
            "trades": int(symbol_values.size),
            "mean": (
                float(symbol_values.mean()) if not symbol_values.empty else None
            ),
            "sum": float(symbol_values.sum()),
            "wins": int((symbol_values > 0.0).sum()),
        }
    return {
        "trades": int(values.size),
        "wins": int((values > 0.0).sum()),
        "losses": int((values < 0.0).sum()),
        "win_rate": float((values > 0.0).mean()),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "sum": float(values.sum()),
        "unit_notional_compounded": float((1.0 + values).prod() - 1.0),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (
            gross_profit / gross_loss if gross_loss > 0.0 else None
        ),
        "largest_winner_share": (
            float(positive.max() / gross_profit) if gross_profit > 0.0 else 1.0
        ),
        "positive_symbols": int(
            sum(item["sum"] > 0.0 for item in by_symbol.values())
        ),
        "by_symbol": by_symbol,
    }


def run(*, start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    if (start, end) != (HOLDOUT_START, HOLDOUT_END):
        raise ValueError(
            f"holdout is frozen to {HOLDOUT_START}..{HOLDOUT_END}; got {start}..{end}"
        )
    if start <= SOURCE_DATA_END:
        raise ValueError("holdout must be strictly after source data")
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    prices, funding, input_evidence = _load_inputs(
        start=start, end=end, cache=cache, output=output
    )
    rows = _feature_rows(
        prices=prices, funding=funding, start=start, end=end
    )
    source = _select_variant(
        rows=rows,
        prices=prices,
        funding=funding,
        confirmation_required=False,
    )
    confirmed = _select_variant(
        rows=rows,
        prices=prices,
        funding=funding,
        confirmation_required=True,
    )

    rows.to_csv(output / "decision_states.csv", index=False)
    source.to_csv(output / "source_baseline_trades.csv", index=False)
    confirmed.to_csv(output / "confirmed_transition_trades.csv", index=False)

    source20 = _summary(source, "fixed_net_20bps")
    source30 = _summary(source, "fixed_net_30bps")
    confirmed20 = _summary(confirmed, "fixed_net_20bps")
    confirmed30 = _summary(confirmed, "fixed_net_30bps")
    structural20 = _summary(confirmed, "structural_net_20bps")
    structural_r = _summary(confirmed, "structural_r_multiple")

    valid_geometry = confirmed[
        pd.to_numeric(
            confirmed.get("structural_geometry_valid", pd.Series(dtype=float)),
            errors="coerce",
        )
        == 1
    ]
    geometry: dict[str, Any] = {
        "valid_trades": int(len(valid_geometry)),
        "invalid_trades": int(len(confirmed) - len(valid_geometry)),
    }
    if not valid_geometry.empty:
        geometry.update(
            {
                "median_price_risk_fraction": float(
                    valid_geometry["structural_price_risk_fraction"].median()
                ),
                "mean_price_risk_fraction": float(
                    valid_geometry["structural_price_risk_fraction"].mean()
                ),
                "median_planned_loss_fraction_including_cost": float(
                    valid_geometry["structural_planned_loss_fraction"].median()
                ),
                "median_cost_share_of_planned_loss": float(
                    valid_geometry[
                        "structural_cost_share_of_planned_loss"
                    ].median()
                ),
                "stop_hit_fraction": float(
                    (
                        valid_geometry["structural_exit_reason"]
                        == "STRUCTURAL_INVALIDATION"
                    ).mean()
                ),
                "median_max_favorable_r": float(
                    valid_geometry["structural_max_favorable_r"].median()
                ),
                "mean_max_favorable_r": float(
                    valid_geometry["structural_max_favorable_r"].mean()
                ),
            }
        )

    source_mean = float(source20.get("mean", -math.inf))
    confirmed_mean = float(confirmed20.get("mean", -math.inf))
    support_checks = {
        "at_least_four_confirmed_independent_trades": int(
            confirmed20.get("trades", 0)
        )
        >= 4,
        "confirmed_fixed_hold_positive_after_20bps": confirmed_mean > 0.0,
        "confirmed_fixed_hold_positive_after_30bps": float(
            confirmed30.get("mean", -math.inf)
        )
        > 0.0,
        "confirmation_improves_source_fixed_hold": confirmed_mean > source_mean,
        "all_confirmed_trades_have_structural_geometry": (
            int(geometry["valid_trades"]) == int(confirmed20.get("trades", 0))
        ),
        "structural_net_positive_after_20bps": float(
            structural20.get("mean", -math.inf)
        )
        > 0.0,
        "structural_expectancy_r_positive": float(
            structural_r.get("mean", -math.inf)
        )
        > 0.0,
        "structural_profit_factor_above_one": float(
            structural20.get("profit_factor") or 0.0
        )
        > 1.0,
        "not_single_winner_dominated": float(
            structural20.get("largest_winner_share", 1.0)
        )
        <= 0.50,
        "at_least_two_positive_symbols": int(
            structural20.get("positive_symbols", 0)
        )
        >= 2,
    }
    warranted = all(support_checks.values())
    verdict = {
        "candidate": "candidate-55",
        "family": "ACTUAL_FUNDING_CONTEXT_RECOVERY_TRANSITION_LONG",
        "stage": "UNTOUCHED_POST_SOURCE_TRANSITION_HOLDOUT",
        "evaluation_start": start.isoformat(),
        "evaluation_end": end.isoformat(),
        "calendar_days": (end - start).days + 1,
        "development_windows_excluded": [list(item) for item in DEVELOPMENT_WINDOWS],
        "source": {
            "repository": SOURCE_REPOSITORY,
            "commit": SOURCE_COMMIT,
            "strategy_path": SOURCE_STRATEGY,
            "source_data_end": SOURCE_DATA_END.isoformat(),
        },
        "development_prior_frozen_before_holdout": DEVELOPMENT_CONFIRMATION_PRIOR,
        "policy_frozen": {
            "symbols": list(SYMBOLS),
            "one_global_slot": True,
            "actual_settled_funding_only": True,
            "funding_lookback_events": FUNDING_LOOKBACK_EVENTS,
            "summed_funding_boundary_bps": -MIN_ABS_FUNDING_BPS,
            "return_lookback_minutes": RETURN_LOOKBACK_MINUTES,
            "idiosyncratic_downside_boundary_bps": MIN_IDIOSYNCRATIC_RETURN_BPS,
            "decision_interval_minutes": DECISION_INTERVAL_MINUTES,
            "decision_lag_minutes": DECISION_LAG_MINUTES,
            "confirmation": "first completed 60m return > 0 in active context episode",
            "entry": "next-minute open after information and decision lag",
            "invalidation": "lowest completed minute low of the 120m downside leg through confirmation",
            "management": "structural invalidation or fixed 12h source exit",
            "round_trip_cost_bps": PROJECT_ROUND_TRIP_COST_BPS,
            "stress_round_trip_cost_bps": list(STRESS_ROUND_TRIP_COST_BPS),
            "risk_sizing_if_promoted": "current continuous NAV x 3% divided by per-unit structural planned loss including entry and stop costs",
        },
        "decision_symbol_rows": int(len(rows)),
        "context_rows": int(rows["context"].sum()),
        "confirmation_rows": int(
            rows["confirmation_60m_above_zero"].sum()
        ),
        "source_baseline_after_20bps": source20,
        "source_baseline_after_30bps": source30,
        "confirmed_fixed_hold_after_20bps": confirmed20,
        "confirmed_fixed_hold_after_30bps": confirmed30,
        "confirmed_structural_after_20bps": structural20,
        "confirmed_structural_r": structural_r,
        "structural_geometry": geometry,
        "support_checks": support_checks,
        "nautilus_implementation_warranted": warranted,
        "medium_or_long_validation_warranted": False,
        "production_ready": False,
        "decision": (
            "PROMOTE_TO_MINIMAL_NAUTILUS_ACCOUNT_IMPLEMENTATION"
            if warranted
            else "REJECT_STANDALONE_FAMILY_WITHOUT_PARAMETER_OR_SYMBOL_RESCUE"
        ),
        "falsification_action": (
            "Zero, funding, dislocation, symbols, cadence and 12h hold remain fixed. "
            "A failed holdout ends this standalone family. A passed holdout permits "
            "only a minimal Nautilus one-account implementation; it does not permit "
            "medium, long or production claims."
        ),
        "input_evidence": input_evidence,
    }
    (output / "VERDICT.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(verdict, indent=2, sort_keys=True, allow_nan=False))
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=HOLDOUT_START.isoformat())
    parser.add_argument("--end", default=HOLDOUT_END.isoformat())
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache.resolve(),
        output=args.output.resolve(),
    )


if __name__ == "__main__":
    main()
