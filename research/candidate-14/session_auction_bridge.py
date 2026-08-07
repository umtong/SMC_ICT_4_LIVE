"""Bridge Candidate 12 I7's frozen session auction into Candidate 14.

The source engine is kept under :mod:`session_auction_i7` without reimplementing
its conditions.  This bridge only converts its completed plans to the common
portfolio plan protocol and routes lifecycle events back to the original engine.
NautilusTrader and Candidate 14's single global mutex remain the sole execution
and account authorities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from logic import BarObs as PortfolioBarObs
from logic import Direction as PortfolioDirection
from semantic_execution import MARKET_ENTRY_SENTINEL_NS
from session_auction_i7 import BarObs as SessionBarObs
from session_auction_i7 import CausalLiquidityAuctionEngine
from session_auction_i7 import Direction as SessionDirection
from session_auction_i7 import EntryOrder
from session_auction_i7 import LogicConfig
from session_auction_i7 import TradePlan as SessionTradePlan


SESSION_LOGIC_KEY = "BTCUSDT::SESSION_I7"
SESSION_MODULE = "SESSION_I7"
SESSION_SOURCE_COMMIT = "036c0e8302c3826aa293f6037405a84fc7118ae8"


@dataclass(frozen=True, slots=True)
class SessionPortfolioPlan:
    """Structural plan shape consumed by the shared Candidate 14 portfolio."""

    scenario_id: str
    scenario: Any
    direction: PortfolioDirection
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    atr: float
    loss_per_unit: float
    gain_per_unit: float
    net_r: float
    reason_code: str
    expire_ts_ns: int
    entry_order_type: str
    entry_post_only: bool
    details: dict[str, Any] = field(default_factory=dict)


def adapt_session_plan(
    plan: SessionTradePlan,
    *,
    logic_key: str = SESSION_LOGIC_KEY,
) -> SessionPortfolioPlan:
    """Losslessly adapt one already-completed I7 plan for shared arbitration."""
    direction = (
        PortfolioDirection.LONG
        if plan.direction is SessionDirection.LONG
        else PortfolioDirection.SHORT
    )
    entry_order_type = "MARKET" if plan.entry_order is EntryOrder.MARKET else "LIMIT"
    expire_ts_ns = (
        MARKET_ENTRY_SENTINEL_NS
        if plan.expire_ts_ns is None
        else int(plan.expire_ts_ns)
    )
    details = dict(plan.details)
    details.update(
        {
            "_logic_key": logic_key,
            "module": SESSION_MODULE,
            "session_source_commit": SESSION_SOURCE_COMMIT,
            "session_entry_order": plan.entry_order.value,
            "entry_cost_assumption": "TAKER",
            "entry_post_only": False,
        },
    )
    return SessionPortfolioPlan(
        scenario_id=plan.scenario_id,
        scenario=plan.scenario,
        direction=direction,
        observed_ts_ns=int(plan.observed_ts_ns),
        expected_entry=float(plan.expected_entry),
        stop_price=float(plan.stop_price),
        target_price=float(plan.target_price),
        atr=float(details.get("decision_atr", 0.0)),
        loss_per_unit=float(plan.loss_per_unit),
        gain_per_unit=float(plan.expected_profit_per_unit),
        net_r=float(plan.net_r),
        reason_code=f"SESSION_I7_{plan.scenario.value}",
        expire_ts_ns=expire_ts_ns,
        entry_order_type=entry_order_type,
        # I7's one-bar protected FVG limit is intentionally marketable and its
        # loss budget already reserves taker entry cost and two ticks slippage.
        entry_post_only=False,
        details=details,
    )


class SessionAuctionBridge:
    """Expose the frozen I7 engine through Candidate 14's lifecycle protocol."""

    def __init__(
        self,
        config: LogicConfig,
        instrument_id: str,
        *,
        logic_key: str = SESSION_LOGIC_KEY,
    ) -> None:
        self.logic_key = logic_key
        self.engine = CausalLiquidityAuctionEngine(config, instrument_id)
        self._originals: dict[str, SessionTradePlan] = {}
        self._active_original: SessionTradePlan | None = None

    @property
    def events(self) -> list[Any]:
        return self.engine.events

    @property
    def skips(self) -> Any:
        return self.engine.skips

    def on_bar(
        self,
        observation: PortfolioBarObs,
        *,
        allow_entry: bool = True,
    ) -> SessionPortfolioPlan | None:
        session_observation = SessionBarObs(
            ts_ns=int(observation.ts_ns),
            open=float(observation.open),
            high=float(observation.high),
            low=float(observation.low),
            close=float(observation.close),
            volume=float(observation.volume),
            taker_buy_volume=float(observation.taker_buy_volume),
        )
        original = self.engine.on_bar(session_observation, allow_entry=allow_entry)
        if original is None:
            return None
        self._originals[original.scenario_id] = original
        return adapt_session_plan(original, logic_key=self.logic_key)

    @staticmethod
    def _ts(value: Any) -> int:
        return int(getattr(value, "ts_ns", value))

    def _original(self, plan: SessionPortfolioPlan) -> SessionTradePlan:
        try:
            return self._originals[plan.scenario_id]
        except KeyError as exc:
            raise RuntimeError(
                f"missing original I7 plan for {plan.scenario_id}",
            ) from exc

    def mark_rejected(
        self,
        plan: SessionPortfolioPlan,
        ts_or_bar: Any,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        original = self._original(plan)
        self.engine.mark_plan_rejected(
            original,
            self._ts(ts_or_bar),
            reason,
            details,
        )

    def mark_submitted(
        self,
        plan: SessionPortfolioPlan,
        quantity: Any,
        details: dict[str, Any],
    ) -> None:
        original = self._original(plan)
        payload = dict(details)
        payload.update(
            {
                "quantity": str(quantity),
                "module": SESSION_MODULE,
                "entry_order_type": plan.entry_order_type,
                "entry_post_only": plan.entry_post_only,
            },
        )
        self.engine.mark_plan_submitted(original, plan.observed_ts_ns, payload)
        self._active_original = original

    def mark_entry_filled(self, ts_ns: int, details: dict[str, Any]) -> None:
        # Candidate 12's original evidence contract transitions directly from
        # SUBMITTED to terminal. Candidate 14 records the fill in the common
        # Nautilus order lifecycle, so no synthetic detector event is inserted.
        del ts_ns, details

    def mark_trade_terminal(self, ts_ns: int, reason: str) -> None:
        if self._active_original is None:
            return
        self.engine.mark_trade_terminal(
            self._active_original,
            int(ts_ns),
            reason,
            {"module": SESSION_MODULE},
        )
        self._active_original = None
