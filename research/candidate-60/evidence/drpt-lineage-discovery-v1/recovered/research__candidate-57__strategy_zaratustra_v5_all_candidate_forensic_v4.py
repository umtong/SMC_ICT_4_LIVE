"""Behaviour-preserving all-candidate shadow audit for public ZaratustraV5.

The actual NautilusTrader account is inherited unchanged.  This wrapper scans
all four source decisions at every completed five-minute boundary, groups
continuous source levels into causal episodes, and evolves non-trading shadow
scenarios under the frozen source lifecycle.  No shadow object submits, cancels,
or closes an order.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from router import FeatureObservation, ZARA_STATE, route_universe
from strategy_base import SYMBOLS
from strategy_zaratustra_source_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    zara_shadow_round_trip_cost_fraction: float = 0.0021


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        if float(config.zara_shadow_round_trip_cost_fraction) < 0.0:
            raise ValueError("shadow cost fraction must be non-negative")
        super().__init__(config)
        self._shadow_last_side = {symbol: 0 for symbol in SYMBOLS}
        self._shadow_active: list[dict[str, Any]] = []
        self._shadow_completed: list[dict[str, Any]] = []
        self._shadow_raw_boundaries: list[dict[str, Any]] = []
        self._shadow_episode_counter = 0
        self.diagnostics.update(
            {
                "candidate57_zara_all_candidate_forensic_v4": 1,
                "zara_all_candidate_policy_changed": 0,
                "zara_shadow_raw_source_signals": 0,
                "zara_shadow_continuous_episode_starts": 0,
                "zara_shadow_collision_boundaries": 0,
                "zara_shadow_account_open_boundaries": 0,
                "zara_shadow_completed_episodes": self._shadow_completed,
                "zara_shadow_raw_boundaries": self._shadow_raw_boundaries,
            }
        )

    @staticmethod
    def _finite(value: Any, default: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        return result if math.isfinite(result) else default

    def _score_components(self, diagnostics: dict[str, Any], side: int) -> dict[str, float]:
        threshold_rsi = float(self.route_config.zara_rsi_threshold)
        threshold_di = float(self.route_config.zara_di_threshold)
        rsi_distance = sum(
            abs(self._finite(diagnostics.get(f"rsi_{label}")) - threshold_rsi)
            for label in ("5m", "15m", "30m")
        )
        direction = "plus_di" if side > 0 else "minus_di"
        di_excess = sum(
            max(
                0.0,
                self._finite(diagnostics.get(f"{direction}_{label}")) - threshold_di,
            )
            for label in ("5m", "15m", "30m")
        )
        band_distance_bps = sum(
            abs(
                self._finite(diagnostics.get(f"close_{label}"))
                - self._finite(diagnostics.get(f"bb_middle_{label}"))
            )
            / max(abs(self._finite(diagnostics.get(f"close_{label}"))), 1e-12)
            * 10_000.0
            for label in ("5m", "15m", "30m")
        )
        return {
            "rsi_distance": rsi_distance,
            "di_excess": di_excess,
            "band_distance_bps": band_distance_bps,
        }

    def _slot_state(self) -> str:
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        if open_symbols:
            return "OPEN_POSITION"
        if self.entry_pending:
            return "ENTRY_PENDING"
        return "FLAT"

    def _create_shadow_episode(
        self,
        decision: Any,
        *,
        ts_event: int,
        router_selected_symbol: str | None,
        score_rank: int,
        total_candidates: int,
        same_side_breadth: int,
        slot_state: str,
    ) -> None:
        self._shadow_episode_counter += 1
        diagnostics = dict(decision.diagnostics or {})
        side = int(decision.side)
        entry = float(decision.entry_reference)
        stop = float(decision.stop_reference)
        risk_fraction = abs(entry - stop) / entry
        if side not in (-1, 1) or risk_fraction <= 1e-12:
            return
        components = self._score_components(diagnostics, side)
        episode = {
            "shadow_episode_id": self._shadow_episode_counter,
            "episode_ts": int(decision.episode_ts),
            "created_ts": int(ts_event),
            "symbol": str(decision.symbol),
            "side": side,
            "entry_reference": entry,
            "stop_reference": stop,
            "risk_fraction": risk_fraction,
            "planned_loss_fraction": risk_fraction
            + float(self.config.zara_shadow_round_trip_cost_fraction),
            "source_score": float(decision.score),
            **components,
            "router_selected": bool(decision.symbol == router_selected_symbol),
            "router_selected_symbol": router_selected_symbol,
            "score_rank": int(score_rank),
            "candidate_count": int(total_candidates),
            "same_side_breadth": int(same_side_breadth),
            "slot_state_at_start": slot_state,
            "trail_active": False,
            "trail_best": entry,
            "mfe_fraction": 0.0,
            "mae_fraction": 0.0,
            "mfe_r": 0.0,
            "mae_r": 0.0,
            "time_to_activation_minutes": None,
            "early_mark_r_15m": None,
            "early_mark_r_30m": None,
            "early_mark_r_60m": None,
        }
        self._shadow_active.append(episode)
        self.diagnostics["zara_shadow_continuous_episode_starts"] += 1

    def _finalize_shadow(
        self,
        episode: dict[str, Any],
        *,
        ts_event: int,
        exit_price: float,
        exit_reason: str,
    ) -> None:
        side = int(episode["side"])
        entry = float(episode["entry_reference"])
        planned_loss = float(episode["planned_loss_fraction"])
        gross_fraction = side * (float(exit_price) - entry) / entry
        cost_fraction = float(self.config.zara_shadow_round_trip_cost_fraction)
        episode.update(
            {
                "exit_ts": int(ts_event),
                "elapsed_minutes": max(
                    0,
                    int((int(ts_event) - int(episode["created_ts"])) // 60_000_000_000),
                ),
                "exit_price": float(exit_price),
                "exit_reason": str(exit_reason),
                "gross_return_fraction": gross_fraction,
                "net_return_fraction": gross_fraction - cost_fraction,
                "gross_r": gross_fraction / max(float(episode["risk_fraction"]), 1e-12),
                "net_r": (gross_fraction - cost_fraction) / max(planned_loss, 1e-12),
            }
        )
        for key in ("trail_active", "trail_best"):
            episode.pop(key, None)
        self._shadow_completed.append(episode)

    def _update_shadow_episodes(self, ts_event: int) -> None:
        if not self._shadow_active:
            return
        remaining: list[dict[str, Any]] = []
        for episode in self._shadow_active:
            symbol = str(episode["symbol"])
            side = int(episode["side"])
            entry = float(episode["entry_reference"])
            stop = float(episode["stop_reference"])
            risk_fraction = float(episode["risk_fraction"])
            elapsed = max(
                0,
                int((int(ts_event) - int(episode["created_ts"])) // 60_000_000_000),
            )
            if elapsed <= 0 or not self.bars[symbol]:
                remaining.append(episode)
                continue
            bar = self.bars[symbol][-1]
            high = float(bar.high)
            low = float(bar.low)
            close = float(bar.close)
            favourable = high if side > 0 else low
            adverse = low if side > 0 else high
            favourable_fraction = max(0.0, side * (favourable - entry) / entry)
            adverse_fraction = max(0.0, -side * (adverse - entry) / entry)
            episode["mfe_fraction"] = max(
                float(episode["mfe_fraction"]), favourable_fraction
            )
            episode["mae_fraction"] = max(
                float(episode["mae_fraction"]), adverse_fraction
            )
            episode["mfe_r"] = float(episode["mfe_fraction"]) / risk_fraction
            episode["mae_r"] = float(episode["mae_fraction"]) / risk_fraction
            mark_r = side * (close - entry) / entry / risk_fraction
            for minute in (15, 30, 60):
                key = f"early_mark_r_{minute}m"
                if elapsed >= minute and episode.get(key) is None:
                    episode[key] = mark_r

            stop_hit = low <= stop if side > 0 else high >= stop
            if stop_hit:
                self._finalize_shadow(
                    episode,
                    ts_event=ts_event,
                    exit_price=stop,
                    exit_reason="SOURCE_STOP",
                )
                continue

            if bool(episode["trail_active"]):
                best = float(episode["trail_best"])
                distance = 0.0013
                trailing_stop = best * (1.0 - side * distance)
                trailing_hit = (
                    low <= trailing_stop if side > 0 else high >= trailing_stop
                )
                if trailing_hit:
                    self._finalize_shadow(
                        episode,
                        ts_event=ts_event,
                        exit_price=trailing_stop,
                        exit_reason="SOURCE_TRAILING",
                    )
                    continue

            if elapsed >= 480 or int(ts_event) >= int(self.config.evaluation_end_ns):
                self._finalize_shadow(
                    episode,
                    ts_event=ts_event,
                    exit_price=close,
                    exit_reason=(
                        "SOURCE_HORIZON"
                        if elapsed >= 480
                        else "EVALUATION_END"
                    ),
                )
                continue

            if not bool(episode["trail_active"]) and favourable_fraction >= 0.0071:
                episode["trail_active"] = True
                episode["trail_best"] = favourable
                episode["time_to_activation_minutes"] = elapsed
            elif bool(episode["trail_active"]):
                episode["trail_best"] = (
                    max(float(episode["trail_best"]), favourable)
                    if side > 0
                    else min(float(episode["trail_best"]), favourable)
                )
            remaining.append(episode)
        self._shadow_active = remaining
        self.diagnostics["zara_shadow_completed_episodes"] = self._shadow_completed

    def _scan_source_candidates(self, ts_event: int) -> None:
        if not (self.config.evaluation_start_ns <= ts_event < self.config.evaluation_end_ns):
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        if any(len(self.bars[symbol]) < 5 * 65 for symbol in SYMBOLS):
            return
        features = {
            symbol: FeatureObservation(int(self.bars[symbol][-1].ts_event), ready=True)
            for symbol in SYMBOLS
        }
        selected, decisions = route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=features,
            config=self.route_config,
        )
        actionable = [decision for decision in decisions.values() if decision.actionable]
        actionable.sort(key=lambda item: (-float(item.score), str(item.symbol)))
        self.diagnostics["zara_shadow_raw_source_signals"] += len(actionable)
        slot_state = self._slot_state()
        if slot_state != "FLAT":
            self.diagnostics["zara_shadow_account_open_boundaries"] += 1
        sides = {symbol: 0 for symbol in SYMBOLS}
        for decision in actionable:
            sides[str(decision.symbol)] = int(decision.side)

        new_decisions = [
            decision
            for decision in actionable
            if int(self._shadow_last_side[str(decision.symbol)]) != int(decision.side)
        ]
        if len(new_decisions) >= 2:
            self.diagnostics["zara_shadow_collision_boundaries"] += 1
        selected_symbol = str(selected.symbol) if selected is not None else None
        boundary_record = {
            "episode_ts": int(ts_event),
            "slot_state": slot_state,
            "raw_actionable": len(actionable),
            "new_continuous_episodes": len(new_decisions),
            "router_selected_symbol": selected_symbol,
            "actionable_symbols": [str(item.symbol) for item in actionable],
            "new_episode_symbols": [str(item.symbol) for item in new_decisions],
        }
        if actionable:
            self._shadow_raw_boundaries.append(boundary_record)

        rank = {str(item.symbol): index + 1 for index, item in enumerate(actionable)}
        for decision in new_decisions:
            side = int(decision.side)
            same_side_breadth = sum(int(item.side) == side for item in actionable)
            self._create_shadow_episode(
                decision,
                ts_event=ts_event,
                router_selected_symbol=selected_symbol,
                score_rank=rank[str(decision.symbol)],
                total_candidates=len(actionable),
                same_side_breadth=same_side_breadth,
                slot_state=slot_state,
            )
        self._shadow_last_side = sides

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self._update_shadow_episodes(ts_event)
        self._scan_source_candidates(ts_event)
        super()._on_complete_universe_minute(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
