"""Strict import-string loader shared by frozen backtest and paper entry points."""
from __future__ import annotations

import importlib
import os
from typing import Any

from variant_registry import VARIANTS, VariantSpec


def load_object(path: str) -> Any:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"invalid import path: {path!r}")
    module = importlib.import_module(module_name)
    return getattr(module, attribute)


def selected_variant() -> tuple[str, VariantSpec]:
    name = os.environ.get("EASYCHART_RE1_VARIANT", "").strip()
    try:
        spec = VARIANTS[name]
    except KeyError as exc:
        raise SystemExit(
            "EASYCHART_RE1_VARIANT must be one of: " + ", ".join(sorted(VARIANTS)),
        ) from exc
    if spec.families is None:
        os.environ.pop("EASYCHART_RE1_FAMILIES", None)
    else:
        os.environ["EASYCHART_RE1_FAMILIES"] = spec.families
    return name, spec


__all__ = ["load_object", "selected_variant"]
