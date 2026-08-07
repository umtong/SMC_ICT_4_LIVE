#!/usr/bin/env python3
"""Deterministically patch Candidate 11 with the v27 all-cost execution overlay."""
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from market_leadership import MarketLeadershipGate\n",
        "from market_leadership import MarketLeadershipGate\n"
        "from c10_v27_overlay import (\n"
        "    CostAwareRiskSizer,\n"
        "    LiveImpactLedger,\n"
        "    apply_cost_overlay,\n"
        "    build_leadership_gate,\n"
        ")\n",
        "overlay import",
    )
    text = replace_once(
        text,
        "    flow: dict[tuple[str, int], tuple[float, float]] = {}\n",
        "    flow: dict[tuple[str, int], tuple[float, float]] = {}\n"
        "    causal_liquidity: dict[tuple[str, int], float] = {}\n",
        "liquidity map",
    )
    text = replace_once(
        text,
        '''        for ts, volume, taker in zip(frame.index, frame["volume"], frame["taker_buy_volume"], strict=True):
            flow[(str(instrument_id), int(ts.value))] = (float(volume), float(taker))
        instruments[symbol] = instrument
''',
        '''        for ts, volume, taker in zip(frame.index, frame["volume"], frame["taker_buy_volume"], strict=True):
            flow[(str(instrument_id), int(ts.value))] = (float(volume), float(taker))
        quote_notional = (frame["close"] * frame["volume"]).shift(1).rolling(
            window=120,
            min_periods=30,
        ).median()
        for ts, value in quote_notional.items():
            if pd.notna(value) and float(value) > 0.0:
                causal_liquidity[(str(instrument_id), int(ts.value))] = float(value)
        instruments[symbol] = instrument
''',
        "causal liquidity calculation",
    )
    text = replace_once(
        text,
        "            self.sizer = RiskSizer(logic_config.risk_fraction)\n",
        "            self.sizer = CostAwareRiskSizer(logic_config.risk_fraction)\n",
        "cost-aware sizer",
    )
    text = replace_once(
        text,
        "            self.leadership = MarketLeadershipGate(SYMBOLS, lookback_bars=1440)\n",
        "            self.leadership = build_leadership_gate(\n"
        "                MarketLeadershipGate,\n"
        "                SYMBOLS,\n"
        "                lookback_bars=1440,\n"
        "            )\n",
        "leadership ablation adapter",
    )
    text = replace_once(
        text,
        "            self.last_ts_ns = 0\n",
        "            self.last_ts_ns = 0\n"
        "            self.impact_ledger = LiveImpactLedger()\n"
        "            self.cost_records: list[dict[str, Any]] = []\n"
        "            self.active_cost_record: dict[str, Any] | None = None\n"
        "            self.active_entry_order_id: str | None = None\n",
        "cost ledger state",
    )
    text = replace_once(
        text,
        '''        def _account_values(self) -> tuple[Decimal, Decimal]:
            account = self.cache.account_for_venue(venue)
            if account is None:
                return self.config.starting_nav, self.config.starting_nav
            total = _decimal(account.balance_total(USDT))
            free = _decimal(account.balance_free(USDT), total) if hasattr(account, "balance_free") else total
            return total, free
''',
        '''        def _account_values(self) -> tuple[Decimal, Decimal]:
            account = self.cache.account_for_venue(venue)
            if account is None:
                raw_total = self.config.starting_nav
                raw_free = self.config.starting_nav
            else:
                raw_total = _decimal(account.balance_total(USDT))
                raw_free = (
                    _decimal(account.balance_free(USDT), raw_total)
                    if hasattr(account, "balance_free")
                    else raw_total
                )
            total = self.impact_ledger.conservative_equity(raw_total)
            free = max(Decimal("0"), raw_free - self.impact_ledger.cumulative_cost)
            return total, free
''',
        "conservative account values",
    )
    text = replace_once(
        text,
        '''            nav, free_balance = self._account_values()
            decision = self.sizer.size(
''',
        '''            nav, free_balance = self._account_values()
            liquidity_notional = causal_liquidity.get(
                (str(instrument.id), self.last_ts_ns),
            )
            if liquidity_notional is None or liquidity_notional <= 0.0:
                self.logic[symbol].mark_rejected(
                    plan,
                    self.last_ts_ns,
                    "MISSING_CAUSAL_LIQUIDITY_NOTIONAL",
                )
                self._capture_events(symbol)
                return
            self.sizer.set_context(
                atr=plan.atr,
                liquidity_notional=liquidity_notional,
                tick_size=float(str(instrument.price_increment)),
            )
            decision = self.sizer.size(
''',
        "sizer context",
    )
    text = replace_once(
        text,
        '''                self.submit_order_list(order_list)
            except Exception as exc:
''',
        '''                self.submit_order_list(order_list)
                parent_orders = list(getattr(order_list, "orders", ()))
                if not parent_orders:
                    raise RuntimeError("Nautilus bracket returned no parent order")
                entry_order_id = str(parent_orders[0].client_order_id)
            except Exception as exc:
''',
        "capture parent order",
    )
    text = replace_once(
        text,
        '''            self.mutex.mark_entry_submitted(candidate)
            self.active_plan = plan
            self.active_symbol = symbol
            self.plans.append({
''',
        '''            self.mutex.mark_entry_submitted(candidate)
            solution = self.sizer.last_solution
            if solution is None:
                raise RuntimeError("cost-aware sizing solution is unavailable")
            self.active_plan = plan
            self.active_symbol = symbol
            self.active_entry_order_id = entry_order_id
            self.active_cost_record = {
                "scenario_id": plan.scenario_id,
                "symbol": symbol,
                "entry_order_id": entry_order_id,
                "conservative_nav_before": float(nav),
                "planned_loss_budget": float(solution.planned_loss_budget),
                "expected_total_loss": float(solution.expected_total_loss),
                "impact_per_side": float(solution.impact_per_side),
                "participation": float(solution.participation),
                "liquidity_notional": float(solution.liquidity_notional),
                "atr": float(solution.atr),
                "quantity": float(solution.quantity),
                "entry_filled_qty": 0.0,
                "exit_filled_qty": 0.0,
                "entry_impact_cost": 0.0,
                "exit_impact_cost": 0.0,
                "first_entry_fill_ts_ns": 0,
                "last_exit_fill_ts_ns": 0,
            }
            self.cost_records.append(self.active_cost_record)
            self.plans.append({
''',
        "cost record initialization",
    )
    text = text.replace(
        '''                    self.active_plan = None
                    self.active_symbol = None
''',
        '''                    self.active_plan = None
                    self.active_symbol = None
                    self.active_cost_record = None
                    self.active_entry_order_id = None
''',
    )
    if text.count("self.active_cost_record = None") < 3:
        raise RuntimeError("terminal cost state reset markers were not patched")

    text = replace_once(
        text,
        '''        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")
''',
        '''        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            if self.active_cost_record is not None:
                quantity = _decimal(event.last_qty)
                is_entry = str(event.client_order_id) == self.active_entry_order_id
                role = "ENTRY" if is_entry else "EXIT"
                impact = Decimal(str(self.active_cost_record["impact_per_side"]))
                cost = self.impact_ledger.debit(
                    quantity=quantity,
                    impact_per_unit=impact,
                )
                if is_entry:
                    self.active_cost_record["entry_filled_qty"] += float(quantity)
                    self.active_cost_record["entry_impact_cost"] += float(cost)
                    if not self.active_cost_record["first_entry_fill_ts_ns"]:
                        self.active_cost_record["first_entry_fill_ts_ns"] = int(event.ts_event)
                else:
                    self.active_cost_record["exit_filled_qty"] += float(quantity)
                    self.active_cost_record["exit_impact_cost"] += float(cost)
                    self.active_cost_record["last_exit_fill_ts_ns"] = int(event.ts_event)
                self.lifecycle.append({
                    "type": "MODELED_IMPACT_DEBITED",
                    "role": role,
                    "ts_event": int(event.ts_event),
                    "scenario_id": self.active_cost_record["scenario_id"],
                    "quantity": float(quantity),
                    "impact_per_unit": float(impact),
                    "cost": float(cost),
                    "cumulative_cost": float(self.impact_ledger.cumulative_cost),
                })
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")
''',
        "fill-time impact debit",
    )
    text = replace_once(
        text,
        '''        metrics = calculate_metrics(
            starting_nav=starting_nav,
            final_nav=final_nav,
            evaluation_days=int(config["selection"]["evaluation_days"]),
            positions=positions,
            plans=strategy.plans,
            logics=strategy.logic,
            errors=strategy.errors,
            lifecycle=strategy.lifecycle,
            gates=config["gates"],
        )
''',
        '''        metrics = calculate_metrics(
            starting_nav=starting_nav,
            final_nav=final_nav,
            evaluation_days=int(config["selection"]["evaluation_days"]),
            positions=positions,
            plans=strategy.plans,
            logics=strategy.logic,
            errors=strategy.errors,
            lifecycle=strategy.lifecycle,
            gates=config["gates"],
        )
        metrics = apply_cost_overlay(
            metrics=metrics,
            positions=positions,
            cost_records=strategy.cost_records,
            starting_nav=float(starting_nav),
            evaluation_days=int(config["selection"]["evaluation_days"]),
        )
        write_json_atomic(
            output_dir / "impact_cost_records.json",
            {"records": strategy.cost_records},
        )
''',
        "cost-adjusted metrics",
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
