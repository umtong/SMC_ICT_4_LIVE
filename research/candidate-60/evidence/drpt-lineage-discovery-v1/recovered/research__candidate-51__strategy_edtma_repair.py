"""Episode- and thesis-aware EDTMA repair experiments.

The public entry, 3%-NAV risk sizing, trailing winner engine and ROI remain
unchanged.  The experiments isolate two questions revealed by v38:

* continuation re-entry is valuable in some trend episodes and destructive in
  others, so ``profit_reentry`` allows late re-entry only after a profitable
  trailing/ROI exit in the same contiguous source condition;
* the very large loss tail generally never reaches the public trailing trigger
  and remains open long after the source thesis weakens, so progress/thesis
  exits are expressed relative to the source's own trailing activation and
  current source condition rather than arbitrary return thresholds.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

import router as _router
import strategy_edtma as _base


class Candidate35Config(_base.Candidate35Config, frozen=True):
    # source | condition_loss | progress_thesis
    edtma_repair_management: str = "source"
    edtma_progress_checkpoint_1_minutes: int = 120
    edtma_progress_checkpoint_2_minutes: int = 240
    edtma_progress_activation_fraction_1: float = 0.50
    edtma_progress_activation_fraction_2: float = 1.00


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self._last_closed: dict[str, Any] | None = None
        self.diagnostics.update(
            {
                "edtma_repair_management": str(config.edtma_repair_management),
                "edtma_profit_reentry_allowed": 0,
                "edtma_profit_reentry_rejected": 0,
                "edtma_condition_loss_exits": 0,
                "edtma_no_progress_exits": 0,
                "edtma_repair_exit_counts": {},
            }
        )

    @staticmethod
    def _pnl(value: Any) -> float:
        match = re.search(r"[-+]?\d+(?:[,_]\d{3})*(?:\.\d+)?", str(value).replace("_", ""))
        return float(match.group().replace(",", "")) if match else 0.0

    def _after_position_closed(self, event: Any, record: dict[str, Any]) -> None:
        snapshot = {
            "symbol": record.get("symbol"),
            "side": int(record.get("side") or 0),
            "condition_run_start_ts": int(
                (record.get("diagnostics") or {}).get("condition_run_start_ts")
                or record.get("condition_run_start_ts") or 0
            ),
            "exit_driver": record.get("edtma_exit_driver"),
            "pnl": self._pnl(record.get("realized_pnl")),
        }
        super()._after_position_closed(event, record)
        self._last_closed = snapshot

    def _submit_decision(self, decision, ts_event: int) -> None:
        mode = str(self.config.edtma_episode_mode).strip().lower()
        diagnostics = dict(decision.diagnostics)
        run_hours = int(diagnostics.get("condition_run_hours") or 0)
        if mode == "profit_reentry" and run_hours > 1:
            prior = self._last_closed or {}
            same_episode = (
                prior.get("symbol") == decision.symbol
                and int(prior.get("side") or 0) == int(decision.side)
                and int(prior.get("condition_run_start_ts") or 0)
                == int(diagnostics.get("condition_run_start_ts") or -1)
            )
            profitable_capture = (
                float(prior.get("pnl") or 0.0) > 0.0
                and prior.get("exit_driver") in {"PUBLIC_TRAILING_EXIT", "PUBLIC_ROI_EXIT"}
            )
            if not (same_episode and profitable_capture):
                self.diagnostics["edtma_profit_reentry_rejected"] += 1
                self._event(
                    "EDTMA_PROFIT_REENTRY_REJECTED",
                    ts_event,
                    symbol=decision.symbol,
                    side=int(decision.side),
                    condition_run_hours=run_hours,
                    same_episode=same_episode,
                    profitable_capture=profitable_capture,
                )
                return
            self.diagnostics["edtma_profit_reentry_allowed"] += 1
        super()._submit_decision(decision, ts_event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "condition_run_hours": run_hours,
                    "condition_run_start_ts": int(diagnostics.get("condition_run_start_ts") or 0),
                    "edtma_repair_management": str(self.config.edtma_repair_management),
                }
            )

    def _submit_repair_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._exit_pending:
            return
        if self.current_scenario is not None:
            self.current_scenario["edtma_exit_driver"] = reason
            self.current_scenario["edtma_repair_exit_details"] = details
        counts = self.diagnostics["edtma_repair_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self._exit_pending = True
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("EDTMA_REPAIR_EXIT", ts_event, reason=reason, **details)

    def _repair_state(self) -> tuple[dict[str, float | int | str], float, float, int] | None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return None
        state = _router.inspect_condition(tuple(self.bars[symbol]), self.route_config)
        if not int(state.get("ready") or 0):
            return None
        entry = float(scenario.get("actual_entry_fill") or scenario.get("entry_reference") or math.nan)
        side = int(scenario.get("side") or 0)
        if not math.isfinite(entry) or entry <= 0.0 or side not in (-1, 1):
            return None
        close = float(self.bars[symbol][-1].close)
        current = side * (close - entry) / entry
        mfe = float(scenario.get("edtma_mfe_fraction") or 0.0)
        held = max(0, self.minute_index - self.position_open_minute)
        return state, current, mfe, held

    def _manage_open_position(self, ts_event: int) -> None:
        if self._exit_pending:
            return
        # Keep public trailing and ROI first. Workflows set the source-signal
        # mode independently, so this method measures interaction rather than
        # silently deleting the public exit.
        super()._manage_open_position(ts_event)
        if self._exit_pending:
            return
        management = str(self.config.edtma_repair_management).strip().lower()
        if management == "source":
            return
        if management not in {"condition_loss", "progress_thesis"}:
            raise ValueError(f"unsupported edtma_repair_management={management!r}")
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute != 59:
            return
        packed = self._repair_state()
        if packed is None:
            return
        state, current, mfe, held = packed
        scenario = self.current_scenario or {}
        side = int(scenario.get("side") or 0)
        condition_active = bool(
            state.get("long_condition") if side > 0 else state.get("short_condition")
        )
        if not condition_active and current <= 0.0:
            self.diagnostics["edtma_condition_loss_exits"] += 1
            self._submit_repair_exit(
                ts_event,
                "SOURCE_CONDITION_LOST_UNDERWATER",
                held_minutes=held,
                mfe_fraction=mfe,
                current_return_fraction=current,
                adx=float(state.get("adx") or 0.0),
                volume_ratio=float(state.get("volume_ratio") or 0.0),
            )
            return
        if management != "progress_thesis":
            return
        leverage = max(float(self.config.edtma_source_leverage), 1e-12)
        activation = float(self.config.edtma_trailing_offset_profit_ratio) / leverage
        checkpoints = (
            (
                int(self.config.edtma_progress_checkpoint_2_minutes),
                float(self.config.edtma_progress_activation_fraction_2),
                "NO_SOURCE_PROGRESS_4H",
            ),
            (
                int(self.config.edtma_progress_checkpoint_1_minutes),
                float(self.config.edtma_progress_activation_fraction_1),
                "NO_SOURCE_PROGRESS_2H",
            ),
        )
        for minutes, fraction, reason in checkpoints:
            if held >= minutes and mfe < activation * fraction and current <= 0.0:
                self.diagnostics["edtma_no_progress_exits"] += 1
                self._submit_repair_exit(
                    ts_event,
                    reason,
                    held_minutes=held,
                    mfe_fraction=mfe,
                    required_mfe_fraction=activation * fraction,
                    current_return_fraction=current,
                    condition_active=condition_active,
                    adx=float(state.get("adx") or 0.0),
                    volume_ratio=float(state.get("volume_ratio") or 0.0),
                )
                return


__all__ = ["Candidate35Config", "Candidate35Strategy"]
