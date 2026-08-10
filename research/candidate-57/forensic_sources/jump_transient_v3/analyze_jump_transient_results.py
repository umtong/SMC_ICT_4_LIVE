"""Mechanism decomposition for Candidate 57 transient jump variants.

This report is intentionally non-binary. It measures how each management repair
changes the winner engine, the loss engine, opportunity reuse, account occupancy,
and the exact causal episodes relative to the impulse-stop control.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any

_PNL = re.compile(r"realized_pnl=([-+0-9_.,]+)\s+USDT")
_DURATION = re.compile(r"duration_ns=(\d+)")
_RETURN = re.compile(r"realized_return=([-+0-9.eE]+)")
_OPEN = re.compile(r"avg_px_open=([-+0-9.eE]+)")
_CLOSE = re.compile(r"avg_px_close=([-+0-9.eE]+)")


def number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else default
    match = re.search(
        r"[-+]?\d+(?:[,_]\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?",
        str(value),
    )
    if not match:
        return default
    try:
        result = float(match.group().replace("_", "").replace(",", ""))
    except ValueError:
        return default
    return result if math.isfinite(result) else default


def ratio(a: float, b: float) -> float:
    return a / b if b else 0.0


def quantile(values: list[float], q: float) -> float:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return 0.0
    if len(clean) == 1:
        return clean[0]
    x = q * (len(clean) - 1)
    lo, hi = math.floor(x), math.ceil(x)
    if lo == hi:
        return clean[lo]
    return clean[lo] * (hi - x) + clean[hi] * (x - lo)


@dataclass(slots=True)
class Trade:
    key: tuple[str, int]
    scenario_id: str
    symbol: str
    side: int
    pnl: float
    risk: float
    r: float
    duration_minutes: float
    realized_return: float
    entry: float
    close: float
    episode_ts: int
    exit_reason: str
    protection_active: bool
    protection_floor_r: float
    favorable_peak_r: float
    protection_escaped: bool
    escape_peak_r: float
    diagnostics: dict[str, Any]


def parse_trade(row: dict[str, Any]) -> Trade:
    event = str(row.get("event") or "")
    pnl = number(row.get("realized_pnl"), math.nan)
    if not math.isfinite(pnl):
        match = _PNL.search(event)
        pnl = number(match.group(1)) if match else 0.0
    risk = number(row.get("planned_account_loss"), 0.0)
    duration_match = _DURATION.search(event)
    duration = (
        number(duration_match.group(1)) / 60_000_000_000
        if duration_match
        else math.nan
    )
    return_match = _RETURN.search(event)
    realized_return = (
        number(return_match.group(1), math.nan) if return_match else math.nan
    )
    open_match, close_match = _OPEN.search(event), _CLOSE.search(event)
    entry = (
        number(open_match.group(1), number(row.get("entry_reference"), math.nan))
        if open_match
        else number(row.get("entry_reference"), math.nan)
    )
    close = number(close_match.group(1), math.nan) if close_match else math.nan
    episode_ts = int(row.get("episode_ts") or row.get("ts_event") or 0)
    symbol = str(row.get("symbol") or "UNKNOWN")
    floor = row.get("protection_floor_r")
    peak = row.get("favorable_net_r_peak")
    return Trade(
        key=(symbol, episode_ts),
        scenario_id=str(row.get("scenario_id") or ""),
        symbol=symbol,
        side=int(row.get("side") or 0),
        pnl=pnl,
        risk=risk,
        r=pnl / risk if risk > 0.0 else math.nan,
        duration_minutes=duration,
        realized_return=realized_return,
        entry=entry,
        close=close,
        episode_ts=episode_ts,
        exit_reason=str(row.get("management_exit_reason") or "BRACKET_OR_UNKNOWN"),
        protection_active=bool(row.get("protection_active")),
        protection_floor_r=number(floor, math.nan),
        favorable_peak_r=number(peak, math.nan),
        protection_escaped=bool(row.get("protection_escaped")),
        escape_peak_r=number(row.get("protection_escape_peak_r"), math.nan),
        diagnostics=dict(row.get("diagnostics") or {}),
    )


def grouped(trades: list[Trade], key_fn) -> list[dict[str, Any]]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        groups[str(key_fn(trade))].append(trade)
    rows = []
    for key, items in groups.items():
        wins = [item for item in items if item.pnl > 0]
        losses = [item for item in items if item.pnl < 0]
        rows.append(
            {
                "key": key,
                "trades": len(items),
                "wins": len(wins),
                "losses": len(losses),
                "gross_profit_r": sum(
                    item.r for item in wins if math.isfinite(item.r)
                ),
                "gross_loss_r": -sum(
                    item.r for item in losses if math.isfinite(item.r)
                ),
                "net_r": sum(
                    item.r for item in items if math.isfinite(item.r)
                ),
            }
        )
    rows.sort(key=lambda row: (-row["gross_loss_r"], -row["trades"], row["key"]))
    return rows


def summary(
    name: str,
    metrics: dict[str, Any],
    diagnostics: dict[str, Any],
    trades: list[Trade],
) -> dict[str, Any]:
    wins = [trade for trade in trades if trade.pnl > 0]
    losses = [trade for trade in trades if trade.pnl < 0]
    r_wins = [trade.r for trade in wins if math.isfinite(trade.r)]
    r_losses = [trade.r for trade in losses if math.isfinite(trade.r)]
    days = max(1, int(metrics.get("calendar_days") or 14))
    gross_profit_r = sum(r_wins)
    gross_loss_r = -sum(r_losses)
    management = [trade for trade in trades if trade.exit_reason.startswith("PROTECTION_")]
    protected_wins = [trade for trade in management if trade.pnl > 0]
    protected_losses = [trade for trade in management if trade.pnl < 0]
    peaks = [trade.favorable_peak_r for trade in trades if math.isfinite(trade.favorable_peak_r)]
    givebacks = [
        trade.favorable_peak_r - trade.r
        for trade in trades
        if math.isfinite(trade.favorable_peak_r) and math.isfinite(trade.r)
    ]
    trade_days = {
        datetime.fromtimestamp(trade.episode_ts / 1e9, tz=timezone.utc)
        .date()
        .isoformat()
        for trade in trades
        if trade.episode_ts
    }
    return {
        "variant": name,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": ratio(len(wins), len(trades)),
        "trade_density_per_day": ratio(len(trades), days),
        "trade_day_coverage": ratio(len(trade_days), days),
        "gross_profit_r": gross_profit_r,
        "gross_loss_r": gross_loss_r,
        "net_r": gross_profit_r - gross_loss_r,
        "gross_profit_r_per_day": ratio(gross_profit_r, days),
        "gross_loss_r_per_day": ratio(gross_loss_r, days),
        "net_r_per_day": ratio(gross_profit_r - gross_loss_r, days),
        "avg_winner_r": statistics.fmean(r_wins) if r_wins else 0.0,
        "avg_loser_r": statistics.fmean(r_losses) if r_losses else 0.0,
        "winner_median_duration_minutes": quantile(
            [trade.duration_minutes for trade in wins], 0.5
        ),
        "loser_median_duration_minutes": quantile(
            [trade.duration_minutes for trade in losses], 0.5
        ),
        "management_exit_count": len(management),
        "management_exit_wins": len(protected_wins),
        "management_exit_losses": len(protected_losses),
        "management_exit_net_r": sum(
            trade.r for trade in management if math.isfinite(trade.r)
        ),
        "protection_active_trade_count": sum(
            trade.protection_active for trade in trades
        ),
        "escaped_trade_count": sum(trade.protection_escaped for trade in trades),
        "median_favorable_peak_r": quantile(peaks, 0.5),
        "median_peak_to_exit_giveback_r": quantile(givebacks, 0.5),
        "geometric_daily_growth": number(metrics.get("geometric_daily_growth")),
        "total_return": number(metrics.get("total_return")),
        "max_drawdown": number(metrics.get("max_drawdown")),
        "ending_nav": number(metrics.get("ending_nav")),
        "source_candidates": int(diagnostics.get("jump_source_candidates") or 0),
        "entry_submissions": int(diagnostics.get("entry_submissions") or 0),
        "pending_created": int(diagnostics.get("jump_pending_created") or 0),
        "pending_expired": int(diagnostics.get("jump_pending_expired") or 0),
        "confirmed_entries": int(diagnostics.get("jump_confirmed_entries") or 0),
        "protection_activations": int(
            diagnostics.get("jump_protection_activations") or 0
        ),
        "protection_ratchets": int(
            diagnostics.get("jump_protection_ratchets") or 0
        ),
        "protection_exit_requests": int(
            diagnostics.get("jump_protection_exit_requests") or 0
        ),
        "escape_events": int(diagnostics.get("jump_protection_escape_events") or 0),
        "protection_disarms": int(diagnostics.get("jump_protection_disarms") or 0),
        "order_rejections": int(diagnostics.get("order_rejections") or 0),
        "global_position_violations": int(
            diagnostics.get("global_position_violations") or 0
        ),
        "exit_reason_structure": grouped(trades, lambda trade: trade.exit_reason),
        "symbol_structure": grouped(trades, lambda trade: trade.symbol),
        "side_structure": grouped(
            trades, lambda trade: "LONG" if trade.side > 0 else "SHORT"
        ),
    }


def compare(control: list[Trade], other: list[Trade]) -> dict[str, Any]:
    base = {trade.key: trade for trade in control}
    alt = {trade.key: trade for trade in other}
    matched = sorted(set(base) & set(alt))
    base_winners = {key for key, trade in base.items() if trade.r > 0}
    base_losses = {key for key, trade in base.items() if trade.r < 0}
    preserved_winners = [
        key for key in base_winners if key in alt and alt[key].r > 0
    ]
    damaged_winners = [
        key for key in base_winners if key in alt and alt[key].r <= 0
    ]
    repaired_losses = [
        key for key in base_losses if key in alt and alt[key].r >= 0
    ]
    reduced_losses = [
        key
        for key in base_losses
        if key in alt and alt[key].r < 0 and alt[key].r > base[key].r
    ]
    worsened_losses = [
        key
        for key in base_losses
        if key in alt and alt[key].r < base[key].r
    ]
    avoided = [key for key in base_losses if key not in alt]
    new_keys = sorted(set(alt) - set(base))
    base_winner_r = sum(base[key].r for key in base_winners)
    preserved_winner_r = sum(
        alt[key].r for key in preserved_winners if math.isfinite(alt[key].r)
    )
    details = []
    for key in matched:
        details.append(
            {
                "key": list(key),
                "control_r": base[key].r,
                "variant_r": alt[key].r,
                "delta_r": alt[key].r - base[key].r,
                "control_duration": base[key].duration_minutes,
                "variant_duration": alt[key].duration_minutes,
                "variant_exit_reason": alt[key].exit_reason,
                "variant_peak_r": alt[key].favorable_peak_r,
            }
        )
    details.sort(key=lambda row: row["delta_r"], reverse=True)
    return {
        "matched_episode_count": len(matched),
        "control_winner_count": len(base_winners),
        "winner_count_preserved": len(preserved_winners),
        "winner_sign_preservation": ratio(
            len(preserved_winners), len(base_winners)
        ),
        "winner_r_preservation": ratio(preserved_winner_r, base_winner_r),
        "winner_sign_damaged": len(damaged_winners),
        "control_loss_count": len(base_losses),
        "losses_repaired_to_nonloss": len(repaired_losses),
        "losses_reduced_but_negative": len(reduced_losses),
        "losses_worsened": len(worsened_losses),
        "loss_episodes_avoided_by_account_timing": len(avoided),
        "new_episode_count": len(new_keys),
        "new_episode_net_r": sum(
            alt[key].r for key in new_keys if math.isfinite(alt[key].r)
        ),
        "matched_delta_r": sum(
            alt[key].r - base[key].r
            for key in matched
            if math.isfinite(base[key].r) and math.isfinite(alt[key].r)
        ),
        "episode_details": details,
    }


def mechanism_read(
    row: dict[str, Any], comparison: dict[str, Any] | None
) -> list[str]:
    notes: list[str] = []
    if row["gross_profit_r_per_day"] >= 0.30:
        notes.append(
            "The gross winner engine is large enough to be strategically important at the project 3% risk budget; management must not be judged only by net PnL."
        )
    if row["trade_density_per_day"] < 1.0:
        notes.append(
            "This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless."
        )
    if row["management_exit_count"]:
        notes.append(
            f"Protection changed {row['management_exit_count']} realized exits; its effect is a management mechanism rather than an entry filter."
        )
    if comparison:
        if comparison["winner_sign_preservation"] >= 2 / 3:
            notes.append(
                "Most control winners keep the same sign, so the repair largely preserves the observed alpha engine."
            )
        if comparison["winner_r_preservation"] < 0.70:
            notes.append(
                "The repair materially truncates winner R even when winner signs survive; apparent drawdown improvement may be purchased by damaging the payoff engine."
            )
        if (
            comparison["losses_repaired_to_nonloss"]
            + comparison["losses_reduced_but_negative"]
        ) >= 2:
            notes.append(
                "Multiple exact control loss episodes improve, evidence that the identified giveback mechanism is causally repairable rather than a single lucky trade."
            )
        if comparison["new_episode_count"]:
            notes.append(
                "Earlier exits free the single account slot and change the opportunity set; final integration must use continuous-account reruns, not arithmetic trade substitution."
            )
    if row["order_rejections"] or row["global_position_violations"]:
        notes.append(
            "Execution/account contamination remains and logic conclusions are provisional."
        )
    return notes


def load(root: Path):
    summaries: dict[str, dict[str, Any]] = {}
    trades_by_name: dict[str, list[Trade]] = {}
    for metrics_path in sorted(root.rglob("metrics.json")):
        folder = metrics_path.parent
        scenarios = folder / "closed_scenarios.json"
        diagnostics = folder / "strategy_diagnostics.json"
        if not scenarios.is_file() or not diagnostics.is_file():
            continue
        tokens = [part for part in folder.parts if "jump-transient-v3-" in part]
        token = tokens[-1] if tokens else folder.name
        name = token.split("jump-transient-v3-", 1)[-1]
        metrics = json.loads(metrics_path.read_text())
        diag = json.loads(diagnostics.read_text())
        rows = json.loads(scenarios.read_text())
        trades = [parse_trade(row) for row in rows if isinstance(row, dict)]
        summaries[name] = summary(name, metrics, diag, trades)
        trades_by_name[name] = trades
    return summaries, trades_by_name


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate 57 — jump transient weak-reversion decomposition v3",
        "",
        "This is not a pass/fail tournament. The impulse-extreme stop and source entry are held fixed. Each variant changes only the causal arm/escape state transition that distinguishes weak reversion failure from a strong source-confirming escape.",
        "",
        "| variant | trades/day | GP R/day | GL R/day | net R/day | avg win R | avg loss R | protection exits | gmean/day | MDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["summaries"].items():
        lines.append(
            f"| `{name}` | {row['trade_density_per_day']:.3f} | {row['gross_profit_r_per_day']:.3f} | {row['gross_loss_r_per_day']:.3f} | {row['net_r_per_day']:.3f} | {row['avg_winner_r']:.3f} | {row['avg_loser_r']:.3f} | {row['management_exit_count']} | {row['geometric_daily_growth']:.3%} | {row['max_drawdown']:.1%} |"
        )
    lines += ["", "## Exact control-relative mechanism effects", ""]
    for name, comp in payload["comparisons"].items():
        lines.append(
            f"- `{name}`: winner sign preservation {comp['winner_sign_preservation']:.1%}; winner R preservation {comp['winner_r_preservation']:.1%}; losses repaired {comp['losses_repaired_to_nonloss']}; losses reduced {comp['losses_reduced_but_negative']}; losses worsened {comp['losses_worsened']}; matched ΔR {comp['matched_delta_r']:.3f}; new episodes {comp['new_episode_count']} ({comp['new_episode_net_r']:.3f}R)."
        )
    lines += ["", "## Mechanism reads", ""]
    for name, notes in payload["mechanism_reads"].items():
        lines.append(f"### `{name}`")
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    lines += [
        "## Research decision",
        "",
        payload["research_decision"],
        "",
        "The decision concerns this state transition only. It neither authorizes long evaluation nor ranks unrelated low- or high-frequency families.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summaries, trades = load(args.root)
    control_name = "impulse_control"
    if control_name not in summaries:
        raise SystemExit(f"control artifact missing; found {sorted(summaries)}")
    comparisons = {
        name: compare(trades[control_name], variant)
        for name, variant in trades.items()
        if name != control_name
    }
    reads = {
        name: mechanism_read(row, comparisons.get(name))
        for name, row in summaries.items()
    }

    # Research choice is based on subsystem evidence. A useful management
    # mechanism must preserve the rare large winners while improving more than
    # one loss/giveback episode. Net PnL supports but does not replace this read.
    vectors = []
    for name, comp in comparisons.items():
        row = summaries[name]
        vectors.append(
            {
                "variant": name,
                "winner_sign_preservation": comp["winner_sign_preservation"],
                "winner_r_preservation": comp["winner_r_preservation"],
                "losses_improved": comp["losses_repaired_to_nonloss"]
                + comp["losses_reduced_but_negative"],
                "losses_worsened": comp["losses_worsened"],
                "matched_delta_r": comp["matched_delta_r"],
                "net_r_per_day": row["net_r_per_day"],
                "max_drawdown": row["max_drawdown"],
            }
        )
    vectors.sort(
        key=lambda item: (
            -(item["winner_sign_preservation"] >= 2 / 3),
            -(item["winner_r_preservation"] >= 0.70),
            -item["losses_improved"],
            item["losses_worsened"],
            -item["matched_delta_r"],
        )
    )
    selected = vectors[0] if vectors else None
    decision = (
        "No management repair preserves enough of the payoff engine while improving repeated loss episodes; retain impulse control and move to delayed rejection/re-entry anatomy."
        if selected is None
        or selected["winner_sign_preservation"] < 2 / 3
        or selected["winner_r_preservation"] < 0.70
        or selected["losses_improved"] < 2
        else f"Freeze `{selected['variant']}` as the current management repair, then run it once on a fresh untouched short interval before any broader expansion."
    )
    payload = {
        "purpose": "Deeply inspect management repair leverage without reducing strategy value to a binary gate.",
        "evaluation_interval": ["2025-11-01", "2025-11-14"],
        "development_data_after_observation": True,
        "control": control_name,
        "summaries": dict(sorted(summaries.items())),
        "comparisons": dict(sorted(comparisons.items())),
        "mechanism_reads": dict(sorted(reads.items())),
        "mechanism_vectors": vectors,
        "selected_vector": selected,
        "research_decision": decision,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "DECOMPOSITION.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    (args.out / "DECOMPOSITION.md").write_text(render(payload))
    print(json.dumps({"variants": sorted(summaries), "decision": decision}, indent=2))


if __name__ == "__main__":
    main()
