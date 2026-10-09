from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import ProductCatalog, ProductRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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

    def test_confirmed_semantic_name_and_unit_match_unique_catalog_product(self) -> None:
        records = [
            ProductRecord("HN-RB1", "黄糯玉米（裸棒）", "HNLB40", "40棒/箱", 0, "仓"),
            ProductRecord("GMHN-1", "光明满元气 黄糯玉米 彩袋单棒装", "BC1", "50棒/箱", 0, "仓"),
            ProductRecord("GMCN-1", "光明满元气 花糯玉米 彩袋单棒装", "BC2", "50棒/箱", 0, "仓"),
        ]
        policies = {
            "manual_text": {
                "semantic_catalog_match": {
                    "strategy": "configured_name_tokens_and_source_spec",
                    "confirmed": True,
                    "name_rules": [
                        {
                            "source_name_contains_any": ["黄糯"],
                            "target_name_contains_all": ["黄糯", "玉米"],
                        },
                        {
                            "source_name_contains_any": ["花糯"],
                            "target_name_contains_all": ["花糯", "玉米"],
                        },
                    ],
                    "spec_rules": [
                        {
                            "source_spec_values": ["根"],
                            "target_name_contains_all": ["裸棒"],
                        },
                        {
                            "source_spec_values": ["袋"],
                            "target_name_contains_all": ["彩袋单棒装"],
                        },
                    ],
                }
            }
        }
        catalog = ProductCatalog(records, [], policies)
        root = catalog.resolve(
            "",
            source_profile_id="manual_text",
            source_product_name="黄糯",
            source_spec="根",
        )
        bag = catalog.resolve(
            "",
            source_profile_id="manual_text",
            source_product_name="花糯",
            source_spec="袋",
        )
        self.assertEqual(root.status, "confirmed")
        self.assertEqual(root.product.product_code, "HN-RB1")
        self.assertEqual(bag.status, "confirmed")
        self.assertEqual(bag.product.product_code, "GMCN-1")

    def test_self_operated_bag_resolves_to_eight_stick_family_pack(self) -> None:
        catalog = ProductCatalog.from_files(
            PROJECT_ROOT / "assets/catalog/current_product_catalog.csv",
            PROJECT_ROOT / "config/catalog/external_sku_mappings.json",
            policy_paths=(
                PROJECT_ROOT / "config/catalog/package_semantics_v1.json",
            ),
        )
        flower = catalog.resolve(
            "",
            source_profile_id="self_operated_text_v1",
            source_product_name="花糯",
            source_spec="袋",
        )
        yellow = catalog.resolve(
            "",
            source_profile_id="self_operated_text_v1",
            source_product_name="黄糯",
            source_spec="袋",
        )
        self.assertEqual(flower.status, "confirmed")
        self.assertEqual(flower.product.product_code, "JTTZE1")
        self.assertEqual(flower.product.spec_code, "6974768564835")
        self.assertEqual(yellow.status, "confirmed")
        self.assertEqual(yellow.product.product_code, "JTW8E1")
        self.assertEqual(yellow.product.spec_code, "6974768564811")

    def test_rongzhida_root_resolves_to_packaged_single_stick(self) -> None:
        catalog = ProductCatalog.from_files(
            PROJECT_ROOT / "assets/catalog/current_product_catalog.csv",
            PROJECT_ROOT / "config/catalog/external_sku_mappings.json",
            policy_paths=(
                PROJECT_ROOT / "config/catalog/package_semantics_v1.json",
            ),
        )
        yellow = catalog.resolve(
            "",
            source_profile_id="rongzhida_text_v1",
            source_product_name="黄糯",
            source_spec="根",
        )
        self.assertEqual(yellow.status, "confirmed")
        self.assertEqual(yellow.product.product_code, "GMHN-1")
        self.assertEqual(yellow.product.spec_code, "6974768564729")

    def test_unregistered_source_unit_requires_package_semantics_confirmation(self) -> None:
        catalog = ProductCatalog.from_files(
            PROJECT_ROOT / "assets/catalog/current_product_catalog.csv",
            PROJECT_ROOT / "config/catalog/external_sku_mappings.json",
            policy_paths=(
                PROJECT_ROOT / "config/catalog/package_semantics_v1.json",
            ),
        )
        result = catalog.resolve(
            "",
            source_profile_id="self_operated_text_v1",
            source_product_name="黄糯",
            source_spec="根",
        )
        self.assertEqual(result.status, "unconfirmed")
        self.assertEqual(result.match_method, "package_semantics_unregistered")
        self.assertIsNone(result.product)

        pack_expression = catalog.resolve(
            "",
            source_profile_id="self_operated_text_v1",
            source_product_name="黄糯",
            source_spec="8根装",
        )
        self.assertEqual(pack_expression.status, "unconfirmed")
        self.assertEqual(
            pack_expression.match_method,
            "package_semantics_unregistered",
        )

    def test_bare_stick_expression_is_forbidden_for_shipping(self) -> None:
        catalog = ProductCatalog.from_files(
            PROJECT_ROOT / "assets/catalog/current_product_catalog.csv",
            PROJECT_ROOT / "config/catalog/external_sku_mappings.json",
            policy_paths=(
                PROJECT_ROOT / "config/catalog/package_semantics_v1.json",
            ),
        )
        result = catalog.resolve(
            "",
            source_profile_id="rongzhida_text_v1",
            source_product_name="黄糯",
            source_spec="裸棒",
        )
        self.assertEqual(result.status, "unconfirmed")
        self.assertEqual(result.match_method, "shipping_expression_forbidden")


if __name__ == "__main__":
    unittest.main()
