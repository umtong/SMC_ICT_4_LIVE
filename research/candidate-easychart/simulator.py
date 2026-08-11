"""Fast, non-authoritative four-symbol continuous-account diagnostic.

The simulator exists only to reject weak scenario logic before an expensive
NautilusTrader run.  It deliberately implements the user's fixed contract and
nothing more: one full-fill entry, one full-position STOP_MARKET, one fixed
full-position target, current-NAV 3% planned loss, no cooldown, no daily stop,
and one global entry/position slot.

Cross-symbol bars sharing a timestamp are processed as one batch.  This avoids
letting an arbitrary symbol sort order choose the winning trade.  A setup whose
first retest occurs while the global slot is occupied is consumed rather than
being allowed to enter on a later touch.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import math
from typing import Iterable, Mapping

from domain import CostAssumptions, Side, TradePlan, size_for_fixed_risk


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    symbol: str
    tick_size: float
    size_increment: str
    min_quantity: float
    min_notional: float


@dataclass(frozen=True, slots=True)
class MinuteBar:
    symbol: str
    ts_open_ns: int
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    funding_rate: float | None = None


@dataclass(slots=True)
class PendingPlan:
    plan: TradePlan
    active: bool = True


@dataclass(slots=True)
class Position:
    plan: TradePlan
    quantity: float
    entry_time_ns: int
    nav_before: float
    entry_fee: float
    planned_account_loss: float


@dataclass(frozen=True, slots=True)
class TradeRecord:
    plan_id: str
    causal_event_id: str
    symbol: str
    family: str
    side: int
    signal_time_ns: int
    entry_time_ns: int
    exit_time_ns: int
    entry: float
    exit: float
    stop: float
    target: float
    gross_rr: float
    quantity: float
    outcome: str
    gross_pnl: float
    fees: float
    slippage: float
    funding: float
    net_pnl: float
    nav_before: float
    nav_after: float
    planned_account_loss: float
    hold_minutes: int


class ContinuousAccountSimulator:
    def __init__(
        self,
        *,
        starting_nav: float,
        specs: dict[str, InstrumentSpec],
        costs: CostAssumptions,
        leverage: float = 20.0,
        risk_fraction: float = 0.03,
        default_funding_rate: float = 0.0001,
    ) -> None:
        if starting_nav <= 0.0:
            raise ValueError("starting NAV must be positive")
        if abs(risk_fraction - 0.03) > 1e-12:
            raise ValueError("candidate-easychart risk fraction is fixed at 3%")
        self.nav = float(starting_nav)
        self.starting_nav = float(starting_nav)
        self.specs = dict(specs)
        self.costs = costs
        self.leverage = float(leverage)
        self.risk_fraction = float(risk_fraction)
        self.default_funding_rate = float(default_funding_rate)
        self.pending: dict[str, PendingPlan] = {}
        self.position: Position | None = None
        self.position_funding = 0.0
        self.trades: list[TradeRecord] = []
        self.equity: list[dict[str, float | int]] = []
        self.diagnostics: dict[str, int | float] = defaultdict(int)
        self.seen_causal: set[str] = set()

    def add_plans(self, plans: Iterable[TradePlan]) -> None:
        for plan in plans:
            if plan.causal_event_id in self.seen_causal:
                self.diagnostics["duplicate_causal_rejected"] += 1
                continue
            self.pending[plan.plan_id] = PendingPlan(plan)
            self.seen_causal.add(plan.causal_event_id)
            self.diagnostics["plans_received"] += 1

    @staticmethod
    def _path(open_: float, high: float, low: float, close: float) -> list[float]:
        """Adaptive OHLC path used only for cheap bar-order diagnostics."""
        if abs(open_ - high) <= abs(open_ - low):
            return [open_, high, low, close]
        return [open_, low, high, close]

    @staticmethod
    def _segment_crosses(a: float, b: float, level: float) -> bool:
        return min(a, b) <= level <= max(a, b)

    def _first_event(self, path: list[float], levels: Mapping[str, float]) -> tuple[str, int] | None:
        for idx in range(len(path) - 1):
            a, b = path[idx], path[idx + 1]
            crossed = [
                (name, abs(level - a))
                for name, level in levels.items()
                if self._segment_crosses(a, b, level)
            ]
            if crossed:
                crossed.sort(key=lambda item: (item[1], item[0]))
                return crossed[0][0], idx
        return None

    @staticmethod
    def _gap_event(plan: TradePlan, open_price: float) -> str | None:
        """Classify an already-consumed setup at the first tradable print."""
        if plan.side is Side.LONG:
            if open_price <= plan.stop:
                return "STOP_INVALID"
            if open_price >= plan.target:
                return "TARGET_CONSUMED"
        else:
            if open_price >= plan.stop:
                return "STOP_INVALID"
            if open_price <= plan.target:
                return "TARGET_CONSUMED"
        return None

    def on_timestamp(self, bars: Mapping[str, MinuteBar]) -> None:
        """Process all symbol bars sharing one close timestamp as one batch."""
        if not bars:
            return
        close_times = {bar.ts_close_ns for bar in bars.values()}
        if len(close_times) != 1:
            raise ValueError("timestamp batch contains different close times")
        close_ns = next(iter(close_times))
        paths = {
            symbol: self._path(bar.open, bar.high, bar.low, bar.close)
            for symbol, bar in bars.items()
        }

        occupied_at_open = self.position is not None
        occupied_symbol = self.position.plan.symbol if self.position is not None else None

        # Existing position is resolved first on its own market path.  We do not
        # infer a second cross-symbol trade from an unknowable sub-minute order.
        if occupied_symbol is not None and occupied_symbol in bars:
            self._process_position(paths[occupied_symbol], close_ns)

        candidates: list[tuple[TradePlan, list[float], MinuteBar]] = []
        for plan_id, pending in list(self.pending.items()):
            plan = pending.plan
            bar = bars.get(plan.symbol)
            if bar is None or not pending.active or plan.observed_time_ns >= bar.ts_open_ns:
                continue
            path = paths[plan.symbol]
            gap = self._gap_event(plan, bar.open)
            if gap is not None:
                self.pending.pop(plan_id, None)
                self.diagnostics[gap.lower()] += 1
                continue
            event = self._first_event(
                path,
                {
                    "ENTRY": plan.entry,
                    "STOP_INVALID": plan.stop,
                    "TARGET_CONSUMED": plan.target,
                },
            )
            if event is None:
                continue
            name, _ = event
            if name != "ENTRY":
                self.pending.pop(plan_id, None)
                self.diagnostics[name.lower()] += 1
                continue
            # First retest is consumed even if another position blocks execution.
            if occupied_at_open:
                self.pending.pop(plan_id, None)
                self.diagnostics["first_retest_missed_global_slot_busy"] += 1
                continue
            candidates.append((plan, path, bar))

        if not occupied_at_open and candidates:
            candidates.sort(
                key=lambda item: (
                    item[0].observed_time_ns,
                    -item[0].gross_rr,
                    item[0].symbol,
                    item[0].plan_id,
                ),
            )
            chosen, chosen_path, chosen_bar = candidates[0]
            for plan, _, _ in candidates:
                self.pending.pop(plan.plan_id, None)
            if len(candidates) > 1:
                self.diagnostics["simultaneous_entry_conflicts"] += len(candidates) - 1
            self._enter(chosen, chosen_bar.ts_open_ns, close_ns, chosen_path)

        # Charge funding only when the position remains open at the settlement
        # boundary.  Actual funding rates can be supplied per symbol.
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
        """Single-symbol compatibility wrapper used by focused unit tests."""
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

    def _enter(self, plan: TradePlan, ts_open_ns: int, ts_close_ns: int, path: list[float]) -> None:
        spec = self.specs[plan.symbol]
        quantity, _, planned = size_for_fixed_risk(
            nav=self._marked_equity({plan.symbol: plan.entry}),
            risk_fraction=self.risk_fraction,
            plan=plan,
            costs=self.costs,
            size_increment=spec.size_increment,
        )
        if quantity < spec.min_quantity or quantity * plan.entry < spec.min_notional:
            self.diagnostics["minimum_size_rejected"] += 1
            return
        notional = quantity * plan.entry
        # This is a real venue-margin feasibility check, not an alpha throttle.
        if notional > self.nav * self.leverage + 1e-9:
            self.diagnostics["margin_rejected"] += 1
            return
        entry_fee = notional * self.costs.entry_fee_bps / 10_000.0
        entry_slippage = notional * self.costs.entry_slippage_bps / 10_000.0
        nav_before = self.nav
        self.nav -= entry_fee + entry_slippage
        self.position = Position(
            plan=plan,
            quantity=quantity,
            entry_time_ns=ts_open_ns,
            nav_before=nav_before,
            entry_fee=entry_fee + entry_slippage,
            planned_account_loss=planned,
        )
        self.position_funding = 0.0
        self.diagnostics["entries"] += 1
        self._process_position_after_entry(path, ts_close_ns, plan.entry)

    def _path_after_level(self, path: list[float], level: float) -> list[float]:
        for idx in range(len(path) - 1):
            if self._segment_crosses(path[idx], path[idx + 1], level):
                return [level] + path[idx + 1 :]
        return [path[-1]]

    def _process_position_after_entry(self, path: list[float], ts_close_ns: int, entry_level: float) -> None:
        self._process_position(self._path_after_level(path, entry_level), ts_close_ns)

    def _process_position(self, path: list[float], ts_close_ns: int) -> None:
        position = self.position
        if position is None:
            return
        plan = position.plan
        open_price = path[0]
        if plan.side is Side.LONG and open_price <= plan.stop:
            self._exit("STOP", ts_close_ns, gap_price=open_price)
            return
        if plan.side is Side.SHORT and open_price >= plan.stop:
            self._exit("STOP", ts_close_ns, gap_price=open_price)
            return
        event = self._first_event(path, {"STOP": plan.stop, "TARGET": plan.target})
        if event is not None:
            self._exit(event[0], ts_close_ns)

    def _apply_funding(self, symbol: str, ts_close_ns: int, close: float, funding_rate: float | None) -> None:
        position = self.position
        if position is None or position.plan.symbol != symbol:
            return
        seconds = ts_close_ns // 1_000_000_000
        minute = (seconds // 60) % (24 * 60)
        if minute not in {0, 8 * 60, 16 * 60}:
            return
        rate = self.default_funding_rate if funding_rate is None else float(funding_rate)
        # The cheap screen uses the adverse sign.  Native funding is required
        # before promotion, so favorable funding never manufactures alpha here.
        charge = position.quantity * close * abs(rate)
        self.nav -= charge
        self.position_funding += charge
        self.diagnostics["funding_settlements"] += 1

    def _exit(self, outcome: str, ts_exit_ns: int, *, gap_price: float | None = None) -> None:
        position = self.position
        if position is None:
            return
        plan = position.plan
        qty = position.quantity
        if outcome == "STOP":
            adverse = self.costs.stop_slippage_bps / 10_000.0
            trigger_or_gap = plan.stop if gap_price is None else gap_price
            exit_price = trigger_or_gap * (1.0 - adverse if plan.side is Side.LONG else 1.0 + adverse)
            exit_fee = qty * exit_price * self.costs.stop_fee_bps / 10_000.0
            slippage = qty * abs(exit_price - plan.stop)
        else:
            adverse = self.costs.target_slippage_bps / 10_000.0
            exit_price = plan.target * (1.0 - adverse if plan.side is Side.LONG else 1.0 + adverse)
            exit_fee = qty * exit_price * self.costs.target_fee_bps / 10_000.0
            slippage = qty * abs(exit_price - plan.target)
        gross = qty * (exit_price - plan.entry) * int(plan.side)
        fees = position.entry_fee + exit_fee
        nav_before = position.nav_before
        self.nav += gross - exit_fee
        hold_minutes = max(0, int((ts_exit_ns - position.entry_time_ns) / 60_000_000_000))
        self.trades.append(
            TradeRecord(
                plan_id=plan.plan_id,
                causal_event_id=plan.causal_event_id,
                symbol=plan.symbol,
                family=plan.family,
                side=int(plan.side),
                signal_time_ns=plan.observed_time_ns,
                entry_time_ns=position.entry_time_ns,
                exit_time_ns=ts_exit_ns,
                entry=plan.entry,
                exit=exit_price,
                stop=plan.stop,
                target=plan.target,
                gross_rr=plan.gross_rr,
                quantity=qty,
                outcome=outcome,
                gross_pnl=gross,
                fees=fees,
                slippage=slippage,
                funding=self.position_funding,
                net_pnl=self.nav - nav_before,
                nav_before=nav_before,
                nav_after=self.nav,
                planned_account_loss=position.planned_account_loss,
                hold_minutes=hold_minutes,
            ),
        )
        self.position = None
        self.position_funding = 0.0
        self.diagnostics[f"exits_{outcome.lower()}"] += 1

    def _marked_equity(self, closes: Mapping[str, float]) -> float:
        position = self.position
        if position is None:
            return self.nav
        close = closes.get(position.plan.symbol)
        if close is None:
            return self.nav
        unrealized = position.quantity * (float(close) - position.plan.entry) * int(position.plan.side)
        return self.nav + unrealized

    def metrics(self, calendar_days: int) -> dict[str, object]:
        if calendar_days <= 0:
            raise ValueError("calendar_days must be positive")
        pnls = [trade.net_pnl for trade in self.trades]
        wins = [value for value in pnls if value > 0.0]
        losses = [value for value in pnls if value < 0.0]
        marked = [self.starting_nav] + [float(item["equity"]) for item in self.equity]
        peak = -math.inf
        max_drawdown = 0.0
        min_equity = math.inf
        for value in marked:
            peak = max(peak, value)
            min_equity = min(min_equity, value)
            if peak > 0.0:
                max_drawdown = max(max_drawdown, 1.0 - value / peak)
        final_equity = float(marked[-1]) if marked else self.nav
        geometric_daily = (
            (final_equity / self.starting_nav) ** (1.0 / calendar_days) - 1.0
            if final_equity > 0.0
            else -1.0
        )
        gross_profit = sum(wins)
        gross_loss = -sum(losses)
        family: dict[str, dict[str, float | int]] = {}
        symbol: dict[str, dict[str, float | int]] = {}
        for trade in self.trades:
            for key, container in ((trade.family, family), (trade.symbol, symbol)):
                bucket = container.setdefault(key, {"trades": 0, "wins": 0, "net_pnl": 0.0})
                bucket["trades"] = int(bucket["trades"]) + 1
                bucket["wins"] = int(bucket["wins"]) + int(trade.net_pnl > 0.0)
                bucket["net_pnl"] = float(bucket["net_pnl"]) + trade.net_pnl
        return {
            "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE",
            "starting_nav": self.starting_nav,
            "ending_nav": final_equity,
            "cash_nav": self.nav,
            "open_position_at_end": self.position is not None,
            "total_return": final_equity / self.starting_nav - 1.0,
            "geometric_daily_growth": geometric_daily,
            "calendar_days": calendar_days,
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(self.trades) if self.trades else 0.0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
            "expectancy_usdt": sum(pnls) / len(pnls) if pnls else 0.0,
            "max_drawdown": max_drawdown,
            "min_equity": min_equity,
            "largest_winner_share": max(wins, default=0.0) / gross_profit if gross_profit > 0.0 else 1.0,
            "family_metrics": family,
            "symbol_metrics": symbol,
            "diagnostics": dict(self.diagnostics),
        }

    def trade_rows(self) -> list[dict[str, object]]:
        return [asdict(trade) for trade in self.trades]
