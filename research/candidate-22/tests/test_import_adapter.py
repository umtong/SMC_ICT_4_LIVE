from __future__ import annotations

import unittest

from candidate22_strategy import Candidate22Config
from candidate22_strategy import Candidate22Strategy
from transmission_strategy import Candidate19Strategy


class ImportAdapterTests(unittest.TestCase):
    def test_adapter_exports_candidate22(self):
        self.assertTrue(issubclass(Candidate22Strategy, Candidate19Strategy))
        self.assertEqual(Candidate22Config.__name__, "Candidate22Config")


if __name__ == "__main__":
    unittest.main()
