#!/usr/bin/env python3
"""Apply the evidence-driven candidate-01 evolution exactly once."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path} but found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_core() -> None:
    path = ROOT / "research" / "candidate-01" / "core.py"
    replace_once(
        path,
        "    min_reversal_flow_z: float = 0.35\n    stop_buffer_atr: float = 0.15\n",
        "    min_reversal_flow_z: float = 0.35\n"
        "    max_structure_overshoot_atr: float = 1.0\n"
        "    stop_buffer_atr: float = 0.15\n",
    )
    replace_once(
        path,
        "        if self.min_displacement_atr <= 0.0:\n"
        "            raise ValueError(\"min_displacement_atr must be positive\")\n"
        "        if self.minimum_stop_atr <= self.stop_buffer_atr:\n",
        "        if self.min_displacement_atr <= 0.0:\n"
        "            raise ValueError(\"min_displacement_atr must be positive\")\n"
        "        if self.max_structure_overshoot_atr <= 0.0:\n"
        "            raise ValueError(\"max_structure_overshoot_atr must be positive\")\n"
        "        if self.minimum_stop_atr <= self.stop_buffer_atr:\n",
    )
    replace_once(
        path,
        "        if not displaced:\n"
        "            return None\n\n"
        "        previous = scenario.phase\n",
        "        if not displaced:\n"
        "            return None\n\n"
        "        structure_overshoot = (\n"
        "            (scenario.internal_break - bar.close) / atr\n"
        "            if scenario.side is Side.SHORT\n"
        "            else (bar.close - scenario.internal_break) / atr\n"
        "        )\n"
        "        if structure_overshoot > self.config.max_structure_overshoot_atr:\n"
        "            self._expire(bar, \"REVERSAL_DISPLACEMENT_ALREADY_OVEREXTENDED\")\n"
        "            return None\n\n"
        "        previous = scenario.phase\n",
    )
    replace_once(
        path,
        "                \"body_atr\": body / atr,\n"
        "                \"internal_break\": scenario.internal_break,\n",
        "                \"body_atr\": body / atr,\n"
        "                \"structure_overshoot_atr\": structure_overshoot,\n"
        "                \"internal_break\": scenario.internal_break,\n",
    )


def patch_execution() -> None:
    path = ROOT / "research" / "candidate-01" / "nautilus_backtest.py"
    replace_once(
        path,
        "    venue_max_leverage: float = 125.0\n    price_precision: int = 1\n",
        "    venue_max_leverage: float = 125.0\n"
        "    minimum_price_risk_fraction: float = 0.65\n"
        "    price_precision: int = 1\n",
    )
    replace_once(
        path,
        "        if self.venue_max_leverage <= 0:\n"
        "            raise ValueError(\"venue_max_leverage must be positive\")\n\n",
        "        if self.venue_max_leverage <= 0:\n"
        "            raise ValueError(\"venue_max_leverage must be positive\")\n"
        "        if not 0.0 < self.minimum_price_risk_fraction < 1.0:\n"
        "            raise ValueError(\"minimum_price_risk_fraction must be in (0, 1)\")\n\n",
    )
    replace_once(
        path,
        "        minimum_net_reward_risk: Decimal\n"
        "        evaluation_start_ns: int\n",
        "        minimum_net_reward_risk: Decimal\n"
        "        minimum_price_risk_fraction: Decimal\n"
        "        evaluation_start_ns: int\n",
    )
    replace_once(
        path,
        "            self.delayed_plan_rejections = 0\n"
        "            self.max_hold_exits = 0\n",
        "            self.delayed_plan_rejections = 0\n"
        "            self.cost_dominated_plan_rejections = 0\n"
        "            self.max_hold_exits = 0\n",
    )
    replace_once(
        path,
        "            equity = self._equity()\n"
        "            cost = float(self.config.cost_fraction_per_side)\n"
        "            planned_loss_per_unit = abs(entry - stop) + entry * cost + stop * cost\n"
        "            planned_gain_per_unit = abs(target - entry) - entry * cost - target * cost\n",
        "            equity = self._equity()\n"
        "            cost = float(self.config.cost_fraction_per_side)\n"
        "            price_risk = abs(entry - stop)\n"
        "            round_trip_cost_at_stop = entry * cost + stop * cost\n"
        "            planned_loss_per_unit = price_risk + round_trip_cost_at_stop\n"
        "            price_risk_fraction = (\n"
        "                price_risk / planned_loss_per_unit if planned_loss_per_unit > 0.0 else 0.0\n"
        "            )\n"
        "            if price_risk_fraction < float(self.config.minimum_price_risk_fraction):\n"
        "                self.cost_dominated_plan_rejections += 1\n"
        "                self.delayed_plan_rejections += 1\n"
        "                self._record(\n"
        "                    \"PLAN_REJECTED_COST_DOMINATED_INVALIDATION\",\n"
        "                    ts_ns,\n"
        "                    scenario_id=plan.scenario_id,\n"
        "                    entry=entry,\n"
        "                    stop=stop,\n"
        "                    price_risk=price_risk,\n"
        "                    round_trip_cost_at_stop=round_trip_cost_at_stop,\n"
        "                    price_risk_fraction=price_risk_fraction,\n"
        "                    minimum_price_risk_fraction=float(self.config.minimum_price_risk_fraction),\n"
        "                )\n"
        "                self.gate.release(plan.scenario_id)\n"
        "                return\n"
        "            planned_gain_per_unit = abs(target - entry) - entry * cost - target * cost\n",
    )
    replace_once(
        path,
        "                \"planned_loss_per_unit_after_cost\": planned_loss_per_unit,\n"
        "                \"planned_gain_per_unit_after_cost\": planned_gain_per_unit,\n",
        "                \"price_risk\": price_risk,\n"
        "                \"round_trip_cost_at_stop\": round_trip_cost_at_stop,\n"
        "                \"price_risk_fraction\": price_risk_fraction,\n"
        "                \"planned_loss_per_unit_after_cost\": planned_loss_per_unit,\n"
        "                \"planned_gain_per_unit_after_cost\": planned_gain_per_unit,\n",
    )
    replace_once(
        path,
        "        \"rejected_after_one_bar_delay\": strategy.delayed_plan_rejections,\n"
        "        \"max_hold_exits\": strategy.max_hold_exits,\n",
        "        \"rejected_after_one_bar_delay\": strategy.delayed_plan_rejections,\n"
        "        \"cost_dominated_plan_rejections\": strategy.cost_dominated_plan_rejections,\n"
        "        \"max_hold_exits\": strategy.max_hold_exits,\n",
    )
    replace_once(
        path,
        "            minimum_net_reward_risk=Decimal(str(execution.minimum_net_reward_risk)),\n"
        "            evaluation_start_ns=int(pd.Timestamp(evaluation_start).value),\n",
        "            minimum_net_reward_risk=Decimal(str(execution.minimum_net_reward_risk)),\n"
        "            minimum_price_risk_fraction=Decimal(str(execution.minimum_price_risk_fraction)),\n"
        "            evaluation_start_ns=int(pd.Timestamp(evaluation_start).value),\n",
    )


def patch_config() -> None:
    path = ROOT / "research" / "candidate-01" / "config.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidate"]["max_structure_overshoot_atr"] = 1.0
    payload["candidate"]["enable_acceptance_failure"] = False
    payload["execution"]["minimum_price_risk_fraction"] = 0.65
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def patch_tests() -> None:
    path = ROOT / "tests" / "test_candidate_01.py"
    replace_once(
        path,
        "        \"min_reversal_flow_z\": 0.3,\n"
        "        \"stop_buffer_atr\": 0.1,\n",
        "        \"min_reversal_flow_z\": 0.3,\n"
        "        \"max_structure_overshoot_atr\": 10.0,\n"
        "        \"stop_buffer_atr\": 0.1,\n",
    )
    replace_once(
        path,
        "    def test_acceptance_failure_requires_two_outside_closes_then_reentry(self) -> None:\n",
        "    def test_overextended_displacement_is_not_chased(self) -> None:\n"
        "        machine = AuctionStateMachine(\n"
        "            base_config(\n"
        "                enable_acceptance_failure=False,\n"
        "                max_structure_overshoot_atr=0.25,\n"
        "            ),\n"
        "        )\n"
        "        sequence = completed_anchor()\n"
        "        sequence.extend(\n"
        "            [\n"
        "                bar(60, 109.8, 110.8, 109.0, 109.4, quote=2_000.0, buy_quote=1_750.0),\n"
        "                bar(61, 109.2, 109.3, 103.5, 104.0, quote=3_000.0, buy_quote=250.0),\n"
        "            ],\n"
        "        )\n"
        "        plans = [plan for item in sequence if (plan := machine.on_bar(item)) is not None]\n"
        "        self.assertEqual(plans, [])\n"
        "        reasons = [event.reason_code for event in machine.transitions]\n"
        "        self.assertIn(\"REVERSAL_DISPLACEMENT_ALREADY_OVEREXTENDED\", reasons)\n\n"
        "    def test_acceptance_failure_requires_two_outside_closes_then_reentry(self) -> None:\n",
    )


def main() -> None:
    patch_core()
    patch_execution()
    patch_config()
    patch_tests()


if __name__ == "__main__":
    main()
