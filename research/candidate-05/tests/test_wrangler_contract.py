from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from wrangler_contract import WritableWranglerFrame


class WritableWranglerFrameTest(unittest.TestCase):
    def test_dataframe_operations_preserve_writable_contiguous_values(self) -> None:
        source = WritableWranglerFrame(
            {
                "open": [1.0, 2.0],
                "high": [2.0, 3.0],
                "low": [0.5, 1.5],
                "close": [1.5, 2.5],
                "volume": [10.0, 20.0],
            },
            index=pd.date_range("2023-01-01", periods=2, freq="min", tz="UTC"),
        )
        transformed = source[["open", "high", "low", "close", "volume"]].astype(float)
        self.assertIsInstance(transformed, WritableWranglerFrame)
        values = transformed.values
        self.assertEqual(values.dtype, np.dtype("float64"))
        self.assertTrue(values.flags.writeable)
        self.assertTrue(values.flags.c_contiguous)
        self.assertTrue(values.flags.owndata)


if __name__ == "__main__":
    unittest.main()
