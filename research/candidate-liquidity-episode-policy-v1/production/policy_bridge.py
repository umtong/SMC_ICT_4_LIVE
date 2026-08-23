from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .binance_public import kline_rows_to_frame, metric_rows_to_frame
from .contracts import EpisodePlan, SYMBOLS
from .event_store import EventStore
from .market_repository import MarketRepository


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    decision_time_ns: int
    plans: tuple[EpisodePlan, ...]
    counts: Mapping[str, Any]
    data_evidence: Mapping[str, Any]


_ACTIVATED = False


def activate_restored_policy_paths() -> None:
    global _ACTIVATED
    if _ACTIVATED:
        return
    candidate_dir = Path(__file__).resolve().parents[1]
    repo_root = candidate_dir.parents[1]
    dependency_dirs = (
        candidate_dir,
        repo_root / "research/candidate-liquidity-world-model-v1",
        repo_root / "research/candidate-liquidity-auction-v2",
        repo_root / "research/candidate-liquidity-auction-v7",
        repo_root / "research/candidate-liquidity-auction-v6",
        repo_root / "research/candidate-liquidity-auction-v5",
        repo_root / "research/candidate-coherent-auction-system-v4",
        repo_root / "research/candidate-coherent-auction-system-v3",
        repo_root / "research/candidate-coherent-liquidity-policy-v2",
        repo_root / "research/candidate-coherent-liquidity-policy-v1",
        repo_root / "research/candidate-hierarchical-liquidity-bpr-v2",
        repo_root / "research/candidate-hierarchical-liquidity-bpr-v1",
        repo_root / "research/candidate-liquidity-displacement-v1",
        repo_root / "research/candidate-auction-dislocation-confluence-v1",
        repo_root / "research/candidate-derivatives-dislocation-v1",
        repo_root / "research/candidate-auction-episode-policy",
        repo_root / "research/candidate-auction-event-v2",
        repo_root / "research/candidate-direct-auction-policy",
        repo_root / "research/candidate-easychart_re1",
        repo_root / "research/candidate-easychart-v5",
        repo_root / "research/candidate-easychart-v3",
    )
    for path in reversed([path for path in dependency_dirs if path.exists()]):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
    _ACTIVATED = True


