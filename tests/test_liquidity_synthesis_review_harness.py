from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
import pytest

from smc_ict_4.episode_policy_live import review_harness
from smc_ict_4.episode_policy_live.domain import SYMBOLS
from smc_ict_4.episode_policy_live.review_harness import (
    ReviewHarnessError,
    evaluate_offline_no_trade,
    load_review_inputs,
    run_review,
    select_terminal_no_trades,
)


MINUTE = 60_000_000_000
ANCHOR = 1_704_110_400_000_000_000  # 2024-01-01 08:00 UTC


def _raw() -> pd.DataFrame:
    index = pd.date_range(
        pd.Timestamp(ANCHOR, unit="ns", tz="UTC") - pd.Timedelta(hours=13),
        pd.Timestamp(ANCHOR, unit="ns", tz="UTC") + pd.Timedelta(hours=8),
        freq="1min",
    )
    frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.4,
            "low": 99.6,
            "close": 100.1,
            "quote_volume": 1_000.0,
            "taker_buy_quote_volume": 525.0,
            "signed_quote_flow": 50.0,
        },
        index=index,
    )
    terminal = pd.Timestamp(ANCHOR + MINUTE, unit="ns", tz="UTC")
    # Both stop and target are hit on the first bar after the no-trade
    # terminal.  The offline clinic must choose the adverse stop.
    frame.loc[terminal + pd.Timedelta(minutes=1), ["high", "low"]] = [101.5, 98.5]
    return frame


def _decision(
    episode: str,
    *,
    outcome: str,
    reason: str,
    trade_id: str | None = None,
    plan_id: str | None = None,
    symbol: str = "BTCUSDT",
    family: str = "FAILED_AUCTION_REVERSAL",
) -> dict[str, object]:
    plan = {
        "plan_id": plan_id,
        "decision_time_ns": ANCHOR + MINUTE,
        "entry": 100.0,
        "stop": 99.0,
        "target": 101.0,
        "entry_zone": {"kind": "OB_FVG_OVERLAP", "lower": 99.8, "upper": 100.2},
        "evidence": {"entry_execution_instruction": "RESTING_FUTURE_FIRST_RETURN_LIMIT"},
    } if plan_id else {}
    evidence = {
        "event_extreme": 98.8,
        "acceptance_retest_time_ns": ANCHOR - MINUTE,
        "acceptance_response_time_ns": ANCHOR,
        "entry_execution_instruction": "RESTING_FUTURE_FIRST_RETURN_LIMIT",
    }
    return {
        "decision_id": f"DEC:{episode}",
        "episode_id": episode,
        "episode_status": "TERMINAL",
        "outcome": outcome,
        "terminal_stage": "POLICY",
        "terminal_reason": reason,
        "symbol": symbol,
        "family": family,
        "side": "LONG",
        "interaction_time_ns": ANCHOR,
        "terminal_time_ns": ANCHOR + MINUTE,
        "source_observed_time_ns": ANCHOR - 2 * MINUTE,
        "source_kind": "PIT_PIVOT",
        "source_timeframe_minutes": 60,
        "interaction_source_lower": 99.5,
        "interaction_source_upper": 100.0,
        "plan_id": plan_id,
        "entry": 100.0 if plan_id else None,
        "stop": 99.0 if plan_id else None,
        "target": 101.0 if plan_id else None,
        "gross_rr": 1.0 if plan_id else None,
        "trade_id": trade_id,
        "trade_outcome": "WIN" if trade_id else None,
        "execution_disposition": "FILLED_CLOSED" if trade_id else "NOT_SELECTED",
        "evidence_json": json.dumps(evidence, sort_keys=True),
        "plan_json": json.dumps(plan, sort_keys=True) if plan else None,
    }


def test_terminal_no_trade_sample_is_bounded_per_reason_family_symbol() -> None:
    rows = [
        _decision(f"EP:{number}", outcome="NO_TRADE", reason="NO_ROUTE")
        for number in range(7)
    ]
    rows += [
        _decision(
            f"EP:OTHER:{number}",
            outcome="NO_TRADE",
            reason="NO_ROUTE",
            symbol="ETHUSDT",
        )
        for number in range(5)
    ]
    first = select_terminal_no_trades(rows, per_group=2, include_all=False)
    second = select_terminal_no_trades(list(reversed(rows)), per_group=2, include_all=False)
    assert [row["episode_id"] for row in first] == [row["episode_id"] for row in second]
    assert len(first) == 4
    assert len(select_terminal_no_trades(rows, per_group=2, include_all=True)) == 12


