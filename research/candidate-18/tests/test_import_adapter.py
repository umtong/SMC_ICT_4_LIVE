from __future__ import annotations

import unittest


class ImportAdapterTests(unittest.TestCase):
    def test_adapter_exports_candidate18_types(self) -> None:
        from candidate18_strategy import Candidate18Config
        from candidate18_strategy import Candidate18Strategy

        self.assertEqual(Candidate18Config.__name__, "Candidate18Config")
        self.assertEqual(Candidate18Strategy.__name__, "Candidate18Strategy")


if __name__ == "__main__":
    unittest.main()
