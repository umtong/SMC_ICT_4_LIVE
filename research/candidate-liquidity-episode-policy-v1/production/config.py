from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import RuntimeMode, SYMBOLS


@dataclass(frozen=True, slots=True)
class ProductionConfig:
    mode: RuntimeMode = RuntimeMode.SHADOW
    symbols: tuple[str, ...] = SYMBOLS
    state_dir: Path = Path("runtime/episode-policy")
    model_bundle: Path = Path("runtime/episode-policy/model_bundle.joblib")
    binance_http_base: str = "https://fapi.binance.com"
    poll_seconds: float = 15.0
    decision_interval_minutes: int = 5
    initial_backfill_days: int = 21
    rolling_window_days: int = 21
    request_timeout_seconds: float = 12.0
    request_retries: int = 4
    starting_balance_usdt: float = 100_000.0
    risk_fraction: float = 0.03
    maximum_leverage: float = 3.0
    minimum_notional_usdt: float = 10.0
    entry_expiry_minutes: int = 45
    heartbeat_seconds: int = 30
    require_model_bundle_for_orders: bool = True
    allow_testnet_orders: bool = False
    close_positions_on_stop: bool = False
    external_order_claims: bool = True
    log_level: str = "INFO"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        mode = self.mode if isinstance(self.mode, RuntimeMode) else RuntimeMode(str(self.mode))
        object.__setattr__(self, "mode", mode)
        symbols = tuple(str(item).upper() for item in self.symbols)
        unknown = sorted(set(symbols) - set(SYMBOLS))
        if unknown or not symbols:
            raise ValueError(f"symbols must be a non-empty subset of {SYMBOLS}; unknown={unknown}")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "state_dir", Path(self.state_dir))
        object.__setattr__(self, "model_bundle", Path(self.model_bundle))
        if self.poll_seconds <= 0.0:
            raise ValueError("poll_seconds must be positive")
        if self.decision_interval_minutes not in {1, 3, 5, 15}:
            raise ValueError("decision_interval_minutes must be one of 1,3,5,15")
        if self.initial_backfill_days < 3 or self.rolling_window_days < 3:
            raise ValueError("at least three days of causal context are required")
        if not (0.0 < self.risk_fraction < 0.10):
            raise ValueError("risk_fraction must be between 0 and 10%")
        if self.maximum_leverage <= 0.0:
            raise ValueError("maximum_leverage must be positive")
        if self.mode is RuntimeMode.TESTNET and not self.allow_testnet_orders:
            raise ValueError(
                "testnet mode is intentionally fail-closed; set allow_testnet_orders=true "
                "after verifying credentials and the paper run"
            )
        safe_metadata = json.loads(json.dumps(dict(self.metadata), ensure_ascii=False, default=str))
        object.__setattr__(self, "metadata", safe_metadata)

    @property
    def database_path(self) -> Path:
        return self.state_dir / "runtime.sqlite3"

    @property
    def evidence_dir(self) -> Path:
        return self.state_dir / "evidence"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ProductionConfig":
        values = dict(payload)
        values["mode"] = RuntimeMode(str(values.get("mode", "shadow")))
        if "symbols" in values:
            values["symbols"] = tuple(values["symbols"])
        for name in ("state_dir", "model_bundle"):
            if name in values:
                values[name] = Path(values[name])
        return cls(**values)

    @classmethod
    def load(cls, path: str | Path) -> "ProductionConfig":
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("configuration root must be a JSON object")
        return cls.from_mapping(payload)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["symbols"] = list(self.symbols)
        payload["state_dir"] = str(self.state_dir)
        payload["model_bundle"] = str(self.model_bundle)
        return payload
