"""Strict lazy loader for every promotable family/mechanism candidate."""
from __future__ import annotations

import importlib
import os
from typing import Any

from variant_registry_v3 import ProductionVariantSpecV3, VARIANTS


def load_object(path: str) -> Any:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"invalid import path: {path!r}")
    return getattr(importlib.import_module(module_name), attribute)


def set_optional(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def selected_variant() -> tuple[str, ProductionVariantSpecV3]:
    name = os.environ.get("EASYCHART_RE1_VARIANT", "").strip()
    try:
        spec = VARIANTS[name]
    except KeyError as exc:
        raise SystemExit(
            "EASYCHART_RE1_VARIANT must be one of: " + ", ".join(sorted(VARIANTS)),
        ) from exc
    set_optional("EASYCHART_RE1_FAMILIES", spec.families)
    set_optional("EASYCHART_RE1_MECHANISMS", spec.mechanisms)
    return name, spec


__all__ = ["load_object", "selected_variant"]
