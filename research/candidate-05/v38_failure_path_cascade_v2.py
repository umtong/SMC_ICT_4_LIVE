#!/usr/bin/env python3
"""Evidence-label repair for the v38 component-cascade diagnostic.

The v1 cascade correctly computed component counts, but its writer received the
base diagnostic's v1 schema before the v2 compatibility writer converted it.
Consequently the persisted payload carried the v2 label despite containing the
full cascade fields. This wrapper changes only the evidence schema label and
atomic writer; all market observations and predicates remain unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import v38_failure_path_cascade as _cascade
import v38_failure_path_diagnostic as _base
import v38_failure_path_diagnostic_v2 as _v2


CASCADE_SCHEMA = "candidate-05-v38-failure-path-component-cascade-v1"


def write_json(path: Path, value: Any) -> None:
    if isinstance(value, dict) and {
        "method",
        "cases",
        "losing_original_v38_cases",
        "nonnegative_original_v38_cases",
    }.issubset(value):
        value = {
            **value,
            "schema": CASCADE_SCHEMA,
            "component_cascade_purpose": (
                "DISTINGUISH_ABSENT_PRICE_REACCEPTANCE_FROM_MISSING_CAUSAL_SUPPORT"
            ),
            "schema_repair": (
                "LABEL_ONLY_NO_MARKET_OBSERVATION_OR_PREDICATE_CHANGED"
            ),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


# Importing _cascade already installs its analyze and aggregate functions into
# the base diagnostic. Replace only the final writer in every participating
# module so no compatibility layer can overwrite the cascade label.
_base.write_json = write_json
_v2.write_json = write_json
_cascade.write_json = write_json


def main() -> None:
    _base.main()


if __name__ == "__main__":
    main()
