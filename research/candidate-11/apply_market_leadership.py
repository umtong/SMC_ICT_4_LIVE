#!/usr/bin/env python3
"""Materialize Candidate 11's dynamic market-leadership research candidate."""
from __future__ import annotations

from pathlib import Path
import json


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if new in source:
        return source
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def patch_logic(root: Path) -> int:
    path = root / "logic.py"
    source = path.read_text(encoding="utf-8")
    old = '''                "range_id": a.pool.range_id,
                "sweep_extreme": a.sweep_extreme,
'''
    new = '''                "range_id": a.pool.range_id,
                "sweep_ts_ns": (a.initial_sweep_ts_ns if a.initial_sweep_ts_ns is not None else a.sweep.ts_ns),
                "sweep_extreme": a.sweep_extreme,
'''
    updated = replace_once(source, old, new, "plan sweep timestamp")
    if updated == source:
        return 0
    path.write_text(updated, encoding="utf-8")
    return 1


def build_runner(root: Path) -> int:
    source_path = root / "run_portfolio_scdam.py"
    destination = root / "run_leadership_scdam.py"
    source = source_path.read_text(encoding="utf-8")

    source = source.replace(
        '"""Four-instrument portfolio evaluation of the unchanged Candidate 11 SCDAM.',
        '"""Dynamic price-discovery leadership evaluation of Candidate 11 SCDAM.',
        1,
    )
    source = replace_once(
        source,
        "from global_allocator import Candidate, GlobalCandidateMutex, SlotState\n",
        "from global_allocator import Candidate, GlobalCandidateMutex, SlotState\n"
        "from market_leadership import MarketLeadershipGate\n",
        "market-leadership import",
    )
    source = replace_once(
        source,
        '"candidate": "candidate-11-four-market-independent-scdam",',
        '"candidate": "candidate-11-market-leadership-scdam",',
        "candidate identity",
    )
    source = replace_once(
        source,
        "            self.mutex = GlobalCandidateMutex()\n",
        "            self.mutex = GlobalCandidateMutex()\n"
        "            self.leadership = MarketLeadershipGate(SYMBOLS, lookback_bars=1440)\n",
        "leadership state",
    )
    source = replace_once(
        source,
        '''        def _process_batch(self, ts_ns: int) -> None:
            plans: list[tuple[TradePlan, Candidate]] = []
''',
        '''        def _process_batch(self, ts_ns: int) -> None:
            # Observe the entire completed minute before any symbol can be
            # approved, preventing subscription-order or future-data bias.
            try:
                self.leadership.observe_batch(
                    ts_ns,
                    {
                        symbol: (self.buffer[symbol].close, self.buffer[symbol].volume)
                        for symbol in SYMBOLS
                    },
                )
            except Exception as exc:
                self.errors.append({
                    "type": "MARKET_LEADERSHIP_OBSERVATION_ERROR",
                    "ts_ns": ts_ns,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                return
            plans: list[tuple[TradePlan, Candidate]] = []
''',
        "synchronized leadership observation",
    )
    source = replace_once(
        source,
        '''                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                    self._capture_events(symbol)
                    continue
                candidate = Candidate(
''',
        '''                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                    self._capture_events(symbol)
                    continue
                leadership = self.leadership.decide(
                    symbol=symbol,
                    scenario=plan.scenario.value,
                    direction=plan.direction.value,
                    sweep_ts_ns=int(plan.details.get("sweep_ts_ns", -1)),
                    confirmation_ts_ns=ts_ns,
                )
                plan.details["market_leadership"] = leadership.to_dict()
                if not leadership.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        leadership.reason,
                        leadership.to_dict(),
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "MARKET_LEADERSHIP_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "reason": leadership.reason,
                        "leader": leadership.leader,
                        "peer_returns": leadership.peer_returns,
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                candidate = Candidate(
''',
        "leadership approval",
    )
    source = replace_once(
        source,
        '''            "candidate_rejections": strategy.rejections,
            "nautilus_result": {
''',
        '''            "candidate_rejections": strategy.rejections,
            "leadership_rejection_counts": dict(Counter(
                item.get("reason", "UNKNOWN")
                for item in strategy.rejections
                if item.get("type") == "MARKET_LEADERSHIP_REJECTED"
            )),
            "nautilus_result": {
''',
        "leadership metric evidence",
    )
    source = replace_once(
        source,
        'run_id=f"candidate-11-portfolio-{week_id.lower()}-',
        'run_id=f"candidate-11-leadership-{week_id.lower()}-',
        "leadership run id",
    )
    source = replace_once(
        source,
        'parser.add_argument("--week", choices=("W1", "W2", "W3"), default="W1")',
        'parser.add_argument("--week", choices=("W1", "W2", "W3", "W4", "W5", "W6"), default="W1")',
        "leadership week choices",
    )
    source = replace_once(
        source,
        'parser.add_argument("--output", type=Path, default=ROOT / "results" / "PORTFOLIO_W1")',
        'parser.add_argument("--output", type=Path, default=ROOT / "results" / "LEADERSHIP_W1")',
        "leadership output",
    )

    previous = destination.read_text(encoding="utf-8") if destination.exists() else None
    if previous == source:
        return 0
    destination.write_text(source, encoding="utf-8")
    return 1


