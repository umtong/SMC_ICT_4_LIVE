"""Cost-aware inventory-release absorption specialist for Candidate 55.

This module does not invent a new execution engine.  It reuses the checksum-
verified Binance feature pipeline, the real micro-auction absorption classifier,
the Candidate 35 single-account/single-slot NautilusTrader shell, and its
current-NAV 3% planned-loss sizing.

The old micro-auction experiment used the midpoint of a ten-minute balance as
the objective.  In the development evidence that objective was commonly
smaller than round-trip costs, so a target fill could still lose money.  This
specialist keeps the causal absorption transition but only trades when three
slow inventory/context facts agree:

* the symbol has moved idiosyncratically against the intended reversal side
  over the completed prior hour;
* aggregate open interest has fallen over the completed prior 15 minutes;
* the five-minute premium change has turned in the intended reversal direction.

The objective is fixed before submission at +2R after the same fee, slippage,
and funding reserves used by the execution shell.  The structural stop remains
the absorption event extreme plus the frozen ATR buffer.  Repeated signals
owned by the same sixty-minute price extreme are rejected as one causal episode.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from router import (
    ABSORPTION_STATE,
    FeatureObservation,
    RouteDecision,
    classify_absorption,
)
from strategy_base import SYMBOLS
from strategy_microauction import (
    Candidate35Config as _MicroAuctionConfig,
    Candidate35Strategy as _MicroAuctionStrategy,
    _MicroAuctionFeatureStore,
)


@dataclass(frozen=True, slots=True)
class InventoryContextObservation:
    observed_time_ns: int
    ready: bool = False
    oi_change_15m: float = math.nan
    premium_change_5m: float = math.nan


class _InventoryFeatureStore(_MicroAuctionFeatureStore):
    """Causal real-data micro-auction view plus OI and premium transitions."""

    _CONTEXT_COLUMNS = ("oi_change_15m", "premium_change_5m")

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        header = set(pd.read_csv(path, compression="infer", nrows=0).columns)
        required = {"observed_time_ns", "feature_ready", *self._CONTEXT_COLUMNS}
        missing = required.difference(header)
        if missing:
            raise RuntimeError(
                f"inventory-release feature schema missing {sorted(missing)} in {path}"
            )
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=["observed_time_ns", "feature_ready", *self._CONTEXT_COLUMNS],
        )
        context_times = (
            pd.to_numeric(frame["observed_time_ns"], errors="raise")
            .astype("int64")
            .to_numpy(copy=True)
        )
        if context_times.shape != self.times.shape or not np.array_equal(
            context_times, self.times
        ):
            raise RuntimeError(f"micro/context clocks differ in {path}")
        self.context_ready = (
            frame["feature_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )
        self.context_values = {
            name: pd.to_numeric(frame[name], errors="coerce").to_numpy(
                dtype=np.float64,
                copy=True,
            )
            for name in self._CONTEXT_COLUMNS
        }

    def context(
        self,
        ts_event: int,
        max_age_seconds: float,
    ) -> InventoryContextObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return InventoryContextObservation(0, ready=False)
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future inventory context reached strategy")
        values = {
            name: float(array[index])
            for name, array in self.context_values.items()
        }
        ready = (
            age <= float(max_age_seconds)
            and bool(self.context_ready[index])
            and all(math.isfinite(value) for value in values.values())
        )
        return InventoryContextObservation(
            observed_time_ns=observed,
            ready=ready,
            **values,
        )


def _cost_aware_target(
    *,
    entry: float,
    stop: float,
    side: int,
    fee_bps_each_side: float,
    slippage_bps_each_side: float,
    funding_reserve_bps: float,
    net_target_r: float,
) -> tuple[float, dict[str, float]]:
    """Return a frozen objective whose modeled payoff is ``net_target_r`` R.

    Planned loss exactly mirrors ``strategy_base._submit_decision``.  The target
    movement also reserves two-sided fees, two-sided adverse slippage and
    funding.  Exit-fee dependence on the target is solved algebraically rather
    than ignored.
    """
    if side not in (-1, 1):
        raise ValueError(f"side must be -1 or 1, received {side}")
    numbers = (
        entry,
        stop,
        fee_bps_each_side,
        slippage_bps_each_side,
        funding_reserve_bps,
        net_target_r,
    )
    if not all(math.isfinite(float(value)) for value in numbers):
        raise ValueError("cost-aware target received non-finite input")
    if entry <= 0.0 or stop <= 0.0 or net_target_r <= 0.0:
        raise ValueError("cost-aware target requires positive prices and R")
    if side > 0 and stop >= entry:
        raise ValueError("long stop must be below entry")
    if side < 0 and stop <= entry:
        raise ValueError("short stop must be above entry")

    fee_rate = float(fee_bps_each_side) / 10_000.0
    slippage_rate = float(slippage_bps_each_side) / 10_000.0
    funding_rate = float(funding_reserve_bps) / 10_000.0
    adverse_entry = entry * (1.0 + side * slippage_rate)
    adverse_stop = stop * (1.0 - side * slippage_rate)
    planned_loss = (
        abs(adverse_entry - adverse_stop)
        + fee_rate * (abs(adverse_entry) + abs(adverse_stop))
        + funding_rate * abs(entry)
    )
    if planned_loss <= 0.0 or not math.isfinite(planned_loss):
        raise ValueError("invalid planned loss")

    fixed_cost = entry * (2.0 * fee_rate + 2.0 * slippage_rate + funding_rate)
    # For a long target, exit fee increases with the upward target distance.
    # For a short target, exit fee decreases with the downward distance.
    denominator = 1.0 - side * fee_rate
    if denominator <= 0.0:
        raise ValueError("invalid fee denominator")
    target_move = (float(net_target_r) * planned_loss + fixed_cost) / denominator
    target = entry + side * target_move
    if target <= 0.0 or not math.isfinite(target):
        raise ValueError("invalid cost-aware objective")

    price_r = target_move / abs(entry - stop)
    return target, {
        "signal_entry_reference": entry,
        "structural_stop_reference": stop,
        "planned_loss_per_unit_reference": planned_loss,
        "fixed_roundtrip_cost_reserve_per_unit": fixed_cost,
        "target_move_per_unit": target_move,
        "target_move_bps": target_move / entry * 10_000.0,
        "price_only_target_r": price_r,
        "modeled_net_target_r": float(net_target_r),
    }


class Candidate35Config(_MicroAuctionConfig, frozen=True):
    inventory_context_lookback_minutes: int = 60
    inventory_target_net_r: float = 2.0


class Candidate35Strategy(_MicroAuctionStrategy):
    """One-slot inventory-release absorption policy on a continuous account."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        if str(config.microauction_mode).strip().lower() != "absorption":
            raise ValueError("inventory-release specialist requires absorption mode")
        if int(config.inventory_context_lookback_minutes) != 60:
            raise ValueError("the frozen inventory context lookback is 60 minutes")
        if abs(float(config.inventory_target_net_r) - 2.0) > 1e-12:
            raise ValueError("the frozen cost-after objective is +2R")
        self.used_inventory_episodes: set[tuple[str, int, int]] = set()
        self.diagnostics.update(
            {
                "candidate": "candidate-55-inventory-release-absorption-v1",
                "state_model": (
                    "idiosyncratic hourly displacement + OI release + "
                    "premium recovery -> real-flow absorption reclaim"
                ),
                "inventory_context_lookback_minutes": 60,
                "inventory_target_net_r": 2.0,
                "base_absorption_states": 0,
                "inventory_context_edges": 0,
                "inventory_episode_rejections": 0,
                "inventory_context_not_ready": 0,
                "inventory_context_rejection_counts": {},
                "inventory_selected_episode_keys": [],
                "inventory_edge_records": [],
            }
        )

    def on_start(self) -> None:
        super().on_start()
        for symbol in SYMBOLS:
            self.features[symbol] = _InventoryFeatureStore(
                self.feature_paths[symbol]
            )

    def _context_observation(
        self,
        symbol: str,
        ts_event: int,
    ) -> InventoryContextObservation:
        store = self.features[symbol]
        if not isinstance(store, _InventoryFeatureStore):
            raise RuntimeError(f"unexpected inventory feature store for {symbol}")
        return store.context(ts_event, self.config.feature_max_age_seconds)

    @staticmethod
    def _hour_return(bars: Any, lookback: int) -> float:
        if len(bars) <= lookback:
            return math.nan
        current = float(bars[-1].close)
        prior = float(bars[-1 - lookback].close)
        if current <= 0.0 or prior <= 0.0:
            return math.nan
        return current / prior - 1.0

    @staticmethod
    def _episode_extreme_ts(bars: Any, side: int, lookback: int) -> int:
        window = list(bars)[-(lookback + 1):]
        if not window:
            return 0
        if side > 0:
            extreme = min(window, key=lambda bar: (float(bar.low), int(bar.ts_event)))
        else:
            extreme = max(window, key=lambda bar: (float(bar.high), -int(bar.ts_event)))
        return int(extreme.ts_event)

    def _reject_context(self, reason: str) -> None:
        counts = self.diagnostics["inventory_context_rejection_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]),
            len(open_symbols),
        )
        if len(open_symbols) > 1:
            self.diagnostics["global_position_violations"] += 1
            for symbol in open_symbols:
                self.cancel_all_orders(self.instrument_ids[symbol])
                self.close_all_positions(self.instrument_ids[symbol])
            return
        if open_symbols:
            self.current_symbol = open_symbols[0]
            self._manage_open_position(ts_event)
            return
        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if self.minute_index - self.entry_pending_minute > 2:
                if self.current_symbol is not None:
                    self.cancel_all_orders(
                        self.instrument_ids[self.current_symbol]
                    )
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return
        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return

        lookback = int(self.config.inventory_context_lookback_minutes)
        minimum = max(
            int(self.route_config.microauction_atr_period) + 2,
            int(self.route_config.microauction_balance_lookback) + 1,
            lookback + 1,
        )
        if any(len(self.bars[symbol]) < minimum for symbol in SYMBOLS):
            return

        observations = {
            symbol: self._observation(symbol, ts_event)
            for symbol in SYMBOLS
        }
        contexts = {
            symbol: self._context_observation(symbol, ts_event)
            for symbol in SYMBOLS
        }
        stale = self.diagnostics["feature_stale_by_symbol"]
        for symbol in SYMBOLS:
            if not observations[symbol].ready:
                stale[symbol] = int(stale.get(symbol, 0)) + 1
        if not all(item.ready for item in observations.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return

        returns = {
            symbol: self._hour_return(self.bars[symbol], lookback)
            for symbol in SYMBOLS
        }
        if not all(math.isfinite(value) for value in returns.values()):
            self._reject_context("HOURLY_RETURN_NOT_READY")
            return
        market_return = sum(returns.values()) / len(returns)

        self.diagnostics["quarter_hour_decisions"] += 1
        edges: list[tuple[RouteDecision, tuple[str, int, int], dict[str, float]]] = []
        route_counts = self.diagnostics["route_counts"]
        reason_counts = self.diagnostics["unresolved_reason_counts"]

        for symbol in SYMBOLS:
            decision = classify_absorption(
                symbol,
                tuple(self.bars[symbol]),
                observations[symbol],
                self.route_config,
            )
            route_counts[decision.state] = int(
                route_counts.get(decision.state, 0)
            ) + 1
            if not decision.actionable:
                for reason in decision.reasons:
                    reason_counts[reason] = int(
                        reason_counts.get(reason, 0)
                    ) + 1
                continue

            self.diagnostics["base_absorption_states"] += 1
            context = contexts[symbol]
            if not context.ready:
                self.diagnostics["inventory_context_not_ready"] += 1
                self._reject_context("OI_OR_PREMIUM_CONTEXT_NOT_READY")
                continue

            side = int(decision.side)
            relative_side_return = side * (returns[symbol] - market_return)
            oi_release = float(context.oi_change_15m)
            premium_recovery = side * float(context.premium_change_5m)
            context_diagnostics = {
                "symbol_return_60m": returns[symbol],
                "market_return_60m": market_return,
                "side_relative_return_60m": relative_side_return,
                "oi_change_15m": oi_release,
                "side_premium_change_5m": premium_recovery,
            }
            if not relative_side_return < 0.0:
                self._reject_context("NO_IDIOSYNCRATIC_ADVERSE_DISPLACEMENT")
                continue
            if not oi_release < 0.0:
                self._reject_context("NO_OPEN_INTEREST_RELEASE")
                continue
            if not premium_recovery > 0.0:
                self._reject_context("NO_PREMIUM_RECOVERY")
                continue

            extreme_ts = self._episode_extreme_ts(
                self.bars[symbol],
                side,
                lookback,
            )
            episode_key = (symbol, side, extreme_ts)
            if episode_key in self.used_inventory_episodes:
                self.diagnostics["inventory_episode_rejections"] += 1
                self._reject_context("CAUSAL_EPISODE_ALREADY_USED")
                continue

            entry = float(self.bars[symbol][-1].close)
            stop = float(decision.stop_reference)
            try:
                objective, target_diagnostics = _cost_aware_target(
                    entry=entry,
                    stop=stop,
                    side=side,
                    fee_bps_each_side=float(
                        self.config.all_in_cost_bps_each_side
                    ),
                    slippage_bps_each_side=float(
                        self.config.adverse_slippage_bps_each_side
                    ),
                    funding_reserve_bps=float(
                        self.config.funding_reserve_bps
                    ),
                    net_target_r=float(self.config.inventory_target_net_r),
                )
            except ValueError:
                self._reject_context("COST_AWARE_TARGET_INVALID")
                continue

            diagnostics = dict(decision.diagnostics)
            diagnostics.update(context_diagnostics)
            diagnostics.update(target_diagnostics)
            diagnostics.update(
                {
                    "inventory_episode_extreme_ts": extreme_ts,
                    "inventory_episode_key": (
                        f"{symbol}:{side}:{extreme_ts}"
                    ),
                    "original_midpoint_objective": float(
                        decision.objective_reference
                    ),
                }
            )
            adapted = replace(
                decision,
                objective_reference=objective,
                reasons=(
                    *decision.reasons,
                    "IDIOSYNCRATIC_HOURLY_DISPLACEMENT",
                    "OPEN_INTEREST_RELEASE",
                    "PREMIUM_RECOVERY_IN_REVERSAL_DIRECTION",
                    "COST_AFTER_TWO_R_OBJECTIVE",
                    "SIXTY_MINUTE_EXTREME_EPISODE_OWNERSHIP",
                ),
                diagnostics=diagnostics,
            )
            edges.append((adapted, episode_key, context_diagnostics))

        if not edges:
            self.diagnostics["unresolved_episodes"] += 1
            return

        self.diagnostics["inventory_context_edges"] += len(edges)
        self.diagnostics["microauction_edges"] += len(edges)
        # Strongest adverse relative displacement first, then largest OI release,
        # then strongest premium recovery.  These are causal arbitration
        # dimensions, not risk multipliers or performance-fitted scores.
        edges.sort(
            key=lambda item: (
                float(item[2]["side_relative_return_60m"]),
                float(item[2]["oi_change_15m"]),
                -float(item[2]["side_premium_change_5m"]),
                -float(item[0].score),
                item[0].symbol,
                int(item[0].episode_ts),
            )
        )
        winner, episode_key, context_diagnostics = edges[0]
        if self._funding_blackout(ts_event):
            self._reject_context("FUNDING_BLACKOUT")
            return
        if (
            self.minute_index - self.last_entry_minute
            < self.config.cooldown_minutes
        ):
            self._reject_context("COOLDOWN")
            return

        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        submitted = int(self.diagnostics["entry_submissions"]) > before
        if not submitted:
            self._reject_context("EXECUTION_SHELL_REJECTED")
            return

        self.used_inventory_episodes.add(episode_key)
        episode_text = f"{episode_key[0]}:{episode_key[1]}:{episode_key[2]}"
        self.diagnostics["inventory_selected_episode_keys"].append(episode_text)
        self.diagnostics["inventory_edge_records"].append(
            {
                "ts_event": int(ts_event),
                "symbol": winner.symbol,
                "side": int(winner.side),
                "episode_key": episode_text,
                **context_diagnostics,
                "target": float(winner.objective_reference),
                "stop": float(winner.stop_reference),
                "modeled_net_target_r": float(
                    self.config.inventory_target_net_r
                ),
            }
        )
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": (
                        "candidate-55-inventory-release-absorption-v1"
                    ),
                    "state_family": ABSORPTION_STATE,
                    "inventory_episode_key": episode_text,
                    "risk_geometry": (
                        "absorption-event-extreme-plus-atr-buffer"
                    ),
                    "management": (
                        "cost-after-two-r-bracket-or-240-minute-timeout"
                    ),
                    "context": dict(context_diagnostics),
                }
            )


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "InventoryContextObservation",
    "_cost_aware_target",
]
