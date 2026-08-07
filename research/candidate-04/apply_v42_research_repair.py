#!/usr/bin/env python3
"""Idempotent implementation-contract repairs for candidate-04 V34/V37/V38/V41.

These changes do not tune alpha thresholds, risk, fees, slippage or order
matching. They only align existing code with its written causal contracts:
completed event-time states, real counter-auctions, event-specific inventory,
pre-existing external-liquidity targets, and route-aware controlled ablation.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
C04 = ROOT / "research/candidate-04"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"{label}: source marker not found")
    return text.replace(old, new, 1)


def patch_target_contract() -> None:
    path = C04 / "nt_rich_signal_strategy.py"
    text = path.read_text(encoding="utf-8")
    old = '''        if target is not None:
            target_price = target.price
            target_net_r = target.net_r
            target_source = target.source
        else:
            projection = self._projection_target(entry, stop, side, cost_rate)
            if projection is None:
                self._event(
                    "RICH_SIGNAL_NO_CAUSAL_TARGET",
                    scenario,
                    row,
                    {"signal": signal, "entry": entry, "stop": stop},
                )
                return False
            target_price, target_net_r, target_source = projection
'''
    new = '''        if target is None:
            self._event(
                "RICH_SIGNAL_NO_CAUSAL_TARGET",
                scenario,
                row,
                {
                    "signal": signal,
                    "entry": entry,
                    "stop": stop,
                    "target_contract": "pre_existing_external_liquidity_only",
                    "projection_fallback_disabled": True,
                },
            )
            return False
        target_price = target.price
        target_net_r = target.net_r
        target_source = target.source
'''
    text = replace_once(text, old, new, "external-liquidity target contract")
    path.write_text(text, encoding="utf-8")


def patch_v37() -> None:
    path = C04 / "volume_clock_impact_residual_compiler.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "OI_RETENTION = 0.999\nOI_REBUILD_TOLERANCE = 1.001\n",
        "NEW_INVENTORY_RETENTION_FRACTION = 0.80\n"
        "NEW_INVENTORY_UNWIND_FRACTION = 0.50\n"
        "LIQUIDATION_REBUILD_FRACTION = 0.20\n"
        "# Retained for compatibility with older V38 evidence only.\n"
        "OI_REBUILD_TOLERANCE = 1.001\n",
        "V37 event-specific inventory constants",
    )
    text = replace_once(
        text,
        '''        while end < len(data) and end < start + MAX_BUCKET_BARS:
            row = data.iloc[end]
            minute_notional = max(finite(row["notional_60s"]), 0.0)
            minute_flow = finite(row["flow_60s"])
            if math.isfinite(minute_notional):
                notional += minute_notional
            if math.isfinite(minute_flow) and math.isfinite(minute_notional):
                signed_effort += minute_flow * minute_notional
            takes.extend(external_takes.get(end, ()))
            if notional >= target:
                break
            end += 1
        if end >= len(data):
            break
        if notional <= 0.0:
            index = end + 1
            continue
''',
        '''        reached_target = False
        last_processed = start - 1
        while end < len(data) and end < start + MAX_BUCKET_BARS:
            row = data.iloc[end]
            minute_notional = max(finite(row["notional_60s"]), 0.0)
            minute_flow = finite(row["flow_60s"])
            if math.isfinite(minute_notional):
                notional += minute_notional
            if math.isfinite(minute_flow) and math.isfinite(minute_notional):
                signed_effort += minute_flow * minute_notional
            takes.extend(external_takes.get(end, ()))
            last_processed = end
            if notional >= target:
                reached_target = True
                break
            end += 1
        if last_processed < start:
            break
        end = last_processed
        if not reached_target or notional <= 0.0:
            # A volume-clock state exists only after the frozen information
            # amount has arrived. Partial states cannot enter later thresholds.
            index = end + 1
            continue
''',
        "V37 completed volume bucket boundary",
    )
    text = replace_once(
        text,
        '''def pullback_retains_displacement(
    shock: VolumeBucket,
    pullback: VolumeBucket,
) -> bool:
    displacement = shock.close - shock.start_price
    if shock.side * displacement <= 0.0:
        return False
    retained = pullback.close - shock.start_price
    return shock.side * retained >= PULLBACK_RETAIN_FRACTION * abs(displacement)


def weak_counter_flow(
    shock: VolumeBucket,
    pullback: VolumeBucket,
) -> bool:
    counter = -shock.side * pullback.imbalance
    if counter <= 0.0:
        return abs(pullback.imbalance) <= abs(shock.imbalance)
    return counter <= MAX_COUNTER_IMBALANCE_FRACTION * abs(shock.imbalance)
''',
        '''def pullback_retains_displacement(
    shock: VolumeBucket,
    pullback: VolumeBucket,
) -> bool:
    displacement = shock.side * (shock.close - shock.start_price)
    counter_price = -shock.side * (pullback.close - shock.close)
    if displacement <= 0.0 or counter_price <= 0.0:
        return False
    retained = shock.side * (pullback.close - shock.start_price)
    return (
        retained >= PULLBACK_RETAIN_FRACTION * displacement
        and retained < displacement
    )


def weak_counter_flow(
    shock: VolumeBucket,
    pullback: VolumeBucket,
) -> bool:
    counter = -shock.side * pullback.imbalance
    return (
        counter > 0.0
        and counter <= MAX_COUNTER_IMBALANCE_FRACTION * abs(shock.imbalance)
    )
''',
        "V37 real counter-auction",
    )
    text = replace_once(
        text,
        '''def oi_creation_retained(shock: VolumeBucket, later: VolumeBucket) -> bool:
    return bool(
        math.isfinite(later.oi_end)
        and math.isfinite(shock.oi_end)
        and later.oi_end >= OI_RETENTION * shock.oi_end
    )
''',
        '''def oi_creation_retained(shock: VolumeBucket, later: VolumeBucket) -> bool:
    if not all(
        math.isfinite(value)
        for value in (shock.oi_before, shock.oi_end, later.oi_end)
    ):
        return False
    created = shock.oi_end - shock.oi_before
    return (
        created > 0.0
        and later.oi_end
        >= shock.oi_before + NEW_INVENTORY_RETENTION_FRACTION * created
    )
''',
        "V37 created-inventory retention",
    )
    text = replace_once(
        text,
        '''def route_inventory_resolved(
    route: str,
    shock: VolumeBucket,
    later: VolumeBucket,
) -> bool:
    if not all(math.isfinite(value) for value in (shock.oi_end, later.oi_end)):
        return False
    if route == "NEW_INVENTORY":
        return later.oi_end < shock.oi_end
    if route == "LIQUIDATION":
        return later.oi_end <= OI_REBUILD_TOLERANCE * shock.oi_end
    return False
''',
        '''def route_inventory_resolved(
    route: str,
    shock: VolumeBucket,
    later: VolumeBucket,
) -> bool:
    if not all(
        math.isfinite(value)
        for value in (shock.oi_before, shock.oi_end, later.oi_end)
    ):
        return False
    if route == "NEW_INVENTORY":
        created = shock.oi_end - shock.oi_before
        return (
            created > 0.0
            and later.oi_end
            <= shock.oi_end - NEW_INVENTORY_UNWIND_FRACTION * created
        )
    if route == "LIQUIDATION":
        depleted = shock.oi_before - shock.oi_end
        return (
            depleted > 0.0
            and later.oi_end
            <= shock.oi_end + LIQUIDATION_REBUILD_FRACTION * depleted
        )
    return False
''',
        "V37 cause-specific inventory resolution",
    )
    text = replace_once(
        text,
        '''        stop = structural_stop(
            data,
            pullback.start_index,
            resume.end_index,
            shock.side,
            impact_parameters,
        )
''',
        '''        if resume.end_index + 1 >= len(data):
            continue
        stop = structural_stop(
            data,
            shock.start_index,
            resume.end_index,
            shock.side,
            impact_parameters,
        )
''',
        "V37 complete continuation invalidation",
    )
    text = replace_once(
        text,
        '''        stop = structural_stop(
            data,
            shock.start_index,
            reclaim.end_index,
            trade_side,
            impact_parameters,
        )
''',
        '''        if reclaim.end_index + 1 >= len(data):
            continue
        stop = structural_stop(
            data,
            shock.start_index,
            reclaim.end_index,
            trade_side,
            impact_parameters,
        )
''',
        "V37 reversal next-bar guard",
    )
    text = replace_once(
        text,
        '''        position = max(position + 1, resolved_position + 1)

    intents.sort(key=lambda item: int(item.signal_index))
''',
        '''        if intent is None:
            # A failed setup does not consume later completed buckets. Each can
            # seed its own independent state on the next loop iteration.
            position += 1
            continue
        next_position = max(position + 1, resolved_position + 1)
        # Confirmation buckets consumed by an accepted pattern remain part of
        # later past-only distributions.
        history.extend(buckets[position + 1 : next_position])
        position = next_position

    intents.sort(key=lambda item: int(item.signal_index))
''',
        "V37 completed-history accounting",
    )
    path.write_text(text, encoding="utf-8")


def patch_v38() -> None:
    path = C04 / "volume_clock_imbalance_gap_compiler.py"
    text = path.read_text(encoding="utf-8")
    marker = "def bucket_touches_gap(bucket: v37.VolumeBucket, gap: ImbalanceGap) -> bool:\n"
    helper = '''def choose_gap_state(
    gap: ImbalanceGap,
    buckets: list[v37.VolumeBucket],
    thresholds: v37.BucketThresholds,
) -> GapState | None:
    """Route exact reclaimed external-pool failure before generic displacement."""

    inverse = inverse_gap_state(gap, buckets, thresholds)
    return inverse if inverse is not None else informed_gap_state(
        gap,
        buckets,
        thresholds,
    )


'''
    if "def choose_gap_state(" not in text:
        if marker not in text:
            raise RuntimeError("V38 state-priority marker not found")
        text = text.replace(marker, helper + marker, 1)
    text = replace_once(
        text,
        '''def weak_retrace(
    bucket: v37.VolumeBucket,
    state: GapState,
) -> bool:
    directional_counter = -state.gap.side * bucket.imbalance
    if directional_counter <= 0.0:
        return abs(bucket.imbalance) <= state.thresholds.imbalance_q75
    return directional_counter <= state.thresholds.imbalance_q50
''',
        '''def weak_retrace(
    bucket: v37.VolumeBucket,
    state: GapState,
) -> bool:
    counter_flow = -state.gap.side * bucket.imbalance
    counter_return = -state.gap.side * bucket.return_bps
    return (
        counter_flow > 0.0
        and counter_return > 0.0
        and counter_flow <= state.thresholds.imbalance_q50
    )
''',
        "V38 actual counter retrace",
    )
    text = replace_once(
        text,
        '''    if state.inventory_route == "LIQUIDATION":
        if change >= 0.0:
            return False
        return bucket.oi_end <= state.source_oi_end * v37.OI_REBUILD_TOLERANCE
''',
        '''    if state.inventory_route == "LIQUIDATION":
        depleted = state.source_oi_before - state.source_oi_end
        return (
            depleted > 0.0
            and bucket.oi_end
            <= state.source_oi_end
            + v37.LIQUIDATION_REBUILD_FRACTION * depleted
        )
''',
        "V38 liquidation resolution",
    )
    text = replace_once(
        text,
        '''        state = informed_gap_state(gap, buckets, thresholds)
        if state is not None:
            counts["informed_gap_states"] += 1
        else:
            state = inverse_gap_state(gap, buckets, thresholds)
            if state is not None:
                counts["inverse_gap_states"] += 1
''',
        '''        state = choose_gap_state(gap, buckets, thresholds)
        if state is not None and state.state == "INFORMED_GAP":
            counts["informed_gap_states"] += 1
        elif state is not None:
            counts["inverse_gap_states"] += 1
''',
        "V38 external-cause priority",
    )
    text = replace_once(
        text,
        '''        next_position = max(position + 1, resolved_position + 1)
        history.extend(buckets[position + 1 : next_position])
        position = next_position
        if intent is None:
            counts["unresolved_gap_states"] += 1
            continue
''',
        '''        if intent is None:
            counts["unresolved_gap_states"] += 1
            position += 1
            continue
        next_position = max(position + 1, resolved_position + 1)
        history.extend(buckets[position + 1 : next_position])
        position = next_position
''',
        "V38 unresolved-state overlap preservation",
    )
    path.write_text(text, encoding="utf-8")


def patch_route_infrastructure() -> None:
    filter_path = C04 / "filter_candidate_signals.py"
    text = filter_path.read_text(encoding="utf-8")
    marker = '''    "v36": {
        "full": None,
        "continuation": {
            "MICRO_BALANCE_NEW_INVENTORY_RETEST_CONTINUATION",
            "MICRO_BALANCE_LIQUIDATION_RETEST_CONTINUATION",
        },
        "reversal": {
            "MICRO_BALANCE_TRAPPED_BREAKOUT_REVERSAL",
            "MICRO_BALANCE_LIQUIDATION_EXHAUSTION_REVERSAL",
        },
    },
'''
    addition = marker + '''    "v37": {
        "full": None,
        "continuation": {
            "VOLUME_CLOCK_INFORMED_INVENTORY_PULLBACK_CONTINUATION",
        },
        "reversal": {
            "VOLUME_CLOCK_TRAPPED_INVENTORY_ABSORPTION_REVERSAL",
            "VOLUME_CLOCK_LIQUIDATION_ABSORPTION_REVERSAL",
        },
    },
    "v38": {
        "full": None,
        "continuation": {
            "VOLUME_CLOCK_INFORMED_GAP_RETEST_CONTINUATION",
        },
        "reversal": {
            "VOLUME_CLOCK_TRAPPED_INVENTORY_INVERSE_GAP_REVERSAL",
            "VOLUME_CLOCK_LIQUIDATION_INVERSE_GAP_REVERSAL",
        },
    },
    "v41": {
        "full": None,
        "continuation": {
            "DEPTH_NORMALIZED_POSITIVE_INNOVATION_PULLBACK_CONTINUATION",
        },
        "reversal": {
            "EXTERNAL_POOL_NEGATIVE_INNOVATION_TRAPPED_REVERSAL",
            "EXTERNAL_POOL_NEGATIVE_INNOVATION_LIQUIDATION_REVERSAL",
        },
    },
'''
    text = replace_once(text, marker, addition, "V37/V38/V41 route families")
    filter_path.write_text(text, encoding="utf-8")

    runner_path = C04 / "run_frozen_btc_route.py"
    runner = runner_path.read_text(encoding="utf-8")
    runner = replace_once(
        runner,
        'choices=("v33", "v34", "v35", "v36"),',
        'choices=("v33", "v34", "v35", "v36", "v37", "v38", "v41"),',
        "route runner family choices",
    )
    runner_path.write_text(runner, encoding="utf-8")


def main() -> None:
    required = [
        C04 / "nt_rich_signal_strategy.py",
        C04 / "volume_clock_impact_residual_compiler.py",
        C04 / "volume_clock_imbalance_gap_compiler.py",
        C04 / "filter_candidate_signals.py",
        C04 / "run_frozen_btc_route.py",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing repair inputs: " + ", ".join(missing))
    patch_target_contract()
    patch_v37()
    patch_v38()
    patch_route_infrastructure()
    print("candidate-04 V42 implementation-contract repair applied")


if __name__ == "__main__":
    main()
