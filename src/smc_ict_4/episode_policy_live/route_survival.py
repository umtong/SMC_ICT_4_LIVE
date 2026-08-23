"""Causal route-outcome survival and economic scoring.

This module deliberately does not choose or execute orders.  It freezes every
post-cascade proposal (selected and unselected alike), follows the proposal's
natural structural lifetime, and learns only from outcomes which have become
observable.  In particular, ``TradePlan.expires_time_ns`` is not used: pending
routes end only on intrinsic invalidation or explicit right-censoring.

The two Bernoulli quantities are kept separate:

``q``
    Probability that a resting route fills before intrinsic invalidation.
``p``
    Probability of target-first conditional on a fill.

Raw counts are stored for every ordered context prefix.  Prediction recursively
backs each child Beta posterior with its parent rather than imposing sample
thresholds or a hard win-rate rule.  The cold conditional target prior is the
route's exact cost-after log-growth break-even probability, which makes an
unknown route economically equal to NULL instead of optimistically tradable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from enum import Enum
from hashlib import sha256
import json
import math
from typing import Any, Mapping

from .domain import Bar, DEFAULT_CONTRACTS, TradePlan


STATE_VERSION = 1
DEFAULT_RISK_FRACTION = Decimal("0.03")
DEFAULT_MAKER_FEE = Decimal("0.0002")
DEFAULT_TAKER_FEE = Decimal("0.0005")
DEFAULT_STOP_SLIPPAGE_TICKS = 2
DEFAULT_STOP_SLIPPAGE_BPS = Decimal("1")


class RouteIntegrityError(RuntimeError):
    """A duplicate, mutation, non-causal observation, or corrupt restore."""


class RouteState(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    TARGET_FIRST = "TARGET_FIRST"
    STOP_FIRST = "STOP_FIRST"
    NO_FILL = "NO_FILL"
    RIGHT_CENSORED = "RIGHT_CENSORED"

    @property
    def terminal(self) -> bool:
        return self in {
            RouteState.TARGET_FIRST,
            RouteState.STOP_FIRST,
            RouteState.NO_FILL,
            RouteState.RIGHT_CENSORED,
        }


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _digest(payload: Any) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _finite(name: str, value: float) -> float:
    output = float(value)
    if not math.isfinite(output):
        raise ValueError(f"{name} must be finite")
    return output


def _rounded(value: Decimal, tick: Decimal, rounding: str) -> Decimal:
    return (value / tick).to_integral_value(rounding=rounding) * tick


@dataclass(frozen=True, slots=True)
class RouteEconomics:
    """Hashable native-price economic assumptions and two terminal returns."""

    entry: float
    stop_trigger: float
    adverse_stop_fill: float
    target: float
    tick_size: float
    gross_rr: float
    risk_fraction: float
    entry_fee_rate: float
    target_fee_rate: float
    stop_fee_rate: float
    stop_slippage_ticks: int
    stop_slippage_bps: float
    target_nav_return: float
    stop_nav_return: float
    target_log_growth: float
    stop_log_growth: float
    conditional_break_even_p: float
    assumptions_id: str


def native_route_economics(
    plan: TradePlan,
    *,
    tick_size: Decimal | float | str | None = None,
    risk_fraction: Decimal | float | str = DEFAULT_RISK_FRACTION,
    entry_fee_rate: Decimal | float | str = DEFAULT_TAKER_FEE,
    target_fee_rate: Decimal | float | str = DEFAULT_MAKER_FEE,
    stop_fee_rate: Decimal | float | str = DEFAULT_TAKER_FEE,
    stop_slippage_ticks: int = DEFAULT_STOP_SLIPPAGE_TICKS,
    stop_slippage_bps: Decimal | float | str = DEFAULT_STOP_SLIPPAGE_BPS,
) -> RouteEconomics:
    """Return exact cost-after wealth changes under structural 3% sizing.

    Quantity is conceptually ``NAV * risk_fraction / abs(entry-stop)``.  Fees
    and adverse stop execution are additional costs; they are not hidden by
    shrinking quantity.  Entry is conservatively taker, target is maker, and
    stop is taker by default, matching the native order roles.
    """

    tick = Decimal(str(
        DEFAULT_CONTRACTS[plan.symbol].tick_size if tick_size is None else tick_size,
    ))
    risk = Decimal(str(risk_fraction))
    entry_fee = Decimal(str(entry_fee_rate))
    target_fee = Decimal(str(target_fee_rate))
    stop_fee = Decimal(str(stop_fee_rate))
    slip_bps = Decimal(str(stop_slippage_bps))
    if tick <= 0 or risk <= 0 or risk >= 1:
        raise ValueError("tick_size and risk_fraction must be valid and positive")
    if min(entry_fee, target_fee, stop_fee, slip_bps) < 0 or stop_slippage_ticks < 0:
        raise ValueError("fees and slippage cannot be negative")

    raw_entry = Decimal(str(plan.entry))
    raw_stop = Decimal(str(plan.stop))
    raw_target = Decimal(str(plan.target))
    entry = _rounded(raw_entry, tick, ROUND_HALF_UP)
    target = _rounded(raw_target, tick, ROUND_HALF_UP)
    if plan.side == "LONG":
        stop = _rounded(raw_stop, tick, ROUND_FLOOR)
        adverse_raw = stop - Decimal(stop_slippage_ticks) * tick - stop * slip_bps / Decimal(10_000)
        adverse_stop = _rounded(adverse_raw, tick, ROUND_FLOOR)
        valid = 0 < adverse_stop < stop < entry < target
    else:
        stop = _rounded(raw_stop, tick, ROUND_CEILING)
        adverse_raw = stop + Decimal(stop_slippage_ticks) * tick + stop * slip_bps / Decimal(10_000)
        adverse_stop = _rounded(adverse_raw, tick, ROUND_CEILING)
        valid = 0 < target < entry < stop < adverse_stop
    if not valid:
        raise ValueError("native rounded route geometry is invalid")

    structural_distance = abs(entry - stop)
    quantity_per_nav = risk / structural_distance
    reward = abs(target - entry)
    target_return = quantity_per_nav * (
        reward - entry * entry_fee - target * target_fee
    )
    stop_return = -quantity_per_nav * (
        abs(entry - adverse_stop) + entry * entry_fee + adverse_stop * stop_fee
    )
    target_wealth = Decimal(1) + target_return
    stop_wealth = Decimal(1) + stop_return
    if target_wealth <= 0 or stop_wealth <= 0:
        raise ValueError("route costs imply non-positive terminal wealth")
    win_log = math.log(float(target_wealth))
    loss_log = math.log(float(stop_wealth))
    if win_log <= 0 or loss_log >= 0:
        raise ValueError("route has no positive cost-after target edge geometry")
    break_even = -loss_log / (win_log - loss_log)
    assumptions = {
        "entry": str(entry),
        "stop_trigger": str(stop),
        "adverse_stop_fill": str(adverse_stop),
        "target": str(target),
        "tick_size": str(tick),
        "risk_fraction": str(risk),
        "entry_fee_rate": str(entry_fee),
        "target_fee_rate": str(target_fee),
        "stop_fee_rate": str(stop_fee),
        "stop_slippage_ticks": stop_slippage_ticks,
        "stop_slippage_bps": str(slip_bps),
        "quantity_rule": "NAV_RISK_FRACTION_DIVIDED_BY_STRUCTURAL_ENTRY_STOP_DISTANCE",
    }
    return RouteEconomics(
        entry=float(entry),
        stop_trigger=float(stop),
        adverse_stop_fill=float(adverse_stop),
        target=float(target),
        tick_size=float(tick),
        gross_rr=float(reward / structural_distance),
        risk_fraction=float(risk),
        entry_fee_rate=float(entry_fee),
        target_fee_rate=float(target_fee),
        stop_fee_rate=float(stop_fee),
        stop_slippage_ticks=stop_slippage_ticks,
        stop_slippage_bps=float(slip_bps),
        target_nav_return=float(target_return),
        stop_nav_return=float(stop_return),
        target_log_growth=win_log,
        stop_log_growth=loss_log,
        conditional_break_even_p=break_even,
        assumptions_id=_digest(assumptions),
    )


def _number(evidence: Mapping[str, Any], key: str) -> float | None:
    value = evidence.get(key)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _risk_bucket(plan: TradePlan) -> str:
    bps = 10_000.0 * plan.risk_distance / plan.entry
    return f"RISK_BPS_LOG2_OCTAVE_{math.floor(math.log2(bps))}"


def _rr_bucket(rr: float) -> str:
    return f"RR_LOG2_OCTAVE_{math.floor(math.log2(rr))}"


def _flow_response_bucket(evidence: Mapping[str, Any]) -> str:
    pressure = _number(evidence, "journey_control_pressure")
    surprise = _number(evidence, "journey_control_pressure_surprise")
    response = _number(evidence, "journey_control_price_response")
    impact = _number(evidence, "journey_control_impact_per_pressure")
    if pressure is None or response is None:
        return "UNKNOWN"
    pressure_role = "OPPOSED" if pressure < 0 else "ALIGNED"
    surprise_role = (
        "SURPRISE_UNKNOWN" if surprise is None else
        "PRESSURE_ABOVE_BASE" if surprise > 0 else "PRESSURE_NOT_ABOVE_BASE"
    )
    response_role = "DELIVERED" if response > 0 else "NOT_DELIVERED"
    impact_role = (
        "IMPACT_UNKNOWN" if impact is None else
        "POSITIVE_IMPACT" if impact > 0 else "NONPOSITIVE_IMPACT"
    )
    return f"{pressure_role}:{surprise_role}:{response_role}:{impact_role}"


CONTEXT_FIELDS: tuple[str, ...] = (
    "entry_mode",
    "family",
    "objective",
    "source",
    "risk",
    "rr",
    "directional_ownership",
    "event_ownership",
    "higher_timeframe",
    "cross_market",
    "inventory",
    "flow_response",
)


def route_context(plan: TradePlan) -> tuple[str, ...]:
    """Return side/symbol/date-free causal categorical semantics."""

    evidence = plan.evidence
    instruction = str(evidence.get("entry_execution_instruction", ""))
    entry_mode = (
        "IMMEDIATE_RESPONSE"
        if plan.family == "ACCEPTED_AUCTION_CONTINUATION" or "IMMEDIATE" in instruction
        else "RESTING_FIRST_RETURN"
    )
    destination_tf = evidence.get("destination_timeframe_minutes", "NA")
    source_tf = evidence.get("source_timeframe_minutes", "NA")
    return (
        entry_mode,
        plan.family,
        f"{evidence.get('destination_kind', 'UNKNOWN')}@{destination_tf}",
        f"{evidence.get('source_kind', plan.entry_zone.kind)}@{source_tf}",
        _risk_bucket(plan),
        _rr_bucket(plan.gross_rr),
        str(evidence.get("directional_ownership_category", "UNKNOWN")),
        str(evidence.get("event_ownership_role", "UNKNOWN")),
        str(evidence.get("higher_timeframe_regime", "UNKNOWN")),
        str(evidence.get("cross_market_ownership_mode", "UNKNOWN")),
        (
            f"{evidence.get('inventory_interpretation', 'UNKNOWN')}:"
            f"{evidence.get('inventory_regime', 'UNKNOWN')}"
        ),
        _flow_response_bucket(evidence),
    )


@dataclass(frozen=True, slots=True)
class FrozenRoute:
    """Immutable proposal intent plus its monotonically replaced lifecycle."""

    route_id: str
    episode_id: str
    symbol: str
    family: str
    side: str
    decision_time_ns: int
    entry: float
    stop: float
    target: float
    selected: bool
    context: tuple[str, ...]
    intent_json: str
    intent_fingerprint: str
    economics: RouteEconomics
    state: RouteState
    fill_time_ns: int | None = None
    resolution_time_ns: int | None = None
    resolution_id: str | None = None
    last_bar_close_time_ns: int | None = None
    prefill_continue_bars: int = 0
    postfill_continue_bars: int = 0
    prefill_continue_minutes: float = 0.0
    postfill_continue_minutes: float = 0.0

    @classmethod
    def from_trade_plan(
        cls,
        plan: TradePlan,
        *,
        selected: bool,
        tick_size: Decimal | float | str | None = None,
    ) -> "FrozenRoute":
        intent_json = _canonical(plan.to_dict())
        immediate = plan.family == "ACCEPTED_AUCTION_CONTINUATION" or (
            "IMMEDIATE" in str(plan.evidence.get("entry_execution_instruction", ""))
        )
        return cls(
            route_id=plan.plan_id,
            episode_id=plan.episode_id,
            symbol=plan.symbol,
            family=plan.family,
            side=plan.side,
            decision_time_ns=plan.decision_time_ns,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            selected=bool(selected),
            context=route_context(plan),
            intent_json=intent_json,
            intent_fingerprint=sha256(intent_json.encode("utf-8")).hexdigest(),
            economics=native_route_economics(plan, tick_size=tick_size),
            state=RouteState.FILLED if immediate else RouteState.PENDING,
            fill_time_ns=plan.decision_time_ns if immediate else None,
        )

    @property
    def active(self) -> bool:
        return not self.state.terminal

    @property
    def active_age_minutes(self) -> float:
        end = self.last_bar_close_time_ns or self.decision_time_ns
        return max(0.0, (end - self.decision_time_ns) / 60_000_000_000)

    @property
    def total_continue_minutes(self) -> float:
        return self.prefill_continue_minutes + self.postfill_continue_minutes


@dataclass(slots=True)
class _PrefixStats:
    q_fill: int = 0
    q_no_fill: int = 0
    p_target: int = 0
    p_stop: int = 0
    resolutions: int = 0
    duration_minutes: float = 0.0
    prefill_continue_bars: int = 0
    postfill_continue_bars: int = 0
    prefill_continue_minutes: float = 0.0
    postfill_continue_minutes: float = 0.0


@dataclass(frozen=True, slots=True)
class RouteScore:
    route_id: str
    fill_probability: float
    target_given_fill_probability: float
    conditional_break_even_p: float
    expected_log_growth: float
    expected_slot_minutes: float
    active_age_minutes: float
    phase: str
    phase_terminal_hazard: float
    expected_remaining_slot_minutes: float
    conditional_remaining_success_probability: float
    growth_per_expected_slot_minute: float
    duration_observations: int
    action: str


class RouteSurvivalBook:
    """Deterministic tracker and hierarchical resolved-outcome model."""

    def __init__(self, *, backoff_concentration: float = 1.0) -> None:
        if backoff_concentration != 1.0:
            raise ValueError("parent backoff concentration is fixed at exactly one")
        self.backoff_concentration = 1.0
        self._routes: dict[str, FrozenRoute] = {}
        self._stats: dict[tuple[str, ...], _PrefixStats] = {}
        self._observations: dict[tuple[str, int], str] = {}
        self._resolution_ids: set[str] = set()

    @staticmethod
    def _prefixes(context: tuple[str, ...]):
        yield ()
        for size in range(1, len(context) + 1):
            yield context[:size]

    def register(
        self,
        plan: TradePlan,
        *,
        selected: bool,
        tick_size: Decimal | float | str | None = None,
    ) -> FrozenRoute:
        route = FrozenRoute.from_trade_plan(plan, selected=selected, tick_size=tick_size)
        prior = self._routes.get(route.route_id)
        if prior is not None:
            detail = (
                "duplicate route"
                if prior.intent_fingerprint == route.intent_fingerprint
                else "route mutation"
            )
            raise RouteIntegrityError(f"{detail}: {route.route_id}")
        self._routes[route.route_id] = route
        return route

    def route(self, route_id: str) -> FrozenRoute:
        try:
            return self._routes[route_id]
        except KeyError as exc:
            raise RouteIntegrityError(f"unknown route: {route_id}") from exc

    @staticmethod
    def _fills(route: FrozenRoute, bar: Bar) -> bool:
        # A limit also fills when a bar gaps through to a more favorable price.
        return bar.low <= route.entry if route.side == "LONG" else bar.high >= route.entry

    @staticmethod
    def _stop_touched(route: FrozenRoute, bar: Bar) -> bool:
        # Inequalities, rather than range containment, catch adverse gaps.
        return bar.low <= route.stop if route.side == "LONG" else bar.high >= route.stop

    @staticmethod
    def _target_touched(route: FrozenRoute, bar: Bar) -> bool:
        return bar.high >= route.target if route.side == "LONG" else bar.low <= route.target

    @staticmethod
    def _resolved(route: FrozenRoute, state: RouteState, time_ns: int) -> FrozenRoute:
        resolution_payload = {
            "route_id": route.route_id,
            "intent_fingerprint": route.intent_fingerprint,
            "state": state.value,
            "fill_time_ns": route.fill_time_ns,
            "resolution_time_ns": time_ns,
        }
        return replace(
            route,
            state=state,
            resolution_time_ns=time_ns,
            resolution_id=f"ROUTE-RES:{_digest(resolution_payload)[:24]}",
            last_bar_close_time_ns=max(time_ns, route.last_bar_close_time_ns or time_ns),
        )

    def _expose(self, route: FrozenRoute, *, postfill: bool, minutes: float) -> FrozenRoute:
        for prefix in self._prefixes(route.context):
            stats = self._stats.setdefault(prefix, _PrefixStats())
            if postfill:
                stats.postfill_continue_bars += 1
                stats.postfill_continue_minutes += minutes
            else:
                stats.prefill_continue_bars += 1
                stats.prefill_continue_minutes += minutes
        if postfill:
            return replace(
                route,
                postfill_continue_bars=route.postfill_continue_bars + 1,
                postfill_continue_minutes=route.postfill_continue_minutes + minutes,
            )
        return replace(
            route,
            prefill_continue_bars=route.prefill_continue_bars + 1,
            prefill_continue_minutes=route.prefill_continue_minutes + minutes,
        )

    def _learn_resolution(self, route: FrozenRoute) -> None:
        if route.resolution_id is None:
            raise RouteIntegrityError("terminal route lacks resolution_id")
        if route.resolution_id in self._resolution_ids:
            raise RouteIntegrityError(f"duplicate resolution: {route.resolution_id}")
        self._resolution_ids.add(route.resolution_id)
        if route.state == RouteState.RIGHT_CENSORED:
            return
        for prefix in self._prefixes(route.context):
            stats = self._stats.setdefault(prefix, _PrefixStats())
            stats.resolutions += 1
            stats.duration_minutes += route.active_age_minutes
            if route.state == RouteState.NO_FILL:
                stats.q_no_fill += 1
            else:
                stats.q_fill += 1
                if route.state == RouteState.TARGET_FIRST:
                    stats.p_target += 1
                elif route.state == RouteState.STOP_FIRST:
                    stats.p_stop += 1

    def observe(
        self,
        route_id: str,
        bar: Bar,
        *,
        intrinsic_invalidated: bool = False,
    ) -> FrozenRoute:
        route = self.route(route_id)
        if route.state.terminal:
            raise RouteIntegrityError(f"observation after terminal route: {route_id}")
        if bar.symbol != route.symbol:
            raise RouteIntegrityError("bar symbol does not own route")
        if bar.close_time_ns < route.decision_time_ns:
            raise RouteIntegrityError("pre-decision observation")
        # The decision bar produced the plan; consuming its high/low is look-ahead.
        if bar.close_time_ns == route.decision_time_ns:
            return route
        observation_payload = {
            "bar": bar.to_dict(),
            "intrinsic_invalidated": bool(intrinsic_invalidated),
        }
        key = (route_id, bar.close_time_ns)
        fingerprint = _digest(observation_payload)
        prior = self._observations.get(key)
        if prior is not None:
            detail = "duplicate observation" if prior == fingerprint else "observation mutation"
            raise RouteIntegrityError(f"{detail}: {route_id}@{bar.close_time_ns}")
        if (
            route.last_bar_close_time_ns is not None
            and bar.close_time_ns <= route.last_bar_close_time_ns
        ):
            raise RouteIntegrityError("non-monotonic route observation")
        self._observations[key] = fingerprint
        exposure_start = max(
            bar.open_time_ns,
            route.last_bar_close_time_ns or bar.open_time_ns,
        )
        minutes = (bar.close_time_ns - exposure_start) / 60_000_000_000
        minutes = max(float(minutes), 0.0)

        if route.state == RouteState.PENDING:
            # Exchange-observable price action owns a same-bar race against a
            # policy invalidation computed at the close.
            if self._fills(route, bar):
                filled = replace(route, state=RouteState.FILLED, fill_time_ns=bar.close_time_ns)
                # Target on the fill candle is never credited.  A simultaneous
                # stop is adverse and therefore resolves stop-first.
                if self._stop_touched(filled, bar):
                    updated = self._resolved(filled, RouteState.STOP_FIRST, bar.close_time_ns)
                else:
                    updated = self._expose(filled, postfill=True, minutes=minutes)
                    updated = replace(updated, last_bar_close_time_ns=bar.close_time_ns)
            elif self._target_touched(route, bar) or intrinsic_invalidated:
                # A destination consumed before the limit fills is a natural
                # no-fill terminal, not a favorable fill-candle target.
                updated = self._resolved(route, RouteState.NO_FILL, bar.close_time_ns)
            else:
                updated = self._expose(route, postfill=False, minutes=minutes)
                updated = replace(updated, last_bar_close_time_ns=bar.close_time_ns)
        else:
            stop_touched = self._stop_touched(route, bar)
            target_touched = self._target_touched(route, bar)
            if stop_touched:  # includes the same-bar TP+SL ambiguity
                updated = self._resolved(route, RouteState.STOP_FIRST, bar.close_time_ns)
            elif target_touched:
                updated = self._resolved(route, RouteState.TARGET_FIRST, bar.close_time_ns)
            else:
                updated = self._expose(route, postfill=True, minutes=minutes)
                updated = replace(updated, last_bar_close_time_ns=bar.close_time_ns)

        self._routes[route_id] = updated
        if updated.state.terminal:
            self._learn_resolution(updated)
        return updated

    def right_censor(self, route_id: str, *, time_ns: int) -> FrozenRoute:
        route = self.route(route_id)
        if route.state.terminal:
            raise RouteIntegrityError(f"duplicate terminal route: {route_id}")
        if time_ns < max(route.decision_time_ns, route.last_bar_close_time_ns or 0):
            raise RouteIntegrityError("right censor precedes observed route state")
        updated = self._resolved(route, RouteState.RIGHT_CENSORED, time_ns)
        self._routes[route_id] = updated
        self._learn_resolution(updated)
        return updated

    def _posterior(
        self,
        context: tuple[str, ...],
        *,
        outcome: str,
        prior_mean: float,
        prior_concentration: float,
    ) -> tuple[float, int]:
        mean = prior_mean
        concentration = prior_concentration
        support = 0
        for prefix in self._prefixes(context):
            stats = self._stats.get(prefix, _PrefixStats())
            if outcome == "q":
                success, failure = stats.q_fill, stats.q_no_fill
            else:
                success, failure = stats.p_target, stats.p_stop
            total = success + failure
            mean = (mean * concentration + success) / (concentration + total)
            support = total
            concentration = 1.0
        return mean, support

    def _phase_hazard(
        self,
        context: tuple[str, ...],
        *,
        phase: str,
    ) -> tuple[float, int, float]:
        """Beta-backoff hazard of leaving a phase on the next observed bar."""

        mean = 0.5  # Jeffreys Beta(1/2, 1/2)
        concentration = 1.0
        support = 0
        minutes_per_continue_bar = 1.0
        for prefix in self._prefixes(context):
            stats = self._stats.get(prefix, _PrefixStats())
            if phase == "PENDING":
                terminals = stats.q_fill + stats.q_no_fill
                continues = stats.prefill_continue_bars
                continue_minutes = stats.prefill_continue_minutes
            else:
                terminals = stats.p_target + stats.p_stop
                continues = stats.postfill_continue_bars
                continue_minutes = stats.postfill_continue_minutes
            mean = (mean * concentration + terminals) / (
                concentration + terminals + continues
            )
            support = terminals
            if continues:
                minutes_per_continue_bar = continue_minutes / continues
            concentration = 1.0
        return mean, support, max(minutes_per_continue_bar, 1e-12)

    def score(self, route_or_id: FrozenRoute | str) -> RouteScore:
        route = self.route(route_or_id) if isinstance(route_or_id, str) else route_or_id
        if route.context[0] == "IMMEDIATE_RESPONSE" or route.state == RouteState.FILLED:
            # The route is already FILLED at its decision close; there is no
            # future fill event to estimate for an immediate response.
            q = 1.0
        else:
            q, _ = self._posterior(
                route.context,
                outcome="q",
                prior_mean=0.5,
                prior_concentration=1.0,  # Jeffreys Beta(1/2, 1/2)
            )
        p0 = route.economics.conditional_break_even_p
        p, _ = self._posterior(
            route.context,
            outcome="p",
            prior_mean=p0,
            prior_concentration=1.0,
        )
        conditional = (
            p * route.economics.target_log_growth
            + (1.0 - p) * route.economics.stop_log_growth
        )
        expected = q * conditional
        if abs(expected) < 1e-15:
            expected = 0.0

        phase = "FILLED" if route.state == RouteState.FILLED else "PENDING"
        hazard, duration_observations, bar_minutes = self._phase_hazard(
            route.context,
            phase=phase,
        )
        remaining_minutes = bar_minutes / max(hazard, 1e-12)
        expected_minutes = route.active_age_minutes + remaining_minutes
        remaining_success = p if phase == "FILLED" else q * p
        per_minute = expected / expected_minutes
        return RouteScore(
            route_id=route.route_id,
            fill_probability=q,
            target_given_fill_probability=p,
            conditional_break_even_p=p0,
            expected_log_growth=expected,
            expected_slot_minutes=expected_minutes,
            active_age_minutes=route.active_age_minutes,
            phase=phase,
            phase_terminal_hazard=hazard,
            expected_remaining_slot_minutes=remaining_minutes,
            conditional_remaining_success_probability=remaining_success,
            growth_per_expected_slot_minute=per_minute,
            duration_observations=duration_observations,
            action="TAKE" if expected > 0.0 else "NULL",
        )

    def export_state(self) -> dict[str, Any]:
        body = {
            "version": STATE_VERSION,
            "backoff_concentration": self.backoff_concentration,
            "routes": [
                {
                    **asdict(self._routes[key]),
                    "state": self._routes[key].state.value,
                    "context": list(self._routes[key].context),
                }
                for key in sorted(self._routes)
            ],
            "stats": [
                {"prefix": list(prefix), **asdict(self._stats[prefix])}
                for prefix in sorted(self._stats)
            ],
            "observations": [
                {"route_id": key[0], "close_time_ns": key[1], "fingerprint": value}
                for key, value in sorted(self._observations.items())
            ],
            "resolution_ids": sorted(self._resolution_ids),
        }
        return {**body, "checksum": _digest(body)}

    @classmethod
    def restore(cls, payload: Mapping[str, Any]) -> "RouteSurvivalBook":
        values = dict(payload)
        checksum = values.pop("checksum", None)
        if not isinstance(checksum, str) or checksum != _digest(values):
            raise RouteIntegrityError("route-survival state checksum mismatch")
        if values.get("version") != STATE_VERSION:
            raise RouteIntegrityError("unsupported route-survival state version")
        book = cls(backoff_concentration=float(values["backoff_concentration"]))
        for raw in values.get("routes", []):
            item = dict(raw)
            item["state"] = RouteState(item["state"])
            item["context"] = tuple(item["context"])
            item["economics"] = RouteEconomics(**item["economics"])
            route = FrozenRoute(**item)
            if sha256(route.intent_json.encode("utf-8")).hexdigest() != route.intent_fingerprint:
                raise RouteIntegrityError(f"restored route intent mutation: {route.route_id}")
            try:
                restored_plan = TradePlan.from_dict(json.loads(route.intent_json))
                pristine = FrozenRoute.from_trade_plan(
                    restored_plan,
                    selected=route.selected,
                    tick_size=route.economics.tick_size,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RouteIntegrityError(
                    f"invalid restored route intent: {route.route_id}",
                ) from exc
            immutable_fields = (
                "route_id", "episode_id", "symbol", "family", "side",
                "decision_time_ns", "entry", "stop", "target", "selected",
                "context", "intent_json", "intent_fingerprint", "economics",
            )
            if any(getattr(route, name) != getattr(pristine, name) for name in immutable_fields):
                raise RouteIntegrityError(f"restored route mutation: {route.route_id}")
            if route.state in {RouteState.PENDING, RouteState.NO_FILL} and route.fill_time_ns is not None:
                raise RouteIntegrityError("pending restored route has a fill time")
            if route.state in {
                RouteState.FILLED,
                RouteState.TARGET_FIRST,
                RouteState.STOP_FIRST,
            } and route.fill_time_ns is None:
                raise RouteIntegrityError("filled restored route lacks a fill time")
            if route.state.terminal != (route.resolution_id is not None):
                raise RouteIntegrityError("restored terminal identity mismatch")
            if route.state.terminal:
                if route.resolution_time_ns is None:
                    raise RouteIntegrityError("restored terminal route lacks resolution time")
                expected_resolution = cls._resolved(
                    replace(route, resolution_id=None, resolution_time_ns=None),
                    route.state,
                    route.resolution_time_ns,
                ).resolution_id
                if route.resolution_id != expected_resolution:
                    raise RouteIntegrityError("restored resolution identity mutation")
            if route.route_id in book._routes:
                raise RouteIntegrityError(f"duplicate restored route: {route.route_id}")
            book._routes[route.route_id] = route
        for raw in values.get("stats", []):
            item = dict(raw)
            prefix = tuple(item.pop("prefix"))
            if prefix in book._stats:
                raise RouteIntegrityError("duplicate restored prefix")
            stats = _PrefixStats(**item)
            counts = (
                stats.q_fill, stats.q_no_fill, stats.p_target, stats.p_stop,
                stats.resolutions, stats.prefill_continue_bars,
                stats.postfill_continue_bars,
            )
            durations = (
                stats.duration_minutes, stats.prefill_continue_minutes,
                stats.postfill_continue_minutes,
            )
            if any(value < 0 for value in counts + durations):
                raise RouteIntegrityError("negative restored route statistic")
            if stats.q_fill != stats.p_target + stats.p_stop:
                raise RouteIntegrityError("restored conditional outcome counts disagree")
            if stats.resolutions != stats.q_fill + stats.q_no_fill:
                raise RouteIntegrityError("restored resolution counts disagree")
            book._stats[prefix] = stats
        for raw in values.get("observations", []):
            key = (str(raw["route_id"]), int(raw["close_time_ns"]))
            if key in book._observations:
                raise RouteIntegrityError("duplicate restored observation")
            if key[0] not in book._routes:
                raise RouteIntegrityError("restored observation has no route")
            book._observations[key] = str(raw["fingerprint"])
        resolution_ids = [str(item) for item in values.get("resolution_ids", [])]
        if len(resolution_ids) != len(set(resolution_ids)):
            raise RouteIntegrityError("duplicate restored resolution")
        book._resolution_ids = set(resolution_ids)
        terminal_ids = {
            route.resolution_id
            for route in book._routes.values()
            if route.state.terminal and route.resolution_id is not None
        }
        if terminal_ids != book._resolution_ids:
            raise RouteIntegrityError("restored resolution index disagrees with routes")
        return book


__all__ = [
    "CONTEXT_FIELDS",
    "FrozenRoute",
    "RouteEconomics",
    "RouteIntegrityError",
    "RouteScore",
    "RouteState",
    "RouteSurvivalBook",
    "native_route_economics",
    "route_context",
]
