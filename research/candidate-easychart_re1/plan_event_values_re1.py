"""Stable audit serialization for RE1 plans from heterogeneous scenario engines.

Legacy plans expose Enum zone kinds while newer auction/continuation families may
carry explicit string kinds.  Audit serialization must preserve either form; it
must never change the immutable plan or abort a backtest merely because the
trace field already is a string.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any


def _value(item: Any) -> Any:
    return getattr(item, "value", item)


def plan_event_values(plan: Any) -> dict[str, Any]:
    values = asdict(plan)
    values["side"] = plan.side.name
    values["higher_zone_kind"] = _value(plan.higher_zone_kind)
    values["lower_zone_kind"] = _value(plan.lower_zone_kind)
    values["target_zone_kind"] = _value(plan.target_zone_kind)
    values["trigger_zone_kind"] = _value(plan.trigger_zone_kind)
    values["context_kind_diversity"] = len(
        {
            str(values["higher_zone_kind"]),
            str(values["lower_zone_kind"]),
        },
    )
    return values
