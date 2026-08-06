"""Public description entry point for the active independent candidate."""


def describe() -> str:
    return (
        "NT-LVCFR-v1: detect a two-stage open-interest contraction with spot-confirmed "
        "price continuation and weak residual futures aggression, then let a native "
        "NautilusTrader Strategy trade either the liquidity-vacuum continuation or one "
        "rapid failure reversal. ParquetDataCatalog, BacktestNode, native GTC market "
        "orders, fills, fees, discrete funding, margin, liquidation, positions, and NAV "
        "accounting are mandatory for every weekly and long evaluation. Execution windows "
        "retain original-time first/last and bid/ask extrema per observed second; "
        "BacktestNode uses its one-shot mixed catalog loader, Portfolio equity is selected "
        "by settlement currency, and position-close accounting follows the NautilusTrader "
        "1.230 event contract."
    )
