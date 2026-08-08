#!/usr/bin/env python3
"""Apply fail-closed handling and independent audit for partial GTD entries.

A partially filled parent may expire while its contingent children are canceled.
The remaining position must be flattened immediately and the evidence audit must
reject any run where that close is delayed beyond the first following one-minute
observation. Detector thresholds, targets, stops, fees, and 3% NAV risk sizing
are unchanged.
"""
from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> int:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return 0
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one migration anchor, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def migrate_runner(root: Path) -> int:
    path = root / "run_portfolio_scdam.py"
    changed = 0
    changed += replace_once(
        path,
        '''        def _flatten(self) -> None:\n            for instrument_id in self.config.instrument_ids:\n                if self.cache.orders_open_count(instrument_id=instrument_id, strategy_id=self.id):\n                    self.cancel_all_orders(instrument_id)\n                if not self.portfolio.is_flat(instrument_id):\n                    self.close_all_positions(instrument_id)\n\n        def on_order_filled(self, event: OrderEvent) -> None:\n''',
        '''        def _flatten(self) -> None:\n            for instrument_id in self.config.instrument_ids:\n                if self.cache.orders_open_count(instrument_id=instrument_id, strategy_id=self.id):\n                    self.cancel_all_orders(instrument_id)\n                if not self.portfolio.is_flat(instrument_id):\n                    self.close_all_positions(instrument_id)\n\n        def _fail_close_partial_entry(self, event: OrderEvent) -> None:\n            """Flatten a residual position when its GTD parent expires.\n\n            The global slot remains POSITION_OPEN until Nautilus confirms the\n            market close. This is execution safety, not an alpha filter.\n            """\n            if (\n                self.active_plan is None\n                or self.active_symbol is None\n                or self.mutex.state != SlotState.POSITION_OPEN\n            ):\n                return\n            instrument_id = instruments[self.active_symbol].id\n            if self.portfolio.is_flat(instrument_id):\n                return\n            ts_ns = int(event.ts_event)\n            record = {\n                "type": "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED",\n                "ts_event": ts_ns,\n                "scenario_id": self.active_plan.scenario_id,\n                "symbol": self.active_symbol,\n                "expired_client_order_id": str(event.client_order_id),\n            }\n            self.lifecycle.append(record)\n            if self.cache.orders_open_count(instrument_id=instrument_id, strategy_id=self.id):\n                self.cancel_all_orders(instrument_id)\n            self.close_all_positions(instrument_id)\n\n        def on_order_filled(self, event: OrderEvent) -> None:\n''',
        "partial-entry fail-close helper",
    )
    changed += replace_once(
        path,
        '''        def on_order_expired(self, event: OrderEvent) -> None:\n            self._record_order_event(event, "ORDER_EXPIRED")\n            self._release_if_terminal(int(event.ts_event), "ENTRY_EXPIRED")\n''',
        '''        def on_order_expired(self, event: OrderEvent) -> None:\n            self._record_order_event(event, "ORDER_EXPIRED")\n            self._fail_close_partial_entry(event)\n            self._release_if_terminal(int(event.ts_event), "ENTRY_EXPIRED")\n''',
        "expiry fail-close callback",
    )
    changed += replace_once(
        path,
        '''        "engine_errors": errors,\n        "liquidation_detected": liquidation_detected,\n''',
        '''        "engine_errors": errors,\n        "partial_entry_fail_closed_count": sum(\n            item.get("type") == "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED"\n            for item in lifecycle\n        ),\n        "liquidation_detected": liquidation_detected,\n''',
        "partial-entry metric",
    )
    return changed


