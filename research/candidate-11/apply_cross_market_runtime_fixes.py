#!/usr/bin/env python3
"""Apply idempotent cross-market causal and execution-boundary repairs."""
from __future__ import annotations

from pathlib import Path

SAME_BATCH_MARKER = "C11_CROSS_SAME_BATCH_CONFIRMATION"
CAUSAL_RETEST_MARKER = "C11_CROSS_CAUSAL_RETEST_ENTRY"
NO_MAX_STOP_MARKER = "C11_CROSS_NO_DUPLICATE_MAX_STOP"
EVENT_FLOOR_MARKER = "C11_CROSS_COMPLETION_EVENT"
PARTIAL_MARKER = "C11_CROSS_PARTIAL_FAIL_CLOSED"
RISK_TYPE_MARKER = "C11_CROSS_RISK_FLOAT_BOUNDARY"
WIDE_STOP_TEST_MARKER = "test_wide_structural_stop_is_governed_by_costed_r_not_arbitrary_cap"


def replace_one(source: str, old: str, new: str, label: str) -> tuple[str, int]:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    return source.replace(old, new, 1), 1


def patch_detector(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    changed = 0
    if NO_MAX_STOP_MARKER not in source:
        source, count = replace_one(
            source,
            '''        if not 0.08 * atr <= stop_distance <= 1.50 * atr:
            self.skips["CROSS_MARKET_STOP_GEOMETRY"] += 1
            return None
''',
            '''        # C11_CROSS_NO_DUPLICATE_MAX_STOP: the exact 3% NAV sizer,
        # available margin and post-cost structural R already govern exposure.
        # A second arbitrary upper stop-width cap rejects valid causal structures.
        if stop_distance < 0.08 * atr:
            self.skips["CROSS_MARKET_STOP_GEOMETRY"] += 1
            return None
''',
            "maximum stop removal",
        )
        changed += count
    if CAUSAL_RETEST_MARKER not in source:
        source, count = replace_one(
            source,
            '''            previous = bars[-4:-1]
            structure = (
                bars[-1].close > max(value.high for value in previous)
                if shock.direction == "LONG"
                else bars[-1].close < min(value.low for value in previous)
            )
            own_catchup = sign * latest_return > 0.0 and sign * residual_latest > 0.0
            if not (structure and own_catchup and flow_z >= 0.50):
                continue
            entry = (bars[-1].open + bars[-1].close) / 2.0
''',
            '''            previous = bars[-4:-1]
            # C11_CROSS_CAUSAL_RETEST_ENTRY: confirmation is the completed close
            # beyond the follower's own local structure. Entry is the first
            # passive retest of that already-known boundary, not an arbitrary
            # midpoint of the displacement candle.
            structure_boundary = (
                max(value.high for value in previous)
                if shock.direction == "LONG"
                else min(value.low for value in previous)
            )
            structure = (
                bars[-1].close > structure_boundary
                if shock.direction == "LONG"
                else bars[-1].close < structure_boundary
            )
            own_catchup = sign * latest_return > 0.0 and sign * residual_latest > 0.0
            if not (structure and own_catchup and flow_z >= 0.50):
                continue
            entry = structure_boundary
''',
            "causal retest entry",
        )
        changed += count
        source, count = replace_one(
            source,
            '''                    "entry_cost_assumption": "MAKER",
                    "target_method": "FROZEN_BETA_IMPLIED_PEER_EQUILIBRIUM",
''',
            '''                    "entry_cost_assumption": "MAKER",
                    "entry_method": "FIRST_RETEST_OF_FOLLOWER_STRUCTURE_BREAK",
                    "target_method": "FROZEN_BETA_IMPLIED_PEER_EQUILIBRIUM_AT_CONFIRMATION",
''',
            "entry method evidence",
        )
        changed += count
    if SAME_BATCH_MARKER not in source:
        source, count = replace_one(
            source,
            '''        self._detect_shock(ts_ns)
        return []
''',
            '''        self._detect_shock(ts_ns)
        # C11_CROSS_SAME_BATCH_CONFIRMATION: all values are from the same fully
        # completed synchronized minute. A follower which confirms during the
        # leader's detection batch may therefore be evaluated immediately.
        if self.active is not None:
            return self._evaluate_followers(ts_ns)
        return []
''',
            "same-batch detector",
        )
        changed += count
    path.write_text(source, encoding="utf-8")
    return changed


def patch_runner(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    changed = 0
    if RISK_TYPE_MARKER not in source:
        source, count = replace_one(
            source,
            '            self.sizer = RiskSizer(Decimal(str(account["risk_fraction"])))\n',
            '''            # C11_CROSS_RISK_FLOAT_BOUNDARY: RiskSizer owns Decimal
            # conversion internally. Passing a Decimal into its float-bounded
            # constructor can make exact 0.03 compare above binary 0.03.
            self.sizer = RiskSizer(float(account["risk_fraction"]))
''',
            "risk-sizer runner",
        )
        changed += count
    if PARTIAL_MARKER not in source:
        source, count = replace_one(
            source,
            '''            if self.position_open and self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                if not self.portfolio.is_flat(instrument_id):
                    self.lifecycle.append({
                        "type": "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED",
''',
            '''            # C11_CROSS_PARTIAL_FAIL_CLOSED: inspect the Nautilus
            # portfolio directly. The parent-expiry event can race the strategy's
            # internal entry-state transition after a partial fill.
            if self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                if not self.portfolio.is_flat(instrument_id):
                    self.lifecycle.append({
                        "type": "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED",
''',
            "partial-entry runner",
        )
        changed += count
    if EVENT_FLOOR_MARKER not in source:
        source, count = replace_one(
            source,
            '''        with (output_dir / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in strategy.detector.events:
                stream.write(json.dumps(event, sort_keys=True, default=str) + "\\n")
''',
            '''        with (output_dir / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in strategy.detector.events:
                stream.write(json.dumps(event, sort_keys=True, default=str) + "\\n")
            # C11_CROSS_COMPLETION_EVENT: preserve an auditable non-empty ledger
            # even when a frozen interval contains no qualifying shock.
            stream.write(json.dumps({
                "type": "CROSS_MARKET_RUN_COMPLETED",
                "observed_ts_ns": evaluation_end_ns,
                "week_id": week_id,
                "detector_events": len(strategy.detector.events),
                "success_claim": False,
            }, sort_keys=True) + "\\n")
''',
            "completion-event runner",
        )
        changed += count
    path.write_text(source, encoding="utf-8")
    return changed


def patch_tests(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    if WIDE_STOP_TEST_MARKER in source:
        return 0
    anchor = '''    def test_signed_flow_matches_aggressor_side(self) -> None:
'''
    test = '''    def test_wide_structural_stop_is_governed_by_costed_r_not_arbitrary_cap(self) -> None:
        engine = CausalLeaderFollowerEngine(self.SYMBOLS)
        shock = _Shock(
            shock_id="TEST-WIDE",
            leader="BTCUSDT",
            direction="LONG",
            detected_ts_ns=10 * MINUTE_NS,
            base_ts_ns=7 * MINUTE_NS,
            base_prices={symbol: 100.0 for symbol in self.SYMBOLS},
            betas={symbol: 1.0 for symbol in self.SYMBOLS},
            residual_rms={symbol: 0.001 for symbol in self.SYMBOLS},
            flow_rms={symbol: 1000.0 for symbol in self.SYMBOLS},
            leader_initial_move=0.01,
            leader_peak_move=0.01,
            leader_score=2.0,
        )
        bar = CrossObservation(
            ts_ns=11 * MINUTE_NS,
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.4,
            volume=100.0,
            quote_volume=10_000.0,
            taker_buy_volume=80.0,
        )
        plan = engine._costed_plan(
            symbol="ETHUSDT",
            bar=bar,
            shock=shock,
            entry=100.2,
            stop=98.0,
            target=106.0,
            atr=1.0,
            signal_score=3.0,
            details={"source": "TEST"},
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertGreater(abs(plan.expected_entry - plan.stop_price), 1.5)
        self.assertGreaterEqual(plan.net_r, 1.25)

'''
    source, _ = replace_one(source, anchor, test + anchor, "wide-stop regression test")
    path.write_text(source, encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    changed = patch_detector(root / "cross_market.py")
    changed += patch_runner(root / "run_cross_market_nautilus.py")
    changed += patch_tests(root / "test_cross_market.py")
    print(f"cross-market runtime fixes applied: {changed}")


if __name__ == "__main__":
    main()
