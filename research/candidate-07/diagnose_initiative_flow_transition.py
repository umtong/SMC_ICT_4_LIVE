#!/usr/bin/env python3
"""Diagnose causal aggressor-flow transfer in initiative-auction entries.

The current initiative-auction candidate is intentionally left unchanged. This
module reuses the exact checksum-verified one-minute Binance USD-M archives and
completed NautilusTrader evidence to answer one narrow question: did aggressive
flow actually transfer from the attack side during the source sweep to the
reversal side during MSS confirmation and the executable entry minute?

The script creates no signals, orders, fills, PnL, or NAV. Outcome fields are
joined only after all causal features have been computed, solely to diagnose the
latent-state error in already completed development weeks.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date
import json
from math import prod
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

import pandas as pd

from data_flow import load_flow_bundle
from smc_ict_4.manifest import write_json_atomic


NS_PER_MINUTE = 60_000_000_000
NS_PER_FIVE_MINUTES = 5 * NS_PER_MINUTE


@dataclass(frozen=True, slots=True)
class OrientedFlowSummary:
    """Completed-window flow expressed in one economically relevant direction."""

    bars: int
    oriented_imbalance: float
    oriented_volume_share: float
    oriented_minute_fraction: float
    terminal_oriented_imbalance: float
    oriented_price_move_atr: float
    oriented_path_efficiency: float
    directional_volume: float
    counter_volume: float


def prepare_flow_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach exact close timestamps and signed taker flow to verified bars."""
    required = {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "taker_buy_base",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"flow frame missing columns: {missing}")
    work = frame.copy()
    work["timestamp_ns"] = [int(value.value) for value in work.index]
    work["taker_sell_base"] = (
        work["volume"].astype(float) - work["taker_buy_base"].astype(float)
    ).clip(lower=0.0)
    work["signed_base"] = (
        work["taker_buy_base"].astype(float) - work["taker_sell_base"]
    )
    if bool((work["volume"].astype(float) < 0.0).any()):
        raise ValueError("flow frame contains negative volume")
    work = work.sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    return work


def exact_completed_window(
    frame: pd.DataFrame,
    *,
    start_exclusive_ns: int,
    end_inclusive_ns: int,
    expected_bars: int | None,
) -> pd.DataFrame:
    """Return a contiguous completed-minute interval with exact endpoint rules."""
    if end_inclusive_ns <= start_exclusive_ns:
        raise ValueError("window end must follow start")
    part = frame[
        (frame["timestamp_ns"] > int(start_exclusive_ns))
        & (frame["timestamp_ns"] <= int(end_inclusive_ns))
    ].copy()
    if expected_bars is not None and len(part.index) != expected_bars:
        raise RuntimeError(
            "unexpected completed flow bars: "
            f"expected={expected_bars}, actual={len(part.index)}, "
            f"start={start_exclusive_ns}, end={end_inclusive_ns}"
        )
    if part.empty:
        raise RuntimeError("completed flow window is empty")
    gaps = part["timestamp_ns"].astype("int64").diff().dropna()
    if bool((gaps != NS_PER_MINUTE).any()):
        raise RuntimeError("completed flow window is not one-minute contiguous")
    return part.reset_index(drop=True)


def summarize_oriented_flow(
    frame: pd.DataFrame,
    *,
    orientation: int,
    atr: float,
) -> OrientedFlowSummary:
    """Summarize a completed window toward LONG (+1) or SHORT (-1)."""
    if orientation not in {-1, 1}:
        raise ValueError("orientation must be -1 or +1")
    if atr <= 0.0:
        raise ValueError("atr must be positive")
    if frame.empty:
        raise ValueError("flow summary frame must not be empty")

    total_volume = float(frame["volume"].sum())
    if total_volume <= 0.0:
        raise ValueError("flow summary requires positive total volume")
    signed = frame["signed_base"].astype(float)
    oriented_signed = orientation * float(signed.sum())
    directional = (
        frame["taker_buy_base"].astype(float)
        if orientation == 1
        else frame["taker_sell_base"].astype(float)
    )
    counter = (
        frame["taker_sell_base"].astype(float)
        if orientation == 1
        else frame["taker_buy_base"].astype(float)
    )
    terminal_volume = float(frame.iloc[-1]["volume"])
    terminal_imbalance = (
        orientation * float(frame.iloc[-1]["signed_base"]) / terminal_volume
        if terminal_volume > 0.0
        else 0.0
    )

    first_open = float(frame.iloc[0]["open"])
    last_close = float(frame.iloc[-1]["close"])
    close_path = [first_open, *frame["close"].astype(float).tolist()]
    path_length = sum(
        abs(current - previous)
        for previous, current in zip(close_path, close_path[1:])
    )
    oriented_move = orientation * (last_close - first_open)
    path_efficiency = oriented_move / path_length if path_length > 0.0 else 0.0

    return OrientedFlowSummary(
        bars=int(len(frame.index)),
        oriented_imbalance=oriented_signed / total_volume,
        oriented_volume_share=float(directional.sum()) / total_volume,
        oriented_minute_fraction=float((orientation * signed > 0.0).mean()),
        terminal_oriented_imbalance=terminal_imbalance,
        oriented_price_move_atr=oriented_move / atr,
        oriented_path_efficiency=path_efficiency,
        directional_volume=float(directional.sum()),
        counter_volume=float(counter.sum()),
    )


