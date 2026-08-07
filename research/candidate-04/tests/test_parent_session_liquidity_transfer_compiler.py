from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import parent_session_liquidity_transfer_compiler as candidate


class ParentSessionFixture(unittest.TestCase):
    def data(self, rows: int = 1500) -> pd.DataFrame:
        index = pd.date_range(
            "2025-01-01",
            periods=rows,
            freq="min",
            tz="UTC",
        )
        base = pd.Series([100.0 + 0.001 * i for i in range(rows)])
        return pd.DataFrame(
            {
                "open": base.to_numpy(),
                "high": (base + 0.5).to_numpy(),
                "low": (base - 0.5).to_numpy(),
                "close": base.to_numpy(),
                "atr": [1.0] * rows,
                "flow_60s": [0.2] * rows,
                "notional_60s": [1000.0] * rows,
                "ret_60s_bps": [1.0] * rows,
                "basis_change_1m": [0.1] * rows,
                "metric_sum_open_interest": [1000.0 + 0.01 * i for i in range(rows)],
                "ask_depth_1": [10000.0] * rows,
                "bid_depth_1": [10000.0] * rows,
                "depth_snapshot_age_seconds": [5.0] * rows,
                "ask_chg_1_60s": [0.0] * rows,
                "bid_chg_1_60s": [0.0] * rows,
            },
            index=index,
        )

    def config(self):
        return SimpleNamespace(
            pivot_left=1,
            pivot_right=1,
            pool_max_age_minutes=720,
            pool_merge_atr=0.18,
            pool_min_age_minutes=2,
            pool_min_prominence_atr=0.1,
            sweep_min_atr=0.03,
            pre_sweep_structure_minutes=5,
            sweep_confirmation_minutes=8,
            trend_structure_minutes=5,
        )


class SessionTests(ParentSessionFixture):
    def test_only_completed_parent_session_is_exposed(self) -> None:
        data = self.data(rows=1000)
        sessions = candidate.completed_parent_sessions(data)
        first_current = 8 * 60
        self.assertIsNone(sessions[first_current - 1])
        context = sessions[first_current]
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.observed_index, first_current - 1)
        self.assertEqual(context.current_start, data.index[first_current])
        self.assertAlmostEqual(
            context.high,
            float(data["high"].iloc[:first_current].max()),
        )
        self.assertAlmostEqual(
            context.low,
            float(data["low"].iloc[:first_current].min()),
        )

    def test_first_boundary_take_consumes_parent_level_once(self) -> None:
        data = self.data(rows=1000)
        sessions = candidate.completed_parent_sessions(data)
        context = sessions[8 * 60]
        assert context is not None
        attack = 8 * 60 + 10
        data.iloc[attack, data.columns.get_loc("high")] = context.high + 1.0
        data.iloc[attack + 1, data.columns.get_loc("high")] = context.high + 2.0
        first, attacks = candidate.parent_boundary_first_takes(
            data, sessions, self.config()
        )
        self.assertEqual(attacks[attack], (1, context.high))
        self.assertNotIn(attack + 1, attacks)
        self.assertEqual(first[(context.current_start, 1)], attack)


class ImpactTests(ParentSessionFixture):
    def test_current_or_post_event_depth_cannot_change_its_impact_pressure(self) -> None:
        data = self.data()
        baseline = candidate.impact_state(data)
        index = candidate.IMPACT_MINIMUM + 20
        changed = data.copy()
        changed.iloc[index:, changed.columns.get_loc("ask_depth_1")] = 1.0
        changed_state = candidate.impact_state(changed)
        self.assertAlmostEqual(
            float(baseline["signed_pressure"].iloc[index]),
            float(changed_state["signed_pressure"].iloc[index]),
        )

    def test_current_impact_slope_is_not_in_its_own_center(self) -> None:
        data = self.data()
        baseline = candidate.impact_state(data)
        index = candidate.IMPACT_MINIMUM + 40
        changed = data.copy()
        changed.iloc[index, changed.columns.get_loc("ret_60s_bps")] = 10000.0
        changed_state = candidate.impact_state(changed)
        self.assertAlmostEqual(
            float(baseline["impact_slope_center"].iloc[index]),
            float(changed_state["impact_slope_center"].iloc[index]),
        )
        self.assertNotEqual(
            float(baseline["impact_innovation_z"].iloc[index]),
            float(changed_state["impact_innovation_z"].iloc[index]),
        )


