"""Causal pending-setup and global-slot lifecycle for the v3 diagnostic."""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Iterable, Mapping

from domain_v3 import ArmedSetup, CostAssumptions, Side, TargetMode, TradePlan
from simulator_v3_types import (
    EntryCandidate,
    InstrumentSpec,
    MinuteBar,
    PendingSetup,
    Position,
    TradeRecord,
)


class PendingEngineMixin:
    def __init__(
        self,
        *,
        starting_nav: float,
        specs: dict[str, InstrumentSpec],
        costs: CostAssumptions,
        risk_fraction: float = 0.03,
        default_funding_rate: float = 0.0001,
    ) -> None:
        if not math.isfinite(starting_nav) or starting_nav <= 0.0:
            raise ValueError("starting NAV must be positive")
        if abs(risk_fraction - 0.03) > 1e-12:
            raise ValueError("candidate-easychart risk fraction is fixed at 3%")
        self.nav = float(starting_nav)
        self.starting_nav = float(starting_nav)
        self.specs = dict(specs)
        self.costs = costs
        self.risk_fraction = float(risk_fraction)
        self.default_funding_rate = float(default_funding_rate)
        self.pending: dict[str, PendingSetup] = {}
        self.seen_causal: set[str] = set()
        self.position: Position | None = None
        self.position_funding = 0.0
        self.trades: list[TradeRecord] = []
        self.equity: list[dict[str, float | int]] = []
        self.diagnostics: dict[str, int | float] = defaultdict(int)

    def add_setups(self, setups: Iterable[ArmedSetup]) -> None:
        for setup in setups:
            if setup.causal_event_id in self.seen_causal:
                self.diagnostics["duplicate_causal_rejected"] += 1
                continue
            self.seen_causal.add(setup.causal_event_id)
            self.pending[setup.setup_id] = PendingSetup(
                setup=setup,
                favorable_extreme=float(setup.initial_target),
            )
            self.diagnostics["setups_received"] += 1
            self.diagnostics[f"setups_received_{setup.family}"] += 1

    @staticmethod
    def _path(open_: float, high: float, low: float, close: float) -> list[float]:
        """Adaptive OHLC path used only for the cheap diagnostic.

        The endpoint nearer the open is visited first.  Authoritative promotion
        must use NautilusTrader and finer event data where available.
        """
        if abs(open_ - high) <= abs(open_ - low):
            return [open_, high, low, close]
        return [open_, low, high, close]

    @staticmethod
    def _crosses(a: float, b: float, level: float) -> bool:
        return min(a, b) <= level <= max(a, b)

    @staticmethod
    def _distance_order(a: float, b: float, levels: Mapping[str, float]) -> list[tuple[str, float]]:
        crossed = [
            (name, abs(level - a))
            for name, level in levels.items()
            if min(a, b) <= level <= max(a, b)
        ]
        return sorted(crossed, key=lambda item: (item[1], item[0]))

    def _update_extreme_to_point(self, pending: PendingSetup, point: float) -> None:
        if pending.setup.target_mode is not TargetMode.IMPULSE_EXTREME:
            return
        if pending.setup.side is Side.LONG:
            pending.favorable_extreme = max(pending.favorable_extreme, point)
        else:
            pending.favorable_extreme = min(pending.favorable_extreme, point)

    def _plan_at_entry(self, pending: PendingSetup) -> TradePlan | None:
        setup = pending.setup
        if setup.target_mode is TargetMode.IMPULSE_EXTREME:
            target = pending.favorable_extreme
            target_id = f"IMPULSE_EXTREME@{target:.12g}"
        else:
            target = setup.initial_target
            target_id = setup.fixed_target_id
        return setup.executable(target, target_id=target_id, min_gross_rr=1.0)

    def _scan_pending_path(
        self,
        pending: PendingSetup,
        path: list[float],
    ) -> tuple[str, TradePlan | None, tuple[float, ...], bool, int] | None:
        """Scan one bar causally and stop at the first decisive setup event."""
        setup = pending.setup
        open_price = path[0]

        # A resting limit crossed at the open is immediately executable if the
        # structural stop has not already failed.  The planned entry remains the
        # limit price; the screen does not manufacture price improvement.
        if setup.side is Side.LONG:
            if open_price <= setup.stop:
                return ("STOP_INVALID", None, tuple(), False, -1)
            entered_at_open = open_price <= setup.entry
        else:
            if open_price >= setup.stop:
                return ("STOP_INVALID", None, tuple(), False, -1)
            entered_at_open = open_price >= setup.entry

        if setup.target_mode is TargetMode.FIXED_STRUCTURE:
            if setup.side is Side.LONG and open_price >= setup.initial_target:
                return ("TARGET_CONSUMED", None, tuple(), False, -1)
            if setup.side is Side.SHORT and open_price <= setup.initial_target:
                return ("TARGET_CONSUMED", None, tuple(), False, -1)

        if entered_at_open:
            plan = self._plan_at_entry(pending)
            if plan is None:
                return ("RR_LT_1_AT_ENTRY", None, tuple(), True, -1)
            return ("ENTRY", plan, tuple(path), True, -1)

        self._update_extreme_to_point(pending, open_price)
        for idx in range(len(path) - 1):
            a, b = path[idx], path[idx + 1]
            levels = {"ENTRY": setup.entry, "STOP_INVALID": setup.stop}
            if setup.target_mode is TargetMode.FIXED_STRUCTURE:
                levels["TARGET_CONSUMED"] = setup.initial_target
            events = self._distance_order(a, b, levels)
            if not events:
                self._update_extreme_to_point(pending, b)
                continue

            name, _ = events[0]
            event_level = levels[name]
            # The favorable excursion can grow only up to the actual first
            # event point.  An endpoint after ENTRY is never included.
            self._update_extreme_to_point(pending, event_level)
            if name != "ENTRY":
                return (name, None, tuple(), False, idx)
            plan = self._plan_at_entry(pending)
            if plan is None:
                return ("RR_LT_1_AT_ENTRY", None, tuple(), False, idx)
            after = (setup.entry, *path[idx + 1 :])
            return ("ENTRY", plan, tuple(after), False, idx)
        return None

    def on_timestamp(self, bars: Mapping[str, MinuteBar]) -> None:
        if not bars:
            return
        close_times = {bar.ts_close_ns for bar in bars.values()}
        if len(close_times) != 1:
            raise ValueError("timestamp batch contains different close times")
        close_ns = next(iter(close_times))
        paths = {symbol: self._path(bar.open, bar.high, bar.low, bar.close) for symbol, bar in bars.items()}

        occupied_at_open = self.position is not None
        occupied_symbol = self.position.plan.symbol if self.position is not None else None
        if occupied_symbol is not None and occupied_symbol in bars:
            self._process_position(paths[occupied_symbol], close_ns)

        candidates: list[EntryCandidate] = []
        for setup_id, pending in list(self.pending.items()):
            setup = pending.setup
            bar = bars.get(setup.symbol)
            if bar is None or setup.observed_time_ns >= bar.ts_open_ns:
                continue
            result = self._scan_pending_path(pending, paths[setup.symbol])
            if result is None:
                continue
            event, plan, path_after_entry, entered_at_open, segment = result
            if event != "ENTRY":
                self.pending.pop(setup_id, None)
                self.diagnostics[event.lower()] += 1
                self.diagnostics[f"{event.lower()}_{setup.family}"] += 1
                continue

            self.pending.pop(setup_id, None)  # first retest is always consumed
            if occupied_at_open:
                self.diagnostics["first_retest_missed_global_slot_busy"] += 1
                self.diagnostics[f"first_retest_missed_global_slot_busy_{setup.family}"] += 1
                continue
            assert plan is not None
            candidates.append(
                EntryCandidate(
                    plan=plan,
                    path_after_entry=path_after_entry,
                    bar=bar,
                    entered_at_open=entered_at_open,
                    path_segment=segment,
                ),
            )

        if not occupied_at_open and candidates:
            # Same-timestamp cross-symbol sub-minute ordering is unavailable.
            # Resolve deterministically by earliest setup, then better executable
            # gross RR, without using future trade outcome.
            candidates.sort(
                key=lambda item: (
                    item.plan.observed_time_ns,
                    -item.plan.gross_rr,
                    item.plan.symbol,
                    item.plan.plan_id,
                ),
            )
            chosen = candidates[0]
            if len(candidates) > 1:
                self.diagnostics["simultaneous_entry_conflicts"] += len(candidates) - 1
            self._enter(chosen.plan, chosen.bar.ts_open_ns, close_ns, list(chosen.path_after_entry))

        if self.position is not None:
            symbol = self.position.plan.symbol
            bar = bars.get(symbol)
            if bar is not None:
                self._apply_funding(symbol, close_ns, bar.close, bar.funding_rate)

        closes = {symbol: bar.close for symbol, bar in bars.items()}
        self.equity.append({"ts_event": close_ns, "equity": self._marked_equity(closes)})

    def on_minute(
        self,
        *,
        symbol: str,
        ts_open_ns: int,
        ts_close_ns: int,
        open_: float,
        high: float,
        low: float,
        close: float,
        funding_rate: float | None = None,
    ) -> None:
        self.on_timestamp(
            {
                symbol: MinuteBar(
                    symbol=symbol,
                    ts_open_ns=ts_open_ns,
                    ts_close_ns=ts_close_ns,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    funding_rate=funding_rate,
                ),
            },
        )