def migrate_portfolio_tests(root: Path) -> int:
    path = root / "test_portfolio_scdam.py"
    source = path.read_text(encoding="utf-8")
    if "test_partial_gtd_entry_is_fail_closed_before_terminal_release" in source:
        return 0
    anchor = '''    def test_same_timestamp_plans_are_arbitrated_before_submission(self) -> None:\n        source = (ROOT / "run_portfolio_scdam.py").read_text(encoding="utf-8")\n        process = source.index("def _process_batch")\n        flush = source.index("arbitration = self.mutex.flush()", process)\n        submit = source.index("self._submit(winner[0], winner[1])", flush)\n        self.assertLess(process, flush)\n        self.assertLess(flush, submit)\n'''
    addition = anchor + '''\n    def test_partial_gtd_entry_is_fail_closed_before_terminal_release(self) -> None:\n        source = (ROOT / "run_portfolio_scdam.py").read_text(encoding="utf-8")\n        callback = source.index("def on_order_expired")\n        fail_close = source.index("self._fail_close_partial_entry(event)", callback)\n        release = source.index("self._release_if_terminal", fail_close)\n        helper = source.index("def _fail_close_partial_entry")\n        market_close = source.index("self.close_all_positions(instrument_id)", helper)\n        self.assertLess(fail_close, release)\n        self.assertGreater(market_close, helper)\n        self.assertIn("PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED", source)\n'''
    if source.count(anchor) != 1:
        raise SystemExit("portfolio fail-close test anchor is not unique")
    path.write_text(source.replace(anchor, addition, 1), encoding="utf-8")
    return 1