def transfer_rules(
    *,
    source: OrientedFlowSummary,
    confirmation: OrientedFlowSummary,
    entry: OrientedFlowSummary,
) -> dict[str, bool]:
    """Return predeclared scale-free state-transfer diagnostics.

    These are not fitted thresholds. A sign flip asks only whether net
    aggression changed sides. Persistence asks whether a strict majority of
    the five confirmation minutes and its terminal minute kept that side.
    Entry confirmation asks whether the already-completed executable minute
    still agreed before the market order was submitted.
    """
    sign_flip = (
        source.oriented_imbalance > 0.0
        and confirmation.oriented_imbalance > 0.0
    )
    persistent = (
        sign_flip
        and confirmation.oriented_minute_fraction > 0.5
        and confirmation.terminal_oriented_imbalance > 0.0
    )
    return {
        "source_attack_flow_present": source.oriented_imbalance > 0.0,
        "confirmation_flow_sign_flip": sign_flip,
        "persistent_confirmation_transfer": persistent,
        "entry_minute_confirms_transfer": (
            persistent and entry.oriented_imbalance > 0.0
        ),
    }


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _first_event(
    events: Iterable[Mapping[str, Any]],
    scenario_id: str,
    reasons: set[str],
) -> Mapping[str, Any]:
    for event in events:
        if event.get("scenario_id") == scenario_id and event.get("reason_code") in reasons:
            return event
    raise RuntimeError(
        f"scenario {scenario_id} missing one of causal events: {sorted(reasons)}"
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "item"):
        return _jsonable(value.item())
    return str(value)


def _feature_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    numeric = (
        "source_attack_imbalance",
        "confirmation_reversal_imbalance",
        "confirmation_reversal_minute_fraction",
        "confirmation_terminal_reversal_imbalance",
        "entry_reversal_imbalance",
        "confirmation_to_source_directional_volume",
        "confirmation_residual_attack_ratio",
        "confirmation_reversal_price_move_atr",
        "confirmation_reversal_path_efficiency",
    )
    output: dict[str, Any] = {}
    for name in numeric:
        winners = [float(row[name]) for row in rows if bool(row["win"])]
        losses = [float(row[name]) for row in rows if not bool(row["win"])]
        output[name] = {
            "winner_count": len(winners),
            "loss_count": len(losses),
            "winner_median": median(winners) if winners else None,
            "loss_median": median(losses) if losses else None,
            "median_difference_winner_minus_loss": (
                median(winners) - median(losses)
                if winners and losses
                else None
            ),
        }
    return output


