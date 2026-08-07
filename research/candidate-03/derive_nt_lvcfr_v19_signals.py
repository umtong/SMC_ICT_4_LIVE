#!/usr/bin/env python3
"""Derive V19 executed-flow resilience auction states.

V18's best-quote resilience question was structurally useful but its official
historical bookTicker coverage was unavailable for the frozen 2022 and 2025
weeks. V19 preserves the question on a uniform official source: futures and
spot aggregate trades.

Each OI-contraction or OI-expansion event receives a ten-minute, event-excluded
baseline of ten-second executed-flow blocks and a sequential sixty-second
observation. Three mutually exclusive outcomes are allowed:

* EXECUTED_FLOW_VACUUM_CONTINUATION: high futures price response relative to
  gross traded notional, two-block boundary persistence, and futures/spot flow
  agreement;
* EXECUTED_FLOW_ABSORPTION_CHOCH_REVERSAL: strong directional futures flow with
  poor price response and spot disagreement, followed by a full event-range
  CHoCH before entry;
* MEASURED_ACCEPTANCE_CONTINUATION: no early microstructure terminal state, but
  the event later completes one full event-range extension in its direction.

Mixed and unresolved auctions are no-trade. The module emits causal schedules
only; NautilusTrader remains the sole order, fill, fee, funding, position,
margin, liquidation, accounting, and NAV engine.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any, Sequence

from derive_nt_lvcfr_v13_signals import (
    MEASURED_ACCEPTANCE_CONTINUATION,
    derive_v13,
)
from derive_nt_lvcfr_v18_signals import derive_expansion_pre_candidates
from nt_lvcfr_data import NS_PER_MINUTE, NS_PER_SECOND, MinuteFact, load_kline_minutes
from nt_lvcfr_trade_proxy import _one_csv_reader, parse_aggtrade_row

BASELINE_MINUTES = 10
BLOCK_SECONDS = 10
OBSERVATION_SECONDS = 60
BASELINE_BLOCKS = BASELINE_MINUTES * 60 // BLOCK_SECONDS
OBSERVATION_BLOCKS = OBSERVATION_SECONDS // BLOCK_SECONDS
TOTAL_BLOCKS = BASELINE_BLOCKS + OBSERVATION_BLOCKS
BLOCK_NS = BLOCK_SECONDS * NS_PER_SECOND
ABSORPTION_EXPIRY_MINUTES = 120
MEASURED_ACCEPTANCE_EXPIRY_MINUTES = 120
EXECUTED_FLOW_VACUUM_CONTINUATION = "EXECUTED_FLOW_VACUUM_CONTINUATION"
EXECUTED_FLOW_ABSORPTION_CHOCH_REVERSAL = (
    "EXECUTED_FLOW_ABSORPTION_CHOCH_REVERSAL"
)


@dataclass(slots=True)
class TradeBlock:
    first_price: float | None = None
    last_price: float | None = None
    high: float = -math.inf
    low: float = math.inf
    gross_notional: float = 0.0
    signed_notional: float = 0.0
    path_bp: float = 0.0
    trades: int = 0

    def add(self, price: float, quantity: float, buyer_was_maker: bool) -> None:
        if not math.isfinite(price) or not math.isfinite(quantity) or price <= 0 or quantity <= 0:
            return
        if self.first_price is None:
            self.first_price = price
        if self.last_price is not None:
            self.path_bp += abs(math.log(price / self.last_price)) * 10_000.0
        self.last_price = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        notional = price * quantity
        self.gross_notional += notional
        self.signed_notional += (-notional if buyer_was_maker else notional)
        self.trades += 1

    @property
    def valid(self) -> bool:
        return (
            self.first_price is not None
            and self.last_price is not None
            and self.gross_notional > 0.0
            and self.trades > 0
        )


@dataclass(frozen=True, slots=True)
class FlowFeatures:
    progress_bp: float
    directional_flow: float
    activity_ratio: float
    response_score: float
    path_efficiency: float
    last_price: float
    gross_notional: float
    trades: int


@dataclass(slots=True)
class CandidateContext:
    candidate: dict[str, Any]
    baseline_start_ns: int
    baseline_end_ns: int
    observation_start_ns: int
    observation_end_ns: int
    blocks: list[TradeBlock] = field(
        default_factory=lambda: [TradeBlock() for _ in range(TOTAL_BLOCKS)]
    )

    @property
    def start_ns(self) -> int:
        return self.baseline_start_ns

    @property
    def end_ns(self) -> int:
        return self.observation_end_ns

    def add(
        self,
        timestamp_ns: int,
        price: float,
        quantity: float,
        buyer_was_maker: bool,
    ) -> None:
        if self.baseline_start_ns <= timestamp_ns < self.baseline_end_ns:
            index = (timestamp_ns - self.baseline_start_ns) // BLOCK_NS
        elif (
            self.observation_start_ns
            <= timestamp_ns
            < self.observation_end_ns
        ):
            index = BASELINE_BLOCKS + (
                timestamp_ns - self.observation_start_ns
            ) // BLOCK_NS
        else:
            # The inventory event is deliberately excluded from both
            # the pre-event baseline and post-confirmation response.
            return
        if 0 <= index < TOTAL_BLOCKS:
            self.blocks[int(index)].add(
                price,
                quantity,
                buyer_was_maker,
            )


@dataclass(frozen=True, slots=True)
class AuctionCandidate:

    scenario_id: str
    inventory_regime: str
    direction: int
    event_start_ns: int
    confirm_time_ns: int
    atr: float
    event_low: float
    event_high: float
    event_midpoint: float
    same_side_boundary: float
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "inventory_regime": self.inventory_regime,
            "direction": self.direction,
            "event_start_ns": self.event_start_ns,
            "confirm_time_ns": self.confirm_time_ns,
            "atr": self.atr,
            "event_low": self.event_low,
            "event_high": self.event_high,
            "event_midpoint": self.event_midpoint,
            "same_side_boundary": self.same_side_boundary,
            "details": dict(self.details),
        }


def quantile(values: Sequence[float], probability: float) -> float:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        raise ValueError("empty quantile input")
    position = (len(clean) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return clean[lower]
    fraction = position - lower
    return clean[lower] * (1.0 - fraction) + clean[upper] * fraction


def block_features(
    block: TradeBlock,
    *,
    direction: int,
    baseline_median_gross: float,
) -> FlowFeatures | None:
    if not block.valid or baseline_median_gross <= 0.0:
        return None
    assert block.first_price is not None and block.last_price is not None
    progress = direction * math.log(block.last_price / block.first_price) * 10_000.0
    flow = direction * block.signed_notional / block.gross_notional
    activity = block.gross_notional / baseline_median_gross
    response = progress / max(activity, 1e-9)
    efficiency = progress / max(block.path_bp, 1e-9)
    return FlowFeatures(
        progress_bp=progress,
        directional_flow=flow,
        activity_ratio=activity,
        response_score=response,
        path_efficiency=efficiency,
        last_price=block.last_price,
        gross_notional=block.gross_notional,
        trades=block.trades,
    )


def cumulative_features(
    blocks: Sequence[TradeBlock],
    *,
    direction: int,
    baseline_median_gross: float,
) -> FlowFeatures | None:
    valid = [block for block in blocks if block.valid]
    if not valid or len(valid) != len(blocks) or baseline_median_gross <= 0.0:
        return None
    first = valid[0].first_price
    last = valid[-1].last_price
    assert first is not None and last is not None
    gross = sum(block.gross_notional for block in valid)
    signed = sum(block.signed_notional for block in valid)
    path = sum(block.path_bp for block in valid)
    for previous, current in zip(valid, valid[1:]):
        assert previous.last_price is not None and current.first_price is not None
        path += abs(math.log(current.first_price / previous.last_price)) * 10_000.0
    progress = direction * math.log(last / first) * 10_000.0
    flow = direction * signed / gross
    activity = gross / (baseline_median_gross * len(valid))
    response = progress / max(activity, 1e-9)
    efficiency = progress / max(path, 1e-9)
    return FlowFeatures(
        progress_bp=progress,
        directional_flow=flow,
        activity_ratio=activity,
        response_score=response,
        path_efficiency=efficiency,
        last_price=last,
        gross_notional=gross,
        trades=sum(block.trades for block in valid),
    )


def collect_contexts(
    paths: Sequence[Path],
    candidates: Sequence[AuctionCandidate],
) -> dict[str, CandidateContext]:
    contexts = {
        candidate.scenario_id: CandidateContext(
    candidate=candidate.to_dict(),
    baseline_start_ns=(
        candidate.event_start_ns
        - BASELINE_MINUTES * NS_PER_MINUTE
    ),
    baseline_end_ns=candidate.event_start_ns,
    observation_start_ns=candidate.confirm_time_ns,
    observation_end_ns=(
        candidate.confirm_time_ns
        + OBSERVATION_SECONDS * NS_PER_SECOND
    ),
)
        for candidate in candidates
    }
    ordered = sorted(contexts.values(), key=lambda context: context.start_ns)
    next_index = 0
    active: list[CandidateContext] = []
    for path in sorted(paths, key=lambda item: item.name):
        archive, reader = _one_csv_reader(path)
        try:
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue
                _, price, quantity, buyer_was_maker, _, timestamp_ns = parse_aggtrade_row(row)
                while next_index < len(ordered) and ordered[next_index].start_ns <= timestamp_ns:
                    active.append(ordered[next_index])
                    next_index += 1
                if active:
                    active = [context for context in active if context.end_ns > timestamp_ns]
                    for context in active:
                        context.add(timestamp_ns, price, quantity, buyer_was_maker)
        finally:
            archive.close()
    return contexts


def event_rows(
    minutes: dict[int, MinuteFact],
    start_ns: int,
    end_ns: int,
) -> list[MinuteFact]:
    start = start_ns // NS_PER_MINUTE
    end = end_ns // NS_PER_MINUTE
    rows = [minutes.get(minute) for minute in range(start, end)]
    return [row for row in rows if row is not None] if not any(row is None for row in rows) else []


def prior_rows(
    minutes: dict[int, MinuteFact],
    event_start_ns: int,
    window: int = 240,
) -> list[MinuteFact]:
    start = event_start_ns // NS_PER_MINUTE
    rows = [minutes.get(minute) for minute in range(start - window, start)]
    return [row for row in rows if row is not None] if not any(row is None for row in rows) else []


def build_candidates(
    *,
    source_signals: Path,
    raw_root: Path,
    evaluation_start_ns: int,
    evaluation_end_ns: int,
) -> tuple[list[AuctionCandidate], dict[str, Any]]:
    futures_minutes = load_kline_minutes(sorted((raw_root / "futures_kline").glob("*.zip")))
    by_minute = {row.minute_index: row for row in futures_minutes}
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    candidates: list[AuctionCandidate] = []
    missing = 0
    for signal in source:
        rows = event_rows(
            by_minute,
            int(signal["first_start_time_ns"]),
            int(signal["confirm_time_ns"]),
        )
        if len(rows) != 10:
            missing += 1
            continue
        direction = int(signal["direction"])
        event_low = min(row.low for row in rows)
        event_high = max(row.high for row in rows)
        candidates.append(
            AuctionCandidate(
                scenario_id="V19-CONTRACTION-" + str(signal["scenario_id"]).split("-")[-1],
                inventory_regime="OI_CONTRACTION",
                direction=direction,
                event_start_ns=int(signal["first_start_time_ns"]),
                confirm_time_ns=int(signal["confirm_time_ns"]),
                atr=float(signal["atr"]),
                event_low=event_low,
                event_high=event_high,
                event_midpoint=(event_low + event_high) / 2.0,
                same_side_boundary=event_high if direction > 0 else event_low,
                details={"source_scenario_id": signal["scenario_id"], **dict(signal.get("details", {}))},
            )
        )

    expansions = derive_expansion_pre_candidates(
        raw_root=raw_root,
        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
    )
    for signal in expansions:
        details = dict(signal.get("details", {}))
        event_low = float(details["event_low"])
        event_high = float(details["event_high"])
        direction = int(signal["direction"])
        candidates.append(
            AuctionCandidate(
                scenario_id="V19-EXPANSION-" + str(signal["scenario_id"]).split("-")[-1],
                inventory_regime="OI_EXPANSION",
                direction=direction,
                event_start_ns=int(signal["first_start_time_ns"]),
                confirm_time_ns=int(signal["confirm_time_ns"]),
                atr=float(signal["atr"]),
                event_low=event_low,
                event_high=event_high,
                event_midpoint=(event_low + event_high) / 2.0,
                same_side_boundary=event_high if direction > 0 else event_low,
                details={"source_scenario_id": signal["scenario_id"], **details},
            )
        )

    candidates.sort(key=lambda item: (item.confirm_time_ns, item.scenario_id))
    return candidates, {
        "source_contraction_events": len(source),
        "expansion_pre_candidates": len(expansions),
        "combined_candidates": len(candidates),
        "missing_event_context": missing,
    }


def waypoint(
    *,
    direction: int,
    reference: float,
    prior: list[MinuteFact],
    prefer_equilibrium: bool,
) -> tuple[str, float] | None:
    if not prior:
        return None
    high = max(row.high for row in prior)
    low = min(row.low for row in prior)
    equilibrium = (high + low) / 2.0
    ordered = (
        [("PRIOR_240_MINUTE_EQUILIBRIUM", equilibrium), ("PRIOR_240_MINUTE_EXTERNAL", high if direction > 0 else low)]
        if prefer_equilibrium
        else [("PRIOR_240_MINUTE_EXTERNAL", high if direction > 0 else low), ("PRIOR_240_MINUTE_EQUILIBRIUM", equilibrium)]
    )
    for name, price in ordered:
        if direction * (price - reference) > 0.0:
            return name, price
    return None


def make_signal(
    *,
    candidate: AuctionCandidate,
    state: str,
    direction: int,
    confirm_ns: int,
    reference: float,
    stop_anchor: float,
    prior: list[MinuteFact],
    details: dict[str, Any],
) -> dict[str, Any] | None:
    stop = stop_anchor - direction * 0.20 * candidate.atr
    if direction * (reference - stop) <= 0.0:
        return None
    kind = "CONTINUATION" if direction == candidate.direction else "REVERSAL"
    target = waypoint(
        direction=direction,
        reference=reference,
        prior=prior,
        prefer_equilibrium=kind == "REVERSAL",
    )
    payload: dict[str, Any] = {
        "scenario_id": "NT-LVCFR-V19-" + state + "-" + sha256(
            f"{candidate.scenario_id}|{state}|{confirm_ns}|{direction}".encode()
        ).hexdigest()[:16],
        "scenario_kind": state,
        "entry_kind": kind,
        "confirm_time_ns": confirm_ns,
        "eligible_time_ns": confirm_ns,
        "direction": direction,
        "initial_stop": stop,
        "atr": candidate.atr,
        "first_start_time_ns": candidate.event_start_ns,
        "first_end_time_ns": candidate.confirm_time_ns,
        "disable_rapid_failure_reversal": True,
        "target_mode": "EXISTING_NET_R_OBJECTIVE",
        "details": {
            "inventory_regime": candidate.inventory_regime,
            "source_candidate_id": candidate.scenario_id,
            "event_low": candidate.event_low,
            "event_high": candidate.event_high,
            "event_midpoint": candidate.event_midpoint,
            "same_side_boundary": candidate.same_side_boundary,
            "stop_anchor": stop_anchor,
            **details,
        },
    }
    if target is not None:
        payload["structural_protection_trigger"] = target[1]
        payload["target_mode"] = "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
        payload["details"]["structural_waypoint_kind"] = target[0]
    return payload


def derive_v19(
    *,
    source_signals: Path,
    raw_root: Path,
    data_manifest_path: Path,
    output_signals: Path,
    output_manifest: Path,
) -> list[dict[str, Any]]:
    data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    evaluation_start_ns = int(data_manifest["evaluation_start_ns"])
    evaluation_end_ns = int(data_manifest["evaluation_end_ns"])
    candidates, candidate_counts = build_candidates(
        source_signals=source_signals,
        raw_root=raw_root,
        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
    )
    futures_contexts = collect_contexts(
        sorted((raw_root / "aggTrades").glob("*.zip")),
        candidates,
    )
    spot_contexts = collect_contexts(
        sorted((raw_root / "spot_aggTrades").glob("*.zip")),
        candidates,
    )
    futures_minutes = load_kline_minutes(sorted((raw_root / "futures_kline").glob("*.zip")))
    minutes = {row.minute_index: row for row in futures_minutes}

    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        v13_signals = derive_v13(
            source_signals=source_signals,
            raw_root=raw_root,
            output_signals=temp / "signals.json",
            output_manifest=temp / "manifest.json",
        )
    measured_by_source = {
        str(signal["scenario_id"]).split("-")[-1]: signal
        for signal in v13_signals
        if signal.get("scenario_kind") == MEASURED_ACCEPTANCE_CONTINUATION
    }

    output: list[dict[str, Any]] = []
    routing_counts: dict[str, int] = {
        EXECUTED_FLOW_VACUUM_CONTINUATION: 0,
        EXECUTED_FLOW_ABSORPTION_CHOCH_REVERSAL: 0,
        MEASURED_ACCEPTANCE_CONTINUATION: 0,
        "MIXED_NO_TRADE": 0,
        "INSUFFICIENT_CONTEXT": 0,
        "ABSORPTION_WITHOUT_CHOCH": 0,
    }
    threshold_rows: list[dict[str, Any]] = []

    for candidate in candidates:
        futures_context = futures_contexts[candidate.scenario_id]
        spot_context = spot_contexts[candidate.scenario_id]
        futures_baseline_blocks = futures_context.blocks[:BASELINE_BLOCKS]
        spot_baseline_blocks = spot_context.blocks[:BASELINE_BLOCKS]
        futures_valid = [block for block in futures_baseline_blocks if block.valid]
        spot_valid = [block for block in spot_baseline_blocks if block.valid]
        if len(futures_valid) < BASELINE_BLOCKS // 2 or len(spot_valid) < BASELINE_BLOCKS // 2:
            routing_counts["INSUFFICIENT_CONTEXT"] += 1
            continue
        futures_median_gross = median(block.gross_notional for block in futures_valid)
        spot_median_gross = median(block.gross_notional for block in spot_valid)
        futures_baseline_features = [
            feature
            for block in futures_valid
            if (feature := block_features(block, direction=candidate.direction, baseline_median_gross=futures_median_gross)) is not None
        ]
        spot_baseline_features = [
            feature
            for block in spot_valid
            if (feature := block_features(block, direction=candidate.direction, baseline_median_gross=spot_median_gross)) is not None
        ]
        if not futures_baseline_features or not spot_baseline_features:
            routing_counts["INSUFFICIENT_CONTEXT"] += 1
            continue
        thresholds = {
            "futures_response_q25": quantile([item.response_score for item in futures_baseline_features], 0.25),
            "futures_response_q75": quantile([item.response_score for item in futures_baseline_features], 0.75),
            "futures_flow_q75": quantile([item.directional_flow for item in futures_baseline_features], 0.75),
            "futures_efficiency_q50": quantile([item.path_efficiency for item in futures_baseline_features], 0.50),
        }
        outside_run = 0
        terminal: tuple[str, int, FlowFeatures, FlowFeatures] | None = None
        absorption_pending_ns: int | None = None
        observation_futures = futures_context.blocks[BASELINE_BLOCKS:]
        observation_spot = spot_context.blocks[BASELINE_BLOCKS:]
        for count in range(1, OBSERVATION_BLOCKS + 1):
            future_block = observation_futures[count - 1]
            outside = (
                future_block.valid
                and future_block.last_price is not None
                and candidate.direction * (future_block.last_price - candidate.same_side_boundary) > 0.0
            )
            outside_run = outside_run + 1 if outside else 0
            if count < 2:
                continue
            future_features = cumulative_features(
                observation_futures[:count],
                direction=candidate.direction,
                baseline_median_gross=futures_median_gross,
            )
            spot_features = cumulative_features(
                observation_spot[:count],
                direction=candidate.direction,
                baseline_median_gross=spot_median_gross,
            )
            if future_features is None or spot_features is None:
                continue
            high_response = (
                future_features.response_score
                >= max(0.0, thresholds["futures_response_q75"])
                and future_features.path_efficiency
                >= max(0.0, thresholds["futures_efficiency_q50"])
            )
            cross_market_agreement = (
                future_features.directional_flow > 0.0
                and spot_features.directional_flow > 0.0
                and spot_features.progress_bp > 0.0
            )
            if outside_run >= 2 and high_response and cross_market_agreement:
                terminal = (
                    EXECUTED_FLOW_VACUUM_CONTINUATION,
                    count,
                    future_features,
                    spot_features,
                )
                break
            strong_chase = future_features.directional_flow >= max(
                0.0, thresholds["futures_flow_q75"]
            )
            poor_response = future_features.response_score <= min(
                0.0, thresholds["futures_response_q25"]
            )
            spot_disagreement = (
                spot_features.directional_flow <= 0.0
                or spot_features.progress_bp <= 0.0
            )
            if strong_chase and poor_response and spot_disagreement:
                absorption_pending_ns = candidate.confirm_time_ns + count * BLOCK_NS
                break

        prior = prior_rows(minutes, candidate.event_start_ns)
        if absorption_pending_ns is not None:
            start_minute = absorption_pending_ns // NS_PER_MINUTE
            reversal_direction = -candidate.direction
            for offset in range(ABSORPTION_EXPIRY_MINUTES):
                minute = start_minute + offset
                row = minutes.get(minute)
                if row is None:
                    break
                choch = (
                    row.close < candidate.event_low
                    if candidate.direction > 0
                    else row.close > candidate.event_high
                )
                if not choch:
                    continue
                signal = make_signal(
                    candidate=candidate,
                    state=EXECUTED_FLOW_ABSORPTION_CHOCH_REVERSAL,
                    direction=reversal_direction,
                    confirm_ns=(minute + 1) * NS_PER_MINUTE,
                    reference=row.close,
                    stop_anchor=candidate.event_high if candidate.direction > 0 else candidate.event_low,
                    prior=prior,
                    details={
                        "absorption_pending_ns": absorption_pending_ns,
                        "choch_wait_minutes": offset + 1,
                        "thresholds": thresholds,
                    },
                )
                if signal is not None:
                    output.append(signal)
                    routing_counts[EXECUTED_FLOW_ABSORPTION_CHOCH_REVERSAL] += 1
                break
            else:
                routing_counts["ABSORPTION_WITHOUT_CHOCH"] += 1
            continue

        if terminal is not None:
            state, block_count, future_features, spot_features = terminal
            confirm_ns = candidate.confirm_time_ns + block_count * BLOCK_NS
            signal = make_signal(
                candidate=candidate,
                state=state,
                direction=candidate.direction,
                confirm_ns=confirm_ns,
                reference=future_features.last_price,
                stop_anchor=candidate.same_side_boundary,
                prior=prior,
                details={
                    "observation_blocks": block_count,
                    "futures_features": asdict(future_features),
                    "spot_features": asdict(spot_features),
                    "thresholds": thresholds,
                },
            )
            if signal is not None:
                output.append(signal)
                routing_counts[state] += 1
            continue

        source_suffix = str(candidate.details.get("source_scenario_id", "")).split("-")[-1]
        measured = measured_by_source.get(source_suffix)
        if measured is not None and candidate.inventory_regime == "OI_CONTRACTION":
            item = dict(measured)
            item["scenario_id"] = "NT-LVCFR-V19-MEASURED-" + source_suffix
            details = dict(item.get("details", {}))
            details.update(
                {
                    "inventory_regime": candidate.inventory_regime,
                    "microstructure_state": "MIXED_THEN_MEASURED_ACCEPTANCE",
                    "thresholds": thresholds,
                }
            )
            item["details"] = details
            output.append(item)
            routing_counts[MEASURED_ACCEPTANCE_CONTINUATION] += 1
            continue

        if candidate.inventory_regime == "OI_EXPANSION":
            objective = candidate.same_side_boundary + candidate.direction * (
                candidate.event_high - candidate.event_low
            )
            start_minute = (candidate.confirm_time_ns + OBSERVATION_SECONDS * NS_PER_SECOND) // NS_PER_MINUTE
            for offset in range(MEASURED_ACCEPTANCE_EXPIRY_MINUTES):
                minute = start_minute + offset
                row = minutes.get(minute)
                if row is None:
                    break
                if candidate.direction * (row.close - objective) < 0.0:
                    continue
                signal = make_signal(
                    candidate=candidate,
                    state=MEASURED_ACCEPTANCE_CONTINUATION,
                    direction=candidate.direction,
                    confirm_ns=(minute + 1) * NS_PER_MINUTE,
                    reference=row.close,
                    stop_anchor=candidate.same_side_boundary,
                    prior=prior,
                    details={
                        "microstructure_state": "MIXED_THEN_MEASURED_ACCEPTANCE",
                        "measured_objective": objective,
                        "measured_wait_minutes": offset + 1,
                        "thresholds": thresholds,
                    },
                )
                if signal is not None:
                    output.append(signal)
                    routing_counts[MEASURED_ACCEPTANCE_CONTINUATION] += 1
                break
            else:
                routing_counts["MIXED_NO_TRADE"] += 1
        else:
            routing_counts["MIXED_NO_TRADE"] += 1
        threshold_rows.append(
            {
                "candidate": candidate.scenario_id,
                "inventory_regime": candidate.inventory_regime,
                "thresholds": thresholds,
            }
        )

    output.sort(key=lambda item: (int(item["confirm_time_ns"]), str(item["scenario_id"])))
    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state_counts: dict[str, int] = {}
    regime_counts: dict[str, int] = {}
    for signal in output:
        state = str(signal.get("scenario_kind"))
        regime = str(signal.get("details", {}).get("inventory_regime", "UNKNOWN"))
        state_counts[state] = state_counts.get(state, 0) + 1
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v19-executed-flow-resilience",
        "engine_status": "causal_schedule_only_no_backtest",
        "candidate_counts": candidate_counts,
        "derived_signal_count": len(output),
        "state_counts": dict(sorted(state_counts.items())),
        "inventory_regime_counts": dict(sorted(regime_counts.items())),
        "routing_counts": routing_counts,
        "state_sequence": [
            "UNIFORM_FUTURES_AND_SPOT_AGGTRADES",
            "TEN_MINUTE_EVENT_EXCLUDED_BASELINE",
            "SEQUENTIAL_SIXTY_SECOND_RESPONSE",
            "HIGH_RESPONSE_CROSS_MARKET_VACUUM_CONTINUATION",
            "OR_STRONG_CHASE_POOR_RESPONSE_SPOT_DISAGREEMENT",
            "THEN_FULL_EVENT_RANGE_CHOCH_REVERSAL",
            "OR_LATER_FULL_EVENT_RANGE_MEASURED_ACCEPTANCE",
            "NO_TRADE_IF_UNRESOLVED",
        ],
        "threshold_policy": "candidate-local causal quartiles, three coherent evidence axes, no return-fit search",
        "baseline_minutes": BASELINE_MINUTES,
        "block_seconds": BLOCK_SECONDS,
        "observation_seconds": OBSERVATION_SECONDS,
        "absorption_expiry_minutes": ABSORPTION_EXPIRY_MINUTES,
        "measured_acceptance_expiry_minutes": MEASURED_ACCEPTANCE_EXPIRY_MINUTES,
        "threshold_diagnostics": threshold_rows,
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    signals = derive_v19(
        source_signals=source,
        raw_root=prepared / "raw",
        data_manifest_path=prepared / "data_manifest.json",
        output_signals=prepared / "signals.json",
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"derived_signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
