#!/usr/bin/env python3
"""Idempotent fail-closed migrations for the materialized Candidate 11 SCDAM."""
from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> bool:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return False
    if source.count(old) != 1:
        raise SystemExit(f"{label}: expected one migration anchor, found {source.count(old)}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def migrate_logic(root: Path) -> int:
    path = root / "logic.py"
    changed = 0
    changed += replace_once(
        path,
        '            "OBSERVE", "PENDING_ENTRY", "RECLAIM_MSS_DISPLACEMENT_TO_PAIRED_DRAW", a.pool.level,',
        '            "OBSERVE", "FAR_CONFIRMED", "RECLAIM_MSS_DISPLACEMENT_TO_PAIRED_DRAW", a.pool.level,',
        "FAR confirmation state",
    )
    changed += replace_once(
        path,
        '            "OBSERVE", "PENDING_ENTRY", "OUTSIDE_HOLD_CAUSAL_PULLBACK_REACCELERATION", a.pool.level,',
        '            "OBSERVE", "AAC_CONFIRMED", "OUTSIDE_HOLD_CAUSAL_PULLBACK_REACCELERATION", a.pool.level,',
        "AAC confirmation state",
    )
    changed += replace_once(
        path,
        '''    def mark_rejected(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:\n        if self.active is None or self.active.pool.scenario_id != plan.scenario_id:\n            return\n        self._event(plan.scenario_id, "ENTRY_PLAN_REJECTED", plan.observed_ts_ns, ts_ns, "CONFIRMED", "TERMINAL", reason, plan.expected_entry, details or {})\n        self.skips[reason] += 1\n        self.active = None\n''',
        '''    def mark_rejected(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:\n        if self.active is None or self.active.pool.scenario_id != plan.scenario_id:\n            return\n        previous_state = self.active.state\n        self._event(\n            plan.scenario_id,\n            "ENTRY_PLAN_REJECTED",\n            plan.observed_ts_ns,\n            ts_ns,\n            previous_state,\n            "TERMINAL",\n            reason,\n            plan.expected_entry,\n            details or {},\n        )\n        self.skips[reason] += 1\n        self.active = None\n''',
        "entry rejection state",
    )
    return changed


def migrate_test(root: Path) -> int:
    path = root / "test_logic.py"
    source = path.read_text(encoding="utf-8")
    if "test_far_confirmation_plan_and_rejection_form_one_state_chain" in source:
        return 0
    anchor = '''    def test_insufficient_costed_r_is_terminal_not_tuned(self) -> None:\n        auction, confirmation = self._auction(Direction.LONG)\n        auction.target_price = 108.0\n        plan = self.engine._costed_limit_plan(auction, confirmation, "TEST")\n        self.assertIsNone(plan)\n        self.assertIsNone(self.engine.active)\n        self.assertEqual(self.engine.skips["INSUFFICIENT_COSTED_STRUCTURAL_R"], 1)\n'''
    addition = '''    def test_far_confirmation_plan_and_rejection_form_one_state_chain(self) -> None:\n        trigger = pool("TRIGGER", Side.HIGH, 100.0, range_id="R", opposite=80.0)\n        target = pool("TARGET", Side.LOW, 80.0, range_id="R", opposite=100.0)\n        self.engine.pools = [trigger, target]\n        confirmation = bar(100 * MINUTE_NS, 104.0, 106.0, 98.0, 100.0, buy=20.0)\n        auction = Auction(\n            pool=trigger,\n            sweep=bar(90 * MINUTE_NS, 99.0, 104.0, 98.0, 101.0, buy=70.0),\n            sweep_index=0,\n            atr=10.0,\n            internal_level=103.0,\n            sweep_extreme=104.0,\n            rejection_seed=True,\n            acceptance_seed=False,\n            reclaim_seen=True,\n            reversal_target_pool_id=target.scenario_id,\n            reversal_target_level=target.level,\n        )\n        self.engine.active = auction\n        self.engine.bars = [confirmation]\n        self.engine._index = 0\n        plan = self.engine._confirm_far(auction, confirmation)\n        self.assertIsNotNone(plan)\n        assert plan is not None\n        self.engine.mark_rejected(plan, confirmation.ts_ns, "TEST_REJECTION")\n        last_by_scenario = {}\n        for event in self.engine.events:\n            previous = last_by_scenario.get(event.scenario_id)\n            if previous is not None:\n                self.assertEqual(event.previous_state, previous.next_state)\n            last_by_scenario[event.scenario_id] = event\n        self.assertEqual(\n            [(event.previous_state, event.next_state) for event in self.engine.events],\n            [("OBSERVE", "FAR_CONFIRMED"), ("FAR_CONFIRMED", "PENDING_ENTRY"), ("PENDING_ENTRY", "TERMINAL")],\n        )\n\n'''
    if source.count(anchor) != 1:
        raise SystemExit("event-chain regression-test anchor is not unique")
    path.write_text(source.replace(anchor, addition + anchor, 1), encoding="utf-8")
    return 1


def migrate_run_evidence(root: Path) -> int:
    path = root / "run.py"
    changed = 0
    changed += replace_once(
        path,
        "from smc_ict_4.event_log import write_events\n",
        "from smc_ict_4.event_log import EventLogError, write_events\n",
        "event-log import",
    )
    source = path.read_text(encoding="utf-8")
    if "def _write_raw_events(" not in source:
        anchor = '''COLUMNS = (\n    "open_time", "open", "high", "low", "close", "volume", "close_time",\n    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",\n)\n\n\n'''
        helper = '''COLUMNS = (\n    "open_time", "open", "high", "low", "close", "volume", "close_time",\n    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",\n)\n\n\ndef _write_raw_events(path: Path, events: list[Any]) -> Path:\n    """Persist diagnostics before validation; raw events are never success evidence."""\n    path.parent.mkdir(parents=True, exist_ok=True)\n    temporary = path.with_suffix(path.suffix + ".tmp")\n    with temporary.open("w", encoding="utf-8", newline="\\n") as stream:\n        for event in events:\n            stream.write(json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False) + "\\n")\n    temporary.replace(path)\n    return path\n\n\n'''
        if source.count(anchor) != 1:
            raise SystemExit("raw-event helper anchor is not unique")
        path.write_text(source.replace(anchor, helper, 1), encoding="utf-8")
        changed += 1
    source = path.read_text(encoding="utf-8")
    if 'metrics["event_log_valid"] = True' not in source:
        old = '''        write_events(output_dir / "scenario_events.jsonl", strategy.logic.events)\n        write_json_atomic(output_dir / "submitted_plans.json", {"plans": strategy.plans})\n        write_json_atomic(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})\n        write_json_atomic(output_dir / "metrics.json", metrics)\n        manifest = create_run_manifest(\n            run_id=f"candidate-11-{week_id.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",\n            candidate=config["candidate"],\n            config_path=config_path,\n            data_manifest_path=output_dir / "data_manifest.json",\n            extra={\n                "week_id": week_id,\n                "bar_type": str(bar_type),\n                "evaluation_start": evaluation_start.isoformat(),\n                "evaluation_end_exclusive": evaluation_end.isoformat(),\n                "logic": config["logic"],\n                "execution": config["execution"],\n                "metrics_path": str(output_dir / "metrics.json"),\n            },\n        )\n        write_json_atomic(output_dir / "run.json", manifest)\n        return metrics\n'''
        new = '''        _write_raw_events(output_dir / "scenario_events.raw.jsonl", strategy.logic.events)\n        write_json_atomic(output_dir / "submitted_plans.json", {"plans": strategy.plans})\n        write_json_atomic(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})\n        event_log_error: str | None = None\n        try:\n            write_events(output_dir / "scenario_events.jsonl", strategy.logic.events)\n            metrics["event_log_valid"] = True\n            metrics["event_log_error"] = None\n        except EventLogError as exc:\n            event_log_error = str(exc)\n            metrics["event_log_valid"] = False\n            metrics["event_log_error"] = event_log_error\n            metrics["promising_gate_passed"] = False\n            metrics["complete_gate_passed"] = False\n            metrics["success_claim"] = False\n        write_json_atomic(output_dir / "metrics.json", metrics)\n        manifest = create_run_manifest(\n            run_id=f"candidate-11-{week_id.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",\n            candidate=config["candidate"],\n            config_path=config_path,\n            data_manifest_path=output_dir / "data_manifest.json",\n            extra={\n                "week_id": week_id,\n                "bar_type": str(bar_type),\n                "evaluation_start": evaluation_start.isoformat(),\n                "evaluation_end_exclusive": evaluation_end.isoformat(),\n                "logic": config["logic"],\n                "execution": config["execution"],\n                "metrics_path": str(output_dir / "metrics.json"),\n                "event_log_valid": metrics["event_log_valid"],\n            },\n        )\n        write_json_atomic(output_dir / "run.json", manifest)\n        if event_log_error is not None:\n            raise EventLogError(event_log_error)\n        return metrics\n'''
        if source.count(old) != 1:
            raise SystemExit("evidence finalization anchor is not unique")
        path.write_text(source.replace(old, new, 1), encoding="utf-8")
        changed += 1
    return changed


def main() -> None:
    root = Path(__file__).resolve().parent
    required = (root / "logic.py", root / "run.py", root / "test_logic.py", root / "session_engine.py")
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"materialized SCDAM source is incomplete: {missing}")
    changed = migrate_logic(root) + migrate_test(root) + migrate_run_evidence(root)
    print(f"SCDAM migrations applied: {changed}")


if __name__ == "__main__":
    main()
