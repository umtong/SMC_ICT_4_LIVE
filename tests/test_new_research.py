from __future__ import annotations

import unittest

from scripts.new_research import slugify


class SlugTests(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(slugify("Candidate A"), "candidate-a")

    def test_non_ascii_only_is_rejected(self):
        with self.assertRaises(ValueError):
            slugify("연구")


if __name__ == "__main__":
    unittest.main()
