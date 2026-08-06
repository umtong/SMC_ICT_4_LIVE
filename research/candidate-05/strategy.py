#!/usr/bin/env python3
"""NautilusTrader strategy for Candidate 05: Liquidity Response Transition.

The module owns only causal market-state interpretation, scenario transitions,
and risk-budget order sizing.  NautilusTrader owns order lifecycle, fills,
fees, positions, margin, liquidation, portfolio accounting, and NAV.
"""
from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from logic import Pool
from logic import SweepEvidence
from logic import choose_liquidity_target
from logic import classify_sweep
from logic import confirmation_passes
from logic import floor_quantity
from logic import is_confirmed_pivot
from logic import planned_loss_per_unit
from logic import ClassificationThresholds
from smc_ict_4.contracts import ResearchEvent
from smc_ict_4.event_log import write_events


def _as_float(value: Any) -> float:
    if value is None:
        return float("nan")
    method = getattr(value, "as_double", None)
    if callable(method):
        return float(method())
    text = str(value).strip().split()[0].replace("_", "").replace(",", "")
    return float(text)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


@dataclass(slots=True)
class PendingSetup:
    scenario_id: str
    branch: str
    side: int
    swept_kind: str
    pool_id: str
    pool_level: float
    created_index: int
    expires_index: int
    sweep_extreme: float
    structure: float
    atr: float
    hold_count: int
    retrace_armed: bool
    details: dict[str, Any]


class LiquidityResponseConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    output_dir: str
    features_path: str
    evaluation_start_ns: int
    evaluation_end_ns: int

    starting_nav: float = 100_000.0
    risk_fraction: float = 0.03
    all_in_cost_bps_each_side: float = 7.5
    adverse_slippage_bps_each_side: float = 2.5

    atr_period: int = 30
    pivot_span: int = 3
    pool_merge_tolerance_atr: float = 0.10
    pool_min_age_bars: int = 3
    pool_max_age_bars: int = 1_440
    session_hours: tuple[int, ...] = (0, 4, 8, 12, 16, 20)
    structure_lookback_bars: int = 5

    sweep_min_penetration_atr: float = 0.08
    sweep_min_notional_burst: float = 1.05
    rejection_flow_min: float = 0.12
    rejection_efficiency_max: float = 0.38
    rejection_depth_refill_min: float = 0.01
    acceptance_flow_min: float = 0.10
    acceptance_efficiency_min: float = 0.45
    acceptance_depth_withdrawal_min: float = 0.01
    acceptance_close_atr: float = 0.05
    acceptance_close_location: float = 0.62

    rejection_confirmation_bars: int = 4
    rejection_confirm_body_atr: float = 0.25
    rejection_confirm_flow_min: float = 0.04
    rejection_confirm_efficiency_min: float = 0.22
    rejection_confirm_close_location: float = 0.58

    acceptance_min_hold_bars: int = 1
    acceptance_retrace_bars: int = 8
    acceptance_hold_tolerance_atr: float = 0.06
    acceptance_retrace_tolerance_atr: float = 0.18
    acceptance_max_counterflow: float = 0.08
    acceptance_retest_close_location: float = 0.54

    stop_buffer_atr: float = 0.08
    min_target_net_r: float = 1.15
    max_target_net_r: float = 3.00
    rejection_target_net_r: float = 1.80
    acceptance_target_net_r: float = 2.00
    cooldown_bars: int = 12
    max_hold_bars: int = 180

    funding_flatten_minute: int = 45
    funding_blackout_before_minutes: int = 25
    funding_blackout_after_minutes: int = 5
    feature_max_age_seconds: float = 65.0
    enable_rejection: bool = True
    enable_acceptance: bool = True


