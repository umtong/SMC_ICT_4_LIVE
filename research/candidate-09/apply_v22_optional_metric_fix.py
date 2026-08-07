#!/usr/bin/env python3
"""Allow sparse Binance metrics rows without inventing taker flow.

Some official metrics rows retain open interest while optional ratio columns are blank.
This is an ingestion-contract fix only: OI remains available, missing taker flow remains
None, and the unchanged v22 pulse logic rejects any event that requires absent flow.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"expected source contract not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    ROOT / "data_loader_v22.py",
    "    taker_ratio: float\n",
    "    taker_ratio: float | None\n",
)
replace_once(
    ROOT / "data_loader_v22.py",
    '            taker_ratio = float(row["sum_taker_long_short_vol_ratio"])\n',
    '            taker_ratio = _optional_float(row, "sum_taker_long_short_vol_ratio")\n',
)
replace_once(
    ROOT / "state_engine_v22_direct.py",
    '            if self.open_interest is None or self.metric_taker_ratio is None:\n'
    '                raise ValueError("metric observation requires open interest and taker ratio")\n',
    '            if self.open_interest is None:\n'
    '                raise ValueError("metric observation requires open interest")\n',
)
replace_once(
    ROOT / "state_engine_v22_direct.py",
    '            if self.open_interest <= 0.0 or self.metric_taker_ratio <= 0.0:\n'
    '                raise ValueError("open interest and metric taker ratio must be positive")\n',
    '            if self.open_interest <= 0.0:\n'
    '                raise ValueError("open interest must be positive")\n'
    '            if self.metric_taker_ratio is not None and self.metric_taker_ratio <= 0.0:\n'
    '                raise ValueError("metric taker ratio must be positive when present")\n',
)

test_path = ROOT / "tests_v22/test_data_loader_v22.py"
test_text = test_path.read_text(encoding="utf-8")
test_method = '''
    def test_blank_optional_metric_ratio_preserves_open_interest_without_inventing_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.zip"
            csv_text = (
                "create_time,symbol,sum_open_interest,sum_open_interest_value,"
                "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
                "count_long_short_ratio,sum_taker_long_short_vol_ratio\\n"
                "300,BTCUSDT,100000,100000000,1.1,1.2,1.0,\\n"
            )
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("BTCUSDT-metrics.csv", csv_text)
            metrics = data_loader.parse_metric_archive(path, expected_symbol="BTCUSDT")
            self.assertEqual(metrics[0].open_interest, 100000.0)
            self.assertIsNone(metrics[0].taker_ratio)
            bar = FlowBar(
                ts_ns=6 * MINUTE_NS,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=10.0,
                taker_buy_volume=5.0,
                trade_count=10,
            )
            enriched = data_loader.enrich_bars([bar], metrics)[0]
            self.assertEqual(enriched.open_interest, 100000.0)
            self.assertIsNone(enriched.metric_taker_ratio)
            self.assertIsNone(enriched.metric_flow_imbalance)
'''
if "test_blank_optional_metric_ratio_preserves_open_interest_without_inventing_flow" not in test_text:
    marker = "\n    def test_forward_fill_never_advances_before_metric_availability(self):\n"
    if marker not in test_text:
        raise RuntimeError("test insertion contract not found")
    test_path.write_text(test_text.replace(marker, test_method + marker, 1), encoding="utf-8")
