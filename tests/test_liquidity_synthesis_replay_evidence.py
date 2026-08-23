from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from smc_ict_4.episode_policy_live.replay_evidence import build_replay_evidence, write_replay_evidence
from smc_ict_4.episode_policy_live.storage import StateStore


DAY = 86_400_000_000_000


def _parent(
    store: StateStore,
    order_id: str = "ENTRY-1",
    *,
    event_type: str = "PARENT_LIMIT_SUBMITTED",
) -> None:
    store.append_event(
        time_ns=10,
        event_type=event_type,
        payload={
            "client_order_id": order_id,
            "plan": {
                "plan_id": "PLAN-1",
                "episode_id": "EP-1",
                "family": "FAILED_AUCTION_REVERSAL",
                "side": "LONG",
                "entry": 100,
                "stop": 95,
                "target": 110,
                "gross_rr": 2.0,
            },
            "sizing": {"planned_stop_loss": "50 USDT", "planned_risk_fraction": "0.03"},
        },
    )


def test_exact_opening_order_join_and_cost_after_r(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite"
    with StateStore(state) as store:
        _parent(store)
        store.append_event(
            time_ns=25,
            event_type="ORDER_ACCEPTED",
            payload={"client_order_id": "CLOSE-1", "role": "TARGET"},
        )
    positions = [{
        "position_id": "POS-1",
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "opening_order_id": "ENTRY-1",
        "closing_order_id": "CLOSE-1",
        "side": "FLAT",
        "entry": "BUY",
        "peak_qty": 10,
        "ts_opened": 20,
        "ts_closed": 30,
        "avg_px_open": 100,
        "avg_px_close": 110,
        "commissions": ["1 USDT"],
        "realized_pnl": "98 USDT",
        "is_snapshot": False,
    }]
    fills = [{"position_id": "POS-1", "slippage": 0.1, "filled_qty": 20}]
    account = [{"timestamp": 20, "currency": "USDT", "total": "99999 USDT"}]

    evidence = build_replay_evidence(
        positions=positions,
        fills=fills,
        account=account,
        state_path=state,
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        initial_nav=100_000,
        final_nav=100_098,
    )
    trade = evidence["trades"][0]
    assert trade["plan_join_status"] == "EXACT_OPENING_ORDER_ID"
    assert trade["episode_id"] == "EP-1"
    assert trade["gross_pnl"] == 100
    assert trade["fees"] == 1
    assert trade["funding_cost"] == 1
    assert trade["gross_r"] == 2
    assert trade["cost_after_r"] == 1.96
    assert trade["exit_reason"] == "TARGET"
    assert evidence["metrics"]["win_rate"] == 1
    assert evidence["metrics"]["overlap_invariant"]["status"] == "OBSERVED_NO_OVERLAP"
    state.unlink()


def test_generic_market_parent_event_joins_immediate_response_lifecycle(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state.sqlite"
    with StateStore(state) as store:
        _parent(store, "MARKET-ENTRY", event_type="PARENT_ORDER_SUBMITTED")
    evidence = build_replay_evidence(
        positions=[{
            "position_id": "POS-MARKET",
            "instrument_id": "SOLUSDT-PERP.BINANCE",
            "opening_order_id": "MARKET-ENTRY",
            "closing_order_id": "MARKET-EXIT",
            "entry": "BUY",
            "side": "FLAT",
            "peak_qty": 1,
            "ts_opened": 20,
            "ts_closed": 30,
            "avg_px_open": 100,
            "avg_px_close": 105,
            "commissions": ["0.1 USDT"],
            "realized_pnl": "4.9 USDT",
        }],
        fills=[],
        account=[],
        state_path=state,
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        initial_nav=100,
        final_nav=104.9,
    )

    trade = evidence["trades"][0]
    assert trade["plan_join_status"] == "EXACT_OPENING_ORDER_ID"
    assert trade["episode_id"] == "EP-1"
    assert trade["risk_cash"] == 50


def test_unknown_plan_join_is_not_inferred(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite"
    with StateStore(state):
        pass
    evidence = build_replay_evidence(
        positions=[{
            "position_id": "POS-X", "instrument_id": "ETHUSDT-PERP.BINANCE",
            "opening_order_id": "ABSENT", "side": "SHORT", "peak_qty": 2,
            "ts_opened": 20, "ts_closed": 30, "avg_px_open": 100,
            "avg_px_close": 90, "commissions": ["1 USDT"],
            "realized_pnl": "19 USDT", "is_snapshot": False,
        }],
        fills=[],
        account=[],
        state_path=state,
        start=date(2024, 1, 1), end=date(2024, 1, 2),
        initial_nav=100, final_nav=119,
    )
    trade = evidence["trades"][0]
    assert trade["plan_join_status"] == "UNKNOWN_NO_PARENT_EVENT"
    assert trade["episode_id"] is None
    assert trade["risk_cash"] is None
    assert trade["cost_after_r"] is None
    assert evidence["metrics"]["episode_identity"]["unknown_episode_ids"] == 1


def test_netting_snapshot_rows_preserve_every_closed_lifecycle(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite"
    with StateStore(state) as store:
        for index in range(4):
            order_id = f"ENTRY-{index}"
            store.append_event(
                time_ns=index * 100 + 1,
                event_type="PARENT_LIMIT_SUBMITTED",
                payload={
                    "client_order_id": order_id,
                    "plan": {
                        "plan_id": f"PLAN-{index}",
                        "episode_id": f"EP-{index}",
                        "family": "FAILED_AUCTION_REVERSAL",
                        "side": "LONG",
                        "entry": 100,
                        "stop": 95,
                        "target": 105,
                        "gross_rr": 1,
                    },
                    "sizing": {"planned_stop_loss": "3 USDT", "planned_risk_fraction": "0.03"},
                },
            )
    positions = []
    for index in range(4):
        positions.append({
            "position_id": "BTCUSDT-PERP.BINANCE-STRATEGY-000",
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "opening_order_id": f"ENTRY-{index}",
            "closing_order_id": f"CLOSE-{index}",
            "entry": "BUY",
            "side": "FLAT",
            "peak_qty": 1,
            "quantity": 0,
            "ts_opened": index * 100 + 10,
            "ts_closed": index * 100 + 20,
            "duration_ns": 10,
            "avg_px_open": 100,
            "avg_px_close": 101,
            "commissions": ["0.1 USDT"],
            "realized_pnl": "0.9 USDT",
            "is_snapshot": index < 3,
        })
    # Same completed lifecycle can appear once as a historical snapshot and
    # once as the current report row.  Only that exact duplicate is removed.
    positions.append({**positions[0], "is_snapshot": False})
    fills = []
    for index in range(4):
        fills.extend([
            {
                "client_order_id": f"ENTRY-{index}",
                "position_id": "SHARED-NETTING-ID",
                "side": "BUY",
                "filled_qty": 1,
                "slippage": 0.01 * (index + 1),
            },
            {
                "client_order_id": f"CLOSE-{index}",
                "position_id": "SHARED-NETTING-ID",
                "side": "SELL",
                "filled_qty": 1,
                "slippage": 0.02 * (index + 1),
            },
        ])

    evidence = build_replay_evidence(
        positions=positions,
        fills=fills,
        account=[],
        state_path=state,
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        initial_nav=100,
        final_nav=103.6,
    )

    assert len(evidence["trades"]) == 4
    assert len({trade["trade_id"] for trade in evidence["trades"]}) == 4
    assert {trade["opening_order_id"] for trade in evidence["trades"]} == {
        "ENTRY-0", "ENTRY-1", "ENTRY-2", "ENTRY-3",
    }
    assert evidence["metrics"]["closed_trades"] == 4
    assert evidence["metrics"]["plan_evidence_join"]["exact_plan_joins"] == 4
    assert evidence["metrics"]["plan_evidence_join"]["closed_position_report_rows"] == 5
    assert evidence["metrics"]["plan_evidence_join"]["deduplicated_closed_report_rows"] == 1
    assert [trade["reported_slippage_cost"] for trade in evidence["trades"]] == pytest.approx(
        [0.03, 0.06, 0.09, 0.12],
    )


def test_minute_mtm_rejects_native_fill_missing_from_sqlite(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite"
    with StateStore(state):
        pass
    start_ns = 1_704_067_200_000_000_000
    with pytest.raises(ValueError, match="exact native/SQLite fill bijection"):
        build_replay_evidence(
            positions=[],
            fills=[{
                "client_order_id": "MISSING-EVENT",
                "position_id": "P",
                "side": "BUY",
                "filled_qty": 1,
                "slippage": 0,
            }],
            account=[],
            state_path=state,
            start=date(2024, 1, 1),
            end=date(2024, 1, 2),
            initial_nav=100,
            final_nav=100,
            equity_minutes=[
                SimpleNamespace(
                    ts_event=start_ns + DAY,
                    bars={"BTCUSDT": SimpleNamespace(close=100)},
                ),
            ],
        )


def test_minute_mtm_rejects_fill_quantity_mismatch(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite"
    start_ns = 1_704_067_200_000_000_000
    with StateStore(state) as store:
        store.append_event(
            time_ns=start_ns + 1,
            event_type="ORDER_FILLED",
            payload={
                "client_order_id": "PARTIAL",
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "last_qty": "0.5",
                "last_px": "100",
            },
        )
    with pytest.raises(ValueError, match="exact native/SQLite fill bijection"):
        build_replay_evidence(
            positions=[],
            fills=[{
                "client_order_id": "PARTIAL", "position_id": "P",
                "side": "BUY", "filled_qty": 1, "slippage": 0,
            }],
            account=[], state_path=state,
            start=date(2024, 1, 1), end=date(2024, 1, 2),
            initial_nav=100, final_nav=100,
            equity_minutes=[SimpleNamespace(
                ts_event=start_ns + DAY,
                bars={"BTCUSDT": SimpleNamespace(close=100)},
            )],
        )


def test_daily_native_total_basis_and_overlap_violation_are_explicit(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite"
    with StateStore(state) as store:
        _parent(store, "ENTRY-1")
        _parent(store, "ENTRY-2")
    common = {
        "instrument_id": "SOLUSDT-PERP.BINANCE", "side": "LONG", "peak_qty": 1,
        "avg_px_open": 100, "avg_px_close": 101, "commissions": ["0 USDT"],
        "realized_pnl": "1 USDT", "is_snapshot": False,
    }
    positions = [
        {**common, "position_id": "P1", "opening_order_id": "ENTRY-1", "ts_opened": 10, "ts_closed": 30},
        {**common, "position_id": "P2", "opening_order_id": "ENTRY-2", "ts_opened": 20, "ts_closed": 40},
    ]
    start = date(2024, 1, 1)
    start_ns = 1_704_067_200_000_000_000
    evidence = build_replay_evidence(
        positions=positions, fills=[],
        account=[
            {"timestamp": start_ns + DAY, "currency": "USDT", "total": 90},
            {"timestamp": start_ns + 2 * DAY, "currency": "USDT", "total": 95},
        ],
        state_path=state, start=start, end=date(2024, 1, 3),
        initial_nav=100, final_nav=95,
    )
    assert evidence["metrics"]["overlap_invariant"] == {
        "overlapping_trade_pair_count": 1,
        "status": "OBSERVED_VIOLATION",
    }
    assert evidence["metrics"]["maximum_continuous_drawdown"] == pytest.approx(0.1)
    assert evidence["metrics"]["continuous_mtm_drawdown"] is None
    assert evidence["daily_equity"][0]["equity_basis"] == "NATIVE_ACCOUNT_TOTAL"
    assert evidence["daily_equity"][0]["includes_unrealized_pnl"] is False


def test_daily_equity_adds_exact_open_position_mtm_from_native_fills(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite"
    start_ns = 1_704_067_200_000_000_000
    with StateStore(state) as store:
        store.append_event(
            time_ns=start_ns + 10,
            event_type="ORDER_FILLED",
            payload={
                "client_order_id": "ENTRY-OPEN",
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "last_qty": "1",
                "last_px": "100",
            },
        )
    evidence = build_replay_evidence(
        positions=[],
        fills=[{
            "client_order_id": "ENTRY-OPEN", "position_id": "OPEN-POS",
            "side": "BUY", "filled_qty": 1, "slippage": 0,
        }],
        account=[{"timestamp": start_ns + DAY, "currency": "USDT", "total": 99}],
        state_path=state,
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        initial_nav=100,
        final_nav=89,
        equity_minutes=[
            SimpleNamespace(
                ts_event=start_ns + DAY,
                bars={"BTCUSDT": SimpleNamespace(close=90)},
            ),
        ],
    )
    point = evidence["daily_equity"][0]
    assert point["equity"] == 89
    assert point["includes_unrealized_pnl"] is True
    assert point["equity_basis"] == "NATIVE_ACCOUNT_TOTAL_PLUS_FILL_RECONSTRUCTED_1M_MTM"
    assert evidence["metrics"]["maximum_continuous_drawdown"] == pytest.approx(0.11)

    output = tmp_path / "evidence"
    output.mkdir()
    write_replay_evidence(output, evidence)
    assert (output / "trades.csv").read_text(encoding="utf-8").startswith("trade_id,")
    assert "NATIVE_ACCOUNT_TOTAL_PLUS_FILL_RECONSTRUCTED_1M_MTM" in (
        output / "daily_equity.csv"
    ).read_text(encoding="utf-8")
    assert '"descriptive_only": true' in (output / "replay_evidence.json").read_text(
        encoding="utf-8",
    )