def _prepare_reference(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    output = frame.copy()
    output.index = pd.DatetimeIndex(output.pop("open_time_dt")) + pd.Timedelta(minutes=1)
    output = output.sort_index()
    return output[["open", "high", "low", "close"]].rename(columns=lambda name: f"{prefix}_{name}")


def _live_metric_features(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    work = frame.copy().sort_index()
    output = pd.DataFrame(index=work.index)
    oi = pd.to_numeric(work.get("sum_open_interest"), errors="coerce")
    if oi is not None:
        output["metric_oi_log_change_1"] = np.log(oi.clip(lower=1e-12)).diff(1)
        output["metric_oi_log_change_3"] = np.log(oi.clip(lower=1e-12)).diff(3)
    mappings = {
        "taker_long_short_ratio": "metric_taker_change_1",
        "top_position_long_short_ratio": "metric_top_position_change_1",
        "all_account_long_short_ratio": "metric_all_account_change_1",
    }
    for source, target in mappings.items():
        if source in work:
            values = pd.to_numeric(work[source], errors="coerce").clip(lower=1e-12)
            output[target] = np.log(values).diff(1)
    return output.replace([np.inf, -np.inf], np.nan)


def prepare_live_market_state(
    futures: pd.DataFrame,
    index_price: pd.DataFrame,
    mark_price: pd.DataFrame,
    metrics: pd.DataFrame,
    tick_size: float,
) -> pd.DataFrame:
    activate_restored_policy_paths()
    from auction_episode_research import prepare_one_minute

    data = prepare_one_minute(futures, tick_size)
    data = data.join(_prepare_reference(index_price, "index"), how="inner")
    data = data.join(_prepare_reference(mark_price, "mark"), how="inner")
    live_metrics = _live_metric_features(metrics)
    if not live_metrics.empty:
        data = pd.merge_asof(
            data.sort_index(),
            live_metrics.sort_index(),
            left_index=True,
            right_index=True,
            direction="backward",
            allow_exact_matches=True,
        )
    log_futures = np.log(data["close"].astype(float).clip(lower=tick_size))
    log_index = np.log(data["index_close"].astype(float).clip(lower=tick_size))
    log_mark = np.log(data["mark_close"].astype(float).clip(lower=tick_size))
    data["basis_bps"] = (log_futures - log_index) * 10_000.0
    data["mark_basis_bps"] = (log_mark - log_index) * 10_000.0
    data["contract_mark_bps"] = (log_futures - log_mark) * 10_000.0
    for minutes in (1, 3, 5, 15, 30):
        data[f"futures_return_{minutes}m"] = log_futures.diff(minutes)
        data[f"index_return_{minutes}m"] = log_index.diff(minutes)
        data[f"mark_return_{minutes}m"] = log_mark.diff(minutes)
        data[f"basis_change_{minutes}m_bps"] = data["basis_bps"].diff(minutes)
        data[f"mark_basis_change_{minutes}m_bps"] = data["mark_basis_bps"].diff(minutes)
    absolute_shock = data["futures_return_3m"].abs()
    absolute_basis = data["basis_change_3m_bps"].abs()
    minimum = min(720, max(60, len(data) // 4))
    data["past_shock_q98"] = absolute_shock.shift(1).rolling(1440, min_periods=minimum).quantile(0.98)
    data["past_basis_q90"] = absolute_basis.shift(1).rolling(1440, min_periods=minimum).quantile(0.90)
    data["past_basis_median"] = data["basis_bps"].shift(1).rolling(1440, min_periods=minimum).median()
    data["past_basis_mad"] = (
        (data["basis_bps"].shift(1) - data["past_basis_median"]).abs()
        .rolling(1440, min_periods=minimum)
        .median()
    )
    return data.replace([np.inf, -np.inf], np.nan)


def _bool_mask(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(False, index=frame.index)
    values = frame[name]
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)
    return values.astype(str).str.lower().isin({"true", "1", "yes"})


def _number(row: pd.Series, *names: str, default: float | None = None) -> float | None:
    for name in names:
        if name in row.index:
            try:
                value = float(row.get(name))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                return value
    return default


def _text(row: pd.Series, *names: str, default: str = "") -> str:
    for name in names:
        value = row.get(name) if name in row.index else None
        if value is not None and str(value).strip() and str(value).lower() != "nan":
            return str(value)
    return default


def row_to_plan(row: pd.Series) -> EpisodePlan:
    entry = _number(row, "entry", "planned_entry")
    stop = _number(row, "stop", "planned_stop")
    target = _number(row, "target", "planned_target")
    gross_rr = _number(row, "gross_rr")
    target_net_r = _number(row, "planned_target_net_r", "target_net_r")
    order_time = _number(row, "order_time_ns")
    if None in {entry, stop, target, gross_rr, target_net_r, order_time}:
        raise ValueError("restored policy row is missing executable geometry")
    source_row = {
        key: value
        for key, value in row.to_dict().items()
        if key not in {
            "outcome", "fill_state", "fill_index", "fill_time_ns", "resolution_index",
            "resolution_time_ns", "net_r", "mfe_r", "mae_r", "actual_entry",
            "actual_target_net_r", "actual_stop_net_r", "actual_gross_rr",
        }
    }
    return EpisodePlan(
        episode_id=_text(row, "episode_id"),
        action_id=_text(row, "action_id", default=_text(row, "episode_id")),
        symbol=_text(row, "symbol"),
        side=_text(row, "side"),
        family=_text(row, "family", "event_type", default="UNKNOWN"),
        order_time_ns=int(order_time),
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        gross_rr=float(gross_rr),
        planned_target_net_r=float(target_net_r),
        entry_geometry=_text(row, "entry_geometry", default="UNKNOWN"),
        route_kind=_text(row, "route_kind", "destination_kind", default="UNKNOWN"),
        mechanism_coherence=float(_number(row, "mechanism_coherence", default=0.0) or 0.0),
        source_row=source_row,
    )


class RestoredPolicyBridge:
    """Build point-in-time state and invoke the restored policy without labels."""

    def __init__(self, store: EventStore, repository: MarketRepository) -> None:
        self.store = store
        self.repository = repository
        activate_restored_policy_paths()

    def evaluate(
        self,
        symbols: tuple[str, ...],
        *,
        end_time_ms: int,
        rolling_window_days: int,
        decision_age_minutes: int,
    ) -> EvaluationResult:
        from auction_episode_research import CONTRACTS
        from liquidity_displacement import _add_common_state
        from semantic_liquidity_v4 import build_semantic_liquidity
        import episode_policy

        prepared: dict[str, pd.DataFrame] = {}
        levels_by: dict[str, Any] = {}
        metadata_by: dict[str, Any] = {}
        evidence: dict[str, Any] = {}
        for symbol in symbols:
            futures_rows = self.repository.window_rows(
                symbol, stream="futures", end_time_ms=end_time_ms, days=rolling_window_days
            )
            mark_rows = self.repository.window_rows(
                symbol, stream="mark", end_time_ms=end_time_ms, days=rolling_window_days
            )
            index_rows = self.repository.window_rows(
                symbol, stream="index", end_time_ms=end_time_ms, days=rolling_window_days
            )
            metric_rows = self.repository.window_rows(
                symbol, stream="metrics", end_time_ms=end_time_ms, days=min(30, rolling_window_days)
            )
            futures = kline_rows_to_frame(futures_rows, require_flow=True)
            mark = kline_rows_to_frame(mark_rows, require_flow=False)
            index_price = kline_rows_to_frame(index_rows, require_flow=False)
            metrics = metric_rows_to_frame(metric_rows)
            if min(len(futures), len(mark), len(index_price)) < 4_320:
                raise RuntimeError(
                    f"insufficient closed one-minute context for {symbol}: "
                    f"futures={len(futures)}, mark={len(mark)}, index={len(index_price)}"
                )
            tick = CONTRACTS[symbol].tick_size
            state = prepare_live_market_state(futures, index_price, mark, metrics, tick)
            levels, metadata = build_semantic_liquidity(symbol, state, futures, tick)
            prepared[symbol] = state
            levels_by[symbol] = levels
            metadata_by[symbol] = metadata
            evidence[symbol] = {
                "futures_rows": len(futures),
                "mark_rows": len(mark),
                "index_rows": len(index_price),
                "metric_rows": len(metrics),
                "prepared_rows": len(state),
                "semantic_levels": len(levels),
                "last_completed_state_ns": int(state.index[-1].value),
            }
        prepared = _add_common_state(prepared)
        trading_start = pd.Timestamp(
            end_time_ms - max(2, decision_age_minutes) * 60_000,
            unit="ms",
            tz="UTC",
        )
        plans: list[EpisodePlan] = []
        counts: dict[str, Any] = {}
        cutoff_ns = int(end_time_ms * 1_000_000)
        earliest_ns = cutoff_ns - int(decision_age_minutes * 60 * 1_000_000_000)
        for symbol in symbols:
            frame, symbol_counts = episode_policy.generate_symbol(
                symbol,
                prepared[symbol],
                levels_by[symbol],
                metadata_by[symbol],
                trading_start,
            )
            counts[symbol] = symbol_counts
            if frame.empty:
                continue
            orders = frame[_bool_mask(frame, "order_exists")].copy()
            if "order_time_ns" in orders:
                times = pd.to_numeric(orders["order_time_ns"], errors="coerce")
                orders = orders[times.between(earliest_ns, cutoff_ns, inclusive="both")]
            for _, row in orders.iterrows():
                plans.append(row_to_plan(row))
        unique: dict[str, EpisodePlan] = {}
        for plan in sorted(plans, key=lambda item: (item.order_time_ns, item.episode_id, item.action_id)):
            if plan.episode_id in unique:
                raise RuntimeError(f"one-plan-per-episode invariant violated: {plan.episode_id}")
            unique[plan.episode_id] = plan
        return EvaluationResult(
            decision_time_ns=cutoff_ns,
            plans=tuple(unique.values()),
            counts=counts,
            data_evidence=evidence,
        )