def test_offline_no_trade_is_adverse_first_and_missing_geometry_stays_unknown() -> None:
    case = _decision(
        "EP:NO_TRADE",
        outcome="NO_TRADE",
        reason="EXECUTION_REJECTED",
        plan_id="PLAN:NO_TRADE",
    )
    outcome, basis, resolution = evaluate_offline_no_trade(
        case,
        _raw(),
        horizon_minutes=120,
    )
    assert outcome == "STOP_FIRST"
    assert "OFFLINE_AUDIT_ONLY" in basis
    assert "STOP_FIRST_ON_SAME_BAR" in basis
    assert resolution == ANCHOR + 2 * MINUTE

    missing = dict(case)
    missing["target"] = None
    missing["plan_json"] = None
    outcome, _, resolution = evaluate_offline_no_trade(
        missing,
        _raw(),
        horizon_minutes=120,
    )
    assert outcome == "NOT_EVALUABLE_MISSING_GEOMETRY"
    assert resolution is None


def test_missing_decision_ledger_fails_before_legacy_run_is_rendered(tmp_path: Path) -> None:
    (tmp_path / "run.json").write_text("{}", encoding="utf-8")
    (tmp_path / "trades.csv").write_text("trade_id\n", encoding="utf-8")
    with pytest.raises(ReviewHarnessError, match="episode_decisions.csv"):
        load_review_inputs(tmp_path)


def test_full_synthetic_review_renders_trade_and_terminal_no_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source_map: dict[str, list[str]] = {}
    integrity: dict[str, list[dict[str, object]]] = {}
    for symbol in SYMBOLS:
        archive = tmp_path / f"{symbol}-1m-2024-01.zip"
        payload = f"synthetic-{symbol}".encode()
        archive.write_bytes(payload)
        digest = sha256(payload).hexdigest()
        source_map[symbol] = [str(archive)]
        integrity[symbol] = [{
            "path": str(archive),
            "bytes": len(payload),
            "sha256": digest,
            "checksum_verified": True,
        }]
    run = {
        "source_sha": "a" * 40,
        "source_working_tree_manifest_sha256": "b" * 64,
        "data_source_manifest_sha256": "c" * 64,
        "sources": {"trade_klines": source_map},
        "source_integrity": {
            "all_verified": True,
            "archives": {"trade_klines": integrity},
        },
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
    trade = {
        "trade_id": "TRADE:1",
        "episode_id": "EP:TRADE",
        "plan_id": "PLAN:TRADE",
        "entry_time_ns": ANCHOR + 2 * MINUTE,
        "exit_time_ns": ANCHOR + 20 * MINUTE,
        "entry_price": 100.05,
        "exit_price": 101.0,
        "outcome": "WIN",
        "net_r": 0.9,
    }
    pd.DataFrame([trade]).to_csv(run_dir / "trades.csv", index=False)
    decisions = [
        _decision(
            "EP:TRADE",
            outcome="SELECTED",
            reason="ENTRY_ORDER_ACCEPTED",
            trade_id="TRADE:1",
            plan_id="PLAN:TRADE",
        ),
        _decision(
            "EP:NO_TRADE",
            outcome="NO_TRADE",
            reason="EXECUTION_REJECTED",
            plan_id="PLAN:NO_TRADE",
        ),
    ]
    pd.DataFrame(decisions).to_csv(run_dir / "episode_decisions.csv", index=False)
    monkeypatch.setattr(
        review_harness,
        "load_case_bars",
        lambda *args, **kwargs: {symbol: _raw() for symbol in SYMBOLS},
    )

    output = tmp_path / "review"
    manifest = run_review(
        run_dir,
        output,
        no_trade_per_group=1,
        offline_horizon_minutes=120,
    )
    assert manifest["actual_trade_cases"] == 1
    assert manifest["terminal_no_trade_cases"] == 1
    assert manifest["renderer_provenance"][0]["commit"].startswith("bf5ef43")
    charts = sorted(output.glob("*.svg"))
    assert len(charts) == 2
    joined = "\n".join(path.read_text(encoding="utf-8") for path in charts)
    assert "direction-owning source" in joined
    assert "entry refinement zone" in joined
    assert "actual fill" in joined
    assert "offline=STOP_FIRST" in joined
    cases = pd.read_csv(output / "cases_manifest.csv")
    assert set(cases.review_case_kind) == {"ACTUAL_TRADE", "TERMINAL_NO_TRADE"}
    no_trade = cases[cases.review_case_kind.eq("TERMINAL_NO_TRADE")].iloc[0]
    assert no_trade.offline_future_outcome == "STOP_FIRST"
    assert "OFFLINE_AUDIT_ONLY" in no_trade.offline_future_outcome_basis
    review = json.loads((output / "review_manifest.json").read_text(encoding="utf-8"))
    assert review["episode_decisions_csv_sha256"]
    assert review["trade_archives"][0]["sha256"]

