#!/usr/bin/env python3
"""Causal semantic first-return policy with one global pending/position slot.

The order is created only from information available at the completed departure bar.
A pending limit can be canceled when the original opportunity is invalidated, the
predeclared target is consumed before entry, or the first-return lifetime ends.
After fill, take-profit and stop-loss are the only exits.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TICKS = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.001, "XRPUSDT": 0.0001}
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
STOP_SLIPPAGE_TICKS = 2
RISK_FRACTION = 0.03
EPS = 1e-12
RR_VARIANTS = (1.0, 1.25, 1.5, 1.75, 2.0)


@dataclass(frozen=True, slots=True)
class Result:
    outcome: str
    net_r: float | None
    fill_time: pd.Timestamp | None
    exit_time: pd.Timestamp
    pending_minutes: float | None
    hold_minutes: float | None
    target_net_r: float
    mfe_r: float | None
    mae_r: float | None


def _period(directory: Path) -> str:
    name = directory.name
    for token in ("fresh-", "dev-", "eval-", "train-", "cal-", "holdout-"):
        position = name.find(token)
        if position >= 0:
            return name[position:]
    return name[3:] if name.startswith("v3-") else name


def load_artifacts(root: Path):
    actions, raw, metadata = [], {}, {}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        action_path = directory / "coherent_actions.csv"
        raw_path = directory / "chart" / "raw_universe_1m.csv.gz"
        metadata_path = directory / "chart" / "metadata.json"
        if not action_path.exists() or not raw_path.exists():
            matches = list(directory.glob("**/coherent_actions.csv"))
            action_path = matches[0] if matches else action_path
            matches = list(directory.glob("**/raw_universe_1m.csv.gz"))
            raw_path = matches[0] if matches else raw_path
            matches = list(directory.glob("**/metadata.json"))
            metadata_path = matches[0] if matches else metadata_path
        if not action_path.exists() or not raw_path.exists():
            continue
        period = _period(directory)
        frame = pd.read_csv(action_path, low_memory=False)
        frame["period"] = period
        actions.append(frame)
        chart = pd.read_csv(raw_path, parse_dates=["open_time_dt"], low_memory=False)
        chart["open_time_dt"] = pd.to_datetime(chart.open_time_dt, utc=True)
        raw[period] = {
            symbol: group.sort_values("open_time_dt").set_index("open_time_dt").copy()
            for symbol, group in chart.groupby("symbol")
        }
        metadata[period] = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    if not actions:
        raise RuntimeError(f"no semantic action artifacts under {root}")
    return pd.concat(actions, ignore_index=True, sort=False), raw, metadata


def common_states(raw):
    output = {}
    for period, symbols in raw.items():
        index = None
        for frame in symbols.values():
            index = frame.index if index is None else index.intersection(frame.index)
        returns = pd.DataFrame(index=index)
        for symbol, frame in symbols.items():
            returns[symbol] = np.log(frame.reindex(index).close.clip(lower=EPS)).diff()
        output[period] = pd.DataFrame(
            {
                "factor_return": returns.mean(axis=1),
                "breadth": np.sign(returns).mean(axis=1),
                "dispersion": returns.std(axis=1),
            },
            index=index,
        )
    return output


def prior_atr(frame: pd.DataFrame, index: int, window: int = 120) -> float:
    start = max(1, index - window)
    part = frame.iloc[start:index + 1]
    previous = frame.close.shift(1).iloc[start:index + 1]
    true_range = pd.concat(
        [part.high - part.low, (part.high - previous).abs(), (part.low - previous).abs()],
        axis=1,
    ).max(axis=1)
    return float(true_range.median())


def departure_features(frame, index, side, source_lower, source_upper, common):
    sign = 1.0 if side == "LONG" else -1.0
    atr = prior_atr(frame, index)
    close = float(frame.close.iloc[index])
    output = {"atr_price": atr}
    for window in (3, 5, 12, 15, 30, 60, 120, 240, 480):
        start = max(0, index - window + 1)
        part = frame.iloc[start:index + 1]
        if len(part) < 2:
            continue
        net = sign * (close - float(part.close.iloc[0])) / max(atr, EPS)
        travel = float(part.close.diff().abs().sum()) / max(atr, EPS)
        quote = float(part.quote_volume.sum())
        flow = sign * float(part.signed_quote_flow.sum()) / max(quote, EPS)
        baseline = frame.quote_volume.iloc[max(0, start - window):start].median() if start else part.quote_volume.median()
        output[f"dep_ret_{window}_atr"] = net
        output[f"dep_eff_{window}"] = net / max(travel, EPS)
        output[f"dep_flow_{window}"] = flow
        output[f"dep_activity_{window}"] = float(part.quote_volume.mean() / max(float(baseline), EPS))
    for window in (30, 60, 120, 240, 480, 1440):
        history = frame.iloc[max(0, index - window):index]
        if len(history) < 12:
            continue
        high, low = float(history.high.max()), float(history.low.min())
        output[f"directional_room_{window}_atr"] = (
            high - close if side == "LONG" else close - low
        ) / max(atr, EPS)
        output[f"loc_{window}"] = (close - low) / max(high - low, EPS)
    output["source_distance_atr"] = sign * (
        close - (source_upper if side == "LONG" else source_lower)
    ) / max(atr, EPS)
    for window in (5, 15, 30, 60):
        chart_index = frame.index[max(0, index - window + 1):index + 1]
        market = common.reindex(chart_index)
        factor = float(market.factor_return.fillna(0.0).sum())
        breadth = float(market.breadth.fillna(0.0).mean())
        dispersion = float(market.dispersion.fillna(0.0).mean())
        symbol_return = math.log(max(close, EPS) / max(float(frame.close.iloc[max(0, index - window + 1)]), EPS))
        output[f"common_ret_{window}_signed"] = sign * factor
        output[f"common_breadth_{window}_signed"] = sign * breadth
        output[f"common_dispersion_{window}"] = dispersion
        output[f"residual_ret_{window}_signed"] = sign * (symbol_return - factor)
    return output


def causal_volume_nodes(frame, index, entry, side, tick):
    sign = 1.0 if side == "LONG" else -1.0
    history = frame.iloc[max(0, index - 1440):index + 1]
    if len(history) < 180:
        return []
    typical = ((history.high + history.low + history.close) / 3.0).to_numpy(float)
    weight = history.quote_volume.to_numpy(float)
    valid = np.isfinite(typical) & np.isfinite(weight) & (weight > 0.0)
    typical, weight = typical[valid], weight[valid]
    if len(typical) < 180:
        return []
    lower, upper = np.quantile(typical, [0.01, 0.99])
    if upper <= lower + tick:
        return []
    edges = np.linspace(lower, upper, 65)
    volume, _ = np.histogram(typical, bins=edges, weights=weight)
    positive = volume[volume > 0.0]
    if len(positive) < 8:
        return []
    threshold = float(np.quantile(positive, 0.75))
    output = []
    for bin_index, value in enumerate(volume):
        left = volume[bin_index - 1] if bin_index else -np.inf
        right = volume[bin_index + 1] if bin_index + 1 < len(volume) else -np.inf
        if value < threshold or value < left or value < right:
            continue
        price = float(edges[bin_index]) if side == "LONG" else float(edges[bin_index + 1])
        price -= sign * tick
        distance = sign * (price - entry)
        if distance > 0.0:
            output.append((distance, price, "CAUSAL_24H_VOLUME_NODE"))
    return output


def causal_routes(frame, index, entry, side, tick):
    sign = 1.0 if side == "LONG" else -1.0
    output = []
    for window in (30, 60, 120, 240, 480, 1440):
        history = frame.iloc[max(0, index - window):index]
        if len(history) < max(12, window // 4):
            continue
        price = float(history.high.max()) if side == "LONG" else float(history.low.min())
        price -= sign * tick
        distance = sign * (price - entry)
        if distance > 0.0:
            output.append((distance, price, f"PRIOR_{'HIGH' if side == 'LONG' else 'LOW'}_{window}"))
    day = frame.index[index].normalize()
    previous_day = frame[(frame.index >= day - pd.Timedelta(days=1)) & (frame.index < day)]
    if len(previous_day) >= 120:
        price = float(previous_day.high.max()) if side == "LONG" else float(previous_day.low.min())
        price -= sign * tick
        distance = sign * (price - entry)
        if distance > 0.0:
            output.append((distance, price, "PDH" if side == "LONG" else "PDL"))
    output.extend(causal_volume_nodes(frame, index, entry, side, tick))
    output.sort(key=lambda item: (item[0], item[2]))
    deduped = []
    for item in output:
        if any(abs(item[1] - prior[1]) <= 3.0 * tick for prior in deduped):
            continue
        deduped.append(item)
    return deduped


def economics(entry, stop, target, side, tick):
    sign = 1.0 if side == "LONG" else -1.0
    stop_fill = stop - sign * STOP_SLIPPAGE_TICKS * tick
    risk = abs(entry - stop_fill)
    raw_stop = sign * (stop_fill - entry) / risk - (
        MAKER_FEE * abs(entry) + TAKER_FEE * abs(stop_fill)
    ) / risk
    normalization = max(abs(raw_stop), EPS)
    raw_target = sign * (target - entry) / risk - (
        MAKER_FEE * abs(entry) + MAKER_FEE * abs(target)
    ) / risk
    return raw_target / normalization, stop_fill, normalization


def label_limit(frame, order_index, terminal_index, side, entry, stop, target, tick):
    target_r, stop_fill, normalization = economics(entry, stop, target, side, tick)
    fill_index = None
    for position in range(order_index + 1, min(terminal_index, len(frame) - 1) + 1):
        row = frame.iloc[position]
        filled = float(row.low) <= entry if side == "LONG" else float(row.high) >= entry
        stop_hit = float(row.low) <= stop if side == "LONG" else float(row.high) >= stop
        target_hit = float(row.high) >= target if side == "LONG" else float(row.low) <= target
        if filled:
            fill_index = position
            if stop_hit:
                return Result("STOP_FIRST", -1.0, frame.index[position], frame.index[position] + pd.Timedelta(minutes=1), position - order_index, 1, target_r, 0.0, -1.0)
            if target_hit:
                return Result("TARGET_FIRST", target_r, frame.index[position], frame.index[position] + pd.Timedelta(minutes=1), position - order_index, 1, target_r, target_r, 0.0)
            break
        if stop_hit:
            return Result("CANCELED_PRE_FILL_INVALIDATED", None, None, frame.index[position] + pd.Timedelta(minutes=1), None, None, target_r, None, None)
        if target_hit:
            return Result("CANCELED_PRE_FILL_TARGET_SPENT", None, None, frame.index[position] + pd.Timedelta(minutes=1), None, None, target_r, None, None)
    if fill_index is None:
        end = min(terminal_index, len(frame) - 1)
        return Result("EXPIRED_UNFILLED", None, None, frame.index[end] + pd.Timedelta(minutes=1), None, None, target_r, None, None)
    cash_risk = abs(entry - stop_fill)
    best, worst = 0.0, 0.0
    for position in range(fill_index, len(frame)):
        row = frame.iloc[position]
        if side == "LONG":
            stop_hit, target_hit = float(row.low) <= stop, float(row.high) >= target
            favorable = (float(row.high) - entry) / cash_risk / normalization
            adverse = (float(row.low) - entry) / cash_risk / normalization
        else:
            stop_hit, target_hit = float(row.high) >= stop, float(row.low) <= target
            favorable = (entry - float(row.low)) / cash_risk / normalization
            adverse = (entry - float(row.high)) / cash_risk / normalization
        best, worst = max(best, favorable), min(worst, adverse)
        if stop_hit:
            return Result("STOP_FIRST", -1.0, frame.index[fill_index], frame.index[position] + pd.Timedelta(minutes=1), fill_index - order_index, position - fill_index + 1, target_r, best, worst)
        if target_hit:
            return Result("TARGET_FIRST", target_r, frame.index[fill_index], frame.index[position] + pd.Timedelta(minutes=1), fill_index - order_index, position - fill_index + 1, target_r, best, worst)
    return Result("CENSORED_OPEN", None, frame.index[fill_index], frame.index[-1] + pd.Timedelta(minutes=1), fill_index - order_index, len(frame) - fill_index, target_r, best, worst)


SAFE_COLUMNS = (
    "source_strength_ratio", "source_defense_count", "source_age_minutes",
    "source_accumulation_minutes_near", "source_accumulation_quote_share",
    "source_accumulation_delta_toward", "source_accumulation_distinct_visits",
    "approach_signed_net_bps", "approach_path_efficiency",
    "approach_delta_share_60m_toward", "approach_delta_share_12m_toward",
    "approach_activity_ratio_12m", "approach_range_ratio_12m",
    "approach_impact_per_activity_12m", "approach_touch_pressure",
    "event_body_bps_signed", "event_delta_share_signed", "event_activity_ratio",
    "event_range_ratio", "event_body_ratio", "event_trade_size_ratio",
    "event_impact_per_activity", "event_close_location_signed",
    "confirmation_body_bps_signed", "confirmation_delta_share_signed",
    "confirmation_activity_ratio", "confirmation_range_ratio",
    "confirmation_body_ratio", "confirmation_trade_size_ratio",
    "confirmation_impact_per_activity", "confirmation_close_location_signed",
    "event_to_confirmation_minutes", "departure_minutes", "zone_width_bps",
    "event_penetration_bps", "directional_gap_body_ratio",
    "directional_gap_range_ratio", "directional_gap_activity_ratio",
    "directional_gap_delta_signed", "order_block_present",
)


def harvest(actions, raw, metadata):
    common = common_states(raw)
    representatives = (
        actions.sort_values(["period", "state_id", "entry_geometry", "stop_geometry"])
        .groupby(["period", "state_id"], sort=False).first().reset_index()
    )
    records = []
    for _, state in representatives.iterrows():
        period, symbol = str(state.period), str(state.symbol)
        side, branch = str(state.side), str(state.narrative_branch)
        if period not in raw or symbol not in raw[period]:
            continue
        if branch not in {"FAILED_AUCTION_REVERSAL", "ACCEPTED_AUCTION_CONTINUATION"}:
            continue
        frame, tick = raw[period][symbol], TICKS[symbol]
        order_time = pd.to_datetime(int(state.diagnostic_departure_time_ns), utc=True)
        order_index = int(frame.index.searchsorted(order_time))
        if order_index < 120 or order_index >= len(frame) - 2:
            continue
        zone_lower, zone_upper = float(state.diagnostic_zone_lower), float(state.diagnostic_zone_upper)
        source_lower, source_upper = float(state.diagnostic_source_lower), float(state.diagnostic_source_upper)
        event_extreme = float(state.diagnostic_event_extreme)
        atr = prior_atr(frame, order_index)
        buffer = max(2.0 * tick, 0.05 * atr)
        if branch == "FAILED_AUCTION_REVERSAL":
            stop = event_extreme - buffer if side == "LONG" else event_extreme + buffer
            family = "FAILED_FIRST_RETURN"
        else:
            stop = min(source_lower, zone_lower) - buffer if side == "LONG" else max(source_upper, zone_upper) + buffer
            family = "ACCEPTED_FIRST_RETURN"
        decision = float(frame.close.iloc[order_index])
        if not (stop < decision if side == "LONG" else stop > decision):
            continue
        expiry = max(10, min(120, int(round(float(state.source_scale_minutes) * 2.0))))
        response_time = pd.to_datetime(int(state.diagnostic_response_time_ns), utc=True)
        terminal_time = min(order_time + pd.Timedelta(minutes=expiry), response_time)
        terminal_index = min(len(frame) - 1, max(order_index + 1, int(frame.index.searchsorted(terminal_time, side="right") - 1)))
        base = {
            "period": period, "state_id": state.state_id, "episode_id": state.episode_id,
            "symbol": symbol, "side": side, "family": family,
            "order_time": order_time, "terminal_time": terminal_time,
            "source_kind": state.source_kind, "source_pool_kind": state.source_pool_kind,
            "source_scale_minutes": float(state.source_scale_minutes),
            "setup_kind": state.setup_kind, "location_kind": state.location_kind,
        }
        for column in SAFE_COLUMNS:
            base[column] = float(state[column]) if column in state and pd.notna(state[column]) else np.nan
        base.update(departure_features(frame, order_index, side, source_lower, source_upper, common[period]))
        for price_kind, entry in (
            ("ZONE_PROXIMAL_LIMIT", zone_upper if side == "LONG" else zone_lower),
            ("ZONE_MID_LIMIT", 0.5 * (zone_lower + zone_upper)),
        ):
            entry = float(entry)
            if not (entry < decision - tick if side == "LONG" else entry > decision + tick):
                continue
            risk = abs(entry - stop)
            route_candidates = causal_routes(frame, order_index, entry, side, tick)
            if not route_candidates:
                continue
            obstacle = route_candidates[0]
            for gross_rr in RR_VARIANTS:
                target = entry + (1.0 if side == "LONG" else -1.0) * gross_rr * risk
                if (1.0 if side == "LONG" else -1.0) * (obstacle[1] - target) < -tick:
                    continue
                result = label_limit(frame, order_index, terminal_index, side, entry, stop, target, tick)
                records.append(
                    {
                        **base, "price_kind": price_kind, "gross_rr": gross_rr,
                        "entry": entry, "stop": stop, "target": target,
                        "risk_bps": risk / abs(entry) * 10_000.0,
                        "route_kind": obstacle[2], "route_price": obstacle[1],
                        "route_rr": obstacle[0] / max(risk, EPS),
                        "fill_state": "FILLED_LIMIT" if result.fill_time is not None else result.outcome,
                        "outcome": result.outcome, "net_r": result.net_r,
                        "target_net_r": result.target_net_r,
                        "entry_time": result.fill_time, "exit_time": result.exit_time,
                        "pending_minutes": result.pending_minutes,
                        "hold_minutes": result.hold_minutes,
                        "mfe_r": result.mfe_r, "mae_r": result.mae_r,
                    }
                )
    return pd.DataFrame(records)


def select_policy(plans):
    failed_proximal = plans[
        (plans.family == "FAILED_FIRST_RETURN")
        & (plans.price_kind == "ZONE_PROXIMAL_LIMIT")
        & np.isclose(plans.gross_rr, 1.25)
    ].copy()
    failed_proximal["strong_location"] = (
        failed_proximal.setup_kind.isin(["BPR", "MSS_FVG"])
        | failed_proximal.source_kind.astype(str).str.contains("CONFIRMED_EXTERNAL", na=False)
    )
    failed_proximal["efficient_ifvg"] = (
        failed_proximal.setup_kind.eq("IFVG")
        & (pd.to_numeric(failed_proximal.source_scale_minutes, errors="coerce") <= 60.0)
        & (pd.to_numeric(failed_proximal.event_activity_ratio, errors="coerce") <= 7.0)
        & (pd.to_numeric(failed_proximal.event_impact_per_activity, errors="coerce") >= 0.25)
    )
    failed_proximal = failed_proximal[
        (failed_proximal.strong_location | failed_proximal.efficient_ifvg)
        & (pd.to_numeric(failed_proximal.target_net_r, errors="coerce") >= 0.40)
    ].copy()
    failed_mid = plans[
        (plans.family == "FAILED_FIRST_RETURN")
        & (plans.price_kind == "ZONE_MID_LIMIT")
        & np.isclose(plans.gross_rr, 1.25)
    ].copy().set_index("state_id")
    selected_failed = []
    for _, proximal in failed_proximal.iterrows():
        choice = proximal
        if bool(proximal.strong_location) and proximal.state_id in failed_mid.index:
            middle = failed_mid.loc[proximal.state_id]
            if isinstance(middle, pd.DataFrame):
                middle = middle.iloc[0]
            deep_return_likely = (
                pd.to_numeric(middle.target_net_r, errors="coerce") >= 0.40
                and pd.to_numeric(middle.source_distance_atr, errors="coerce") <= 1.20
                and pd.to_numeric(middle.dep_eff_3, errors="coerce") <= 0.50
            )
            if deep_return_likely:
                choice = middle
        choice = choice.copy()
        choice["policy_family"] = "FAILED_AUCTION_FIRST_RETURN"
        selected_failed.append(choice)
    accepted = plans[
        (plans.family == "ACCEPTED_FIRST_RETURN")
        & (plans.price_kind == "ZONE_PROXIMAL_LIMIT")
        & np.isclose(plans.gross_rr, 2.0)
    ].copy()
    accepted = accepted[
        (pd.to_numeric(accepted.target_net_r, errors="coerce") >= 0.40)
        & (pd.to_numeric(accepted.common_ret_60_signed, errors="coerce") > 0.0)
        & (pd.to_numeric(accepted.dep_ret_3_atr, errors="coerce") >= -1.0)
    ].copy()
    accepted["policy_family"] = "ACCEPTED_AUCTION_FIRST_RETURN"
    output = pd.concat([pd.DataFrame(selected_failed), accepted], ignore_index=True, sort=False)
    output["filled"] = pd.to_datetime(output.entry_time, utc=True, errors="coerce").notna()
    output["quality_score"] = np.where(
        output.policy_family.eq("ACCEPTED_AUCTION_FIRST_RETURN"), 2.0, 1.5
    ) + pd.to_numeric(output.target_net_r, errors="coerce").fillna(0.0) * 0.30
    output["quality_score"] += np.where(
        output.setup_kind.isin(["BPR", "MSS_FVG"]), 0.40,
        np.where(output.source_kind.astype(str).str.contains("CONFIRMED_EXTERNAL", na=False), 0.25, 0.0),
    )
    return output


def route_account(candidates):
    frame = candidates.copy()
    for column in ("order_time", "terminal_time", "entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame = frame.sort_values(
        ["order_time", "quality_score", "target_net_r", "state_id"],
        ascending=[True, False, False, True],
    )
    selected, busy_until = [], pd.Timestamp.min.tz_localize("UTC")
    for timestamp, group in frame.groupby("order_time", sort=True):
        timestamp = pd.Timestamp(timestamp)
        if timestamp < busy_until:
            continue
        row = group.iloc[0]
        selected.append(row)
        busy_until = pd.Timestamp(row.exit_time if bool(row.filled) else row.terminal_time)
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else frame.iloc[:0].copy()
    trades = orders[
        orders.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST"])
        & pd.to_numeric(orders.net_r, errors="coerce").notna()
    ].copy().reset_index(drop=True)
    nav, peak, maximum_drawdown = 1.0, 1.0, 0.0
    nav_before, nav_after = [], []
    for result in pd.to_numeric(trades.net_r, errors="coerce"):
        nav_before.append(nav)
        nav *= max(EPS, 1.0 + RISK_FRACTION * float(result))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        nav_after.append(nav)
    trades["nav_before"], trades["nav_after"] = nav_before, nav_after
    summary = {
        "selected_orders": int(len(orders)),
        "closed_trades": int(len(trades)),
        "unfilled_or_open_orders": int(len(orders) - len(trades)),
        "target_first": int(trades.outcome.eq("TARGET_FIRST").sum()),
        "target_first_rate": float(trades.outcome.eq("TARGET_FIRST").mean()) if len(trades) else None,
        "mean_net_r": float(pd.to_numeric(trades.net_r).mean()) if len(trades) else None,
        "mean_planned_gross_rr": float(pd.to_numeric(trades.gross_rr).mean()) if len(trades) else None,
        "median_hold_minutes": float(pd.to_numeric(trades.hold_minutes).median()) if len(trades) else None,
        "mean_hold_minutes": float(pd.to_numeric(trades.hold_minutes).mean()) if len(trades) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
    }
    return orders, trades, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    actions, raw, metadata = load_artifacts(args.root)
    plans = harvest(actions, raw, metadata)
    candidates = select_policy(plans)
    orders, trades, account = route_account(candidates)
    calendar_days = sum(
        (pd.Timestamp(value["decision_end"]) - pd.Timestamp(value["decision_start"])).days
        for value in metadata.values()
        if value.get("decision_start") and value.get("decision_end")
    )
    account["calendar_days"] = int(calendar_days)
    account["closed_trades_per_day"] = float(len(trades) / calendar_days) if calendar_days else 0.0
    account["by_period"] = {
        str(period): {
            "trades": int(len(group)),
            "target_first_rate": float(group.outcome.eq("TARGET_FIRST").mean()),
            "mean_net_r": float(pd.to_numeric(group.net_r).mean()),
        }
        for period, group in trades.groupby("period")
    }
    summary = {
        "policy": "causal semantic first-return: failed auction plus trend-aligned accepted auction; one global pending order/position; TP or SL only",
        "plans": int(len(plans)),
        "candidate_actions": int(len(candidates)),
        "periods": sorted(metadata),
        "account": account,
    }
    plans.to_csv(args.output / "all_first_return_plans.csv.gz", index=False, compression="gzip")
    candidates.to_csv(args.output / "policy_candidates.csv.gz", index=False, compression="gzip")
    orders.to_csv(args.output / "account_orders.csv", index=False)
    trades.to_csv(args.output / "account_trades.csv", index=False)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
