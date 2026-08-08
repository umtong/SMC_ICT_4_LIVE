#!/usr/bin/env python3
"""Patch v50 with the v51 size-dependent all-cost reward certificate."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v50_patch import patch as patch_v50


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v50(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v49_overlay import (\n",
        "from c10_v51_overlay import (\n"
        "    size_dependent_reward_certificate,\n",
        "v51 overlay import",
    )

    preview = '''                instrument = instruments[symbol]
                nav, free_balance = self._account_values()
                liquidity_notional = causal_liquidity.get(
                    (str(instrument.id), ts_ns),
                )
                if liquidity_notional is None or liquidity_notional <= 0.0:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        "MISSING_CAUSAL_LIQUIDITY_NOTIONAL",
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "SIZE_DEPENDENT_REWARD_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "reason": "MISSING_CAUSAL_LIQUIDITY_NOTIONAL",
                    })
                    continue
                self.sizer.set_context(
                    atr=plan.atr,
                    liquidity_notional=liquidity_notional,
                    tick_size=float(str(instrument.price_increment)),
                )
                preview_decision = self.sizer.size(
                    nav=nav,
                    loss_per_unit=Decimal(str(plan.loss_per_unit)),
                    entry_price=Decimal(str(plan.expected_entry)),
                    quantity_increment=Decimal(
                        str(instrument.size_increment),
                    ),
                    min_quantity=Decimal(str(instrument.min_quantity)),
                    min_notional=_decimal(instrument.min_notional),
                    margin_init=instrument.margin_init,
                    free_balance=free_balance,
                )
                if not preview_decision.feasible:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        preview_decision.reason,
                        {
                            "required_margin": str(
                                preview_decision.required_margin
                            ),
                            "free_balance": str(free_balance),
                        },
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "SIZE_DEPENDENT_REWARD_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "reason": preview_decision.reason,
                    })
                    continue
                preview_solution = self.sizer.last_solution
                if preview_solution is None:
                    raise RuntimeError(
                        "size-dependent preview solution is unavailable"
                    )
                reward_economics = size_dependent_reward_certificate(
                    plan,
                    preview_solution,
                    minimum_net_r=float(logic_config.min_net_r),
                )
                plan.details["size_dependent_all_cost_economics"] = (
                    reward_economics.details
                )
                if not reward_economics.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        reward_economics.reason,
                        reward_economics.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "SIZE_DEPENDENT_REWARD_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": reward_economics.reason,
                        "details": reward_economics.details,
                    })
                    continue
                candidate = Candidate(
'''
    text = replace_once(
        text,
        "                candidate = Candidate(\n",
        preview,
        "v51 pre-arbitration cost certificate",
    )
    text = replace_once(
        text,
        "                    net_structural_r=Decimal(str(plan.net_r)),\n",
        "                    net_structural_r=(\n"
        "                        reward_economics.impact_adjusted_net_r\n"
        "                    ),\n",
        "v51 arbitration all-cost R",
    )
    text = replace_once(
        text,
        "                    expected_loss_per_unit=Decimal(str(plan.loss_per_unit)),\n",
        "                    expected_loss_per_unit=(\n"
        "                        preview_decision.expected_loss_per_unit\n"
        "                    ),\n",
        "v51 arbitration all-cost loss",
    )

    submit_old = '''            if not decision.feasible:
                self.logic[symbol].mark_rejected(plan, self.last_ts_ns, decision.reason, {
                    "required_margin": str(decision.required_margin),
                    "free_balance": str(free_balance),
                })
                self._capture_events(symbol)
                return

            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
'''
    submit_new = '''            if not decision.feasible:
                self.logic[symbol].mark_rejected(plan, self.last_ts_ns, decision.reason, {
                    "required_margin": str(decision.required_margin),
                    "free_balance": str(free_balance),
                })
                self._capture_events(symbol)
                return

            submission_solution = self.sizer.last_solution
            if submission_solution is None:
                raise RuntimeError(
                    "size-dependent submission solution is unavailable"
                )
            submission_economics = size_dependent_reward_certificate(
                plan,
                submission_solution,
                minimum_net_r=float(logic_config.min_net_r),
            )
            plan.details["size_dependent_all_cost_economics"] = (
                submission_economics.details
            )
            if not submission_economics.approved:
                self.logic[symbol].mark_rejected(
                    plan,
                    self.last_ts_ns,
                    submission_economics.reason,
                    submission_economics.details,
                )
                self._capture_events(symbol)
                self.rejections.append({
                    "type": "SIZE_DEPENDENT_REWARD_REJECTED_AT_SUBMISSION",
                    "observed_ts_ns": plan.observed_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "symbol": symbol,
                    "direction": plan.direction.value,
                    "reason": submission_economics.reason,
                    "details": submission_economics.details,
                })
                return

            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
'''
    text = replace_once(
        text,
        submit_old,
        submit_new,
        "v51 submission cost certificate",
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
