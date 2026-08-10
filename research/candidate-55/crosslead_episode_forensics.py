"""Result-blind causal-episode audit of the Candidate-51 cross-lead family.

The original family produced zero trades because a long chain of statistical and
geometry gates collapsed all opportunity.  This script does not relax those
gates or simulate a new strategy.  It inspects every independent BTC/ETH leader
shock and the exact reason each SOL/XRP opportunity was rejected, then opens the
future path only for forensic attribution.

Stage A (decision record): only completed 5-minute candles available at the
leader-shock close are used to fit target[t+1] ~ leader[t].  Stage B (outcome
record): the later 5/10/15-minute target path is appended after the decision is
frozen.  The output therefore separates false negatives, correct no-trades,
unstable relationships and cost-insufficient responses without pretending that
the diagnostic itself is a trading backtest.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from kline_only_inputs import load_range
from router_crosslead import fit_relationship

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TARGETS = ("SOLUSDT", "XRPUSDT")


def _aggregate(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["close_time_dt"] = pd.to_datetime(data["close_time_dt"], utc=True)
    data = data.set_index("close_time_dt")
    result = data.resample("5min", label="right", closed="right").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    counts = data["close"].resample("5min", label="right", closed="right").count()
    result = result.loc[counts == 5].dropna().copy()
    return result


def _reason(
    *,
    beta: float,
    beta_first: float,
    beta_second: float,
    tstat: float,
    shock_z: float,
    predicted_bps: float,
    mode: str,
) -> str:
    if beta_first * beta_second <= 0.0:
        return "BETA_SIGN_UNSTABLE"
    if abs(beta) < 0.10:
        return "BETA_TOO_SMALL"
    if abs(tstat) < 2.0:
        return "TSTAT_TOO_SMALL"
    if mode == "follow" and not (beta > 0 and beta_first > 0 and beta_second > 0 and tstat > 0):
        return "NOT_STABLE_FOLLOW"
    if mode == "seesaw" and not (beta < 0 and beta_first < 0 and beta_second < 0 and tstat < 0):
        return "NOT_STABLE_SEESAW"
    if abs(shock_z) < 2.0:
        return "LEADER_SHOCK_TOO_SMALL"
    if abs(predicted_bps) < 30.0:
        return "PREDICTED_MOVE_BELOW_COST_FLOOR"
    return "RELATIONSHIP_AND_SHOCK_GATE_PASSED"


def _episode_starts(leader_returns: pd.Series, shock_z: pd.Series) -> set[int]:
    """Collapse overlapping same-direction shocks into one three-bar episode."""
    starts: set[int] = set()
    active_until = -1
    active_side = 0
    for index, (ret, zvalue) in enumerate(zip(leader_returns, shock_z)):
        side = 1 if ret > 0 else -1 if ret < 0 else 0
        if not math.isfinite(float(zvalue)) or abs(float(zvalue)) < 1.0 or side == 0:
            if index > active_until:
                active_side = 0
            continue
        if index <= active_until and side == active_side:
            continue
        starts.add(index)
        active_side = side
        active_until = index + 3
    return starts


def _summary(events: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {
        "rows": int(len(events)),
        "independent_leader_episodes": int(events["episode_id"].nunique()),
        "targets": {},
        "by_gate_reason": {},
        "by_relationship_state": {},
        "false_negative_candidates": {},
    }
    for target, group in events.groupby("target"):
        output["targets"][target] = {
            "episodes": int(group["episode_id"].nunique()),
            "next_5m_mean_directional_net_bps": float(group["next_5m_directional_net_bps"].mean()),
            "next_5m_positive_fraction": float((group["next_5m_directional_net_bps"] > 0).mean()),
            "next_15m_mean_directional_net_bps": float(group["next_15m_directional_net_bps"].mean()),
        }
    for reason, group in events.groupby("source_gate_reason"):
        output["by_gate_reason"][reason] = {
            "rows": int(len(group)),
            "episodes": int(group["episode_id"].nunique()),
            "next_5m_mean_directional_net_bps": float(group["next_5m_directional_net_bps"].mean()),
            "next_5m_positive_fraction": float((group["next_5m_directional_net_bps"] > 0).mean()),
            "next_15m_mean_directional_net_bps": float(group["next_15m_directional_net_bps"].mean()),
        }
    for state, group in events.groupby("relationship_state"):
        output["by_relationship_state"][state] = {
            "rows": int(len(group)),
            "episodes": int(group["episode_id"].nunique()),
            "next_5m_mean_directional_net_bps": float(group["next_5m_directional_net_bps"].mean()),
            "next_5m_positive_fraction": float((group["next_5m_directional_net_bps"] > 0).mean()),
            "next_15m_mean_directional_net_bps": float(group["next_15m_directional_net_bps"].mean()),
        }
    missed = events[
        (events["source_gate_reason"] != "RELATIONSHIP_AND_SHOCK_GATE_PASSED")
        & (events["next_5m_directional_net_bps"] >= 30.0)
    ]
    for reason, group in missed.groupby("source_gate_reason"):
        output["false_negative_candidates"][reason] = {
            "rows": int(len(group)),
            "episodes": int(group["episode_id"].nunique()),
            "mean_next_5m_directional_net_bps": float(group["next_5m_directional_net_bps"].mean()),
            "median_next_5m_directional_net_bps": float(group["next_5m_directional_net_bps"].median()),
        }
    return output


def run(
    *,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    one_minute: dict[str, pd.DataFrame] = {}
    manifest: dict[str, Any] = {}
    for symbol in SYMBOLS:
        frame, _, files, evidence = load_range(
            symbol=symbol,
            start=start,
            end=end,
            cache=cache,
            output=output / "source" / symbol,
        )
        one_minute[symbol] = frame
        manifest[symbol] = {
            "rows": int(len(frame)),
            "files": [str(path) for path in files],
            "evidence": [asdict(item) for item in evidence],
        }

    candles = {symbol: _aggregate(frame) for symbol, frame in one_minute.items()}
    common = sorted(set.intersection(*(set(frame.index) for frame in candles.values())))
    aligned = {symbol: candles[symbol].loc[common].copy() for symbol in SYMBOLS}
    log_returns = {
        symbol: np.log(frame["close"] / frame["close"].shift(1))
        for symbol, frame in aligned.items()
    }
    leader = 0.5 * (log_returns["BTCUSDT"] + log_returns["ETHUSDT"])

    rolling_std = leader.shift(1).rolling(288, min_periods=144).std(ddof=0)
    shock_z_series = leader / rolling_std
    starts = _episode_starts(leader.fillna(0.0), shock_z_series)

    records: list[dict[str, Any]] = []
    episode_counter = 0
    for index in sorted(starts):
        if index < 146 or index + 3 >= len(common):
            continue
        timestamp = common[index]
        episode_counter += 1
        x_history = leader.iloc[: index + 1].tolist()
        for target in TARGETS:
            y_history = log_returns[target].iloc[: index + 1].tolist()
            relationship = fit_relationship(x_history, y_history, 288, 144)
            if relationship is None:
                continue
            current_leader = float(leader.iloc[index])
            shock_z = current_leader / relationship.x_std
            predicted = relationship.alpha + relationship.beta * current_leader
            predicted_bps = predicted * 10_000.0
            mode = "follow" if relationship.beta > 0 else "seesaw"
            source_reason = _reason(
                beta=relationship.beta,
                beta_first=relationship.beta_first,
                beta_second=relationship.beta_second,
                tstat=relationship.tstat,
                shock_z=shock_z,
                predicted_bps=predicted_bps,
                mode=mode,
            )
            side = 1 if predicted > 0 else -1 if predicted < 0 else 0
            future = aligned[target].iloc[index + 1 : index + 4]
            entry = float(future.iloc[0]["open"])
            close_5 = float(future.iloc[0]["close"])
            close_10 = float(future.iloc[1]["close"])
            close_15 = float(future.iloc[2]["close"])
            net_cost_bps = 20.0
            directional = lambda price: side * math.log(price / entry) * 10_000.0 - net_cost_bps
            favourable_15 = (
                float(future["high"].max()) / entry - 1.0
                if side > 0
                else 1.0 - float(future["low"].min()) / entry
            ) * 10_000.0 - net_cost_bps
            adverse_15 = (
                1.0 - float(future["low"].min()) / entry
                if side > 0
                else float(future["high"].max()) / entry - 1.0
            ) * 10_000.0 + net_cost_bps
            stable = relationship.beta_first * relationship.beta_second > 0.0
            state = (
                "STABLE_FOLLOW" if stable and relationship.beta > 0.0
                else "STABLE_SEESAW" if stable and relationship.beta < 0.0
                else "SIGN_UNSTABLE"
            )
            records.append(
                {
                    "episode_id": episode_counter,
                    "timestamp": timestamp.isoformat(),
                    "target": target,
                    "leader_side": 1 if current_leader > 0 else -1,
                    "leader_return_bps": current_leader * 10_000.0,
                    "leader_shock_z": shock_z,
                    "relationship_state": state,
                    "samples": relationship.samples,
                    "beta": relationship.beta,
                    "beta_first": relationship.beta_first,
                    "beta_second": relationship.beta_second,
                    "correlation": relationship.correlation,
                    "tstat": relationship.tstat,
                    "predicted_bps": predicted_bps,
                    "predicted_side": side,
                    "source_gate_reason": source_reason,
                    "entry_next_5m_open": entry,
                    "next_5m_directional_net_bps": directional(close_5),
                    "next_10m_directional_net_bps": directional(close_10),
                    "next_15m_directional_net_bps": directional(close_15),
                    "next_15m_favourable_net_bps": favourable_15,
                    "next_15m_adverse_with_cost_bps": adverse_15,
                }
            )

    events = pd.DataFrame.from_records(records)
    if events.empty:
        raise RuntimeError("no independent leader episodes were recorded")
    events.to_csv(output / "crosslead_episode_decisions_and_paths.csv", index=False)
    summary = {
        "candidate": "candidate-55",
        "purpose": "FORENSIC_FALSE_NEGATIVE_AND_CAUSAL_PREMISE_AUDIT",
        "interval": [start.isoformat(), end.isoformat()],
        "decision_then_outcome_separation": True,
        "execution_backtest": False,
        "source_gate_parameters_changed": False,
        "cost_floor_bps_round_trip": 20.0,
        "episode_definition": "one leader shock per direction per three completed 5m bars",
        "summary": _summary(events),
        "data_manifest": manifest,
        "next_decision": (
            "Only a relationship state with repeated positive net response across "
            "independent episodes may be frozen into a Nautilus strategy. Otherwise "
            "the cross-lead family is abandoned without gate tuning."
        ),
    }
    (output / "SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2025-04-30")
    parser.add_argument("--cache", type=Path, default=Path(".cache/candidate-55-crosslead-forensics"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-55/crosslead-episode-forensics"))
    args = parser.parse_args()
    result = run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