def patch_validation_config(root: Path) -> int:
    path = root / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    weeks = config.setdefault("selection", {}).setdefault("weeks", {})
    frozen = {
        "W4": {"start": "2023-11-18", "end_exclusive": "2023-11-25"},
        "W5": {"start": "2024-07-24", "end_exclusive": "2024-07-31"},
        "W6": {"start": "2025-04-05", "end_exclusive": "2025-04-12"},
        # These post-gate holdouts were selected and committed before any W7-W9
        # market data was downloaded. They extend, but do not alter, W4-W6.
        "W7": {"start": "2024-12-24", "end_exclusive": "2024-12-31"},
        "W8": {"start": "2024-10-26", "end_exclusive": "2024-11-02"},
        "W9": {"start": "2025-09-22", "end_exclusive": "2025-09-29"},
    }
    changed = 0
    for week, interval in frozen.items():
        if week in weeks and weeks[week] != interval:
            raise SystemExit(f"precommitted {week} interval changed")
        if week not in weeks:
            weeks[week] = interval
            changed = 1
    protocol = {
        "seed": 2026080711,
        "method": (
            "random.Random(seed) over 2023-01-01 through 2025-12-25; "
            "first three non-overlapping seven-day starts excluding W1-W3, "
            "fixed before downloading W4-W6 data"
        ),
        "diagnostic_weeks": ["W1", "W2", "W3"],
        "untouched_weeks": ["W4", "W5", "W6"],
        "post_freeze_seed": 2026080712,
        "post_freeze_method": (
            "random.Random(post_freeze_seed) over 2023-01-01 through 2025-12-25; "
            "first three non-overlapping seven-day starts excluding W1-W6, "
            "committed before downloading W7-W9 data"
        ),
        "post_freeze_weeks": ["W7", "W8", "W9"],
    }
    existing = config.get("leadership_validation")
    if existing is not None and existing != protocol:
        raise SystemExit("leadership validation protocol changed")
    if existing is None:
        config["leadership_validation"] = protocol
        changed = 1
    rendered = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    if path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8")
        changed = 1
    return changed


def patch_evidence_audit(root: Path) -> int:
    path = root / "evidence_audit.py"
    source = path.read_text(encoding="utf-8")
    old = 'parser.add_argument("--week", choices=("W1", "W2", "W3", "LONG"), required=True)'
    new = 'parser.add_argument("--week", choices=("W1", "W2", "W3", "W4", "W5", "W6", "LONG"), required=True)'
    updated = replace_once(source, old, new, "leadership audit week choices")
    if updated == source:
        return 0
    path.write_text(updated, encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    required = (
        root / "logic.py",
        root / "run_portfolio_scdam.py",
        root / "market_leadership.py",
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"leadership candidate inputs missing: {missing}")
    changed = patch_logic(root) + build_runner(root) + patch_validation_config(root) + patch_evidence_audit(root)
    print(f"market-leadership candidate materialization applied: {changed}")


if __name__ == "__main__":
    main()