def _rule_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    names = (
        "source_attack_flow_present",
        "confirmation_flow_sign_flip",
        "persistent_confirmation_transfer",
        "entry_minute_confirms_transfer",
    )
    output: dict[str, Any] = {}
    for name in names:
        selected = [row for row in rows if bool(row[name])]
        returns = [float(row["net_return_on_nav"]) for row in selected]
        output[name] = {
            "selected": len(selected),
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
            "note": (
                "diagnostic only; exact counterfactual NAV requires a fresh "
                "NautilusTrader replay with the rule frozen"
            ),
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
        manifest_destination=output / "flow_data_manifest.json",
    )
    flow = prepare_flow_frame(bundle.frame)
    events = _read_events(run_root / "events.jsonl")
    trades = pd.read_csv(run_root / "trades.csv")
    absorption = trades[trades["kind"] == "ABSORPTION_RECLAIM"].copy()

    rows: list[dict[str, Any]] = []
    for trade in absorption.itertuples(index=False):
        scenario_id = str(trade.scenario_id)
        contact = _first_event(
            events,
            scenario_id,
            {"UPPER_POOL_SWEEP_RECLAIM", "LOWER_POOL_SWEEP_RECLAIM"},
        )
        confirmation_event = _first_event(
            events,
            scenario_id,
            {"OPPOSITE_DISPLACEMENT_MSS"},
        )
        contact_ns = int(contact["event_time_ns"])
        confirmation_ns = int(confirmation_event["event_time_ns"])
        opened_ns = int(trade.opened_ns)
        if confirmation_ns - contact_ns != NS_PER_FIVE_MINUTES:
            raise RuntimeError(
                f"scenario {scenario_id} confirmation is not the next five-minute bar"
            )
        if opened_ns <= confirmation_ns:
            raise RuntimeError(f"scenario {scenario_id} opened before executable minute")

        reason = str(contact["reason_code"])
        attack_orientation = 1 if reason.startswith("UPPER_") else -1
        reversal_orientation = -attack_orientation
        atr = float((confirmation_event.get("details") or {})["atr"])
        source_window = exact_completed_window(
            flow,
            start_exclusive_ns=contact_ns - NS_PER_FIVE_MINUTES,
            end_inclusive_ns=contact_ns,
            expected_bars=5,
        )
        confirmation_window = exact_completed_window(
            flow,
            start_exclusive_ns=contact_ns,
            end_inclusive_ns=confirmation_ns,
            expected_bars=5,
        )
        entry_window = exact_completed_window(
            flow,
            start_exclusive_ns=confirmation_ns,
            end_inclusive_ns=opened_ns,
            expected_bars=None,
        )
        source_summary = summarize_oriented_flow(
            source_window,
            orientation=attack_orientation,
            atr=atr,
        )
        confirmation_summary = summarize_oriented_flow(
            confirmation_window,
            orientation=reversal_orientation,
            atr=atr,
        )
        entry_summary = summarize_oriented_flow(
            entry_window,
            orientation=reversal_orientation,
            atr=atr,
        )
        rules = transfer_rules(
            source=source_summary,
            confirmation=confirmation_summary,
            entry=entry_summary,
        )
        source_directional = max(source_summary.directional_volume, 1e-12)
        confirmation_directional = max(
            confirmation_summary.directional_volume,
            1e-12,
        )
        rows.append(
            {
                "stage": str(args.stage),
                "scenario_id": scenario_id,
                "direction": str(trade.direction),
                "contact_reason": reason,
                "contact_time_ns": contact_ns,
                "confirmation_time_ns": confirmation_ns,
                "opened_ns": opened_ns,
                "closed_ns": int(trade.closed_ns),
                "net_pnl": float(trade.net_pnl),
                "net_return_on_nav": float(trade.net_return_on_nav),
                "win": float(trade.net_pnl) > 0.0,
                "expected_rr": float(trade.expected_rr),
                "penetration_atr": float((contact.get("details") or {}).get("penetration_atr", 0.0)),
                "wick_fraction": float((contact.get("details") or {}).get("wick_fraction", 0.0)),
                "volume_z": float((contact.get("details") or {}).get("volume_z", 0.0)),
                "source_attack_imbalance": source_summary.oriented_imbalance,
                "source_attack_minute_fraction": source_summary.oriented_minute_fraction,
                "source_attack_price_move_atr": source_summary.oriented_price_move_atr,
                "source_attack_path_efficiency": source_summary.oriented_path_efficiency,
                "confirmation_reversal_imbalance": confirmation_summary.oriented_imbalance,
                "confirmation_reversal_minute_fraction": confirmation_summary.oriented_minute_fraction,
                "confirmation_terminal_reversal_imbalance": confirmation_summary.terminal_oriented_imbalance,
                "confirmation_reversal_price_move_atr": confirmation_summary.oriented_price_move_atr,
                "confirmation_reversal_path_efficiency": confirmation_summary.oriented_path_efficiency,
                "entry_reversal_imbalance": entry_summary.oriented_imbalance,
                "entry_terminal_reversal_imbalance": entry_summary.terminal_oriented_imbalance,
                "confirmation_to_source_directional_volume": (
                    confirmation_summary.directional_volume / source_directional
                ),
                "confirmation_residual_attack_ratio": (
                    confirmation_summary.counter_volume / confirmation_directional
                ),
                **rules,
                "source_flow": asdict(source_summary),
                "confirmation_flow": asdict(confirmation_summary),
                "entry_flow": asdict(entry_summary),
            }
        )

    pd.DataFrame(rows).to_csv(output / "initiative_flow_features.csv", index=False)
    summary = {
        "candidate": "candidate-07-initiative-flow-transition-diagnostic",
        "stage": str(args.stage),
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "source_run_root": str(run_root),
        "absorption_trades": len(rows),
        "wins": sum(bool(row["win"]) for row in rows),
        "losses": sum(not bool(row["win"]) for row in rows),
        "feature_separation": _feature_summary(rows),
        "predeclared_rules": _rule_summary(rows),
        "causal_contract": {
            "source_window": "five completed minutes ending at sweep/reclaim observation",
            "confirmation_window": "next five completed minutes ending at MSS observation",
            "entry_window": "completed minute(s) after MSS through actual market-order submission",
            "outcome_joined_after_feature_computation": True,
            "orders_or_pnl_created": False,
            "future_information_in_features": False,
        },
    }
    write_json_atomic(output / "initiative_flow_summary.json", _jsonable(summary))
    print(json.dumps(_jsonable(summary), indent=2, sort_keys=True))
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
