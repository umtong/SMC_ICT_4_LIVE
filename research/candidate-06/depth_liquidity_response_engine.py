"""Prior-only passive-liquidity vacuum and replenishment response scenario.

The passive-liquidity detector is deliberately separate from the trading state
machine.  It transforms only observations already available at a bar event into
robust, prior-normalized features.  The scenario engine then requires an
ordered sequence:

1. a liquidity-pool breach with aggressive flow,
2. a provisional passive-liquidity vacuum or replenishment interpretation,
3. a later structural retest which holds the relevant boundary, and
4. a separate directional response before a signal may be emitted.

No OHLCV-derived synthetic depth is accepted.
"""

from __future__ import annotations

import bisect
import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


ONE_MINUTE_NS = 60_000_000_000


def robust_z(value: float, history: list[float], minimum_history: int = 12) -> float:
    """Return a median/MAD z-score using prior observations only."""

    if len(history) < minimum_history:
        return 0.0
    center = median(history)
    deviations = [abs(item - center) for item in history]
    mad = median(deviations)
    scale = max(1.4826 * mad, abs(center) * 1e-6, 1e-12)
    return (value - center) / scale


@dataclass(frozen=True, slots=True)
class PassiveLiquidityRecord:
    ts_ns: int
    bid_near: float
    ask_near: float
    bid_total: float
    ask_total: float

    @property
    def near_imbalance(self) -> float:
        total = self.bid_near + self.ask_near
        return (self.bid_near - self.ask_near) / total if total > 0.0 else 0.0

    @property
    def total_imbalance(self) -> float:
        total = self.bid_total + self.ask_total
        return (self.bid_total - self.ask_total) / total if total > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class PassiveLiquidityFeatures:
    bid_near_z: float
    ask_near_z: float
    bid_total_z: float
    ask_total_z: float
    near_imbalance: float
    total_imbalance: float
    bid_near_change: float
    ask_near_change: float
    prior_observations: int

    def details(self) -> dict[str, float | int]:
        return dict(asdict(self))


class PriorOnlyPassiveLiquidityDetector:
    """Detect passive-liquidity states without seeing the current record early."""

    def __init__(self, *, warmup: int, history_capacity: int = 720) -> None:
        if warmup < 4:
            raise ValueError("dlvr_depth_warmup must be at least 4")
        self.warmup = warmup
        self.history_capacity = max(history_capacity, warmup + 8)
        self._bid_near: list[float] = []
        self._ask_near: list[float] = []
        self._bid_total: list[float] = []
        self._ask_total: list[float] = []
        self._previous: PassiveLiquidityRecord | None = None
        self.last_ts_ns: int | None = None

    def features(self, record: PassiveLiquidityRecord) -> PassiveLiquidityFeatures:
        previous = self._previous
        return PassiveLiquidityFeatures(
            bid_near_z=robust_z(record.bid_near, self._bid_near[-180:], self.warmup),
            ask_near_z=robust_z(record.ask_near, self._ask_near[-180:], self.warmup),
            bid_total_z=robust_z(record.bid_total, self._bid_total[-180:], self.warmup),
            ask_total_z=robust_z(record.ask_total, self._ask_total[-180:], self.warmup),
            near_imbalance=record.near_imbalance,
            total_imbalance=record.total_imbalance,
            bid_near_change=(
                0.0
                if previous is None or previous.bid_near <= 0.0
                else record.bid_near / previous.bid_near - 1.0
            ),
            ask_near_change=(
                0.0
                if previous is None or previous.ask_near <= 0.0
                else record.ask_near / previous.ask_near - 1.0
            ),
            prior_observations=len(self._bid_near),
        )

    def commit(self, record: PassiveLiquidityRecord) -> None:
        if self.last_ts_ns == record.ts_ns:
            return
        self._bid_near.append(record.bid_near)
        self._ask_near.append(record.ask_near)
        self._bid_total.append(record.bid_total)
        self._ask_total.append(record.ask_total)
        for values in (self._bid_near, self._ask_near, self._bid_total, self._ask_total):
            if len(values) > self.history_capacity:
                del values[: len(values) - self.history_capacity]
        self._previous = record
        self.last_ts_ns = record.ts_ns


