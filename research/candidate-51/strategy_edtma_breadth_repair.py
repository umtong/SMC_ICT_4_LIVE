"""Dynamic peer-breadth repair for the public EDTMA winner engine.

The v42 experiment showed that requiring at least two same-side source states at
entry removed most catastrophic losses without deleting the trailing/ROI gross
profit.  The remaining spring losses occurred after a peer-confirmed entry but
before the public winner engine activated.  This adapter therefore tests the
entry context as an explicit thesis throughout the trade:

* the public EDTMA source condition, source risk and trailing/ROI are unchanged;
* cross-asset breadth is recomputed only on completed hourly candles;
* an exit is permitted only while the position is underwater and the entry
  context has actually failed (breadth collapse, opposite-side majority, or an
  optional BTC anchor failure);
* own-symbol condition/progress exits remain independently selectable so their
  interaction is measurable rather than silently bundled.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
from typing import Any

import router as _router
import strategy_edtma_repair as _base

SYMBOLS = _base._base.SYMBOLS


class Candidate35Config(_base.Candidate35Config, frozen=True):
    edtma_arbitration_mode: str = "source_score"
    edtma_min_same_side_breadth: int = 2
    edtma_require_side_majority: bool = True
    edtma_require_btc_anchor: bool = False

    # source | context_loss
    edtma_breadth_management: str = "source"
    edtma_dynamic_min_same_side_breadth: int = 2
    edtma_dynamic_require_side_majority: bool = True
    edtma_dynamic_require_btc_anchor: bool = False


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            edtma_arbitration_mode=str(config.edtma_arbitration_mode),
            edtma_min_same_side_breadth=int(config.edtma_min_same_side_breadth),
            edtma_require_side_majority=bool(config.edtma_require_side_majority),
            edtma_require_btc_anchor=bool(config.edtma_require_btc_anchor),
        )
        self.diagnostics.update(
            {
                "edtma_arbitration_mode": str(config.edtma_arbitration_mode),
                "edtma_min_same_side_breadth": int(config.edtma_min_same_side_breadth),
                "edtma_require_side_majority": int(config.edtma_require_side_majority),
                "edtma_require_btc_anchor": int(config.edtma_require_btc_anchor),
                "edtma_breadth_management": str(config.edtma_breadth_management),
                "edtma_dynamic_min_same_side_breadth": int(
                    config.edtma_dynamic_min_same_side_breadth
                ),
                "edtma_dynamic_require_side_majority": int(
                    config.edtma_dynamic_require_side_majority
                ),
                "edtma_dynamic_require_btc_anchor": int(
                    config.edtma_dynamic_require_btc_anchor
                ),
                "edtma_breadth_context_checks": 0,
                "edtma_breadth_context_exits": 0,
                "edtma_breadth_context_reason_counts": {},
            }
        )

    def _current_context(
        self,
    ) -> tuple[dict[str, _router.RouteDecision], int, int, int] | None:
        if any(not self.bars[symbol] for symbol in SYMBOLS):
            return None
        features = {
            symbol: _router.FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        _, decisions = _router.route_universe(
            {symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features,
            self.route_config,
        )
        long_breadth = sum(
            1 for decision in decisions.values()
            if decision.actionable and int(decision.side) > 0
        )
        short_breadth = sum(
            1 for decision in decisions.values()
            if decision.actionable and int(decision.side) < 0
        )
        btc = decisions.get("BTCUSDT")
        btc_side = int(btc.side) if btc is not None and btc.actionable else 0
        return decisions, long_breadth, short_breadth, btc_side

    def _breadth_repair(self, ts_event: int) -> None:
        management = str(self.config.edtma_breadth_management).strip().lower()
        if management == "source":
            return
        if management != "context_loss":
            raise ValueError(f"unsupported edtma_breadth_management={management!r}")
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return
        entry = float(
            scenario.get("actual_entry_fill")
            or scenario.get("entry_reference")
            or math.nan
        )
        side = int(scenario.get("side") or 0)
        if not math.isfinite(entry) or entry <= 0.0 or side not in (-1, 1):
            return
        close = float(self.bars[symbol][-1].close)
        current = side * (close - entry) / entry
        if current > 0.0:
            return
        packed = self._current_context()
        if packed is None:
            return
        decisions, long_breadth, short_breadth, btc_side = packed
        self.diagnostics["edtma_breadth_context_checks"] += 1
        same = long_breadth if side > 0 else short_breadth
        opposite = short_breadth if side > 0 else long_breadth
        current_decision = decisions.get(symbol)
        own_condition_active = bool(
            current_decision is not None
            and current_decision.actionable
            and int(current_decision.side) == side
        )
        minimum = max(1, int(self.config.edtma_dynamic_min_same_side_breadth))
        breadth_failed = same < minimum
        majority_failed = bool(
            self.config.edtma_dynamic_require_side_majority and same <= opposite
        )
        btc_failed = bool(
            self.config.edtma_dynamic_require_btc_anchor
            and (btc_side == 0 or btc_side != side)
        )
        if not (breadth_failed or majority_failed or btc_failed):
            return
        reasons: list[str] = []
        if breadth_failed:
            reasons.append("BREADTH_COLLAPSED")
        if majority_failed:
            reasons.append("SIDE_MAJORITY_LOST")
        if btc_failed:
            reasons.append("BTC_ANCHOR_LOST")
        reason = "+".join(reasons)
        counts = self.diagnostics["edtma_breadth_context_reason_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self.diagnostics["edtma_breadth_context_exits"] += 1
        held = max(0, self.minute_index - self.position_open_minute)
        mfe = float(scenario.get("edtma_mfe_fraction") or 0.0)
        self._submit_repair_exit(
            ts_event,
            f"PEER_CONTEXT_FAILED_UNDERWATER:{reason}",
            held_minutes=held,
            current_return_fraction=current,
            mfe_fraction=mfe,
            own_condition_active=own_condition_active,
            long_breadth=long_breadth,
            short_breadth=short_breadth,
            same_side_breadth=same,
            opposite_side_breadth=opposite,
            btc_anchor_side=btc_side,
            dynamic_min_same_side_breadth=minimum,
        )

    def _manage_open_position(self, ts_event: int) -> None:
        if self._exit_pending:
            return
        # Public trailing/ROI and independently selected own-symbol repairs act
        # first.  Peer context is checked only if they leave the position open.
        super()._manage_open_position(ts_event)
        if self._exit_pending:
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute != 59:
            return
        self._breadth_repair(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