def migrate_audit(root: Path) -> int:
    path = root / "evidence_audit.py"
    changed = 0
    changed += replace_once(
        path,
        '''import argparse\nimport csv\nfrom decimal import Decimal, InvalidOperation\n''',
        '''import argparse\nimport csv\nfrom datetime import datetime\nfrom decimal import Decimal, InvalidOperation\n''',
        "audit datetime import",
    )
    changed += replace_once(
        path,
        '''def time_value(row: dict[str, str], names: tuple[str, ...]) -> Decimal | None:\n    for name in names:\n        value = row.get(name)\n        if value in (None, "", "None", "nan", "NaT"):\n            continue\n        try:\n            return dec(value)\n        except InvalidOperation:\n            pass\n    return None\n\n\ndef audit(root: Path, week: str) -> dict[str, Any]:\n''',
        '''def time_value(row: dict[str, str], names: tuple[str, ...]) -> Decimal | None:\n    for name in names:\n        value = row.get(name)\n        if value in (None, "", "None", "nan", "NaT"):\n            continue\n        try:\n            return dec(value)\n        except InvalidOperation:\n            pass\n    return None\n\n\ndef timestamp_ns(row: dict[str, str], names: tuple[str, ...]) -> Decimal | None:\n    """Parse either integer nanoseconds or an ISO-8601 report timestamp."""\n    for name in names:\n        value = row.get(name)\n        if value in (None, "", "None", "nan", "NaT"):\n            continue\n        try:\n            number = dec(value)\n            if abs(number) >= Decimal("1000000000000"):\n                return number\n        except InvalidOperation:\n            pass\n        try:\n            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))\n            return Decimal(str(parsed.timestamp())) * Decimal("1000000000")\n        except (ValueError, TypeError, OverflowError):\n            continue\n    return None\n\n\ndef audit(root: Path, week: str) -> dict[str, Any]:\n''',
        "audit timestamp parser",
    )
    changed += replace_once(
        path,
        '''    if not slot_ok:\n        reasons.append("overlapping position intervals violate the global one-position invariant")\n\n    liquidation = pick(metrics, "liquidation_detected", "was_liquidated")\n''',
        '''    if not slot_ok:\n        reasons.append("overlapping position intervals violate the global one-position invariant")\n\n    order_rows = csv_rows(root / "orders.csv")\n    position_rows = csv_rows(root / "positions.csv")\n    partial_expiry_records: list[dict[str, Any]] = []\n    protection_ok = True\n    max_close_delay_ns = Decimal("60000000000")\n    for row in order_rows:\n        try:\n            quantity = dec(row.get("quantity"))\n            filled = dec(row.get("filled_qty"))\n        except InvalidOperation:\n            continue\n        tags = str(row.get("tags", "")).upper()\n        is_partial_expiry = (\n            str(row.get("status", "")).upper() == "EXPIRED"\n            and str(row.get("time_in_force", "")).upper() == "GTD"\n            and "ENTRY" in tags\n            and Decimal("0") < filled < quantity\n        )\n        if not is_partial_expiry:\n            continue\n        expired_ns = timestamp_ns(row, ("ts_last", "expire_time_ns"))\n        matching_positions: list[tuple[Decimal, dict[str, str]]] = []\n        if expired_ns is not None:\n            for position_row in position_rows:\n                if position_row.get("instrument_id") != row.get("instrument_id"):\n                    continue\n                opened_ns = timestamp_ns(position_row, ("ts_opened", "ts_init"))\n                position_closed_ns = timestamp_ns(position_row, ("ts_closed", "ts_last"))\n                if (\n                    opened_ns is not None\n                    and position_closed_ns is not None\n                    and opened_ns <= expired_ns <= position_closed_ns\n                ):\n                    matching_positions.append((opened_ns, position_row))\n        position = max(matching_positions, key=lambda item: item[0])[1] if matching_positions else None\n        closed_ns = None if position is None else timestamp_ns(position, ("ts_closed", "ts_last"))\n        delay_ns = None if expired_ns is None or closed_ns is None else closed_ns - expired_ns\n        passed = delay_ns is not None and Decimal("0") <= delay_ns <= max_close_delay_ns\n        partial_expiry_records.append({\n            "instrument_id": row.get("instrument_id"),\n            "position_id": row.get("position_id"),\n            "quantity": str(quantity),\n            "filled_qty": str(filled),\n            "expired_ns": None if expired_ns is None else str(expired_ns),\n            "closed_ns": None if closed_ns is None else str(closed_ns),\n            "close_delay_ns": None if delay_ns is None else str(delay_ns),\n            "fail_closed_within_one_bar": passed,\n        })\n        if not passed:\n            protection_ok = False\n            reasons.append(\n                f"partially filled GTD entry {row.get('position_id')} remained open beyond one bar after expiry",\n            )\n\n    liquidation = pick(metrics, "liquidation_detected", "was_liquidated")\n''',
        "partial-expiry audit",
    )
    changed += replace_once(
        path,
        '''    if not all((evidence_ok, metric_ok, risk_ok, slot_ok, no_liquidation, engine_ok)):\n''',
        '''    if not all((evidence_ok, metric_ok, risk_ok, slot_ok, protection_ok, no_liquidation, engine_ok)):\n''',
        "audit classification gate",
    )
    changed += replace_once(
        path,
        '''    success_allowed = week != "W1" and complete and recorded_success and all((evidence_ok, metric_ok, risk_ok, slot_ok, no_liquidation, engine_ok))\n''',
        '''    success_allowed = week != "W1" and complete and recorded_success and all((evidence_ok, metric_ok, risk_ok, slot_ok, protection_ok, no_liquidation, engine_ok))\n''',
        "audit success gate",
    )
    changed += replace_once(
        path,
        '''        "global_slot_passed": slot_ok,\n        "no_liquidation_passed": no_liquidation,\n''',
        '''        "global_slot_passed": slot_ok,\n        "partial_entry_protection_passed": protection_ok,\n        "partial_entry_expiry_records": partial_expiry_records,\n        "no_liquidation_passed": no_liquidation,\n''',
        "audit output fields",
    )
    changed += replace_once(
        path,
        '''    for key in ("advance_allowed", "success_claim_allowed", "evidence_complete", "metric_recalculation_passed", "risk_budget_passed", "global_slot_passed", "no_liquidation_passed"):\n''',
        '''    for key in ("advance_allowed", "success_claim_allowed", "evidence_complete", "metric_recalculation_passed", "risk_budget_passed", "global_slot_passed", "partial_entry_protection_passed", "no_liquidation_passed"):\n''',
        "audit markdown summary",
    )
    return changed