@dataclass(slots=True)
class _LiquidityEpisode:
    scenario_id: str
    state: str
    family: str
    direction: str
    boundary: float
    impulse_high: float
    impulse_low: float
    impulse_range: float
    started_index: int
    expires_index: int
    source_ts_ns: int
    source_kind: str
    depth_confirmation_required: bool
    pullback_low: float | None = None
    pullback_high: float | None = None
    touch_index: int | None = None


class DepthLiquidityVacuumReplenishmentEngine:
    """Trade only after a passive-liquidity interpretation survives a retest."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = dict(params)
        path = Path(str(self.params["depth_series_path"]))
        self._records = self._load(path)
        self._times = [record.ts_ns for record in self._records]
        self._detector = PriorOnlyPassiveLiquidityDetector(
            warmup=int(self.params.get("dlvr_depth_warmup", 24)),
        )
        self._episode: _LiquidityEpisode | None = None
        self._sequence = 0
        self._cooldown_until = -1
        self._source_kind = str(self.params.get("dlvr_liquidity_source", "unknown"))

    @staticmethod
    def _load(path: Path) -> list[PassiveLiquidityRecord]:
        rows: list[PassiveLiquidityRecord] = []
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"ts_ns", "bid_near", "ask_near", "bid_total", "ask_total"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ValueError(f"passive-liquidity schema mismatch in {path}: {reader.fieldnames}")
            for row in reader:
                record = PassiveLiquidityRecord(
                    ts_ns=int(row["ts_ns"]),
                    bid_near=float(row["bid_near"]),
                    ask_near=float(row["ask_near"]),
                    bid_total=float(row["bid_total"]),
                    ask_total=float(row["ask_total"]),
                )
                if min(record.bid_near, record.ask_near, record.bid_total, record.ask_total) < 0.0:
                    raise ValueError(f"negative passive-liquidity value at {record.ts_ns}")
                rows.append(record)
        rows.sort(key=lambda item: item.ts_ns)
        if not rows:
            raise ValueError(f"no passive-liquidity records loaded from {path}")
        if len({row.ts_ns for row in rows}) != len(rows):
            raise ValueError(f"duplicate passive-liquidity timestamps in {path}")
        return rows

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        record = self._latest(snapshot.observation.ts_ns)
        if record is None:
            if self._episode is None:
                return ScenarioStep()
            return self._reset(snapshot, self._episode, "PASSIVE_LIQUIDITY_DATA_STALE")

        fresh = record.ts_ns != self._detector.last_ts_ns
        features = self._detector.features(record)
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None

        if self._episode is not None:
            advanced = self._advance(snapshot, record, features, allow_new=allow_new)
            transitions.extend(advanced.transitions)
            signal = advanced.signal

        # A new scenario must be tied to a newly observed passive-liquidity
        # record.  A reset on this bar may not immediately reclassify the same
        # shock, and stale snapshots may not generate repeated opportunities.
        if (
            fresh
            and not transitions
            and self._episode is None
            and signal is None
            and allow_new
            and snapshot.index >= self._cooldown_until
        ):
            started = self._classify(snapshot, record, features)
            if started is not None:
                transitions.append(started)

        if fresh:
            self._detector.commit(record)
        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        episode = self._episode
        if episode is None:
            return ScenarioStep()
        transition = self._transition(
            episode,
            episode.state,
            "RESET",
            reason,
            snapshot.observation.close,
            {"aborted": True},
        )
        self._episode = None
        return ScenarioStep(transitions=(transition,))

    def _latest(self, ts_ns: int) -> PassiveLiquidityRecord | None:
        index = bisect.bisect_right(self._times, ts_ns) - 1
        if index < 0:
            return None
        record = self._records[index]
        maximum_age = int(
            float(self.params.get("depth_max_age_minutes", 3.0)) * ONE_MINUTE_NS,
        )
        age = ts_ns - record.ts_ns
        return record if 0 <= age <= maximum_age else None

    def _classify(
        self,
        snapshot: PrimitiveSnapshot,
        record: PassiveLiquidityRecord,
        features: PassiveLiquidityFeatures,
    ) -> ScenarioTransition | None:
        if (
            not snapshot.ready
            or snapshot.atr <= 0.0
            or snapshot.upper_fast is None
            or snapshot.lower_fast is None
            or features.prior_observations < int(self.params.get("dlvr_depth_warmup", 24))
        ):
            return None

        observation = snapshot.observation
        minimum_flow = float(self.params.get("dlvr_flow_ratio", 0.12))
        minimum_body = float(self.params.get("dlvr_body_atr", 0.35))
        vacuum_z = float(self.params.get("dlvr_vacuum_z", -0.60))
        support_z = float(self.params.get("dlvr_support_z", -0.10))
        imbalance = float(self.params.get("dlvr_near_imbalance", 0.05))
        replenish_z = float(self.params.get("dlvr_replenish_z", 0.75))
        replenish_change = float(self.params.get("dlvr_replenish_change", 0.20))
        reversal_imbalance = float(self.params.get("dlvr_reversal_imbalance", 0.03))
        require_depth = bool(self.params.get("dlvr_require_depth_confirmation", True))
        enable_vacuum = bool(self.params.get("dlvr_enable_vacuum", True))
        enable_reversal = bool(
            self.params.get("dlvr_enable_replenishment_reversal", True),
        )

        direction: str | None = None
        family: str | None = None
        boundary = 0.0
        reason = ""

        if observation.high > snapshot.upper_fast and snapshot.flow_ratio >= minimum_flow:
            accepted = (
                observation.close > snapshot.upper_fast
                and observation.close > observation.open
                and snapshot.body_atr >= minimum_body
            )
            failed = (
                observation.close <= snapshot.upper_fast
                and observation.close < observation.open
            )
            vacuum = (
                features.ask_near_z <= vacuum_z
                and features.bid_near_z >= support_z
                and features.near_imbalance >= imbalance
            )
            replenished = (
                (features.ask_near_z >= replenish_z or features.ask_near_change >= replenish_change)
                and features.near_imbalance <= -reversal_imbalance
            )
            if not require_depth:
                vacuum = True
                replenished = True
            if enable_vacuum and accepted and vacuum:
                direction = "LONG"
                family = "DLVC"
                boundary = float(snapshot.upper_fast)
                reason = "UPPER_POOL_BREAK_WITH_OFFER_VACUUM_OBSERVED"
            elif enable_reversal and failed and replenished:
                direction = "SHORT"
                family = "DLRR"
                boundary = float(snapshot.upper_fast)
                reason = "BUY_AGGRESSION_MET_REPLENISHING_OFFER_LIQUIDITY"

        elif observation.low < snapshot.lower_fast and snapshot.flow_ratio <= -minimum_flow:
            accepted = (
                observation.close < snapshot.lower_fast
                and observation.close < observation.open
                and snapshot.body_atr >= minimum_body
            )
            failed = (
                observation.close >= snapshot.lower_fast
                and observation.close > observation.open
            )
            vacuum = (
                features.bid_near_z <= vacuum_z
                and features.ask_near_z >= support_z
                and features.near_imbalance <= -imbalance
            )
            replenished = (
                (features.bid_near_z >= replenish_z or features.bid_near_change >= replenish_change)
                and features.near_imbalance >= reversal_imbalance
            )
            if not require_depth:
                vacuum = True
                replenished = True
            if enable_vacuum and accepted and vacuum:
                direction = "SHORT"
                family = "DLVC"
                boundary = float(snapshot.lower_fast)
                reason = "LOWER_POOL_BREAK_WITH_BID_VACUUM_OBSERVED"
            elif enable_reversal and failed and replenished:
                direction = "LONG"
                family = "DLRR"
                boundary = float(snapshot.lower_fast)
                reason = "SELL_AGGRESSION_MET_REPLENISHING_BID_LIQUIDITY"

        if direction is None or family is None:
            return None

        self._sequence += 1
        scenario_id = f"DLVR-{observation.ts_ns}-{self._sequence:06d}"
        self._episode = _LiquidityEpisode(
            scenario_id=scenario_id,
            state="PROVISIONAL_PASSIVE_LIQUIDITY_SHOCK",
            family=family,
            direction=direction,
            boundary=boundary,
            impulse_high=observation.high,
            impulse_low=observation.low,
            impulse_range=max(observation.high - observation.low, snapshot.atr),
            started_index=snapshot.index,
            expires_index=snapshot.index + int(self.params.get("dlvr_retest_bars", 12)),
            source_ts_ns=record.ts_ns,
            source_kind=self._source_kind,
            depth_confirmation_required=require_depth,
        )
        return self._transition(
            self._episode,
            "IDLE",
            "PROVISIONAL_PASSIVE_LIQUIDITY_SHOCK",
            reason,
            observation.close,
            {
                **features.details(),
                "family": family,
                "direction": direction,
                "boundary": boundary,
                "passive_liquidity_ts_ns": record.ts_ns,
                "passive_liquidity_source": self._source_kind,
                "depth_confirmation_required": require_depth,
            },
        )

    def _advance(
        self,
        snapshot: PrimitiveSnapshot,
        record: PassiveLiquidityRecord,
        features: PassiveLiquidityFeatures,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        observation = snapshot.observation
        tolerance = float(self.params.get("dlvr_boundary_tolerance_atr", 0.08)) * snapshot.atr

        if snapshot.index > episode.expires_index:
            return self._reset(snapshot, episode, "PROVISIONAL_LIQUIDITY_RETEST_WINDOW_EXPIRED")
        if episode.direction == "LONG" and observation.close < episode.boundary - tolerance:
            return self._reset(snapshot, episode, "LONG_PASSIVE_LIQUIDITY_STRUCTURE_INVALIDATED")
        if episode.direction == "SHORT" and observation.close > episode.boundary + tolerance:
            return self._reset(snapshot, episode, "SHORT_PASSIVE_LIQUIDITY_STRUCTURE_INVALIDATED")

        if episode.state == "PROVISIONAL_PASSIVE_LIQUIDITY_SHOCK":
            band = float(self.params.get("dlvr_retest_band_atr", 0.22)) * snapshot.atr
            maximum_retest_flow = float(self.params.get("dlvr_retest_max_flow", 0.12))
            if episode.direction == "LONG":
                retest = (
                    observation.low <= episode.boundary + band
                    and observation.close > episode.boundary - tolerance
                    and snapshot.flow_ratio <= maximum_retest_flow
                )
            else:
                retest = (
                    observation.high >= episode.boundary - band
                    and observation.close < episode.boundary + tolerance
                    and snapshot.flow_ratio >= -maximum_retest_flow
                )
            if not retest:
                return ScenarioStep()
            episode.pullback_low = observation.low
            episode.pullback_high = observation.high
            episode.touch_index = snapshot.index
            return ScenarioStep(
                transitions=(
                    self._transition(
                        episode,
                        "PROVISIONAL_PASSIVE_LIQUIDITY_SHOCK",
                        "STRUCTURAL_RETEST_OBSERVED",
                        "PROVISIONAL_SHOCK_RETESTED_WITH_BOUNDARY_HELD",
                        observation.close,
                        {
                            **features.details(),
                            "passive_liquidity_ts_ns": record.ts_ns,
                            "passive_liquidity_source": episode.source_kind,
                            "touch_low": observation.low,
                            "touch_high": observation.high,
                        },
                    ),
                ),
            )

        assert episode.state == "STRUCTURAL_RETEST_OBSERVED"
        assert episode.pullback_low is not None
        assert episode.pullback_high is not None
        assert episode.touch_index is not None

        response_flow = float(self.params.get("dlvr_response_flow", 0.10))
        response_body = float(self.params.get("dlvr_response_body_atr", 0.22))
        response_imbalance = float(self.params.get("dlvr_response_imbalance", 0.0))
        require_depth = episode.depth_confirmation_required
        separate_bar = snapshot.index > episode.touch_index

        if episode.direction == "LONG":
            depth_ok = (features.near_imbalance >= response_imbalance) if require_depth else True
            response = (
                separate_bar
                and observation.close > observation.open
                and observation.close > episode.pullback_high
                and snapshot.flow_ratio >= response_flow
                and snapshot.body_atr >= response_body
                and depth_ok
            )
        else:
            depth_ok = (features.near_imbalance <= -response_imbalance) if require_depth else True
            response = (
                separate_bar
                and observation.close < observation.open
                and observation.close < episode.pullback_low
                and snapshot.flow_ratio <= -response_flow
                and snapshot.body_atr >= response_body
                and depth_ok
            )

        if response:
            if not allow_new:
                return self._reset(
                    snapshot,
                    episode,
                    "GLOBAL_ENTRY_SLOT_UNAVAILABLE_AT_PASSIVE_LIQUIDITY_RESPONSE",
                )
            return self._emit(snapshot, episode, record, features)

        episode.pullback_low = min(episode.pullback_low, observation.low)
        episode.pullback_high = max(episode.pullback_high, observation.high)
        return ScenarioStep()

    def _emit(
        self,
        snapshot: PrimitiveSnapshot,
        episode: _LiquidityEpisode,
        record: PassiveLiquidityRecord,
        features: PassiveLiquidityFeatures,
    ) -> ScenarioStep:
        observation = snapshot.observation
        buffer_value = float(self.params.get("dlvr_stop_buffer_atr", 0.08)) * snapshot.atr
        projection = episode.impulse_range * float(
            self.params.get("dlvr_projection_fraction", 1.0),
        )

        if episode.direction == "LONG":
            assert episode.pullback_low is not None
            stop = min(episode.pullback_low, episode.boundary - buffer_value) - buffer_value
            candidates = [
                (snapshot.upper_slow, "PRIOR_SLOW_BUYSIDE_LIQUIDITY"),
                (episode.impulse_high + projection, "PASSIVE_LIQUIDITY_IMPULSE_PROJECTION"),
            ]
        else:
            assert episode.pullback_high is not None
            stop = max(episode.pullback_high, episode.boundary + buffer_value) + buffer_value
            candidates = [
                (snapshot.lower_slow, "PRIOR_SLOW_SELLSIDE_LIQUIDITY"),
                (episode.impulse_low - projection, "PASSIVE_LIQUIDITY_IMPULSE_PROJECTION"),
            ]

        target = self._select_target(episode.direction, observation.close, stop, candidates)
        if target is None:
            return self._reset(
                snapshot,
                episode,
                "NO_PASSIVE_LIQUIDITY_OBJECTIVE_WITH_SUFFICIENT_SPACE",
            )
        target_price, target_reason = target
        details: dict[str, Any] = {
            "engine": "DEPTH_LIQUIDITY_VACUUM_REPLENISHMENT",
            "passive_liquidity_source": episode.source_kind,
            "passive_liquidity_ts_ns": record.ts_ns,
            "depth_confirmation_required": episode.depth_confirmation_required,
            "provisional_shock_ts_ns": episode.source_ts_ns,
            **features.details(),
        }
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family=episode.family,
            direction=episode.direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=observation.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=episode.boundary,
            details=details,
        )
        transition = self._transition(
            episode,
            "STRUCTURAL_RETEST_OBSERVED",
            "ENTRY_ARMED",
            "RETEST_RESPONSE_CONFIRMED_PASSIVE_LIQUIDITY_SCENARIO",
            observation.close,
            {
                "stop_price": stop,
                "target_price": target_price,
                "target_reason": target_reason,
                **details,
            },
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("cooldown_bars", 4))
        return ScenarioStep(transitions=(transition,), signal=signal)

    def _select_target(
        self,
        direction: str,
        entry: float,
        stop: float,
        candidates: list[tuple[float | None, str]],
    ) -> tuple[float, str] | None:
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        minimum = float(self.params.get("minimum_structural_rr", 1.0))
        valid: list[tuple[float, str]] = []
        for price, reason in candidates:
            if price is None:
                continue
            reward = float(price) - entry if direction == "LONG" else entry - float(price)
            if reward > 0.0 and reward / risk >= minimum:
                valid.append((float(price), reason))
        valid.sort(key=lambda item: abs(item[0] - entry))
        return valid[0] if valid else None

    def _reset(
        self,
        snapshot: PrimitiveSnapshot,
        episode: _LiquidityEpisode,
        reason: str,
    ) -> ScenarioStep:
        transition = self._transition(
            episode,
            episode.state,
            "RESET",
            reason,
            snapshot.observation.close,
            {},
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("cooldown_bars", 4))
        return ScenarioStep(transitions=(transition,))

    @staticmethod
    def _transition(
        episode: _LiquidityEpisode,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        episode.state = next_state
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="PASSIVE_LIQUIDITY_STATE_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
