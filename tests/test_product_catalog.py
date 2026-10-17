from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import (
    ProductCatalog,
    ProductPhraseNormalizer,
    ProductRecord,
)
from auto_shipped.catalog.catalog_layers import CatalogLayer, build_effective_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProductCatalogTests(unittest.TestCase):
    def test_global_exact_name_alias_is_reused_across_sources(self) -> None:
        product = ProductRecord(
            product_code="KKAW0030",
            product_name="标准名称",
            spec_code="4548404200030",
            spec_name="蓝色硬款",
            weight_grams=None,
            default_warehouse="测试仓",
        )
        catalog = ProductCatalog(
            [product],
            [
                {
                    "company_id": "*",
                    "source_profile_id": "*",
                    "identifier_type": "product_name",
                    "source_value": "蓝色搓澡巾",
                    "source_spec": "硬款",
                    "product_code": "KKAW0030",
                    "spec_code": "4548404200030",
                    "mapping_authority": "feishu_official",
                    "reuse_scope": "future",
                    "confirmed": True,
                }
            ],
            {"kqyd": {"source_identifier_type": "product_name"}},
        )

        result = catalog.resolve(
            "",
            source_profile_id="kqyd",
            source_product_name="蓝色搓澡巾",
            source_spec="硬款",
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.product, product)

    def test_source_specific_alias_overrides_global_alias(self) -> None:
        global_product = ProductRecord(
            product_code="GLOBAL",
            product_name="全局商品",
            spec_code="G1",
            spec_name="蓝色",
            weight_grams=None,
            default_warehouse="测试仓",
        )
        source_product = ProductRecord(
            product_code="SOURCE",
            product_name="来源例外商品",
            spec_code="S1",
            spec_name="蓝色",
            weight_grams=None,
            default_warehouse="测试仓",
        )
        common = {
            "identifier_type": "product_name",
            "source_value": "来源别名",
            "source_spec": "蓝色",
            "mapping_authority": "feishu_official",
            "reuse_scope": "future",
            "confirmed": True,
        }
        catalog = ProductCatalog(
            [global_product, source_product],
            [
                {
                    **common,
                    "company_id": "*",
                    "source_profile_id": "*",
                    "product_code": "GLOBAL",
                    "spec_code": "G1",
                },
                {
                    **common,
                    "company_id": "company-a",
                    "source_profile_id": "profile-a",
                    "product_code": "SOURCE",
                    "spec_code": "S1",
                },
            ],
            {"profile-a": {"source_identifier_type": "product_name"}},
        )

        result = catalog.resolve(
            "",
            source_profile_id="profile-a",
            source_product_name="来源别名",
            source_spec="蓝色",
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.product, source_product)

    def test_cross_source_exact_alias_is_candidate_until_promoted(self) -> None:
        product = ProductRecord(
            product_code="KKAW0030",
            product_name="标准名称",
            spec_code="4548404200030",
            spec_name="蓝色硬款",
            weight_grams=None,
            default_warehouse="测试仓",
        )
        catalog = ProductCatalog(
            [product],
            [
                {
                    "company_id": "self_operated",
                    "source_profile_id": "taobao",
                    "identifier_type": "product_name",
                    "source_value": "蓝色搓澡巾",
                    "source_spec": "硬款",
                    "product_code": "KKAW0030",
                    "spec_code": "4548404200030",
                    "mapping_authority": "feishu_official",
                    "reuse_scope": "future",
                    "confirmed": True,
                }
            ],
            {"kqyd": {"source_identifier_type": "product_name"}},
        )

        result = catalog.resolve(
            "",
            source_profile_id="kqyd",
            source_product_name="蓝色搓澡巾",
            source_spec="硬款",
        )

        self.assertEqual(result.status, "unconfirmed")
        self.assertEqual(result.match_method, "cross_source_exact_alias_candidate")
        self.assertEqual(result.product, product)

    def test_cross_source_exact_alias_accepts_only_exact_split_or_joined_phrase(self) -> None:
        product = ProductRecord(
            product_code="KKAW0030",
            product_name="标准名称",
            spec_code="4548404200030",
            spec_name="蓝色硬款",
            weight_grams=None,
            default_warehouse="测试仓",
        )
        catalog = ProductCatalog(
            [product],
            [
                {
                    "company_id": "self_operated",
                    "source_profile_id": "taobao",
                    "identifier_type": "product_name",
                    "source_value": "搓澡巾",
                    "source_spec": "蓝色硬款",
                    "product_code": "KKAW0030",
                    "spec_code": "4548404200030",
                    "mapping_authority": "feishu_official",
                    "reuse_scope": "future",
                    "confirmed": True,
                }
            ],
            {"kqyd": {"source_identifier_type": "product_name"}},
        )

        result = catalog.resolve(
            "",
            source_profile_id="kqyd",
            source_product_name="蓝色硬款搓澡巾",
            source_spec="",
        )

        self.assertEqual(result.match_method, "cross_source_exact_alias_candidate")
        self.assertEqual(result.product, product)

    def test_current_source_name_mapping_ignores_agent_field_boundary(self) -> None:
        product = ProductRecord(
            product_code="KKAW0290",
            product_name="Yellow超软",
            spec_code="4548404200290",
            spec_name="超软",
            weight_grams=None,
            default_warehouse="测试仓",
        )
        normalizer = ProductPhraseNormalizer.from_file(
            PROJECT_ROOT / "config/catalog/product_phrase_lexicon_v1.json"
        )
        catalog = ProductCatalog(
            [product],
            [
                {
                    "mapping_id": "mapping-yellow-soft",
                    "source_profile_id": "taojuzi_kqyd_text_v1",
                    "identifier_type": "product_name",
                    "source_value": "搓澡巾",
                    "source_spec": "kikulon超软黄色",
                    "product_code": "KKAW0290",
                    "spec_code": "4548404200290",
                    "mapping_authority": "feishu_official",
                    "reuse_scope": "future",
                    "confirmed": True,
                }
            ],
            {"taojuzi_kqyd_text_v1": {"source_identifier_type": "product_name"}},
            phrase_normalizer=normalizer,
            mapping_revision="23",
            mapping_snapshot_sha256="sha256:test",
        )

        result = catalog.resolve(
            "",
            source_profile_id="taojuzi_kqyd_text_v1",
            source_item_text="kikulon超软黄色搓澡巾1条",
            source_product_name="kikulon搓澡巾",
            source_spec="超软黄色",
            unit="条",
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.match_method, "confirmed_name_alias_canonical_identity")
        self.assertEqual(result.product, product)
        self.assertEqual(result.mapping_ids, ("mapping-yellow-soft",))
        self.assertEqual(result.canonical_identity.brand, "kikulon")
        self.assertEqual(result.canonical_identity.product, "搓澡巾")
        self.assertEqual(result.canonical_identity.spec, "黄色|超软")

    def test_unknown_extra_spec_does_not_reuse_canonical_name_mapping(self) -> None:
        product = ProductRecord(
            product_code="KKAW0290",
            product_name="Yellow超软",
            spec_code="4548404200290",
            spec_name="超软",
            weight_grams=None,
            default_warehouse="测试仓",
        )
        normalizer = ProductPhraseNormalizer.from_file(
            PROJECT_ROOT / "config/catalog/product_phrase_lexicon_v1.json"
        )
        catalog = ProductCatalog(
            [product],
            [
                {
                    "source_profile_id": "source-a",
                    "identifier_type": "product_name",
                    "source_value": "搓澡巾",
                    "source_spec": "kikulon超软黄色",
                    "product_code": "KKAW0290",
                    "spec_code": "4548404200290",
                    "mapping_authority": "feishu_official",
                    "reuse_scope": "future",
                    "confirmed": True,
                }
            ],
            {"source-a": {"source_identifier_type": "product_name"}},
            phrase_normalizer=normalizer,
        )

        result = catalog.resolve(
            "",
            source_profile_id="source-a",
            source_item_text="kikulon超软黄色加长版搓澡巾1条",
            source_product_name="kikulon搓澡巾",
            source_spec="超软黄色加长版",
            unit="条",
        )

        self.assertNotEqual(result.status, "confirmed")
        self.assertNotEqual(result.match_method, "confirmed_name_alias_canonical_identity")
    def test_preferred_catalog_overrides_and_enriches_guanyi_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guanyi = root / "guanyi.csv"
            guanyi.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,管易名称,S1,管易规格,120,上海尚舆商贸有限公司\n",
                encoding="gb18030",
            )
            preferred = root / "preferred.csv"
            preferred.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库,目录状态\n"
                "P1,业务名称,S1,业务规格,,,active\n",
                encoding="utf-8",
            )
            mappings = root / "mappings.json"
            mappings.write_text('{"mappings": []}', encoding="utf-8")

            catalog = ProductCatalog.from_files(
                guanyi,
                mappings,
                preferred_catalog_paths=(preferred,),
            )
            result = catalog.resolve_unique_target("P1", "S1")

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.product_name, "业务名称")
            self.assertEqual(result.spec_name, "业务规格")
            self.assertEqual(result.weight_grams, 120)
            self.assertEqual(result.default_warehouse, "上海尚舆商贸有限公司")
            self.assertEqual(result.catalog_source_id, "business_preferred_0")
            self.assertEqual(
                result.catalog_source_ids,
                ("business_preferred_0", "guanyi_master"),
            )

    def test_catalog_falls_back_to_guanyi_when_preferred_has_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guanyi = root / "guanyi.csv"
            guanyi.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,管易商品,S1,管易规格,120,测试仓\n",
                encoding="gb18030",
            )
            preferred = root / "preferred.csv"
            preferred.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库,目录状态\n"
                "P2,优先商品,S2,优先规格,,,active\n",
                encoding="utf-8",
            )
            mappings = root / "mappings.json"
            mappings.write_text('{"mappings": []}', encoding="utf-8")

            catalog = ProductCatalog.from_files(
                guanyi,
                mappings,
                preferred_catalog_paths=(preferred,),
            )
            result = catalog.resolve_unique_target("P1", "S1")

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.catalog_source_id, "guanyi_master")

    def test_pending_preferred_rows_do_not_enter_effective_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guanyi = root / "guanyi.csv"
            guanyi.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,管易商品,S1,管易规格,120,测试仓\n",
                encoding="gb18030",
            )
            preferred = root / "preferred.csv"
            preferred.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库,目录状态\n"
                ",待补全商品,S2,待补全规格,,,pending_missing_product_code\n",
                encoding="utf-8",
            )
            mappings = root / "mappings.json"
            mappings.write_text('{"mappings": []}', encoding="utf-8")

            catalog = ProductCatalog.from_files(
                guanyi,
                mappings,
                preferred_catalog_paths=(preferred,),
            )

            self.assertEqual(len(catalog.records), 1)
            self.assertEqual(catalog.records[0].product_code, "P1")

    def test_active_preferred_row_without_product_code_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guanyi = root / "guanyi.csv"
            guanyi.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,管易商品,S1,管易规格,120,测试仓\n",
                encoding="gb18030",
            )
            preferred = root / "preferred.csv"
            preferred.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库,目录状态\n"
                ",错误商品,S2,错误规格,,,active\n",
                encoding="utf-8",
            )
            mappings = root / "mappings.json"
            mappings.write_text('{"mappings": []}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "生效商品缺少商品代码"):
                ProductCatalog.from_files(
                    guanyi,
                    mappings,
                    preferred_catalog_paths=(preferred,),
                )

    def test_real_preferred_catalog_supplies_ten_stick_gift_box(self) -> None:
        catalog = ProductCatalog.from_files(
            PROJECT_ROOT / "assets/catalog/current_product_catalog.csv",
            PROJECT_ROOT / "config/catalog/external_sku_mappings.json",
            preferred_catalog_paths=(
                PROJECT_ROOT / "assets/catalog/preferred_product_catalog.csv",
            ),
        )

        product = catalog.resolve_unique_target("6974768564859", "")

        self.assertIsNotNone(product)
        assert product is not None
        self.assertEqual(product.product_name, "黄糯玉米10根礼盒装")
        self.assertEqual(product.spec_code, "")
        self.assertEqual(product.catalog_source_id, "business_preferred_0")

    def test_real_preferred_catalog_matches_current_replacement_snapshot(self) -> None:
        build = build_effective_catalog(
            [
                CatalogLayer(
                    source_id="business_preferred",
                    path=PROJECT_ROOT / "assets/catalog/preferred_product_catalog.csv",
                    priority=1000,
                )
            ]
        )

        self.assertEqual(build.layers[0].active_rows, 10)
        self.assertEqual(build.layers[0].pending_rows, 2)
        self.assertEqual(
            {(row.product_code, row.spec_code) for row in build.rows},
            {
                ("GMBN1", "6974768564804"),
                ("GMCN-1", "6974768564798"),
                ("GMHN-1", "6974768564729"),
                ("JTW8E1", "6974768564811"),
                ("JTTZE1", "6974768564835"),
                ("JTBN1E1-EH", "6974768564842"),
                ("MYQLH10", "MYQLH10"),
                ("6974768564859", ""),
                ("6974768564873", ""),
                ("6974768564880", ""),
            },
        )

    def test_formal_mapping_can_target_preferred_only_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mappings = Path(tmp) / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "source_profile_id": "manual_text",
                                "identifier_type": "product_name",
                                "source_value": "黄糯10根礼盒",
                                "source_spec": "",
                                "product_code": "6974768564859",
                                "spec_code": "",
                                "confirmed": True,
                                "mapping_authority": "feishu_official",
                                "reuse_scope": "future",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            catalog = ProductCatalog.from_files(
                PROJECT_ROOT / "assets/catalog/current_product_catalog.csv",
                mappings,
                preferred_catalog_paths=(
                    PROJECT_ROOT / "assets/catalog/preferred_product_catalog.csv",
                ),
            )

            result = catalog.resolve(
                "",
                source_profile_id="manual_text",
                source_product_name="黄糯10根礼盒",
            )

            self.assertEqual(result.status, "confirmed")
            self.assertIsNotNone(result.product)
            assert result.product is not None
            self.assertEqual(result.product.product_code, "6974768564859")
            self.assertEqual(
                result.product.catalog_source_id,
                "business_preferred_0",
            )

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

    def test_supplied_source_spec_never_falls_back_to_code_only_mapping(self) -> None:
        product_catalog = ProductCatalog(
            [ProductRecord("P1", "默认商品", "S1", "默认规格", 100, "测试仓")],
            [
                {
                    "source_profile_id": "sam_order_v1",
                    "identifier_type": "product_code",
                    "source_value": "84292384",
                    "source_spec": "",
                    "product_code": "P1",
                    "spec_code": "S1",
                    "confirmed": True,
                }
            ],
            {"sam_order_v1": {"source_identifier_type": "product_code"}},
        )

        code_only = product_catalog.resolve(
            "84292384",
            source_profile_id="sam_order_v1",
            source_spec="",
        )
        variant = product_catalog.resolve(
            "84292384",
            source_profile_id="sam_order_v1",
            source_spec="SWSH8096",
        )

        self.assertEqual(code_only.status, "confirmed")
        self.assertEqual(
            code_only.match_method,
            "confirmed_source_mapping_code_only",
        )
        self.assertEqual(variant.status, "unmapped")
        self.assertIsNone(variant.product)

    def test_spec_only_mapping_uses_the_same_universal_row_shape(self) -> None:
        product_catalog = ProductCatalog(
            [
                ProductRecord("P-GREEN", "绿色手套", "GTIN-GREEN", "M", 100, "测试仓"),
                ProductRecord("P-ORANGE", "橘色手套", "GTIN-ORANGE", "L", 100, "测试仓"),
            ],
            [
                {
                    "source_profile_id": "sam_order_v1",
                    "identifier_type": "product_code",
                    "source_value": "",
                    "source_spec": "SWSH8096",
                    "product_code": "P-GREEN",
                    "spec_code": "GTIN-GREEN",
                    "confirmed": True,
                },
                {
                    "source_profile_id": "sam_order_v1",
                    "identifier_type": "product_code",
                    "source_value": "",
                    "source_spec": "SWJC8140",
                    "product_code": "P-ORANGE",
                    "spec_code": "GTIN-ORANGE",
                    "confirmed": True,
                },
            ],
            {
                "sam_order_v1": {
                    "source_identifier_type": "product_code",
                    "allow_spec_only_mapping": True,
                }
            },
        )

        green = product_catalog.resolve(
            "84292384",
            source_profile_id="sam_order_v1",
            source_spec="SWSH8096",
        )
        orange = product_catalog.resolve(
            "84292384",
            source_profile_id="sam_order_v1",
            source_spec="SWJC8140",
        )

        self.assertEqual(green.status, "confirmed")
        self.assertEqual(green.product.product_code, "P-GREEN")
        self.assertEqual(green.match_method, "confirmed_source_mapping_spec_only")
        self.assertEqual(orange.status, "confirmed")
        self.assertEqual(orange.product.product_code, "P-ORANGE")

    def test_exact_product_code_and_spec_wins_before_spec_only_fallback(self) -> None:
        product_catalog = ProductCatalog(
            [ProductRecord("P1", "旧式组合映射", "S1", "红色", 100, "测试仓")],
            [
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
                    "source_value": "",
                    "source_spec": "RED",
                    "product_code": "P2",
                    "spec_code": "S2",
                    "confirmed": True,
                }
            ],
            {
                "sam_order_v1": {
                    "source_identifier_type": "product_code",
                    "allow_spec_only_mapping": True,
                }
            },
        )

        result = product_catalog.resolve(
            "EXT-1",
            source_profile_id="sam_order_v1",
            source_spec="RED",
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.product.product_code, "P1")
        self.assertEqual(result.match_method, "confirmed_source_mapping_code_spec")

    def test_code_only_mapping_to_multi_spec_product_requires_reuse_authorization(self) -> None:
        records = [
            ProductRecord("P1", "测试商品", "S1", "红色", 100, "测试仓"),
            ProductRecord("P1", "测试商品", "S2", "蓝色", 100, "测试仓"),
        ]
        mapping = {
            "source_profile_id": "company_a",
            "identifier_type": "product_code",
            "source_value": "EXT-1",
            "source_spec": "",
            "product_code": "P1",
            "spec_code": "S1",
            "confirmed": True,
        }

        first_use = ProductCatalog(records, [mapping]).resolve(
            "EXT-1",
            source_profile_id="company_a",
        )
        reusable = ProductCatalog(
            records,
            [
                {
                    **mapping,
                    "reuse_scope": "future",
                    "mapping_authority": "feishu_official",
                }
            ],
        ).resolve(
            "EXT-1",
            source_profile_id="company_a",
        )

        self.assertEqual(first_use.status, "unconfirmed")
        self.assertEqual(
            first_use.match_method,
            "code_only_multi_spec_requires_confirmation",
        )
        self.assertEqual(len(first_use.candidates), 2)
        self.assertEqual(reusable.status, "confirmed")
        self.assertEqual(reusable.product.spec_code, "S1")

    def test_batch_confirmation_only_applies_to_its_bound_source_file(self) -> None:
        records = [
            ProductRecord("P1", "商品一红", "S1", "红", 100, "测试仓"),
            ProductRecord("P1", "商品一蓝", "S2", "蓝", 100, "测试仓"),
        ]
        fingerprint = "sha256:" + "a" * 64
        mapping = {
            "source_profile_id": "company_a",
            "identifier_type": "product_code",
            "source_value": "EXT-1",
            "source_spec": "",
            "product_code": "P1",
            "spec_code": "S1",
            "confirmed": True,
            "reuse_scope": "current_batch",
            "mapping_authority": "user_batch_confirmation",
            "source_fingerprint": fingerprint,
        }
        product_catalog = ProductCatalog(records, [mapping])

        current = product_catalog.resolve(
            "EXT-1",
            source_profile_id="company_a",
            source_fingerprint=fingerprint,
        )
        another = product_catalog.resolve(
            "EXT-1",
            source_profile_id="company_a",
            source_fingerprint="sha256:" + "b" * 64,
        )

        self.assertEqual(current.status, "confirmed")
        self.assertEqual(current.product.spec_code, "S1")
        self.assertEqual(current.mapping_authority, "user_batch_confirmation")
        self.assertEqual(current.reuse_scope, "current_batch")
        self.assertEqual(another.status, "unconfirmed")
        self.assertEqual(
            another.match_method,
            "current_batch_mapping_fingerprint_mismatch",
        )

    def test_batch_confirmation_binding_also_applies_to_unique_target(self) -> None:
        fingerprint = "sha256:" + "a" * 64
        product_catalog = ProductCatalog(
            [ProductRecord("P1", "商品一", "S1", "默认", 100, "测试仓")],
            [
                {
                    "source_profile_id": "company_a",
                    "identifier_type": "product_code",
                    "source_value": "EXT-1",
                    "source_spec": "",
                    "product_code": "P1",
                    "spec_code": "S1",
                    "confirmed": True,
                    "reuse_scope": "current_batch",
                    "mapping_authority": "user_batch_confirmation",
                    "source_fingerprint": fingerprint,
                }
            ],
        )

        mismatch = product_catalog.resolve(
            "EXT-1",
            source_profile_id="company_a",
            source_fingerprint="sha256:" + "b" * 64,
        )

        self.assertEqual(mismatch.status, "unconfirmed")
        self.assertEqual(
            mismatch.match_method,
            "current_batch_mapping_fingerprint_mismatch",
        )

    def test_unmapped_source_code_matching_multi_spec_guanyi_code_returns_candidates(self) -> None:
        product_catalog = ProductCatalog(
            [
                ProductRecord("P1", "测试商品", "S1", "红色", 100, "测试仓"),
                ProductRecord("P1", "测试商品", "S2", "蓝色", 100, "测试仓"),
            ],
            [],
            {"company_a": {"source_identifier_type": "product_code"}},
        )

        result = product_catalog.resolve("P1", source_profile_id="company_a")

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.match_method, "direct_product_code_candidates")
        self.assertEqual({item.spec_code for item in result.candidates}, {"S1", "S2"})

    def test_similar_name_only_returns_unconfirmed_candidates(self) -> None:
        product_catalog = ProductCatalog(
            [
                ProductRecord("P1", "蓝色搓澡巾", "S1", "蓝色", 100, "测试仓"),
                ProductRecord("P2", "橘色搓澡巾", "S2", "橘色", 100, "测试仓"),
            ],
            [],
            {
                "company_a": {
                    "source_identifier_type": "product_name",
                    "allow_exact_catalog_name": False,
                    "fuzzy_name_action": "candidate_requires_user_confirmation",
                }
            },
        )

        result = product_catalog.resolve(
            "",
            source_profile_id="company_a",
            source_product_name="蓝色搓背巾",
            source_spec="蓝色",
        )

        self.assertFalse(result.confirmed)
        self.assertEqual(result.match_method, "fuzzy_name_candidates")
        self.assertTrue(result.candidates)
        self.assertEqual(result.candidates[0].product_code, "P1")

    def test_single_low_confidence_fuzzy_candidate_is_still_blocking(self) -> None:
        product_catalog = ProductCatalog(
            [
                ProductRecord(
                    "J-579",
                    "和匠日本进口搓澡巾手套去鸡皮去角质搓泥双面两用沐浴花洗澡巾",
                    "J-579",
                    "双面两用",
                    100,
                    "测试仓",
                )
            ],
            [],
            {
                "company_a": {
                    "source_identifier_type": "product_name",
                    "allow_exact_catalog_name": False,
                    "fuzzy_name_action": "candidate_requires_user_confirmation",
                }
            },
        )

        result = product_catalog.resolve(
            "",
            source_profile_id="company_a",
            source_product_name="搓澡巾",
            source_spec="超硬深蓝色",
        )

        self.assertEqual(result.status, "low_confidence")
        self.assertEqual(result.confidence_tier, "blocking")
        self.assertIsNone(result.product)
        self.assertEqual(result.candidates[0].product_code, "J-579")
        self.assertIn("颜色:深蓝", result.candidate_evidence[0].missing_attributes)
        self.assertIn("软硬:超硬", result.candidate_evidence[0].missing_attributes)

    def test_single_high_confidence_fuzzy_candidate_can_build_preview(self) -> None:
        product_catalog = ProductCatalog(
            [ProductRecord("P1", "蓝色搓澡巾", "S1", "蓝色", 100, "测试仓")],
            [],
            {
                "company_a": {
                    "source_identifier_type": "product_name",
                    "allow_exact_catalog_name": False,
                    "fuzzy_name_action": "candidate_requires_user_confirmation",
                }
            },
        )

        result = product_catalog.resolve(
            "",
            source_profile_id="company_a",
            source_product_name="蓝色搓背巾",
            source_spec="蓝色",
        )

        self.assertEqual(result.status, "unconfirmed")
        self.assertEqual(result.confidence_tier, "preview_safe")
        self.assertEqual(result.product.product_code, "P1")
        self.assertEqual(result.candidate_evidence[0].confidence_tier, "preview_safe")

    def test_official_alias_family_can_suggest_unique_new_variant(self) -> None:
        records = [
            ProductRecord(
                "KKAW0290",
                "Yellow超软",
                "4548404200290",
                "超软",
                100,
                "测试仓",
            ),
            ProductRecord(
                "KKAW0030",
                "蓝色【硬款】",
                "4548404200030",
                "硬款",
                100,
                "测试仓",
            ),
            ProductRecord(
                "KKAW1532",
                "Blue超硬",
                "4548404201532",
                "超硬",
                100,
                "测试仓",
            ),
            ProductRecord(
                "KKAW1006",
                "Green软",
                "4548404201006",
                "软",
                100,
                "测试仓",
            ),
            ProductRecord(
                "J-579",
                "和匠日本进口搓澡巾手套去鸡皮去角质搓泥双面两用沐浴花洗澡巾",
                "J-579",
                "双面两用",
                100,
                "测试仓",
            ),
        ]
        official_aliases = [
            {
                "source_profile_id": "source_a",
                "identifier_type": "product_name",
                "source_value": "搓澡巾",
                "source_spec": "超软黄色",
                "product_code": "KKAW0290",
                "spec_code": "4548404200290",
                "confirmed": True,
                "reuse_scope": "future",
                "mapping_authority": "feishu_official",
            },
            {
                "source_profile_id": "source_b",
                "identifier_type": "product_name",
                "source_value": "搓澡巾",
                "source_spec": "蓝色",
                "product_code": "KKAW0030",
                "spec_code": "4548404200030",
                "confirmed": True,
                "reuse_scope": "future",
                "mapping_authority": "feishu_official",
            },
        ]
        catalog = ProductCatalog(
            records,
            official_aliases,
            {
                "source_c": {
                    "source_identifier_type": "product_name",
                    "allow_exact_catalog_name": False,
                    "fuzzy_name_action": "candidate_requires_user_confirmation",
                }
            },
        )

        deep_blue = catalog.resolve(
            "",
            source_profile_id="source_c",
            source_product_name="搓澡巾",
            source_spec="超硬深蓝色",
        )
        green = catalog.resolve(
            "",
            source_profile_id="source_c",
            source_product_name="搓澡巾",
            source_spec="绿色",
        )

        self.assertEqual(deep_blue.status, "unconfirmed")
        self.assertEqual(deep_blue.confidence_tier, "preview_safe")
        self.assertEqual(deep_blue.product.product_code, "KKAW1532")
        self.assertEqual(
            deep_blue.match_method,
            "official_alias_family_spec_candidate",
        )
        self.assertEqual(green.status, "unconfirmed")
        self.assertEqual(green.product.product_code, "KKAW1006")

    def test_one_official_alias_does_not_establish_a_product_family(self) -> None:
        catalog = ProductCatalog(
            [
                ProductRecord(
                    "KKAW0030",
                    "蓝色【硬款】",
                    "4548404200030",
                    "硬款",
                    100,
                    "测试仓",
                ),
                ProductRecord(
                    "KKAW1006",
                    "Green软",
                    "4548404201006",
                    "软",
                    100,
                    "测试仓",
                ),
            ],
            [
                {
                    "source_profile_id": "source_a",
                    "identifier_type": "product_name",
                    "source_value": "搓澡巾",
                    "source_spec": "蓝色",
                    "product_code": "KKAW0030",
                    "spec_code": "4548404200030",
                    "confirmed": True,
                    "reuse_scope": "future",
                    "mapping_authority": "feishu_official",
                }
            ],
            {
                "source_b": {
                    "source_identifier_type": "product_name",
                    "allow_exact_catalog_name": False,
                    "fuzzy_name_action": "candidate_requires_user_confirmation",
                }
            },
        )

        result = catalog.resolve(
            "",
            source_profile_id="source_b",
            source_product_name="搓澡巾",
            source_spec="绿色",
        )

        self.assertNotEqual(
            result.match_method,
            "official_alias_family_spec_candidate",
        )

    def test_company_name_alias_uses_the_universal_mapping_table(self) -> None:
        product_catalog = ProductCatalog(
            [ProductRecord("P1", "标准搓澡巾", "S1", "蓝色", 100, "测试仓")],
            [
                {
                    "source_profile_id": "company_a",
                    "identifier_type": "product_name",
                    "source_value": "蓝色搓背巾",
                    "source_spec": "",
                    "product_code": "P1",
                    "spec_code": "S1",
                    "confirmed": True,
                }
            ],
            {"company_a": {"source_identifier_type": "product_code"}},
        )

        result = product_catalog.resolve(
            "",
            source_profile_id="company_a",
            source_product_name="蓝色搓背巾",
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.match_method, "confirmed_name_alias_name_only")
        self.assertEqual(result.product.product_code, "P1")

    def test_unsplit_name_and_spec_uses_exact_published_composite_mapping(self) -> None:
        product_catalog = ProductCatalog(
            [ProductRecord("P1", "Orange普通", "S1", "普通", 100, "测试仓")],
            [
                {
                    "source_profile_id": "taojuzi_taobao_text_v1",
                    "identifier_type": "product_name",
                    "source_value": "搓澡巾",
                    "source_spec": "橘色",
                    "product_code": "P1",
                    "spec_code": "S1",
                    "confirmed": True,
                }
            ],
            {
                "taojuzi_taobao_text_v1": {
                    "source_identifier_type": "product_name",
                    "allow_exact_catalog_name": False,
                    "fuzzy_name_action": "candidate_requires_user_confirmation",
                }
            },
        )

        for combined_name in ("橘色搓澡巾", "搓澡巾（橘色）"):
            with self.subTest(combined_name=combined_name):
                result = product_catalog.resolve(
                    "",
                    source_profile_id="taojuzi_taobao_text_v1",
                    source_product_name=combined_name,
                    source_spec="",
                )

                self.assertEqual(result.status, "confirmed")
                self.assertEqual(
                    result.match_method,
                    "confirmed_name_alias_composite_decomposition",
                )
                self.assertIsNotNone(result.product)
                assert result.product is not None
                self.assertEqual(result.product.product_code, "P1")

    def test_composite_mapping_never_discards_separate_source_spec(self) -> None:
        product_catalog = ProductCatalog(
            [ProductRecord("P1", "Orange普通", "S1", "普通", 100, "测试仓")],
            [
                {
                    "source_profile_id": "taojuzi_taobao_text_v1",
                    "identifier_type": "product_name",
                    "source_value": "搓澡巾",
                    "source_spec": "橘色",
                    "product_code": "P1",
                    "spec_code": "S1",
                    "confirmed": True,
                }
            ],
            {
                "taojuzi_taobao_text_v1": {
                    "source_identifier_type": "product_name",
                    "allow_exact_catalog_name": False,
                }
            },
        )

        result = product_catalog.resolve(
            "",
            source_profile_id="taojuzi_taobao_text_v1",
            source_product_name="橘色搓澡巾",
            source_spec="条",
        )

        self.assertEqual(result.status, "unmapped")
        self.assertNotEqual(
            result.match_method,
            "confirmed_name_alias_composite_decomposition",
        )

    def test_missing_target_spec_resolves_only_single_variant_product(self) -> None:
        product_catalog = ProductCatalog(
            [
                ProductRecord("P1", "单规格商品", "S1", "默认", 100, "测试仓"),
                ProductRecord("P2", "多规格商品", "S2A", "A", 100, "测试仓"),
                ProductRecord("P2", "多规格商品", "S2B", "B", 100, "测试仓"),
            ]
        )

        self.assertEqual(
            product_catalog.resolve_unique_target("P1", "").spec_code,
            "S1",
        )
        self.assertIsNone(product_catalog.resolve_unique_target("P2", ""))

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
        self.assertFalse(matched.review_warnings)
        self.assertEqual(changed.status, "confirmed")
        self.assertEqual(
            changed.match_method,
            "confirmed_source_mapping_code_only",
        )
        self.assertEqual(
            changed.review_warnings[0]["code"],
            "SOURCE_NAME_DIFFERS_FROM_CONFIRMED_CODE_MAPPING",
        )

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
        self.assertEqual(result.status, "confirmed")
        self.assertIn("缺少已登记", result.reason)
        self.assertEqual(
            result.review_warnings[0]["code"],
            "SOURCE_NAME_DIFFERS_FROM_CONFIRMED_CODE_MAPPING",
        )

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