class LiquidityResponseStrategy(Strategy):
    """Causal sweep-response state machine with one global entry/position."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config=config)
        self.instrument = None
        self.bars: deque[dict[str, float | int]] = deque(maxlen=max(4_000, config.pool_max_age_bars + 100))
        self.bar_index = -1

        self.features: list[dict[str, Any]] = []
        self.feature_cursor = -1
        self.current_feature: dict[str, Any] | None = None

        self.active_pools: dict[str, Pool] = {}
        self.pool_counter = 0
        self.scenario_counter = 0
        self.scenario_states: dict[str, str] = {}
        self.events: list[ResearchEvent] = []

        self.current_session_key: int | None = None
        self.current_session_high = -math.inf
        self.current_session_low = math.inf
        self.current_session_start_ns = 0

        self.pending: PendingSetup | None = None
        self.entry_pending = False
        self.entry_pending_index = -1
        self.position_open_index = -1
        self.last_entry_index = -10**12
        self.current_scenario_id: str | None = None
        self.current_branch: str | None = None
        self.current_pool_level: float | None = None

        self.equity: list[dict[str, Any]] = []
        self.closed_scenarios: list[dict[str, Any]] = []
        self.diagnostics: dict[str, Any] = {
            "entry_submissions": 0,
            "order_rejections": 0,
            "max_simultaneous_entry_intents": 0,
            "max_open_positions_observed": 0,
            "unresolved_sweeps": 0,
            "rejection_setups": 0,
            "acceptance_setups": 0,
            "expired_setups": 0,
            "feature_stale_bars": 0,
        }

        self.thresholds = ClassificationThresholds(
            min_penetration_atr=config.sweep_min_penetration_atr,
            min_notional_burst=config.sweep_min_notional_burst,
            rejection_flow_min=config.rejection_flow_min,
            rejection_efficiency_max=config.rejection_efficiency_max,
            rejection_depth_refill_min=config.rejection_depth_refill_min,
            acceptance_flow_min=config.acceptance_flow_min,
            acceptance_efficiency_min=config.acceptance_efficiency_min,
            acceptance_depth_withdrawal_min=config.acceptance_depth_withdrawal_min,
            acceptance_close_atr=config.acceptance_close_atr,
            acceptance_close_location=config.acceptance_close_location,
        )

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(f"instrument not found: {self.config.instrument_id}")
        destination = Path(self.config.output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        self._load_features(Path(self.config.features_path))
        self.subscribe_bars(self.config.bar_type)

    def _load_features(self, path: Path) -> None:
        frame = pd.read_csv(path, compression="infer")
        if "observed_time_ns" not in frame or "feature_ready" not in frame:
            raise RuntimeError(f"invalid feature schema: {list(frame.columns)}")
        frame["observed_time_ns"] = pd.to_numeric(frame["observed_time_ns"], errors="raise").astype("int64")
        if frame["observed_time_ns"].duplicated().any() or not frame["observed_time_ns"].is_monotonic_increasing:
            raise RuntimeError("feature observation times must be unique and monotonic")
        ready = frame["feature_ready"]
        if ready.dtype != bool:
            frame["feature_ready"] = ready.astype(str).str.lower().isin({"true", "1", "yes"})
        self.features = frame.to_dict(orient="records")
        if not self.features:
            raise RuntimeError("feature file is empty")

    def on_bar(self, bar: Bar) -> None:
        self.bar_index += 1
        row = {
            "ts": int(bar.ts_event),
            "open": _as_float(bar.open),
            "high": _as_float(bar.high),
            "low": _as_float(bar.low),
            "close": _as_float(bar.close),
            "volume": _as_float(bar.volume),
        }
        previous_close = float(self.bars[-1]["close"]) if self.bars else float(row["open"])
        self.bars.append(row)
        self._advance_features(int(row["ts"]))
        self._record_equity(int(row["ts"]))
        self._roll_session(row)
        self._confirm_pivots(row)
        self._prune_pools(row)

        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["max_open_positions_observed"] = max(
                int(self.diagnostics["max_open_positions_observed"]),
                1,
            )
            self._manage_open_position(row)
            return

        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if self.bar_index - self.entry_pending_index > 2:
                self.cancel_all_orders(self.config.instrument_id)
                if self.current_scenario_id is not None:
                    self._transition(
                        self.current_scenario_id,
                        "ENTRY_EXPIRED",
                        int(row["ts"]),
                        int(row["ts"]),
                        "CLOSED",
                        "ENTRY_NOT_FILLED_WITHIN_TWO_BARS",
                        float(row["close"]),
                        {},
                    )
                self._clear_trade_state()
            return

        if not self._in_evaluation(int(row["ts"])):
            self.pending = None
            return
        if self._funding_blackout(int(row["ts"])):
            self._expire_pending(row, "FUNDING_BLACKOUT")
            return
        if not self._features_ready(int(row["ts"])):
            return
        if len(self.bars) < max(self.config.atr_period + 2, 2 * self.config.pivot_span + 2):
            return

        if self.pending is not None:
            handled = self._process_pending(row)
            if handled:
                return

        if self.pending is None and self.bar_index - self.last_entry_index >= self.config.cooldown_bars:
            self._detect_sweep(row, previous_close)

    def on_position_opened(self, event: Any) -> None:
        self.entry_pending = False
        self.position_open_index = self.bar_index
        if self.current_scenario_id is not None:
            ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
            self._transition(
                self.current_scenario_id,
                "POSITION_OPENED",
                ts,
                ts,
                "POSITION_OPEN",
                "NAUTILUS_POSITION_OPENED",
                float(self.bars[-1]["close"]),
                {"event": str(event)},
            )

    def on_position_closed(self, event: Any) -> None:
        scenario_id = self.current_scenario_id or "unmatched-position"
        branch = self.current_branch or "UNKNOWN"
        realized = getattr(event, "realized_pnl", None)
        ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
        if scenario_id in self.scenario_states and self.scenario_states[scenario_id] != "CLOSED":
            self._transition(
                scenario_id,
                "POSITION_CLOSED",
                ts,
                ts,
                "CLOSED",
                "NAUTILUS_POSITION_CLOSED",
                float(self.bars[-1]["close"]),
                {"event": str(event), "realized_pnl": str(realized) if realized is not None else None},
            )
        self.closed_scenarios.append(
            {
                "scenario_id": scenario_id,
                "branch": branch,
                "ts_event": ts,
                "realized_pnl": str(realized) if realized is not None else None,
                "event": str(event),
            },
        )
        self._clear_trade_state()

    def on_order_rejected(self, event: Any) -> None:
        self.diagnostics["order_rejections"] = int(self.diagnostics["order_rejections"]) + 1
        ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
        if self.current_scenario_id is not None and self.scenario_states.get(self.current_scenario_id) != "CLOSED":
            self._transition(
                self.current_scenario_id,
                "ORDER_REJECTED",
                ts,
                ts,
                "CLOSED",
                "NAUTILUS_ORDER_REJECTED",
                float(self.bars[-1]["close"]),
                {"event": str(event)},
            )
        if self.portfolio.is_flat(self.config.instrument_id):
            self._clear_trade_state()

    def on_stop(self) -> None:
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
        self._record_equity(int(self.bars[-1]["ts"]) if self.bars else 0)
        destination = Path(self.config.output_dir)
        write_events(destination / "scenario_events.jsonl", self.events)
        (destination / "closed_scenarios.json").write_text(
            json.dumps(self.closed_scenarios, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (destination / "strategy_diagnostics.json").write_text(
            json.dumps(self.diagnostics, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if self.equity:
            with (destination / "equity.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["ts_event", "equity"])
                writer.writeheader()
                writer.writerows(self.equity)

    def _clear_trade_state(self) -> None:
        self.pending = None
        self.entry_pending = False
        self.entry_pending_index = -1
        self.position_open_index = -1
        self.current_scenario_id = None
        self.current_branch = None
        self.current_pool_level = None

    def _in_evaluation(self, ts_event: int) -> bool:
        return self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns

    def _advance_features(self, ts_event: int) -> None:
        while (
            self.feature_cursor + 1 < len(self.features)
            and int(self.features[self.feature_cursor + 1]["observed_time_ns"]) <= ts_event
        ):
            self.feature_cursor += 1
            self.current_feature = self.features[self.feature_cursor]

    def _features_ready(self, ts_event: int) -> bool:
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return False
        age_seconds = (ts_event - int(feature["observed_time_ns"])) / 1_000_000_000
        if age_seconds < -1e-9:
            raise RuntimeError("future feature observation reached strategy")
        if age_seconds > self.config.feature_max_age_seconds:
            self.diagnostics["feature_stale_bars"] = int(self.diagnostics["feature_stale_bars"]) + 1
            return False
        return True

    def _feature(self, name: str) -> float:
        if self.current_feature is None:
            return float("nan")
        return _finite(self.current_feature.get(name), float("nan"))

    def _transition(
        self,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        next_state: str,
        reason_code: str,
        reference_price: float,
        details: dict[str, Any],
    ) -> None:
        previous_state = self.scenario_states.get(scenario_id, "OBSERVE")
        event = ResearchEvent(
            scenario_id=scenario_id,
            instrument_id=str(self.config.instrument_id),
            event_type=event_type,
            event_time_ns=int(event_time_ns),
            observed_time_ns=int(observed_time_ns),
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=f"{reference_price:.10f}",
            details=details,
        )
        self.events.append(event)
        self.scenario_states[scenario_id] = next_state

    def _atr(self) -> float:
        rows = list(self.bars)
        if len(rows) < self.config.atr_period + 1:
            return float("nan")
        selected = rows[-(self.config.atr_period + 1) :]
        values: list[float] = []
        for previous, current in zip(selected, selected[1:]):
            values.append(
                max(
                    float(current["high"]) - float(current["low"]),
                    abs(float(current["high"]) - float(previous["close"])),
                    abs(float(current["low"]) - float(previous["close"])),
                ),
            )
        return sum(values[-self.config.atr_period :]) / self.config.atr_period

    def _session_key(self, ts_event: int) -> int:
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        valid = [hour for hour in self.config.session_hours if hour <= moment.hour]
        boundary = max(valid) if valid else max(self.config.session_hours)
        day = int(moment.strftime("%Y%m%d"))
        return day * 100 + boundary

    def _roll_session(self, row: dict[str, float | int]) -> None:
        key = self._session_key(int(row["ts"]))
        if self.current_session_key is None:
            self.current_session_key = key
            self.current_session_start_ns = int(row["ts"])
        elif key != self.current_session_key:
            if math.isfinite(self.current_session_high) and math.isfinite(self.current_session_low):
                self._add_pool(
                    "HIGH",
                    self.current_session_high,
                    self.current_session_start_ns,
                    int(row["ts"]),
                    "SESSION_4H",
                    strength=2,
                )
                self._add_pool(
                    "LOW",
                    self.current_session_low,
                    self.current_session_start_ns,
                    int(row["ts"]),
                    "SESSION_4H",
                    strength=2,
                )
            self.current_session_key = key
            self.current_session_high = -math.inf
            self.current_session_low = math.inf
            self.current_session_start_ns = int(row["ts"])
        self.current_session_high = max(self.current_session_high, float(row["high"]))
        self.current_session_low = min(self.current_session_low, float(row["low"]))

    def _confirm_pivots(self, row: dict[str, float | int]) -> None:
        span = self.config.pivot_span
        rows = list(self.bars)
        if len(rows) < 2 * span + 1:
            return
        window = rows[-(2 * span + 1) :]
        center = window[span]
        highs = [float(item["high"]) for item in window]
        lows = [float(item["low"]) for item in window]
        if is_confirmed_pivot(highs, span=span, kind="HIGH"):
            self._add_pool(
                "HIGH",
                float(center["high"]),
                int(center["ts"]),
                int(row["ts"]),
                "CONFIRMED_SWING",
                strength=1,
            )
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_pool(
                "LOW",
                float(center["low"]),
                int(center["ts"]),
                int(row["ts"]),
                "CONFIRMED_SWING",
                strength=1,
            )

    def _add_pool(
        self,
        kind: str,
        level: float,
        event_time_ns: int,
        observed_time_ns: int,
        source: str,
        *,
        strength: int,
    ) -> None:
        atr = self._atr()
        tolerance = self.config.pool_merge_tolerance_atr * atr if math.isfinite(atr) else 0.0
        merge: Pool | None = None
        for pool in self.active_pools.values():
            if pool.kind == kind and abs(pool.level - level) <= tolerance:
                merge = pool
                break
        if merge is not None:
            new_level = max(merge.level, level) if kind == "HIGH" else min(merge.level, level)
            updated = replace(
                merge,
                level=new_level,
                observed_time_ns=observed_time_ns,
                strength=merge.strength + strength,
            )
            self.active_pools[merge.pool_id] = updated
            self._transition(
                merge.pool_id,
                "POOL_STRENGTHENED",
                event_time_ns,
                observed_time_ns,
                "POOL_ARMED",
                "NEAR_EQUAL_LIQUIDITY_CLUSTER",
                new_level,
                {"source": source, "strength": updated.strength},
            )
            return

        self.pool_counter += 1
        pool_id = f"pool-{self.pool_counter:07d}"
        pool = Pool(
            pool_id=pool_id,
            kind=kind,
            level=level,
            event_time_ns=event_time_ns,
            observed_time_ns=observed_time_ns,
            source=source,
            strength=strength,
            created_index=self.bar_index,
        )
        self.active_pools[pool_id] = pool
        self._transition(
            pool_id,
            "POOL_CONFIRMED",
            event_time_ns,
            observed_time_ns,
            "POOL_ARMED",
            source,
            level,
            {"kind": kind, "strength": strength},
        )

    def _prune_pools(self, row: dict[str, float | int]) -> None:
        expired = [
            pool
            for pool in self.active_pools.values()
            if self.bar_index - pool.created_index > self.config.pool_max_age_bars
        ]
        for pool in expired:
            self._transition(
                pool.pool_id,
                "POOL_EXPIRED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "MAX_POOL_AGE",
                pool.level,
                {"age_bars": self.bar_index - pool.created_index},
            )
            self.active_pools.pop(pool.pool_id, None)

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        min_age = self.config.pool_min_age_bars
        high_crossed = [
            pool
            for pool in self.active_pools.values()
            if pool.kind == "HIGH"
            and self.bar_index - pool.created_index >= min_age
            and previous_close <= pool.level
            and float(row["high"]) >= pool.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            pool
            for pool in self.active_pools.values()
            if pool.kind == "LOW"
            and self.bar_index - pool.created_index >= min_age
            and previous_close >= pool.level
            and float(row["low"]) <= pool.level - self.config.sweep_min_penetration_atr * atr
        ]
        if high_crossed and low_crossed:
            for pool in high_crossed + low_crossed:
                self._consume_pool(pool, row, "AMBIGUOUS_TWO_SIDED_SWEEP")
            return
        if not high_crossed and not low_crossed:
            return

        if high_crossed:
            pool = max(high_crossed, key=lambda item: (item.level, item.strength))
            kind = "HIGH"
            crossed = high_crossed
        else:
            pool = min(low_crossed, key=lambda item: (item.level, -item.strength))
            kind = "LOW"
            crossed = low_crossed
        for item in crossed:
            self._consume_pool(item, row, "LIQUIDITY_ACCESSED")

        evidence = SweepEvidence(
            kind=kind,
            pool_level=pool.level,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            open=float(row["open"]),
            atr=atr,
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            notional_burst=self._feature("notional_burst"),
            efficiency_60s=self._feature("efficiency_60s"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
        )
        branch = classify_sweep(evidence, self.thresholds)
        self.scenario_counter += 1
        scenario_id = f"lrt-{self.scenario_counter:07d}"
        base_details = {
            "pool_id": pool.pool_id,
            "pool_kind": kind,
            "pool_level": pool.level,
            "pool_source": pool.source,
            "pool_strength": pool.strength,
            "penetration_atr": evidence.penetration_atr,
            "flow_15s": evidence.flow_15s,
            "flow_60s": evidence.flow_60s,
            "flow_3m": self._feature("flow_3m"),
            "notional_burst": evidence.notional_burst,
            "efficiency_60s": evidence.efficiency_60s,
            "absorption_60s": self._feature("absorption_60s"),
            "depth_imbalance_1": evidence.depth_imbalance_1,
            "bid_depth_change_1m": evidence.bid_depth_change_1m,
            "ask_depth_change_1m": evidence.ask_depth_change_1m,
            "depth_snapshot_age_seconds": self._feature("depth_snapshot_age_seconds"),
        }
        if branch is None:
            self.diagnostics["unresolved_sweeps"] = int(self.diagnostics["unresolved_sweeps"]) + 1
            self._transition(
                scenario_id,
                "SWEEP_UNRESOLVED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "PRICE_AND_LIQUIDITY_RESPONSE_NOT_COHERENT",
                float(row["close"]),
                base_details,
            )
            return
        if branch == "REJECTION" and not self.config.enable_rejection:
            return
        if branch == "ACCEPTANCE" and not self.config.enable_acceptance:
            return

        rows = list(self.bars)
        pre = rows[-(self.config.structure_lookback_bars + 1) : -1]
        direction = 1 if kind == "HIGH" else -1
        if branch == "REJECTION":
            side = -direction
            structure = (
                max(float(item["high"]) for item in pre)
                if side > 0
                else min(float(item["low"]) for item in pre)
            )
            expires = self.bar_index + self.config.rejection_confirmation_bars
            next_state = "REJECTION_ARMED"
            reason = "AGGRESSIVE_FLOW_ABSORBED_AND_POOL_RECLAIMED"
            self.diagnostics["rejection_setups"] = int(self.diagnostics["rejection_setups"]) + 1
        else:
            side = direction
            structure = pool.level
            expires = self.bar_index + self.config.acceptance_retrace_bars
            next_state = "ACCEPTANCE_ARMED"
            reason = "AGGRESSIVE_FLOW_EFFICIENT_AND_LIQUIDITY_WITHDREW"
            self.diagnostics["acceptance_setups"] = int(self.diagnostics["acceptance_setups"]) + 1

        sweep_extreme = float(row["high"]) if kind == "HIGH" else float(row["low"])
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch=branch,
            side=side,
            swept_kind=kind,
            pool_id=pool.pool_id,
            pool_level=pool.level,
            created_index=self.bar_index,
            expires_index=expires,
            sweep_extreme=sweep_extreme,
            structure=structure,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=base_details,
        )
        self._transition(
            scenario_id,
            "SWEEP_CLASSIFIED",
            int(row["ts"]),
            int(row["ts"]),
            next_state,
            reason,
            float(row["close"]),
            base_details,
        )

    def _consume_pool(self, pool: Pool, row: dict[str, float | int], reason: str) -> None:
        if pool.pool_id not in self.active_pools:
            return
        self._transition(
            pool.pool_id,
            "POOL_CONSUMED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            pool.level,
            {"bar_high": float(row["high"]), "bar_low": float(row["low"])},
        )
        self.active_pools.pop(pool.pool_id, None)

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if self.bar_index > setup.expires_index:
            self._expire_pending(row, "SETUP_WINDOW_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True

        if setup.branch == "REJECTION":
            atr = self._atr()
            passed = confirmation_passes(
                side=setup.side,
                open_price=float(row["open"]),
                close_price=float(row["close"]),
                high=float(row["high"]),
                low=float(row["low"]),
                structure=setup.structure,
                atr=atr,
                flow_60s=self._feature("flow_60s"),
                efficiency_60s=self._feature("efficiency_60s"),
                min_body_atr=self.config.rejection_confirm_body_atr,
                min_flow=self.config.rejection_confirm_flow_min,
                min_efficiency=self.config.rejection_confirm_efficiency_min,
                min_close_location=self.config.rejection_confirm_close_location,
            )
            if passed:
                return self._submit_entry(setup, row)
            invalid = (
                float(row["close"]) > setup.sweep_extreme
                if setup.side < 0
                else float(row["close"]) < setup.sweep_extreme
            )
            if invalid:
                self._expire_pending(row, "REJECTION_EXTREME_ACCEPTED")
                return False
            return True

        side = setup.side
        atr = self._atr()
        outside_distance = side * (float(row["close"]) - setup.pool_level) / atr
        if outside_distance < -self.config.acceptance_hold_tolerance_atr:
            self._expire_pending(row, "ACCEPTANCE_FAILED_RANGE_REENTRY")
            return False
        if not setup.retrace_armed:
            setup.hold_count += 1
            if setup.hold_count >= self.config.acceptance_min_hold_bars:
                setup.retrace_armed = True
                self._transition(
                    setup.scenario_id,
                    "ACCEPTANCE_HELD",
                    int(row["ts"]),
                    int(row["ts"]),
                    "RETRACE_ARMED",
                    "PRICE_HELD_OUTSIDE_CONSUMED_POOL",
                    float(row["close"]),
                    {**setup.details, "hold_count": setup.hold_count},
                )
            return True

        touched = (
            float(row["low"]) <= setup.pool_level + self.config.acceptance_retrace_tolerance_atr * atr
            if side > 0
            else float(row["high"]) >= setup.pool_level - self.config.acceptance_retrace_tolerance_atr * atr
        )
        closed_outside = float(row["close"]) > setup.pool_level if side > 0 else float(row["close"]) < setup.pool_level
        directional_tail_flow = side * self._feature("flow_15s")
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        close_location = (
            (float(row["close"]) - float(row["low"])) / span
            if side > 0
            else (float(row["high"]) - float(row["close"])) / span
        )
        if (
            touched
            and closed_outside
            and directional_tail_flow >= -self.config.acceptance_max_counterflow
            and close_location >= self.config.acceptance_retest_close_location
        ):
            return self._submit_entry(setup, row)
        return True

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        if self.pending is None:
            return
        self.diagnostics["expired_setups"] = int(self.diagnostics["expired_setups"]) + 1
        self._transition(
            self.pending.scenario_id,
            "SETUP_INVALIDATED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            self.pending.details,
        )
        self.pending = None

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        atr = self._atr()
        side = setup.side
        entry = float(row["close"])
        if setup.branch == "REJECTION":
            stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
            fallback_r = self.config.rejection_target_net_r
        else:
            if side > 0:
                stop = min(
                    setup.pool_level - self.config.stop_buffer_atr * atr,
                    float(row["low"]) - 0.25 * self.config.stop_buffer_atr * atr,
                )
            else:
                stop = max(
                    setup.pool_level + self.config.stop_buffer_atr * atr,
                    float(row["high"]) + 0.25 * self.config.stop_buffer_atr * atr,
                )
            fallback_r = self.config.acceptance_target_net_r

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_pending(row, "INVALID_STOP_GEOMETRY")
            return False
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(raw_quantity, int(self.instrument.size_precision))
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        target, target_source, target_r = choose_liquidity_target(
            entry=entry,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=self.config.min_target_net_r,
            max_net_r=self.config.max_target_net_r,
            fallback_net_r=fallback_r,
        )
        if side > 0 and not (stop < entry < target):
            self._expire_pending(row, "INVALID_LONG_BRACKET")
            return False
        if side < 0 and not (target < entry < stop):
            self._expire_pending(row, "INVALID_SHORT_BRACKET")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = setup.branch
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] = int(self.diagnostics["entry_submissions"]) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "CAUSAL_CONFIRMATION_AND_EXECUTABLE_BRACKET",
            entry,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "entry_estimate": entry,
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True

    def _funding_blackout(self, ts_event: int) -> bool:
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        minute_of_day = moment.hour * 60 + moment.minute
        points = (0, 8 * 60, 16 * 60, 24 * 60)
        to_next = min((point - minute_of_day for point in points if point >= minute_of_day), default=24 * 60)
        since_last = min((minute_of_day - point for point in points if point <= minute_of_day), default=minute_of_day)
        return (
            to_next <= self.config.funding_blackout_before_minutes
            or since_last <= self.config.funding_blackout_after_minutes
        )

    def _manage_open_position(self, row: dict[str, float | int]) -> None:
        moment = datetime.fromtimestamp(int(row["ts"]) / 1_000_000_000, tz=timezone.utc)
        before_funding = moment.hour in (7, 15, 23) and moment.minute >= self.config.funding_flatten_minute
        timed_out = (
            self.position_open_index >= 0
            and self.bar_index - self.position_open_index >= self.config.max_hold_bars
        )
        evaluation_ended = int(row["ts"]) >= self.config.evaluation_end_ns
        if before_funding or timed_out or evaluation_ended:
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            if self.current_scenario_id is not None:
                self._transition(
                    self.current_scenario_id,
                    "FORCED_DAYTRADE_EXIT",
                    int(row["ts"]),
                    int(row["ts"]),
                    "EXIT_PENDING",
                    "FUNDING_OR_HOLD_OR_EVALUATION_BOUNDARY",
                    float(row["close"]),
                    {
                        "before_funding": before_funding,
                        "timed_out": timed_out,
                        "evaluation_ended": evaluation_ended,
                    },
                )

    def _equity_value(self) -> float:
        try:
            values = self.portfolio.equity(self.config.instrument_id.venue)
            for currency, money in values.items():
                if str(currency) == "USDT":
                    return _as_float(money)
        except Exception:
            pass
        try:
            account = self.portfolio.account(self.config.instrument_id.venue)
            usdt = Currency.from_str("USDT")
            total = account.balance_total(usdt)
            unrealized = self.portfolio.unrealized_pnl(self.config.instrument_id)
            return _as_float(total) + (0.0 if unrealized is None else _as_float(unrealized))
        except Exception:
            return float(self.equity[-1]["equity"]) if self.equity else self.config.starting_nav

    def _record_equity(self, ts_event: int) -> None:
        if ts_event <= 0:
            return
        value = self._equity_value()
        if not math.isfinite(value) or value <= 0.0:
            return
        if self.equity and int(self.equity[-1]["ts_event"]) == ts_event:
            self.equity[-1]["equity"] = value
        else:
            self.equity.append({"ts_event": ts_event, "equity": value})


__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
