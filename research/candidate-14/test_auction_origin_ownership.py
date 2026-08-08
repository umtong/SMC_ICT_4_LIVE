from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch, sentinel

import auction_origin_ownership as ownership


class AuctionOriginOwnershipTest(unittest.TestCase):
    def auction(self, *, rejection: bool, acceptance: bool) -> SimpleNamespace:
        return SimpleNamespace(
            rejection_seed=rejection,
            acceptance_seed=acceptance,
        )

    def test_only_exclusive_rejection_owns_ordinary_far(self) -> None:
        self.assertTrue(
            ownership.far_origin_is_exclusive_rejection(
                self.auction(rejection=True, acceptance=False),
            ),
        )
        for rejection, acceptance in ((False, False), (False, True), (True, True)):
            with self.subTest(rejection=rejection, acceptance=acceptance):
                self.assertFalse(
                    ownership.far_origin_is_exclusive_rejection(
                        self.auction(rejection=rejection, acceptance=acceptance),
                    ),
                )

    def test_exclusive_rejection_delegates_without_changing_base_logic(self) -> None:
        auction = self.auction(rejection=True, acceptance=False)
        engine = object()
        bar = object()
        with patch.object(
            ownership,
            "BASE_CONFIRM_FAR",
            return_value=sentinel.plan,
        ) as base:
            result = ownership.confirm_far_from_owned_origin(engine, auction, bar)
        self.assertIs(result, sentinel.plan)
        base.assert_called_once_with(engine, auction, bar)

    def test_mixed_origin_does_not_execute_or_mutate_base_far(self) -> None:
        auction = self.auction(rejection=True, acceptance=True)
        with patch.object(ownership, "BASE_CONFIRM_FAR") as base:
            result = ownership.confirm_far_from_owned_origin(
                object(),
                auction,
                object(),
            )
        self.assertIsNone(result)
        base.assert_not_called()


if __name__ == "__main__":
    unittest.main()
