from __future__ import annotations

from types import SimpleNamespace

from domain import Candle, Side
from features_ml3v3 import FEATURE_NAMES, ML3V3FeatureBook


def test_completed_hour_context_is_causal_and_symbol_agnostic() -> None:
    assert not any("symbol" in name.lower() for name in FEATURE_NAMES)
    book = ML3V3FeatureBook()
    price = 100.0
    for hour in range(24):
        close = price * 1.001
        candle = Candle(
            ts_close_ns=(hour + 1) * 60 * 60 * 1_000_000_000,
            open=price,
            high=max(price, close) * 1.0005,
            low=min(price, close) * 0.9995,
            close=close,
            volume=1000.0 + hour,
        )
        book.observe_bucket([("BTCUSDT", 60, candle)])
        price = close
    plan = SimpleNamespace(symbol="BTCUSDT", side=Side.LONG)
    features = book.context_features(plan)
    assert features["ctx4h_available"] == 1.0
    assert features["ctx24h_available"] == 1.0
    assert features["ctx4h_side_return_z"] > 0.0
    assert features["ctx24h_side_return_z"] > 0.0
    assert features["ctx4h_24h_alignment"] == 1.0
