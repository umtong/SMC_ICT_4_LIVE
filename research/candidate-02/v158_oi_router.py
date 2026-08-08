#!/usr/bin/env python3
"""Causal open-interest state router for Candidate-02 V158.

The router reuses the pre-existing Candidate-05 positioning-reset contract:
a failed-auction reversal is eligible only when an official Binance USD-M
five-minute metrics observation created after the sweep is visible by the
Candidate-13 confirmation time and the causal 15-minute OI change is no more
than +0.10%. Candidate-13 AAC decisions are untouched.

This module owns no orders, fills, sizing, fees, positions or NAV. It only
refines the market-state decision returned by Candidate-13's synchronized
price-discovery gate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pandas as pd

from semantic_market_leadership_v4 import SemanticMarketLeadershipGate

BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
MAX_NONMATERIAL_OI_EXPANSION_15M = 0.001
OBSERVATION_DELAY = pd.Timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ArchiveEvidence:
    symbol: str
    day: str
    status: str
    url: str
    path: str | None
    bytes: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class OIDecision:
    approved: bool
    reason: str
    symbol: str
    scenario: str
    sweep_ts_ns: int
    confirmation_ts_ns: int
    metrics_capture: bool
    metrics_create_time: str | None
    metrics_observed_time: str | None
    oi_change_15m: float | None


class CausalOIRouter:
    """Prepared, fail-closed OI registry queried at confirmation time."""

    def __init__(self) -> None:
        self.frames: dict[str, pd.DataFrame] = {}
        self.archive_evidence: list[ArchiveEvidence] = []
        self.decisions: list[OIDecision] = []
        self.prepared = False

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            while block := stream.read(1 << 20):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _download(url: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 100:
            return
        request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-02-v158"})
        with urlopen(request, timeout=60) as response:  # noqa: S310 fixed HTTPS host
            payload = response.read()
        if len(payload) < 100:
            raise RuntimeError(f"unexpectedly small response from {url}")
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)

    def _archive(self, symbol: str, day: date, cache: Path) -> Path | None:
        stamp = day.isoformat()
        filename = f"{symbol}-metrics-{stamp}.zip"
        url = f"{BASE}/{symbol}/{filename}"
        root = cache / symbol
        archive = root / filename
        checksum = root / f"{filename}.CHECKSUM"
        try:
            self._download(url, archive)
            self._download(url + ".CHECKSUM", checksum)
        except HTTPError as exc:
            if exc.code == 404:
                archive.unlink(missing_ok=True)
                checksum.unlink(missing_ok=True)
                self.archive_evidence.append(ArchiveEvidence(
                    symbol=symbol,
                    day=stamp,
                    status="OFFICIAL_ARCHIVE_UNAVAILABLE",
                    url=url,
                    path=None,
                    bytes=None,
                    sha256=None,
                ))
                return None
            raise
        expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
        actual = self._sha256_file(archive)
        if actual != expected:
            raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
        with ZipFile(archive) as zipped:
            bad = zipped.testzip()
            if bad is not None:
                raise RuntimeError(f"corrupt metrics member {bad} in {archive}")
        self.archive_evidence.append(ArchiveEvidence(
            symbol=symbol,
            day=stamp,
            status="VERIFIED",
            url=url,
            path=str(archive),
            bytes=archive.stat().st_size,
            sha256=actual,
        ))
        return archive

    @staticmethod
    def _read_archive(path: Path) -> pd.DataFrame:
        frame = pd.read_csv(path, compression="zip")
        required = {"create_time", "sum_open_interest"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RuntimeError(f"metrics columns missing from {path}: {missing}")
        frame = frame[["create_time", "sum_open_interest"]].copy()
        frame["metrics_create_time"] = pd.to_datetime(
            frame.pop("create_time"), utc=True, errors="raise",
        )
        frame["metrics_observed_time"] = frame["metrics_create_time"] + OBSERVATION_DELAY
        frame["sum_open_interest"] = pd.to_numeric(
            frame["sum_open_interest"], errors="raise",
        ).astype("float64")
        frame = frame.loc[frame["sum_open_interest"] > 0].copy()
        return frame.sort_values("metrics_observed_time", kind="stable")

    @staticmethod
    def _deduplicate(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        frame = frame.sort_values(
            ["metrics_observed_time", "metrics_create_time"], kind="stable",
        )
        groups: list[pd.Series] = []
        for observed, group in frame.groupby("metrics_observed_time", sort=True):
            values = group["sum_open_interest"].dropna().unique()
            if len(values) > 1:
                raise RuntimeError(
                    f"conflicting OI values at causal observation {observed}: {values.tolist()}"
                )
            groups.append(group.iloc[-1])
        result = pd.DataFrame(groups).sort_values("metrics_observed_time", kind="stable")
        result["oi_change_15m"] = result["sum_open_interest"].pct_change(
            3, fill_method=None,
        )
        if result["metrics_observed_time"].duplicated().any():
            raise RuntimeError("duplicate causal OI observation timestamp")
        return result.reset_index(drop=True)

    @staticmethod
    def _days(start: date, end_inclusive: date) -> Iterable[date]:
        cursor = start
        while cursor <= end_inclusive:
            yield cursor
            cursor += timedelta(days=1)

    def prepare(
        self,
        *,
        symbols: tuple[str, ...],
        evaluation_start: date,
        evaluation_end_exclusive: date,
        cache: Path,
    ) -> None:
        if evaluation_end_exclusive <= evaluation_start:
            raise ValueError("evaluation interval must be positive")
        self.frames.clear()
        self.archive_evidence.clear()
        self.decisions.clear()
        self.prepared = False
        first_day = evaluation_start - timedelta(days=1)
        last_day = evaluation_end_exclusive - timedelta(days=1)
        for symbol in symbols:
            pieces: list[pd.DataFrame] = []
            for day in self._days(first_day, last_day):
                archive = self._archive(symbol, day, cache)
                if archive is not None:
                    pieces.append(self._read_archive(archive))
            self.frames[symbol] = self._deduplicate(
                pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(
                    columns=[
                        "metrics_create_time",
                        "metrics_observed_time",
                        "sum_open_interest",
                        "oi_change_15m",
                    ]
                )
            )
        self.prepared = True

    @staticmethod
    def _finite(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def evaluate(
        self,
        *,
        symbol: str,
        scenario: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
    ) -> OIDecision:
        if not self.prepared:
            raise RuntimeError("OI router queried before prepare")
        if scenario != "FAR":
            result = OIDecision(
                approved=True,
                reason="AAC_OI_ROUTER_NOT_APPLICABLE",
                symbol=symbol,
                scenario=scenario,
                sweep_ts_ns=int(sweep_ts_ns),
                confirmation_ts_ns=int(confirmation_ts_ns),
                metrics_capture=False,
                metrics_create_time=None,
                metrics_observed_time=None,
                oi_change_15m=None,
            )
            self.decisions.append(result)
            return result
        frame = self.frames.get(symbol)
        if frame is None or frame.empty:
            result = OIDecision(
                False, "FAR_OI_UNRESOLVED_NO_ARCHIVE", symbol, scenario,
                int(sweep_ts_ns), int(confirmation_ts_ns), False, None, None, None,
            )
            self.decisions.append(result)
            return result
        sweep = pd.Timestamp(int(sweep_ts_ns), unit="ns", tz="UTC")
        confirmation = pd.Timestamp(int(confirmation_ts_ns), unit="ns", tz="UTC")
        if confirmation <= sweep:
            raise ValueError("confirmation must follow sweep")
        eligible = frame.loc[frame["metrics_observed_time"] <= confirmation]
        if eligible.empty:
            result = OIDecision(
                False, "FAR_OI_UNRESOLVED_NO_VISIBLE_METRIC", symbol, scenario,
                int(sweep_ts_ns), int(confirmation_ts_ns), False, None, None, None,
            )
            self.decisions.append(result)
            return result
        row = eligible.iloc[-1]
        create_time = pd.Timestamp(row["metrics_create_time"])
        observed_time = pd.Timestamp(row["metrics_observed_time"])
        capture = bool(create_time >= sweep)
        oi_change = self._finite(row.get("oi_change_15m"))
        approved = bool(
            capture
            and oi_change is not None
            and oi_change <= MAX_NONMATERIAL_OI_EXPANSION_15M
        )
        if not capture:
            reason = "FAR_OI_UNRESOLVED_NO_POST_SWEEP_METRIC"
        elif oi_change is None:
            reason = "FAR_OI_UNRESOLVED_MISSING_15M_CHANGE"
        elif oi_change > MAX_NONMATERIAL_OI_EXPANSION_15M:
            reason = "FAR_OI_FRESH_INVENTORY_SPONSORSHIP"
        else:
            reason = "FAR_OI_POSITIONING_RESET_CONFIRMED"
        result = OIDecision(
            approved=approved,
            reason=reason,
            symbol=symbol,
            scenario=scenario,
            sweep_ts_ns=int(sweep_ts_ns),
            confirmation_ts_ns=int(confirmation_ts_ns),
            metrics_capture=capture,
            metrics_create_time=create_time.isoformat(),
            metrics_observed_time=observed_time.isoformat(),
            oi_change_15m=oi_change,
        )
        self.decisions.append(result)
        return result

    def summary(self) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for item in self.decisions:
            reasons[item.reason] = reasons.get(item.reason, 0) + 1
        return {
            "schema": "candidate-02-v158-oi-router-evidence-v1",
            "threshold": {
                "maximum_nonmaterial_oi_expansion_15m": MAX_NONMATERIAL_OI_EXPANSION_15M,
                "source": "pre-existing Candidate-05 positioning-reset predicate",
            },
            "prepared": self.prepared,
            "archive_evidence": [asdict(item) for item in self.archive_evidence],
            "decision_counts": dict(sorted(reasons.items())),
            "decisions": [asdict(item) for item in self.decisions],
            "future_information_used": False,
            "missing_archives_synthetically_filled": False,
        }


ROUTER = CausalOIRouter()


class OIGatedSemanticMarketLeadershipGate(SemanticMarketLeadershipGate):
    """Candidate-13 v4 semantics with the pre-existing FAR OI reset gate."""

    def decide(self, **kwargs: Any):  # type: ignore[override]
        decision = super().decide(**kwargs)
        if not decision.approved:
            return decision
        oi = ROUTER.evaluate(
            symbol=str(kwargs["symbol"]),
            scenario=str(kwargs["scenario"]),
            sweep_ts_ns=int(kwargs["sweep_ts_ns"]),
            confirmation_ts_ns=int(kwargs["confirmation_ts_ns"]),
        )
        if oi.approved:
            return decision
        return replace(decision, approved=False, reason=oi.reason)


def write_summary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ROUTER.summary(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
