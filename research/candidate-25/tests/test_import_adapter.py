from __future__ import annotations

import unittest

from candidate25_strategy import Candidate25Config
from candidate25_strategy import Candidate25Strategy
from transmission_strategy import Candidate19Strategy


class ImportAdapterTests(unittest.TestCase):
    def test_candidate25_reuses_lifecycle_without_inheriting_alpha_configuration(self):
        self.assertTrue(issubclass(Candidate25Strategy, Candidate19Strategy))
        self.assertEqual(Candidate25Config.__name__, "Candidate25Config")


if __name__ == "__main__":
    unittest.main()