class TargetTests(ParentSessionFixture):
    def test_target_must_be_active_and_observed_before_attack(self) -> None:
        data = self.data(rows=1000)
        sessions = candidate.completed_parent_sessions(data)
        attack = 8 * 60 + 20
        signal = attack + 3
        entry = float(data["close"].iloc[signal])
        stop = entry + 1.0
        good = candidate.CausalPool(
            pool_id=1,
            side=-1,
            level=entry - 10.0,
            created_index=10,
            observed_index=12,
            last_touch_index=10,
            touches=1,
            prominence_atr=0.5,
        )
        future = candidate.CausalPool(
            pool_id=2,
            side=-1,
            level=entry - 5.0,
            created_index=attack,
            observed_index=attack,
            last_touch_index=attack,
            touches=1,
            prominence_atr=0.5,
        )
        snapshots = [tuple() for _ in range(len(data))]
        snapshots[signal] = (good, future)
        target = candidate.choose_causal_target(
            data=data,
            snapshots=snapshots,
            sessions=sessions,
            boundary_takes={},
            attack_index=attack,
            signal_index=signal,
            side=-1,
            stop=stop,
            config=self.config(),
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.source, "causal_pivot_pool_1_low")
        self.assertLess(target.observed_index, attack)

    def test_uneconomic_nearby_pool_is_skipped_for_farther_causal_pool(self) -> None:
        data = self.data(rows=1000)
        sessions = candidate.completed_parent_sessions(data)
        attack = 8 * 60 + 20
        signal = attack + 3
        entry = float(data["close"].iloc[signal])
        stop = entry + 1.0
        near = candidate.CausalPool(
            1, -1, entry - 0.5, 10, 12, 10, 1, 0.5
        )
        far = candidate.CausalPool(
            2, -1, entry - 10.0, 20, 22, 20, 1, 0.5
        )
        snapshots = [tuple() for _ in range(len(data))]
        snapshots[signal] = (near, far)
        target = candidate.choose_causal_target(
            data=data,
            snapshots=snapshots,
            sessions=sessions,
            boundary_takes={},
            attack_index=attack,
            signal_index=signal,
            side=-1,
            stop=stop,
            config=self.config(),
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.source, "causal_pivot_pool_2_low")


class InventoryTests(unittest.TestCase):
    def test_inventory_routes_have_distinct_resolution_contracts(self) -> None:
        details = {
            "attack_oi_before": 1000.0,
            "attack_oi_end": 1010.0,
        }
        self.assertTrue(
            candidate._inventory_reversal_resolved(
                "NEW_INVENTORY", details, 1005.0
            )
        )
        self.assertFalse(
            candidate._inventory_reversal_resolved(
                "NEW_INVENTORY", details, 1006.0
            )
        )
        liquidation = {
            "attack_oi_before": 1000.0,
            "attack_oi_end": 980.0,
        }
        self.assertTrue(
            candidate._inventory_reversal_resolved(
                "LIQUIDATION", liquidation, 984.0
            )
        )
        self.assertFalse(
            candidate._inventory_reversal_resolved(
                "LIQUIDATION", liquidation, 985.0
            )
        )
        self.assertTrue(
            candidate._inventory_reversal_resolved(
                "PASSIVE_ABSORPTION", details, float("nan")
            )
        )


if __name__ == "__main__":
    unittest.main()