def migrate_audit_tests(root: Path) -> int:
    path = root / "test_evidence_audit.py"
    source = path.read_text(encoding="utf-8")
    if "test_partial_entry_expiry_must_fail_close_within_one_bar" in source:
        return 0
    anchor = '''    def test_overlapping_positions_violate_global_mutex(self) -> None:\n        with tempfile.TemporaryDirectory() as temp:\n            root = self.make_result(Path(temp))\n            with (root / "positions.csv").open("w", encoding="utf-8", newline="") as stream:\n                writer = csv.DictWriter(stream, fieldnames=("ts_opened", "ts_closed"))\n                writer.writeheader()\n                writer.writerow({"ts_opened": "1", "ts_closed": "5"})\n                writer.writerow({"ts_opened": "4", "ts_closed": "6"})\n            result = audit(root, "W1")\n        self.assertFalse(result["global_slot_passed"])\n        self.assertEqual(result["classification"], "IMPLEMENTATION_OR_EVIDENCE_FAILURE")\n'''
    addition = anchor + '''\n    def test_partial_entry_expiry_must_fail_close_within_one_bar(self) -> None:\n        with tempfile.TemporaryDirectory() as temp:\n            root = self.make_result(Path(temp))\n            with (root / "orders.csv").open("w", encoding="utf-8", newline="") as stream:\n                writer = csv.DictWriter(stream, fieldnames=(\n                    "instrument_id", "position_id", "status", "time_in_force",\n                    "tags", "quantity", "filled_qty", "ts_last",\n                ))\n                writer.writeheader()\n                writer.writerow({\n                    "instrument_id": "XRPUSDT-PERP.BINANCE",\n                    "position_id": "P-1",\n                    "status": "EXPIRED",\n                    "time_in_force": "GTD",\n                    "tags": "['ENTRY']",\n                    "quantity": "1000",\n                    "filled_qty": "100",\n                    "ts_last": "2024-01-01 00:10:00+00:00",\n                })\n            with (root / "positions.csv").open("w", encoding="utf-8", newline="") as stream:\n                writer = csv.DictWriter(stream, fieldnames=("instrument_id", "position_id", "ts_opened", "ts_closed", "realized_pnl"))\n                writer.writeheader()\n                writer.writerow({\n                    "instrument_id": "XRPUSDT-PERP.BINANCE",\n                    "position_id": "P-1",\n                    "ts_opened": "2024-01-01 00:01:00+00:00",\n                    "ts_closed": "2024-01-01 00:20:00+00:00",\n                    "realized_pnl": "-10",\n                })\n            result = audit(root, "W1")\n        self.assertFalse(result["partial_entry_protection_passed"])\n        self.assertEqual(result["classification"], "IMPLEMENTATION_OR_EVIDENCE_FAILURE")\n\n    def test_partial_entry_expiry_closed_next_bar_passes_protection_audit(self) -> None:\n        with tempfile.TemporaryDirectory() as temp:\n            root = self.make_result(Path(temp))\n            with (root / "orders.csv").open("w", encoding="utf-8", newline="") as stream:\n                writer = csv.DictWriter(stream, fieldnames=(\n                    "instrument_id", "position_id", "status", "time_in_force",\n                    "tags", "quantity", "filled_qty", "ts_last",\n                ))\n                writer.writeheader()\n                writer.writerow({\n                    "instrument_id": "XRPUSDT-PERP.BINANCE",\n                    "position_id": "P-1",\n                    "status": "EXPIRED",\n                    "time_in_force": "GTD",\n                    "tags": "['ENTRY']",\n                    "quantity": "1000",\n                    "filled_qty": "100",\n                    "ts_last": "2024-01-01 00:10:00+00:00",\n                })\n            with (root / "positions.csv").open("w", encoding="utf-8", newline="") as stream:\n                writer = csv.DictWriter(stream, fieldnames=("instrument_id", "position_id", "ts_opened", "ts_closed", "realized_pnl"))\n                writer.writeheader()\n                writer.writerow({\n                    "instrument_id": "XRPUSDT-PERP.BINANCE",\n                    "position_id": "P-1",\n                    "ts_opened": "2024-01-01 00:01:00+00:00",\n                    "ts_closed": "2024-01-01 00:11:00+00:00",\n                    "realized_pnl": "-10",\n                })\n            result = audit(root, "W1")\n        self.assertTrue(result["partial_entry_protection_passed"])\n'''
    if source.count(anchor) != 1:
        raise SystemExit("partial-expiry audit test anchor is not unique")
    path.write_text(source.replace(anchor, addition, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    required = (
        root / "run_portfolio_scdam.py",
        root / "test_portfolio_scdam.py",
        root / "evidence_audit.py",
        root / "test_evidence_audit.py",
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"partial-fill safety source is incomplete: {missing}")
    changed = (
        migrate_runner(root)
        + migrate_portfolio_tests(root)
        + migrate_audit(root)
        + migrate_audit_tests(root)
    )
    print(f"partial-fill fail-closed migrations applied: {changed}")


if __name__ == "__main__":
    main()
