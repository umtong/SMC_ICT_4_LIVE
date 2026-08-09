"""NautilusTrader strategy for Candidate 35's four-asset clock-phase router.

One strategy process receives completed one-minute bars for BTCUSDT, ETHUSDT,
SOLUSDT and XRPUSDT.  It waits until all four observations for a minute are
available, classifies the same quarter-hour episode across the universe, and
submits at most one risk-sized bracket.  NautilusTrader owns matching, fees,
orders, contingent children, positions, liquidation and portfolio accounting.
"""
from __future__ import annotations

import csv
from collections import deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from router import BarObservation, FeatureObservation, RouteConfig, RouteDecision, route_universe


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def _as_float(value: Any, default: float = math.nan) -> float:
    if value is None:
        return default
    method = getattr(value, "as_double", None)
    if callable(method):
        number = float(method())
        return number if math.isfinite(number) else default
    text = str(value).strip().split()[0].replace("_", "").replace(",", "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _floor_quantity(value: float, precision: int) -> float:
    if not math.isfinite(value) or value <= 0.0:
        return 0.0
    scale = 10**precision
    return math.floor(value * scale + 1e-12) / scale


def _bool_array(values: pd.Series) -> np.ndarray:
    if values.dtype == bool:
        return values.to_numpy(dtype=np.bool_, copy=True)
    return (
        values.astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes"})
        .to_numpy(dtype=np.bool_, copy=True)
    )


def _json_safe(value: Any) -> Any:
    """Replace non-finite floats before strict JSON persistence."""
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, deque)):
        return [_json_safe(item) for item in value]
    return value


