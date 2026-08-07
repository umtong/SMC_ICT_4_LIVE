from __future__ import annotations

import unittest

import pandas as pd

import parent_session_liquidity_transfer_no_innovation_ablation as candidate


class NoInnovationAblationTests(unittest.TestCase):
    def frame(self, innovation: float) -> tuple[pd.DataFrame, pd.DataFrame]:
        data = pd.DataFrame(
            {
                "flow_60s": [0.5],
                "ret_60s_bps": [4.0],
                "basis_change_1m": [0.2],
            }
        )
        impact = pd.DataFrame(
            {
                "signed_pressure": [2.0],
                "absolute_pressure": [2.0],
                "pressure_cutoff": [1.0],
                "impact_innovation_z": [innovation],
            }
        )
        return data, impact

    def test_innovation_z_is_the_only_removed_component(self) -> None:
        positive_data, positive_impact = self.frame(10.0)
        negative_data, negative_impact = self.frame(-10.0)
        self.assertTrue(
            candidate.direct_flow_aligned(
                positive_impact,
                positive_data,
                0,
                1,
                999.0,
            )
        )
        self.assertTrue(
            candidate.direct_flow_aligned(
                negative_impact,
                negative_data,
                0,
                1,
                -999.0,
            )
        )

    def test_pressure_flow_return_and_basis_remain_required(self) -> None:
        data, impact = self.frame(0.0)
        for column in ("flow_60s", "ret_60s_bps", "basis_change_1m"):
            changed = data.copy()
            changed.loc[0, column] = -abs(float(changed.loc[0, column]))
            self.assertFalse(
                candidate.direct_flow_aligned(
                    impact,
                    changed,
                    0,
                    1,
                    0.0,
                )
            )
        weak = impact.copy()
        weak.loc[0, "absolute_pressure"] = 0.5
        self.assertFalse(
            candidate.direct_flow_aligned(weak, data, 0, 1, 0.0)
        )


if __name__ == "__main__":
    unittest.main()
