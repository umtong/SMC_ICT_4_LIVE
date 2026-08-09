"""Candidate 53 strategy-performance health router over Candidate 47 structural ichiFan.

The execution policy is unchanged from Candidate 47's causal structural-risk
ichiFan.  Candidate 53 adds a non-trading counterfactual observer which records
every rising-edge ichiFan episode across the four-symbol universe, including
signals that occur while the real account is already occupied.  Each virtual
probe is resolved causally by the same structural stop / source trend-cross
logic, or at a fixed 60 minute diagnostic horizon.  Only already-resolved
virtual outcomes may influence a later real entry.

The router is deliberately strategy-specific rather than a generic price-regime
filter: the last eight hours of resolved probe R outcomes estimate whether this
exact causal family currently has positive after-cost contribution.  With fewer
than four resolved observations the state is UNKNOWN and the frozen source
policy is allowed to trade.  Otherwise new real entries are accepted only while
the clipped mean probe R is positive.  Virtual probes continue while OFF, so
the family can re-enable itself without a real-money calibration trade.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable

import ichifan_strategy as _exact
import ichifan_structural_strategy as _structural
import router as _router

Candidate53HealthConfig = _structural.Candidate47IchiFanStructuralConfig
Candidate35Config = Candidate53HealthConfig
SYMBOLS = _structural.SYMBOLS

_PROBE_HORIZON_NS = 60 * 60 * 1_000_000_000
_HEALTH_LOOKBACK_NS = 8 * 60 * 60 * 1_000_000_000
_MIN_RESOLVED_PROBES = 4
_R_FLOOR = -1.0
_R_CAP = 3.0


@dataclass(slots=True)
class VirtualProbe:
    episode_id: str
    symbol: str
    opened_ts: int
    entry: float
    stop: float
    planned_loss_per_unit: float
    peak: float


def clipped_probe_r(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("probe R must be finite")
    return min(_R_CAP, max(_R_FLOOR, value))


def health_decision(
    resolved: Iterable[tuple[int, float]],
    *,
    now_ts: int,
) -> tuple[str, float, int]:
    """Return causal family state, clipped mean R and eligible sample count."""
    now = int(now_ts)
    eligible = [
        clipped_probe_r(r_value)
        for resolved_ts, r_value in resolved
        if 0 <= now - int(resolved_ts) <= _HEALTH_LOOKBACK_NS
    ]
    if not eligible:
        return "UNKNOWN", 0.0, 0
    score = sum(eligible) / len(eligible)
    if len(eligible) < _MIN_RESOLVED_PROBES:
        return "UNKNOWN", score, len(eligible)
    return ("ON" if score > 0.0 else "OFF"), score, len(eligible)


class Candidate53HealthStrategy(_structural.Candidate47IchiFanStructuralStrategy):
    """Frozen structural ichiFan gated by causal counterfactual family health."""

    def __init__(self, config: Candidate53HealthConfig) -> None:
        super().__init__(config)
        self._virtual_seen: set[str] = set()
        self._virtual_active: dict[str, VirtualProbe] = {}
        self._virtual_resolved: list[dict[str, Any]] = []
        self.diagnostics.update(
            {
                "candidate53_health_probe_horizon_minutes": 60,
                "candidate53_health_lookback_hours": 8,
                "candidate53_health_min_resolved_probes": _MIN_RESOLVED_PROBES,
                "candidate53_virtual_probe_signals": 0,
                "candidate53_virtual_probe_resolutions": 0,
                "candidate53_virtual_probe_positive": 0,
                "candidate53_virtual_probe_negative": 0,
                "candidate53_health_entry_checks": 0,
                "candidate53_health_on_entries": 0,
                "candidate53_health_unknown_entries": 0,
                "candidate53_health_off_rejections": 0,
                "candidate53_health_last_state": "UNKNOWN",
                "candidate53_health_last_score": 0.0,
                "candidate53_health_last_sample_count": 0,
                "candidate53_health_policy": (
                    "counterfactual-ichiFan-probes;60m-resolution;8h-clipped-mean-R;"
                    "min4;trade-iff-mean-positive-or-unknown"
                ),
            }
        )

    def _planned_loss_per_unit(self, entry: float, stop: float) -> float:
        fee_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        funding_rate = self.config.funding_reserve_bps / 10_000.0
        adverse_entry = entry * (1.0 + slippage_rate)
        adverse_stop = stop * (1.0 - slippage_rate)
        return (
            abs(adverse_entry - adverse_stop)
            + fee_rate * (abs(adverse_entry) + abs(adverse_stop))
            + funding_rate * abs(entry)
        )

    def _net_probe_r(self, probe: VirtualProbe, exit_price: float) -> float:
        fee_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        funding_rate = self.config.funding_reserve_bps / 10_000.0
        adverse_entry = probe.entry * (1.0 + slippage_rate)
        adverse_exit = float(exit_price) * (1.0 - slippage_rate)
        pnl = (
            adverse_exit
            - adverse_entry
            - fee_rate * (abs(adverse_entry) + abs(adverse_exit))
            - funding_rate * abs(probe.entry)
        )
        return pnl / probe.planned_loss_per_unit

    def _resolve_probe(self, probe: VirtualProbe, ts_event: int, exit_price: float, reason: str) -> None:
        raw_r = self._net_probe_r(probe, exit_price)
        record = {
            "episode_id": probe.episode_id,
            "symbol": probe.symbol,
            "opened_ts": probe.opened_ts,
            "resolved_ts": int(ts_event),
            "entry": probe.entry,
            "stop": probe.stop,
            "exit_price": float(exit_price),
            "raw_r": raw_r,
            "clipped_r": clipped_probe_r(raw_r),
            "reason": reason,
        }
        self._virtual_resolved.append(record)
        self._virtual_active.pop(probe.episode_id, None)
        self.diagnostics["candidate53_virtual_probe_resolutions"] += 1
        if raw_r > 0.0:
            self.diagnostics["candidate53_virtual_probe_positive"] += 1
        else:
            self.diagnostics["candidate53_virtual_probe_negative"] += 1

    def _update_virtual_probes(self, ts_event: int) -> None:
        if not self._virtual_active:
            return
        five_minute_boundary = (
            int(ts_event // 1_000_000_000 // 60) % 5 == 4
        )
        states_by_symbol: dict[str, _exact.FanState | None] = {}
        for probe in list(self._virtual_active.values()):
            bars = self.bars[probe.symbol]
            if not bars or ts_event <= probe.opened_ts:
                continue
            latest = bars[-1]
            probe.peak = max(probe.peak, float(latest.high))

            if float(latest.low) <= probe.stop:
                self._resolve_probe(probe, ts_event, probe.stop, "STRUCTURAL_STOP")
                continue
            if float(latest.high) >= probe.entry * 1.30:
                self._resolve_probe(probe, ts_event, probe.entry * 1.30, "REMOTE_OBJECTIVE")
                continue

            trailing_active = probe.peak / probe.entry - 1.0 >= 0.08
            if trailing_active and float(latest.close) <= probe.peak * (1.0 - 0.06):
                self._resolve_probe(probe, ts_event, float(latest.close), "SOURCE_TRAILING")
                continue

            if five_minute_boundary:
                if probe.symbol not in states_by_symbol:
                    states = _exact.fan_states(
                        _exact.aggregate_five_minute(tuple(self.bars[probe.symbol]))
                    )
                    states_by_symbol[probe.symbol] = states[-1] if states else None
                state = states_by_symbol[probe.symbol]
                if state is not None and state.exit_cross_down:
                    self._resolve_probe(
                        probe,
                        ts_event,
                        float(latest.close),
                        "SOURCE_5M_90M_CROSS",
                    )
                    continue

            if ts_event - probe.opened_ts >= _PROBE_HORIZON_NS:
                self._resolve_probe(probe, ts_event, float(latest.close), "FIXED_60M_HORIZON")

    def _scan_virtual_entries(self, ts_event: int) -> None:
        minute = int(ts_event // 1_000_000_000 // 60)
        if minute % 5 != 4:
            return
        if any(len(self.bars[symbol]) < 800 for symbol in SYMBOLS):
            return
        for symbol in SYMBOLS:
            five = _exact.aggregate_five_minute(tuple(self.bars[symbol]))
            states = _exact.fan_states(five)
            if len(states) < 2 or not states[-1].ready:
                continue
            current, previous = states[-1], states[-2]
            if not current.entry or previous.entry:
                continue
            episode_id = f"{symbol}:{current.ts_event}:ICHIFAN_LONG"
            if episode_id in self._virtual_seen:
                continue
            self._virtual_seen.add(episode_id)
            entry = float(self.bars[symbol][-1].close)
            signal_bar = five[-2]
            try:
                stop, _ = _structural.causal_structural_stop(
                    entry=entry,
                    signal_bar_low=float(signal_bar.low),
                    trend_close_90m=float(current.trend_close_90m),
                    cloud_a=float(current.cloud_a),
                    cloud_b=float(current.cloud_b),
                )
            except ValueError:
                continue
            planned_loss = self._planned_loss_per_unit(entry, stop)
            if not math.isfinite(planned_loss) or planned_loss <= 0.0:
                continue
            self._virtual_active[episode_id] = VirtualProbe(
                episode_id=episode_id,
                symbol=symbol,
                opened_ts=int(ts_event),
                entry=entry,
                stop=stop,
                planned_loss_per_unit=planned_loss,
                peak=float(self.bars[symbol][-1].high),
            )
            self.diagnostics["candidate53_virtual_probe_signals"] += 1

    def _health(self, ts_event: int) -> tuple[str, float, int]:
        state, score, count = health_decision(
            ((int(item["resolved_ts"]), float(item["raw_r"])) for item in self._virtual_resolved),
            now_ts=ts_event,
        )
        self.diagnostics["candidate53_health_last_state"] = state
        self.diagnostics["candidate53_health_last_score"] = score
        self.diagnostics["candidate53_health_last_sample_count"] = count
        return state, score, count

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        # Observe the counterfactual family before the real account's one-slot
        # early returns.  No current/future outcome enters the same decision.
        self._update_virtual_probes(ts_event)
        self._scan_virtual_entries(ts_event)
        super()._on_complete_universe_minute(ts_event)

    def _submit_decision(self, decision: _router.RouteDecision, ts_event: int) -> None:
        state, score, count = self._health(ts_event)
        self.diagnostics["candidate53_health_entry_checks"] += 1
        if state == "OFF":
            self.diagnostics["candidate53_health_off_rejections"] += 1
            self._event(
                "CANDIDATE53_FAMILY_HEALTH_REJECTED",
                ts_event,
                symbol=decision.symbol,
                health_state=state,
                health_score=score,
                health_sample_count=count,
                causal_episode_id=decision.diagnostics.get("causal_episode_id"),
            )
            return
        if state == "ON":
            self.diagnostics["candidate53_health_on_entries"] += 1
        else:
            self.diagnostics["candidate53_health_unknown_entries"] += 1
        super()._submit_decision(decision, ts_event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-53-counterfactual-health-structural-ichifan",
                    "family_health_state": state,
                    "family_health_score": score,
                    "family_health_sample_count": count,
                }
            )

    def on_stop(self) -> None:
        # Resolve nothing with future information at shutdown; unresolved probes
        # remain explicitly unresolved and are diagnostic only.
        super().on_stop()
        destination = Path(self.config.output_dir)
        payload = {
            "resolved": self._virtual_resolved,
            "unresolved": [
                {
                    "episode_id": probe.episode_id,
                    "symbol": probe.symbol,
                    "opened_ts": probe.opened_ts,
                    "entry": probe.entry,
                    "stop": probe.stop,
                    "peak": probe.peak,
                }
                for probe in self._virtual_active.values()
            ],
        }
        (destination / "candidate53_virtual_probes.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )


Candidate35Strategy = Candidate53HealthStrategy

__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "Candidate53HealthConfig",
    "Candidate53HealthStrategy",
    "VirtualProbe",
    "clipped_probe_r",
    "health_decision",
]