class _FeatureStore:
    """Small columnar causal feature view; no future row can be returned."""

    _ALIASES = {
        "flow_open_10s": ("flow_open_10s", "flow_15s", "flow_60s"),
        "notional_open_10s_burst": (
            "notional_open_10s_burst",
            "notional_burst",
        ),
        "flow_60s": ("flow_60s", "flow_15s"),
        "efficiency_60s": ("efficiency_60s",),
        "open_interest": (
            "sum_open_interest",
            "open_interest",
            "sum_open_interest_value",
        ),
        "premium": ("premium_index", "mark_index_basis", "basis"),
    }

    def __init__(self, path: Path) -> None:
        header = list(pd.read_csv(path, compression="infer", nrows=0).columns)
        required = {"observed_time_ns", "feature_ready"}
        if not required.issubset(header):
            raise RuntimeError(f"invalid feature schema in {path}: {header}")
        selected_names: dict[str, str] = {}
        for logical, aliases in self._ALIASES.items():
            selected = next((name for name in aliases if name in header), None)
            if selected is not None:
                selected_names[logical] = selected
        usecols = ["observed_time_ns", "feature_ready", *sorted(set(selected_names.values()))]
        frame = pd.read_csv(path, compression="infer", usecols=usecols)
        self.times = pd.to_numeric(frame["observed_time_ns"], errors="raise").astype("int64").to_numpy(copy=True)
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"feature times must be non-empty, unique and monotonic: {path}")
        self.ready = _bool_array(frame["feature_ready"])
        self.values: dict[str, np.ndarray] = {}
        for logical, source in selected_names.items():
            self.values[logical] = pd.to_numeric(frame[source], errors="coerce").to_numpy(dtype=np.float64, copy=True)

    def _index(self, ts_event: int) -> int:
        return int(np.searchsorted(self.times, ts_event, side="right") - 1)

    def observation(self, ts_event: int, max_age_seconds: float) -> FeatureObservation:
        index = self._index(ts_event)
        if index < 0:
            return FeatureObservation(0, ready=False)
        observed = int(self.times[index])
        age = (ts_event - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future feature observation reached Candidate 35")
        if age > max_age_seconds or not bool(self.ready[index]):
            return FeatureObservation(observed, ready=False)

        def value(name: str, default: float = math.nan) -> float:
            array = self.values.get(name)
            if array is None:
                return default
            number = float(array[index])
            return number if math.isfinite(number) else default

        oi_change = math.nan
        oi = self.values.get("open_interest")
        if oi is not None and index >= 15:
            current = float(oi[index])
            previous = float(oi[index - 15])
            if math.isfinite(current) and math.isfinite(previous) and previous > 0.0:
                oi_change = current / previous - 1.0

        premium_z = math.nan
        premium = self.values.get("premium")
        if premium is not None:
            start = max(0, index - 95)
            history = premium[start : index + 1]
            clean = history[np.isfinite(history)]
            if clean.size >= 24:
                standard = float(clean.std(ddof=0))
                if standard > 1e-12:
                    premium_z = (float(premium[index]) - float(clean.mean())) / standard

        return FeatureObservation(
            observed_time_ns=observed,
            ready=True,
            flow_open_10s=value("flow_open_10s"),
            notional_open_10s_burst=value("notional_open_10s_burst"),
            flow_60s=value("flow_60s"),
            efficiency_60s=value("efficiency_60s"),
            oi_change_15m=oi_change,
            premium_z=premium_z,
        )


class Candidate35Config(StrategyConfig, frozen=True):
    btc_instrument_id: InstrumentId
    eth_instrument_id: InstrumentId
    sol_instrument_id: InstrumentId
    xrp_instrument_id: InstrumentId
    btc_bar_type: BarType
    eth_bar_type: BarType
    sol_bar_type: BarType
    xrp_bar_type: BarType
    btc_features_path: str
    eth_features_path: str
    sol_features_path: str
    xrp_features_path: str
    output_dir: str
    evaluation_start_ns: int
    evaluation_end_ns: int

    starting_nav: float = 100_000.0
    risk_fraction: float = 0.03
    all_in_cost_bps_each_side: float = 7.5
    adverse_slippage_bps_each_side: float = 2.5
    funding_reserve_bps: float = 1.0
    feature_max_age_seconds: float = 65.0
    cooldown_minutes: int = 12
    max_hold_minutes: int = 90
    funding_flatten_minute: int = 45
    funding_blackout_before_minutes: int = 25
    funding_blackout_after_minutes: int = 5

    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.75
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.12
    min_participation_ratio: float = 1.05
    min_route_score: float = 3.10
    ambiguity_score_gap: float = 0.20
    continuation_target_r: float = 2.20
    reversal_target_r: float = 1.80


class Candidate35Strategy(Strategy):
    """One continuous four-symbol account with one global execution slot."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config=config)
        if abs(config.risk_fraction - 0.03) > 1e-12:
            raise ValueError("Candidate 35 must use the project 3% NAV risk budget")
        self.instrument_ids = {
            "BTCUSDT": config.btc_instrument_id,
            "ETHUSDT": config.eth_instrument_id,
            "SOLUSDT": config.sol_instrument_id,
            "XRPUSDT": config.xrp_instrument_id,
        }
        self.bar_types = {
            "BTCUSDT": config.btc_bar_type,
            "ETHUSDT": config.eth_bar_type,
            "SOLUSDT": config.sol_bar_type,
            "XRPUSDT": config.xrp_bar_type,
        }
        self.feature_paths = {
            "BTCUSDT": Path(config.btc_features_path),
            "ETHUSDT": Path(config.eth_features_path),
            "SOLUSDT": Path(config.sol_features_path),
            "XRPUSDT": Path(config.xrp_features_path),
        }
        self.route_config = RouteConfig(
            atr_period=config.atr_period,
            min_impulse_atr_continuation=config.min_impulse_atr_continuation,
            min_impulse_atr_reversal=config.min_impulse_atr_reversal,
            min_response_atr=config.min_response_atr,
            min_participation_ratio=config.min_participation_ratio,
            min_route_score=config.min_route_score,
            ambiguity_score_gap=config.ambiguity_score_gap,
            continuation_target_r=config.continuation_target_r,
            reversal_target_r=config.reversal_target_r,
        )
        self.instruments: dict[str, Any] = {}
        self.features: dict[str, _FeatureStore] = {}
        self.bars: dict[str, deque[BarObservation]] = {
            symbol: deque(maxlen=2_000) for symbol in SYMBOLS
        }
        self._id_to_symbol = {str(value): key for key, value in self.instrument_ids.items()}
        self.bucket_ts: int | None = None
        self.bucket_symbols: set[str] = set()
        self.minute_index = -1
        self.last_entry_minute = -10**12
        self.entry_pending = False
        self.entry_pending_minute = -1
        self.current_symbol: str | None = None
        self.current_scenario: dict[str, Any] | None = None
        self.position_open_minute = -1
        self.events: list[dict[str, Any]] = []
        self.closed_scenarios: list[dict[str, Any]] = []
        self.equity: list[dict[str, float | int]] = []
        self.diagnostics: dict[str, Any] = {
            "complete_universe_minutes": 0,
            "incomplete_universe_minutes": 0,
            "quarter_hour_decisions": 0,
            "unresolved_episodes": 0,
            "entry_submissions": 0,
            "order_rejections": 0,
            "entry_expirations": 0,
            "max_simultaneous_entry_intents": 0,
            "max_open_positions_observed": 0,
            "global_position_violations": 0,
            "feature_stale_episodes": 0,
            "exchange_max_quantity_bounds": 0,
            "route_counts": {},
            "selected_symbols": {},
        }

    def on_start(self) -> None:
        destination = Path(self.config.output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for symbol in SYMBOLS:
            instrument = self.cache.instrument(self.instrument_ids[symbol])
            if instrument is None:
                raise RuntimeError(f"instrument not found: {self.instrument_ids[symbol]}")
            self.instruments[symbol] = instrument
            self.features[symbol] = _FeatureStore(self.feature_paths[symbol])
            self.subscribe_bars(self.bar_types[symbol])

    def on_bar(self, bar: Bar) -> None:
        instrument_id = str(bar.bar_type.instrument_id)
        symbol = self._id_to_symbol.get(instrument_id)
        if symbol is None:
            raise RuntimeError(f"unexpected bar instrument: {instrument_id}")
        row = BarObservation(
            ts_event=int(bar.ts_event),
            open=_as_float(bar.open),
            high=_as_float(bar.high),
            low=_as_float(bar.low),
            close=_as_float(bar.close),
            volume=_as_float(bar.volume, 0.0),
        )
        self.bars[symbol].append(row)
        ts_event = row.ts_event
        if self.bucket_ts is None:
            self.bucket_ts = ts_event
        elif ts_event != self.bucket_ts:
            if len(self.bucket_symbols) != len(SYMBOLS):
                self.diagnostics["incomplete_universe_minutes"] += 1
            self.bucket_ts = ts_event
            self.bucket_symbols.clear()
        self.bucket_symbols.add(symbol)
        if len(self.bucket_symbols) == len(SYMBOLS):
            self._on_complete_universe_minute(ts_event)
            self.bucket_ts = None
            self.bucket_symbols.clear()

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
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES")
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 15 != 2:
            return
        if any(len(self.bars[symbol]) < 64 for symbol in SYMBOLS):
            return

        features: dict[str, FeatureObservation] = {}
        for symbol in SYMBOLS:
            boundary_ts = list(self.bars[symbol])[-3].ts_event
            observation = self.features[symbol].observation(
                boundary_ts,
                self.config.feature_max_age_seconds,
            )
            features[symbol] = observation
        if not all(item.ready for item in features.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        winner, decisions = route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=features,
            config=self.route_config,
        )
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self._submit_decision(winner, ts_event)

    def _submit_decision(self, decision: RouteDecision, ts_event: int) -> None:
        symbol = decision.symbol
        instrument = self.instruments[symbol]
        side = decision.side
        entry = float(self.bars[symbol][-1].close)
        stop = float(decision.stop_reference)
        target = float(decision.objective_reference)
        if side > 0 and not (stop < entry < target):
            self._event("INVALID_BRACKET", ts_event, symbol=symbol, reason="LONG_GEOMETRY")
            return
        if side < 0 and not (target < entry < stop):
            self._event("INVALID_BRACKET", ts_event, symbol=symbol, reason="SHORT_GEOMETRY")
            return

        fee_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        funding_rate = self.config.funding_reserve_bps / 10_000.0
        adverse_entry = entry * (1.0 + side * slippage_rate)
        adverse_stop = stop * (1.0 - side * slippage_rate)
        per_unit_loss = (
            abs(adverse_entry - adverse_stop)
            + fee_rate * (abs(adverse_entry) + abs(adverse_stop))
            + funding_rate * abs(entry)
        )
        if not math.isfinite(per_unit_loss) or per_unit_loss <= 0.0:
            self._event("INVALID_RISK_GEOMETRY", ts_event, symbol=symbol)
            return
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / per_unit_loss
        quantity = _floor_quantity(raw_quantity, int(instrument.size_precision))
        exchange_max = _as_float(getattr(instrument, "max_quantity", None), math.inf)
        if math.isfinite(exchange_max) and quantity > exchange_max:
            quantity = _floor_quantity(exchange_max, int(instrument.size_precision))
            self.diagnostics["exchange_max_quantity_bounds"] += 1
        minimum_quantity = _as_float(getattr(instrument, "min_quantity", None), 0.0)
        minimum_notional = _as_float(getattr(instrument, "min_notional", None), 0.0)
        if quantity <= 0.0 or quantity + 1e-12 < minimum_quantity or quantity * entry + 1e-12 < minimum_notional:
            self._event(
                "QUANTITY_BELOW_EXCHANGE_MINIMUM",
                ts_event,
                symbol=symbol,
                quantity=quantity,
                raw_quantity=raw_quantity,
            )
            return

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.instrument_ids[symbol],
            order_side=order_side,
            quantity=instrument.make_qty(quantity),
            time_in_force=TimeInForce.GTC,
            tp_price=instrument.make_price(target),
            sl_trigger_price=instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_minute = self.minute_index
        self.last_entry_minute = self.minute_index
        self.current_symbol = symbol
        self.current_scenario = {
            "scenario_id": f"c35-{self.diagnostics['entry_submissions'] + 1:07d}",
            "symbol": symbol,
            "state": decision.state,
            "side": side,
            "score": decision.score,
            "entry_reference": entry,
            "stop": stop,
            "target": target,
            "quantity": quantity,
            "risk_budget": risk_budget,
            "planned_loss_per_unit": per_unit_loss,
            "planned_account_loss": quantity * per_unit_loss,
            "episode_ts": decision.episode_ts,
            "reasons": list(decision.reasons),
            "diagnostics": dict(decision.diagnostics),
        }
        self.diagnostics["entry_submissions"] += 1
        selected = self.diagnostics["selected_symbols"]
        selected[symbol] = int(selected.get(symbol, 0)) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._event("ENTRY_SUBMITTED", ts_event, **self.current_scenario)

    def on_position_opened(self, event: Any) -> None:
        self.entry_pending = False
        self.position_open_minute = self.minute_index
        self._event(
            "POSITION_OPENED",
            int(getattr(event, "ts_event", self._latest_ts())),
            event=str(event),
        )

    def on_position_closed(self, event: Any) -> None:
        ts_event = int(getattr(event, "ts_event", self._latest_ts()))
        record = dict(self.current_scenario or {})
        record.update(
            {
                "ts_event": ts_event,
                "realized_pnl": str(getattr(event, "realized_pnl", None)),
                "event": str(event),
            },
        )
        self.closed_scenarios.append(record)
        self._event("POSITION_CLOSED", ts_event, **record)
        self._clear_trade_state()

    def on_order_rejected(self, event: Any) -> None:
        self.diagnostics["order_rejections"] += 1
        ts_event = int(getattr(event, "ts_event", self._latest_ts()))
        self._event("ORDER_REJECTED", ts_event, event=str(event))
        if self._global_flat():
            self._clear_trade_state()

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        before_funding = moment.hour in (7, 15, 23) and moment.minute >= self.config.funding_flatten_minute
        timed_out = (
            self.position_open_minute >= 0
            and self.minute_index - self.position_open_minute >= self.config.max_hold_minutes
        )
        evaluation_ended = ts_event >= self.config.evaluation_end_ns
        if before_funding or timed_out or evaluation_ended:
            instrument_id = self.instrument_ids[self.current_symbol]
            self.cancel_all_orders(instrument_id)
            self.close_all_positions(instrument_id)
            self._event(
                "FORCED_DAYTRADE_EXIT",
                ts_event,
                before_funding=before_funding,
                timed_out=timed_out,
                evaluation_ended=evaluation_ended,
            )

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

    def _global_flat(self) -> bool:
        return all(self.portfolio.is_flat(instrument_id) for instrument_id in self.instrument_ids.values())

    def _equity_value(self) -> float:
        venue = next(iter(self.instrument_ids.values())).venue
        try:
            values = self.portfolio.equity(venue)
            for currency, money in values.items():
                if str(currency) == "USDT":
                    return _as_float(money, self.config.starting_nav)
        except Exception:
            pass
        try:
            account = self.portfolio.account(venue)
            total = account.balance_total(Currency.from_str("USDT"))
            unrealized = sum(
                _as_float(self.portfolio.unrealized_pnl(instrument_id), 0.0)
                for instrument_id in self.instrument_ids.values()
            )
            return _as_float(total, self.config.starting_nav) + unrealized
        except Exception:
            if self.equity:
                return float(self.equity[-1]["equity"])
            return self.config.starting_nav

    def _record_equity(self, ts_event: int) -> None:
        value = self._equity_value()
        if not math.isfinite(value) or value <= 0.0:
            return
        if self.equity and int(self.equity[-1]["ts_event"]) == ts_event:
            self.equity[-1]["equity"] = value
        else:
            self.equity.append({"ts_event": ts_event, "equity": value})

    def _event(self, event_type: str, ts_event: int, **details: Any) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "ts_event": ts_event,
                "observed_time_ns": ts_event,
                "symbol": self.current_symbol,
                "scenario_id": (
                    None if self.current_scenario is None else self.current_scenario.get("scenario_id")
                ),
                "details": _json_safe(details),
            },
        )

    def _latest_ts(self) -> int:
        values = [items[-1].ts_event for items in self.bars.values() if items]
        return max(values, default=0)

    def _clear_trade_state(self) -> None:
        self.entry_pending = False
        self.entry_pending_minute = -1
        self.current_symbol = None
        self.current_scenario = None
        self.position_open_minute = -1

    def on_stop(self) -> None:
        for symbol in SYMBOLS:
            instrument_id = self.instrument_ids[symbol]
            if not self.portfolio.is_flat(instrument_id):
                self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
        self._record_equity(self._latest_ts())
        destination = Path(self.config.output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        with (destination / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in self.events:
                stream.write(json.dumps(_json_safe(event), sort_keys=True, allow_nan=False, default=str) + "\n")
        (destination / "closed_scenarios.json").write_text(
            json.dumps(_json_safe(self.closed_scenarios), indent=2, sort_keys=True, allow_nan=False, default=str) + "\n",
            encoding="utf-8",
        )
        (destination / "strategy_diagnostics.json").write_text(
            json.dumps(_json_safe(self.diagnostics), indent=2, sort_keys=True, allow_nan=False, default=str) + "\n",
            encoding="utf-8",
        )
        with (destination / "equity.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["ts_event", "equity"])
            writer.writeheader()
            writer.writerows(self.equity)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
