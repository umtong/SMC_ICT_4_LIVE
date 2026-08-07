"""Bridge frozen Candidate 12 I7 plans into Candidate 14's shared portfolio.

The source engine remains byte-identical under :mod:`session_auction_i7`.  This
module only converts its completed plans, reconstructs the causal observation
window needed by Candidate 14's already-frozen four-market semantic gate, and
routes lifecycle events back to the original engine.

Causal windows are scenario identities, not fitted lookbacks:

* failed session auction: external raid close -> completed confirmation;
* first accepted auction: bullish FVG formation -> defended retest decision;
* fresh reacceptance: prior accepted-auction failure back inside -> fresh FVG
  reacceptance decision.

If a causal start cannot be reconstructed from information already observed by
the I7 engine, the plan carries ``-1`` and the market gate fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

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
DAY_NS = 86_400_000_000_000

REVERSAL_SCENARIOS = frozenset(
    {
        "ASIA_HIGH_REJECTION",
        "ASIA_LOW_REJECTION",
        "LONDON_HIGH_REJECTION",
        "LONDON_LOW_REJECTION",
    },
)
CONTINUATION_SCENARIOS = frozenset(
    {
        "ASIA_HIGH_ACCEPTANCE",
        "ASIA_HIGH_REACCEPTANCE",
        "LONDON_HIGH_ACCEPTANCE",
    },
)


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


def session_market_semantic(plan: SessionTradePlan) -> str:
    """Map an I7 scenario to the existing FAR/AAC economic state machine."""
    scenario = plan.scenario.value
    if scenario in REVERSAL_SCENARIOS:
        return "FAR"
    if scenario in CONTINUATION_SCENARIOS:
        return "AAC"
    return "UNSUPPORTED"


def _event_value(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def session_causal_start_ns(
    plan: SessionTradePlan,
    events: Iterable[Any] = (),
) -> int:
    """Return the earliest already-observed event proving this exact plan.

    Reacceptance plan details intentionally do not contain the earlier failure
    timestamp.  The unchanged source engine does record that event, so this
    bridge retrieves the most recent same-day, same-source failure which
    causally precedes the plan.  No future event or arbitrary time window is
    used.
    """
    details = plan.details
    scenario = plan.scenario.value
    observed = int(plan.observed_ts_ns)

    if scenario in REVERSAL_SCENARIOS:
        value = details.get("sweep_ts_ns")
        return int(value) if value is not None else -1

    if scenario == "ASIA_HIGH_REACCEPTANCE":
        source = str(details.get("source", ""))
        day = (observed - 1) // DAY_NS
        for event in reversed(tuple(events)):
            event_type = str(_event_value(event, "event_type", ""))
            event_ts = int(_event_value(event, "observed_time_ns", -1))
            event_details = _event_value(event, "details", {}) or {}
            if event_type != "HIGH_ACCEPTANCE_FAILED_BACK_INSIDE":
                continue
            if event_ts < 0 or event_ts >= observed:
                continue
            if (event_ts - 1) // DAY_NS != day:
                continue
            if str(event_details.get("source", "")) != source:
                continue
            return event_ts
        return -1

    if scenario in CONTINUATION_SCENARIOS:
        value = details.get("fvg_formed_ts_ns")
        return int(value) if value is not None else -1

    return -1


def adapt_session_plan(
    plan: SessionTradePlan,
    *,
    logic_key: str = SESSION_LOGIC_KEY,
    causal_start_ts_ns: int | None = None,
) -> SessionPortfolioPlan:
    """Losslessly adapt one completed I7 plan for shared arbitration."""
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
    if causal_start_ts_ns is None:
        causal_start_ts_ns = session_causal_start_ns(plan)
    semantic = session_market_semantic(plan)
    details = dict(plan.details)
    details.update(
        {
            "_logic_key": logic_key,
            "module": SESSION_MODULE,
            "session_source_commit": SESSION_SOURCE_COMMIT,
            "session_entry_order": plan.entry_order.value,
            "entry_cost_assumption": "TAKER",
            "entry_post_only": False,
            "market_semantic_scenario": semantic,
            "causal_start_ts_ns": int(causal_start_ts_ns),
            "causal_confirmation_ts_ns": int(plan.observed_ts_ns),
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
        reason_code=f"SESSION_I7_{plan.scenario.value}_{semantic}",
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
        causal_start = session_causal_start_ns(original, self.engine.events)
        return adapt_session_plan(
            original,
            logic_key=self.logic_key,
            causal_start_ts_ns=causal_start,
        )

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
                "market_semantic_scenario": plan.details.get("market_semantic_scenario"),
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
