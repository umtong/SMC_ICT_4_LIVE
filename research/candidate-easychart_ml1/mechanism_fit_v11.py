#!/usr/bin/env python3
"""Family-routed competing-risk policy for the exact-tape grammar.

Reversal, continuation, forced-flow exhaustion, common-factor catch-up and
failed acceptance are different latent auctions. A single pooled model can
average away the evidence that distinguishes them. V11 keeps a shared global
prior but lets each sufficiently represented mechanism supply a low-capacity
expert. Predictions are blended, never selected by symbol or calendar, and are
still calibrated only from period-held-out development predictions.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_fit_v3 as fit
from mechanism_tape_v11 import FEATURE_COLUMNS

MIN_FAMILY_ROWS = 700
FAMILY_BLEND = 0.60


@dataclass
class FamilyRoutedModel:
    global_model: fit.OutcomeModel
    experts: dict[str, fit.OutcomeModel]
    kind: str

    def predict(self, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        output = self.global_model.predict(frame)
        for family, expert in self.experts.items():
            mask = frame["family"].astype(str).eq(family).to_numpy()
            if not np.any(mask):
                continue
            local = expert.predict(frame.loc[mask])
            output["probability"][mask] = (
                (1.0 - FAMILY_BLEND) * output["probability"][mask]
                + FAMILY_BLEND * local["probability"]
            )
            for key in ("target_log", "stop_log", "timeout_log", "duration"):
                output[key][mask] = (
                    (1.0 - FAMILY_BLEND) * output[key][mask]
                    + FAMILY_BLEND * local[key]
                )
        probability = np.clip(output["probability"], 1e-7, None)
        output["probability"] = probability / probability.sum(axis=1, keepdims=True)
        return output

    def description(self) -> dict[str, Any]:
        return {
            "kind": f"family_routed_{self.kind}",
            "family_blend": FAMILY_BLEND,
            "global": self.global_model.description(),
            "experts": {
                family: model.description()
                for family, model in sorted(self.experts.items())
            },
        }


def _fit_family_routed(
    frame: pd.DataFrame,
    kind: str,
    seed: int,
) -> FamilyRoutedModel:
    global_model = fit.OutcomeModel.fit(frame, kind=kind, seed=seed)
    experts: dict[str, fit.OutcomeModel] = {}
    for family_index, (family, group) in enumerate(
        frame.groupby("family", sort=True)
    ):
        if len(group) < MIN_FAMILY_ROWS or group["outcome"].nunique() < 2:
            continue
        try:
            experts[str(family)] = fit.OutcomeModel.fit(
                group,
                kind=kind,
                seed=seed + 100 + family_index,
            )
        except Exception:
            continue
    return FamilyRoutedModel(
        global_model=global_model,
        experts=experts,
        kind=kind,
    )


def _fit_ensemble(frame: pd.DataFrame, seed: int = 7) -> list[Any]:
    fit_frame = fit._thin_for_fit(frame)
    models: list[Any] = []
    for kind_index, kind in enumerate(("linear", "tree")):
        try:
            models.append(
                _fit_family_routed(
                    fit_frame,
                    kind=kind,
                    seed=seed + kind_index,
                )
            )
        except Exception:
            continue

    # Asset transfer is tested through low-variance leave-one-symbol linear
    # models. They are ensemble members, not symbol-specific live policies.
    for symbol_index, symbol in enumerate(sorted(fit_frame["symbol"].unique())):
        subset = fit_frame[fit_frame["symbol"] != symbol]
        if len(subset) < 1800 or subset["outcome"].nunique() < 2:
            continue
        try:
            models.append(
                fit.OutcomeModel.fit(
                    subset,
                    kind="linear",
                    seed=seed + 500 + symbol_index,
                )
            )
        except Exception:
            continue
    if not models:
        raise RuntimeError("no exact-tape family-routed model could be fitted")
    return models


def _rewrite_summary(output: Path) -> None:
    path = output / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["policy"]["name"] = "EXACT_TAPE_FAMILY_ROUTED_COMPETING_RISK_V11"
    summary["policy"]["family_routing"] = {
        "blend": FAMILY_BLEND,
        "minimum_family_rows": MIN_FAMILY_ROWS,
        "principle": "SHARED_GLOBAL_PRIOR_PLUS_MECHANISM_EXPERT_NO_SYMBOL_IDENTITY",
    }
    summary["policy"]["exact_tape"] = (
        "CHECKSUM_VERIFIED_AGGTRADES_STRICTLY_BEFORE_DECISION"
    )
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    fit.FEATURE_COLUMNS = FEATURE_COLUMNS
    fit._fit_ensemble = _fit_ensemble
    args = parse_args()
    fit.run(args.root, args.output)
    _rewrite_summary(args.output)


if __name__ == "__main__":
    main()
