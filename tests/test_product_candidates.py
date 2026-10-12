from __future__ import annotations

import unittest

from auto_shipped.catalog.product_candidates import rank_similar_products
from auto_shipped.catalog.product_catalog import (
    ProductRecord,
    normalize_product_text,
)


class ProductCandidateTests(unittest.TestCase):
    def test_candidates_are_ranked_but_never_resolved(self) -> None:
        blue = ProductRecord("P-BLUE", "蓝色搓澡巾", "S-BLUE", "蓝色", 100, "仓")
        orange = ProductRecord("P-ORANGE", "橘色搓澡巾", "S-ORANGE", "橘色", 100, "仓")

        candidates = rank_similar_products(
            [orange, blue],
            source_name="蓝色搓背巾",
            source_spec="蓝色",
            normalizer=normalize_product_text,
        )

        self.assertTrue(candidates)
        self.assertEqual(candidates[0], blue)

    def test_unrelated_name_returns_no_candidate(self) -> None:
        candidates = rank_similar_products(
            [ProductRecord("P1", "玉米礼盒", "S1", "十根装", 100, "仓")],
            source_name="蓝色搓澡巾",
            source_spec="蓝色",
            normalizer=normalize_product_text,
        )

        self.assertEqual(candidates, ())


if __name__ == "__main__":
    unittest.main()
