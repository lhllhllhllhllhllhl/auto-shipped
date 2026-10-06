from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import ProductCatalog, ProductRecord


class ProductCatalogTests(unittest.TestCase):
    def test_unconfirmed_external_mapping_resolves_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.csv"
            catalog.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,测试商品,S1,测试规格,120,测试仓\n",
                encoding="gb18030",
            )
            mappings = root / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": {
                            "EXT-1": {
                                "product_code": "P1",
                                "spec_code": "S1",
                                "confirmed": False,
                                "reason": "待确认",
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = ProductCatalog.from_files(catalog, mappings).resolve("EXT-1")
            self.assertEqual(result.status, "unconfirmed")
            self.assertEqual(result.product.product_code, "P1")
            self.assertFalse(result.confirmed)

    def test_unique_spec_code_does_not_cross_namespaces_without_source_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.csv"
            catalog.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,测试商品,S1,测试规格,120,测试仓\n",
                encoding="gb18030",
            )
            mappings = root / "mappings.json"
            mappings.write_text('{"mappings": {}}', encoding="utf-8")
            result = ProductCatalog.from_files(catalog, mappings).resolve("S1")
            self.assertEqual(result.status, "unmapped")

    def test_source_policy_can_explicitly_allow_direct_catalog_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.csv"
            catalog.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,测试商品,S1,测试规格,120,测试仓\n",
                encoding="gb18030",
            )
            mappings = root / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "source_policies": {
                            "same_namespace": {
                                "allow_direct_catalog_identifier": True
                            }
                        },
                        "mappings": [],
                    }
                ),
                encoding="utf-8",
            )
            result = ProductCatalog.from_files(catalog, mappings).resolve(
                "S1",
                source_profile_id="same_namespace",
            )
            self.assertEqual(result.status, "confirmed")
            self.assertEqual(result.product.product_code, "P1")

    def test_mapping_is_scoped_by_source_company(self) -> None:
        records = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.csv"
            catalog.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,测试商品,S1,测试规格,120,测试仓\n",
                encoding="gb18030",
            )
            mappings = root / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "source_profile_id": "company_a",
                                "identifier_type": "product_code",
                                "source_value": "EXT-1",
                                "product_code": "P1",
                                "spec_code": "S1",
                                "confirmed": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            product_catalog = ProductCatalog.from_files(catalog, mappings)
            matched = product_catalog.resolve("EXT-1", source_profile_id="company_a")
            not_matched = product_catalog.resolve("EXT-1", source_profile_id="company_b")
            self.assertEqual(matched.status, "confirmed")
            self.assertEqual(not_matched.status, "unmapped")

    def test_new_source_code_can_fall_back_to_unique_exact_product_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.csv"
            catalog.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,光明满元气 黄糯玉米8棒家庭装,S1,8袋/箱,120,测试仓\n",
                encoding="gb18030",
            )
            mappings = root / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "source_policies": {
                            "company_a": {"allow_exact_catalog_name": True}
                        },
                        "mappings": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = ProductCatalog.from_files(catalog, mappings).resolve(
                "NEW-CODE",
                source_profile_id="company_a",
                source_product_name="光明满元气   黄糯玉米8棒家庭装",
            )
            self.assertEqual(result.status, "confirmed")
            self.assertEqual(result.match_method, "exact_catalog_name")
            self.assertEqual(result.product.spec_code, "S1")

    def test_source_code_can_be_scoped_by_source_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.csv"
            catalog.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,测试商品红色,S1,红色,120,测试仓\n"
                "P2,测试商品蓝色,S2,蓝色,120,测试仓\n",
                encoding="gb18030",
            )
            mappings = root / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "source_policies": {
                            "sam_order_v1": {
                                "source_identifier_type": "product_code"
                            }
                        },
                        "mappings": [
                            {
                                "source_profile_id": "sam_order_v1",
                                "identifier_type": "product_code",
                                "source_value": "EXT-1",
                                "source_spec": "RED",
                                "product_code": "P1",
                                "spec_code": "S1",
                                "confirmed": True,
                            },
                            {
                                "source_profile_id": "sam_order_v1",
                                "identifier_type": "product_code",
                                "source_value": "EXT-1",
                                "source_spec": "BLUE",
                                "product_code": "P2",
                                "spec_code": "S2",
                                "confirmed": True,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            product_catalog = ProductCatalog.from_files(catalog, mappings)
            red = product_catalog.resolve(
                "EXT-1",
                source_profile_id="sam_order_v1",
                source_spec="RED",
            )
            blue = product_catalog.resolve(
                "EXT-1",
                source_profile_id="sam_order_v1",
                source_spec="BLUE",
            )
            self.assertEqual(red.product.product_code, "P1")
            self.assertEqual(blue.product.product_code, "P2")

    def test_blank_spec_mapping_requires_registered_source_name_guard(self) -> None:
        records = [
            {
                "source_profile_id": "company_a",
                "identifier_type": "product_code",
                "source_value": "EXT-1",
                "product_code": "P1",
                "spec_code": "S1",
                "confirmed": True,
            }
        ]
        policies = {
            "company_a": {
                "source_identifier_type": "product_code",
                "require_identity_guard_for_blank_source_spec": True,
                "mapping_identity_guards": [
                    {
                        "identifier_type": "product_code",
                        "source_value": "EXT-1",
                        "allowed_product_names": ["已确认商品"],
                    }
                ],
            }
        }
        product_catalog = ProductCatalog(
            [
                ProductRecord(
                    "P1",
                    "管易商品",
                    "S1",
                    "管易规格",
                    100,
                    "测试仓",
                )
            ],
            records,
            policies,
        )
        matched = product_catalog.resolve(
            "EXT-1",
            source_profile_id="company_a",
            source_product_name="已确认商品",
        )
        changed = product_catalog.resolve(
            "EXT-1",
            source_profile_id="company_a",
            source_product_name="完全不同的新商品",
        )
        self.assertEqual(matched.status, "confirmed")
        self.assertEqual(changed.status, "unconfirmed")
        self.assertEqual(changed.match_method, "source_mapping_identity_guard")

    def test_blank_spec_mapping_without_required_guard_is_unconfirmed(self) -> None:
        product_catalog = ProductCatalog(
            [ProductRecord("P1", "管易商品", "S1", "管易规格", 100, "测试仓")],
            [
                {
                    "source_profile_id": "company_a",
                    "identifier_type": "product_code",
                    "source_value": "EXT-1",
                    "product_code": "P1",
                    "spec_code": "S1",
                    "confirmed": True,
                }
            ],
            {
                "company_a": {
                    "source_identifier_type": "product_code",
                    "require_identity_guard_for_blank_source_spec": True,
                }
            },
        )
        result = product_catalog.resolve(
            "EXT-1",
            source_profile_id="company_a",
            source_product_name="任意商品",
        )
        self.assertEqual(result.status, "unconfirmed")
        self.assertIn("缺少已登记", result.reason)


if __name__ == "__main__":
    unittest.main()
