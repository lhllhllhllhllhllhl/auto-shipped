from __future__ import annotations

import unittest
from pathlib import Path

from auto_shipped.catalog import ProductPhraseNormalizer


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProductPhraseNormalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.normalizer = ProductPhraseNormalizer.from_file(
            PROJECT_ROOT / "config/catalog/product_phrase_lexicon_v1.json"
        )

    def test_different_agent_splits_have_the_same_identity(self) -> None:
        variants = [
            self.normalizer.normalize(
                source_product_name="搓澡巾",
                source_spec="kikulon超软黄色",
                unit="条",
            ),
            self.normalizer.normalize(
                source_product_name="kikulon搓澡巾",
                source_spec="超软黄色",
                unit="条",
            ),
            self.normalizer.normalize(
                source_item_text="kikulon超软黄色搓澡巾1条",
                source_product_name="kikulon搓澡巾",
                source_spec="超软黄色",
                unit="条",
            ),
        ]

        self.assertEqual(len({item.signature for item in variants}), 1)
        self.assertEqual(variants[0].brand, "kikulon")
        self.assertEqual(variants[0].product, "搓澡巾")
        self.assertEqual(variants[0].spec, "黄色|超软")

    def test_quantity_and_unit_are_removed_from_identity_only(self) -> None:
        identity = self.normalizer.normalize(
            source_item_text="蓝色搓澡巾2条",
            source_product_name="搓澡巾",
            source_spec="蓝色",
            unit="条",
        )

        self.assertEqual(identity.product, "搓澡巾")
        self.assertEqual(identity.spec, "蓝色")
        self.assertNotIn("2", identity.signature)
        self.assertNotIn("条", identity.signature)

    def test_unknown_spec_is_preserved_in_residual(self) -> None:
        identity = self.normalizer.normalize(
            source_product_name="kikulon搓澡巾",
            source_spec="超软黄色加长版",
        )

        self.assertEqual(identity.product, "搓澡巾")
        self.assertEqual(identity.residual, "加长版")
        self.assertEqual(identity.spec, "黄色|超软|加长版")

    def test_canonical_identity_is_idempotent_for_multi_part_specs(self) -> None:
        originals = [
            self.normalizer.normalize(
                source_product_name="搓澡巾",
                source_spec="kikulon超软黄色",
            ),
            self.normalizer.normalize(
                source_product_name="玉米",
                source_spec="白糯彩袋单棒装",
            ),
            self.normalizer.normalize(
                source_product_name="kikulon搓澡巾",
                source_spec="超软黄色加长版",
            ),
        ]

        for original in originals:
            with self.subTest(signature=original.signature):
                repeated = self.normalizer.normalize(
                    source_brand=original.brand,
                    source_product_name=original.product,
                    source_spec=original.spec,
                )
                self.assertEqual(repeated.signature, original.signature)
                self.assertNotIn("|", repeated.residual)


if __name__ == "__main__":
    unittest.main()
