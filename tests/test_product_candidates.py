from __future__ import annotations

import unittest

from auto_shipped.catalog.product_candidates import (
    rank_product_candidates,
    rank_similar_products,
)
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

    def test_unique_candidate_keeps_scores_and_semantic_evidence(self) -> None:
        candidate = ProductRecord(
            "J-579",
            "和匠日本进口搓澡巾手套去鸡皮去角质搓泥双面两用沐浴花洗澡巾",
            "J-579",
            "双面两用",
            100,
            "仓",
        )

        ranked = rank_product_candidates(
            [candidate],
            source_name="搓澡巾",
            source_spec="超硬深蓝色",
            normalizer=normalize_product_text,
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].product, candidate)
        self.assertEqual(ranked[0].confidence_tier, "blocking")
        self.assertIn("颜色:深蓝", ranked[0].missing_attributes)
        self.assertIn("软硬:超硬", ranked[0].missing_attributes)

    def test_only_semantically_complete_unique_candidate_is_preview_safe(self) -> None:
        blue = ProductRecord(
            "P-BLUE",
            "蓝色搓澡巾",
            "S-BLUE",
            "蓝色",
            100,
            "仓",
        )

        ranked = rank_product_candidates(
            [blue],
            source_name="蓝色搓背巾",
            source_spec="蓝色",
            normalizer=normalize_product_text,
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].confidence_tier, "preview_safe")
        self.assertGreaterEqual(ranked[0].combined_score, 0.84)
        self.assertEqual(ranked[0].missing_attributes, ())
        self.assertEqual(ranked[0].conflicting_attributes, ())

    def test_conflicting_source_and_target_attributes_are_blocking(self) -> None:
        orange = ProductRecord(
            "P-ORANGE",
            "搓澡巾",
            "S-ORANGE",
            "橘色",
            100,
            "仓",
        )

        ranked = rank_product_candidates(
            [orange],
            source_name="搓澡巾",
            source_spec="蓝色",
            normalizer=normalize_product_text,
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].confidence_tier, "blocking")
        self.assertIn("颜色:蓝!=橘", ranked[0].conflicting_attributes)


if __name__ == "__main__":
    unittest.main()
