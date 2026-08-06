#!/usr/bin/env python3
"""Run the common corrected rich-feature builder for one allowed symbol.

No feature is reimplemented here.  The common ``rich_features.py`` source is
loaded from the candidate directory, its BTCUSDT literal is replaced only with
one of the four allowed experiment symbols, and the already validated
elapsed-time depth aggregation from ``rich_features_v2.py`` is injected.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile

import rich_features_v2 as corrected


ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
BASE_PATH = Path(__file__).with_name("rich_features.py")


def transform_source(source: str, symbol: str) -> str:
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError(f"unsupported symbol: {symbol}")
    return source.replace('"BTCUSDT"', f'"{symbol}"').replace(
        "'BTCUSDT'",
        f"'{symbol}'",
    )


def load_symbol_module(symbol: str):
    source = transform_source(BASE_PATH.read_text(encoding="utf-8"), symbol)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=f"_{symbol.lower()}.py",
        prefix=".rich_features_symbol_",
        dir=BASE_PATH.parent,
        delete=False,
        encoding="utf-8",
    )
    try:
        handle.write(source)
        handle.close()
        path = Path(handle.name)
        name = f"candidate04_rich_features_{symbol.lower()}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load generated feature module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        module.aggregate_depth = corrected.aggregate_depth
        return module, path
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--symbol", required=True, choices=sorted(ALLOWED_SYMBOLS))
    known, remaining = parser.parse_known_args()
    module, generated = load_symbol_module(known.symbol)
    original_argv = sys.argv
    try:
        sys.argv = [str(BASE_PATH), *remaining]
        module.main()
    finally:
        sys.argv = original_argv
        generated.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
