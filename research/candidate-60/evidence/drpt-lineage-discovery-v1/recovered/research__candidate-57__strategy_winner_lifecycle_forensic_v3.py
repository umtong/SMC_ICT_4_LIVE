"""Diagnostics-only lifecycle observer for the source-faithful Winner15m account."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from router import WINNER_STATE, _aggregate_complete, _winner_series
from strategy_source import (
    Candidate35Config as Candidate35Config,
    Candidate35Strategy as _SourceStrategy,
)


class Candidate35Strategy(_SourceStrategy):
    """Record source-thesis persistence without changing any order or route."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self._winner_lifecycle_mfe_fraction = 0.0
        self._winner_lifecycle_mae_fraction = 0.0
        self._winner_lifecycle_last_snapshot_ts = -1
        self.diagnostics.update(
            {
                "winner_lifecycle_forensic_v3": 1,
                "winner_lifecycle_policy_changed": 0,
                "winner_lifecycle_snapshots": 0,
                "winner_lifecycle_first_failures": 0,
                "winner_lifecycle_orders_changed": 0,
            }
        )

    @staticmethod
    def _component_state(
        row: dict[str, float | int | str],
        side: int,
        config: Any,
    ) -> dict[str, int]:
        if not int(row.get("ready", 0)) or side not in (-1, 1):
            return {
                "ema_supports_entry": 0,
                "macd_supports_entry": 0,
                "roc_supports_entry": 0,
                "adx_supports_entry": 0,
                "volume_supports_entry": 0,
            }
        ema_fast = float(row["ema_fast"])
        ema_slow = float(row["ema_slow"])
        macd = float(row["macd"])
        macd_signal = float(row["macd_signal"])
        roc = float(row["roc"])
        adx = float(row["adx"])
        volume_ratio = float(row.get("volume_ratio", math.nan))
        return {
            "ema_supports_entry": int(side * (ema_fast - ema_slow) > 0.0),
            "macd_supports_entry": int(side * (macd - macd_signal) > 0.0),
            "roc_supports_entry": int(
                side * roc > float(config.winner_roc_threshold)
            ),
            "adx_supports_entry": int(
                adx > float(config.winner_adx_threshold)
            ),
            "volume_supports_entry": int(
                math.isfinite(volume_ratio)
                and volume_ratio > float(config.winner_volume_ratio)
            ),
        }

    def _observe_winner_lifecycle(self, ts_event: int) -> None:
        if self.current_symbol is None or self.current_scenario is None:
            return
        scenario = self.current_scenario
        if scenario.get("state") != WINNER_STATE:
            return
        side = int(scenario.get("side", 0))
        entry = float(scenario.get("entry_reference", math.nan))
        if side not in (-1, 1) or not math.isfinite(entry) or entry <= 0.0:
            return

        bar = self.bars[self.current_symbol][-1]
        favourable_price = float(bar.high) if side > 0 else float(bar.low)
        adverse_price = float(bar.low) if side > 0 else float(bar.high)
        favourable_move = side * (favourable_price - entry) / entry
        adverse_move = side * (adverse_price - entry) / entry
        self._winner_lifecycle_mfe_fraction = max(
            self._winner_lifecycle_mfe_fraction,
            favourable_move,
        )
        self._winner_lifecycle_mae_fraction = min(
            self._winner_lifecycle_mae_fraction,
            adverse_move,
        )
        scenario["winner_lifecycle_mfe_fraction"] = (
            self._winner_lifecycle_mfe_fraction
        )
        scenario["winner_lifecycle_mae_fraction"] = (
            self._winner_lifecycle_mae_fraction
        )

        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000,
            tz=timezone.utc,
        )
        bucket = int(self.config.winner_bucket_minutes)
        if bucket <= 0 or moment.minute % bucket != bucket - 1:
            return

        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            bucket,
        )
        if len(candles) < int(self.config.winner_source_startup_candles):
            return
        candle_ts = int(candles[-1].ts_event)
        if candle_ts == self._winner_lifecycle_last_snapshot_ts:
            return
        self._winner_lifecycle_last_snapshot_ts = candle_ts

        sides, rows = _winner_series(candles, self.route_config)
        current_side = int(sides[-1])
        row = dict(rows[-1])
        components = self._component_state(row, side, self.route_config)
        close = float(candles[-1].close)
        directional_close_return = side * (close - entry) / entry
        trailing_active = bool(self._trail_active)
        direct_failure = bool(
            not trailing_active
            and current_side != side
            and directional_close_return <= 0.0
        )
        snapshot = {
            "ts_event": candle_ts,
            "age_minutes": max(
                0,
                int(self.minute_index - self.position_open_minute),
            ),
            "entry_side": side,
            "current_source_side": current_side,
            "source_side_still_matches_entry": int(current_side == side),
            "source_condition_absent": int(current_side == 0),
            "source_condition_opposite": int(current_side == -side),
            "directional_close_return": directional_close_return,
            "mfe_fraction": self._winner_lifecycle_mfe_fraction,
            "mae_fraction": self._winner_lifecycle_mae_fraction,
            "trailing_active": int(trailing_active),
            "direct_thesis_failure": int(direct_failure),
            "source_causal_episode_start_ts": int(
                (scenario.get("diagnostics") or {}).get(
                    "causal_episode_start_ts",
                    scenario.get("episode_ts", 0),
                )
            ),
            "adx": row.get("adx"),
            "roc": row.get("roc"),
            "volume_ratio": row.get("volume_ratio"),
            **components,
            "supporting_component_count": sum(components.values()),
        }
        scenario.setdefault("winner_lifecycle_snapshots", []).append(snapshot)
        self.diagnostics["winner_lifecycle_snapshots"] += 1

        if (
            direct_failure
            and scenario.get("winner_lifecycle_first_thesis_failure_ts") is None
        ):
            scenario["winner_lifecycle_first_thesis_failure_ts"] = candle_ts
            scenario["winner_lifecycle_first_thesis_failure_age_minutes"] = (
                snapshot["age_minutes"]
            )
            self.diagnostics["winner_lifecycle_first_failures"] += 1
            self._event(
                "WINNER_LIFECYCLE_THESIS_FAILURE_OBSERVED",
                candle_ts,
                current_source_side=current_side,
                directional_close_return=directional_close_return,
                trailing_active=int(trailing_active),
                supporting_component_count=snapshot[
                    "supporting_component_count"
                ],
            )

    def _manage_open_position(self, ts_event: int) -> None:
        self._observe_winner_lifecycle(ts_event)
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._winner_lifecycle_mfe_fraction = 0.0
        self._winner_lifecycle_mae_fraction = 0.0
        self._winner_lifecycle_last_snapshot_ts = -1


__all__ = ["Candidate35Config", "Candidate35Strategy"]
