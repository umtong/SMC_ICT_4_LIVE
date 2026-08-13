#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
from typing import Any

import run_mtf_backtest_re1 as _runner
from research_variant_registry_v10 import VARIANTS


def load_object(path: str) -> Any:
    module_name, separator, attribute = path.partition(":")
    if not separator:
        raise ValueError(f"invalid import path: {path!r}")
    return getattr(importlib.import_module(module_name), attribute)


def set_optional(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def main() -> None:
    name = os.environ.get("EASYCHART_RE1_VARIANT", "").strip()
    try:
        spec = VARIANTS[name]
    except KeyError as exc:
        raise SystemExit(
            "EASYCHART_RE1_VARIANT must be one of: " + ", ".join(sorted(VARIANTS)),
        ) from exc
    set_optional("EASYCHART_RE1_FAMILIES", spec.families)
    set_optional("EASYCHART_RE1_MECHANISMS", spec.mechanisms)
    _runner.EasyChartRE1NaturalBundle = load_object(spec.bundle)
    _runner.EasyChartRE1StructuralStrategy = load_object(spec.strategy)
    _runner.main()


if __name__ == "__main__":
    main()
