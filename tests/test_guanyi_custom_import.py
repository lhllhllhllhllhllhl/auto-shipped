from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from auto_shipped.catalog.product_catalog import ProductCatalog, ProductRecord
from auto_shipped.domain import (
    ParsedItem,
    ParsedOrder,
    ParsedRecipient,
    ParsedShipmentFacts,
    ParsedSource,
)
from auto_shipped.platforms.guanyi import build_custom_import_lines, render_custom_import


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def confirmed_rules() -> dict:
    return {
        "platform_order_number": {"strategy": "source_order_no", "confirmed": True},
        "store_assignment_policy": {
            "strategy": "all_items_same_store_by_product",
            "confirmed": True,
            "rules": [
                {
                    "store": "光明满元气",
                    "match": {"product_codes": ["P1"], "spec_codes": ["S1"]},
                }
            ],
        },
        "field_output_policy": {
            "product_name": {
                "strategy": "catalog_product_name",
                "confirmed": True,
            },
            "spec_name": {
                "strategy": "catalog_spec_name",
                "confirmed": True,
            },
            "order_created_at": {
                "strategy": "source_ordered_at",
                "confirmed": True,
            },
            "contact_columns": {
                "strategy": "mobile_to_both_phone_preserved",
                "confirmed": True,
            },
            "recipient_region": {
                "strategy": "source_components",
                "confirmed": True,
            },
        },
        "buyer_member_policy": {
            "strategy": "fixed",
            "value": "张",
            "confirmed": True,
        },
        "defaults": {
            "payment_amount": {"value": 0, "confirmed": True},
            "unit_price": {"value": 0, "confirmed": True},
            "freight": {"value": 0, "confirmed": True},
            "warehouse_name": {"value": "上海尚舆商贸有限公司", "confirmed": True},
            "payment_method": {"value": "网银在线", "confirmed": True},
            "order_type": {"value": "销售订单", "confirmed": True},
            "is_mobile_order": {"value": 0, "confirmed": True},
            "is_cod": {"value": 0, "confirmed": True},
            "is_distributor_order": {"value": 0, "confirmed": True},
        },
        "logistics_policy": {
            "strategy": "source_override_then_confirmed_packaging_rules",
            "source_carrier_priority": True,
            "source_carrier_aliases": {
                "顺丰速运": "顺丰速运",
            },
            "confirmed": True,
            "default_carrier": "韵达快递",
            "heavy_carrier": "中通快递（重货）",
            "heavy_conditions": [
                {
                    "match": {"product_codes": ["P1"]},
                    "minimum_quantity": 2,
                }
            ],
        },
        "seller_remark_policy": {
            "strategy": "source_note_then_system_notes_deduplicated",
            "separator": "；",
            "confirmed": True,
        },
    }


def parsed_order() -> ParsedOrder:
    return ParsedOrder(
        record_key="order-1",
        source=ParsedSource(
            profile_id="tiantian_warehouse_v2",
            source_label="恬田代发仓",
            order_type="warehouse_order",
            source_order_no="SRC-001",
            file_name="sample.xlsx",
            file_fingerprint="sha256:test",
            sheet_name="仓库订单",
            row_numbers=(2,),
        ),
        recipient=ParsedRecipient(
            name="测试用户",
            mobile="13800138000",
            province="上海",
            city="上海市",
            district="宝山区",
            raw_address="上海市宝山区测试路1号",
        ),
        items=[
            ParsedItem(
                source_product_code="EXT-1",
                source_product_name="测试商品",
                quantity=2,
                source_line_no=2,
            )
        ],
        ordered_at="2026-09-20 14:09:15",
    )


class GuanyiCustomImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ProductCatalog(
            [ProductRecord("P1", "管易测试商品", "S1", "测试规格", 100, "上海仓")],
            {
                "EXT-1": {
                    "product_code": "P1",
                    "spec_code": "S1",
                    "confirmed": True,
                }
            },
        )
        self.profile = json.loads(
            (PROJECT_ROOT / "config/platform_profiles/guanyi_order_import_v1.json").read_text(
                encoding="utf-8"
            )
        )

    def test_confirmed_rules_build_one_row_per_item(self) -> None:
        result = build_custom_import_lines(
            [parsed_order()],
            self.catalog,
            confirmed_rules(),
            self.profile,
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(len(result.lines), 1)
        line = result.lines[0]
        self.assertEqual(line["店铺"], "光明满元气")
        self.assertEqual(line["平台单号"], "SRC-001A")
        self.assertEqual(line["商品代码"], "P1")
        self.assertEqual(line["规格代码"], "S1")
        self.assertEqual(line["商品名称"], "管易测试商品")
        self.assertEqual(line["规格名称"], "测试规格")
        self.assertEqual(line["数量"], 2)
        self.assertEqual(line["收货地址"], "上海市宝山区测试路1号")
        self.assertEqual(line["联系电话"], "13800138000")
        self.assertEqual(line["联系手机"], "13800138000")
        self.assertEqual(line["省"], "上海")
        self.assertEqual(line["市"], "上海市")
        self.assertEqual(line["区"], "宝山区")
        self.assertEqual(line["订单创建时间"], "2026-09-20 14:09:15")
        self.assertEqual(line["物流公司"], "中通快递（重货）")

    def test_platform_order_number_supports_company_prefix_and_agent_suffix(self) -> None:
        rules = confirmed_rules()
        rules["platform_order_number"] = {
            "strategy": "source_order_no_with_affixes",
            "prefix": "TT",
            "suffix": "",
            "confirmed": True,
        }
        result = build_custom_import_lines(
            [parsed_order()],
            self.catalog,
            rules,
            self.profile,
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.lines[0]["平台单号"], "TTSRC-001A")

    def test_logistics_defaults_to_yunda_below_heavy_threshold(self) -> None:
        order = parsed_order()
        order.items[0] = ParsedItem(
            source_product_code="EXT-1",
            source_product_name="测试商品",
            quantity=1,
            source_line_no=2,
        )
        result = build_custom_import_lines(
            [order],
            self.catalog,
            confirmed_rules(),
            self.profile,
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.lines[0]["物流公司"], "韵达快递")

    def test_source_carrier_has_priority_over_packaging_rule(self) -> None:
        order = parsed_order()
        order.shipment_facts = ParsedShipmentFacts(carrier_name="顺丰速运")
        result = build_custom_import_lines(
            [order],
            self.catalog,
            confirmed_rules(),
            self.profile,
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.lines[0]["物流公司"], "顺丰速运")

    def test_sam_bundle_expands_to_eight_pack_and_single_item(self) -> None:
        order = parsed_order()
        order.items = [
            ParsedItem(
                source_product_code="276388650",
                source_product_name="光明满元气鲜糯玉米（加送1根）",
                source_spec="光9-JTBN1T1-EH",
                source_display_spec="白糯8根+1根",
                quantity=2,
                source_line_no=2,
            )
        ]
        order.source_extensions["sam_group_number"] = "848"
        catalog = ProductCatalog(
            [
                ProductRecord(
                    "JTBN1E1-EH",
                    "光明满元气 白糯玉米8棒家庭装",
                    "6974768564842",
                    "8袋/箱",
                    1210,
                    "上海尚舆商贸有限公司",
                ),
                ProductRecord(
                    "GMBN1",
                    "光明满元气 白糯玉米单根装",
                    "6974768564804",
                    "50根/箱",
                    180,
                    "上海尚舆商贸有限公司",
                ),
            ],
            [],
        )
        rules = confirmed_rules()
        rules.pop("store_assignment_policy")
        rules["defaults"]["store"] = {"value": "sam", "confirmed": True}
        rules["defaults"]["freight"] = {"value": "", "confirmed": True}
        rules["field_output_policy"]["product_name"]["strategy"] = (
            "source_name_with_display_spec_for_standard_items"
        )
        rules["buyer_member_policy"] = {
            "strategy": "sam_product_type_date_group",
            "platform_prefix": "sam",
            "group_number_field": "sam_group_number",
            "group_separator": "_",
            "date_format": "%y%m%d",
            "timezone": "Asia/Shanghai",
            "product_type_rules": [
                {
                    "type_code": "YM",
                    "match": {"product_name_contains_all": ["玉米"]},
                    "confirmed": True,
                }
            ],
            "confirmed": True,
        }
        rules["item_expansion_policy"] = {
            "strategy": "confirmed_source_item_expansions",
            "confirmed": True,
            "rules": [
                {
                    "rule_id": "sam_corn_white_8_plus_1",
                    "match": {
                        "source_product_code": "276388650",
                        "source_spec": "光9-JTBN1T1-EH",
                    },
                    "components": [
                        {
                            "product_code": "JTBN1E1-EH",
                            "spec_code": "6974768564842",
                            "quantity_multiplier": 1,
                        },
                        {
                            "product_code": "GMBN1",
                            "spec_code": "6974768564804",
                            "quantity_multiplier": 1,
                        },
                    ],
                    "confirmed": True,
                }
            ],
        }
        rules["logistics_policy"]["heavy_conditions"] = [
            {
                "match": {"product_codes": ["JTBN1E1-EH"]},
                "minimum_quantity": 2,
            }
        ]

        result = build_custom_import_lines(
            [order],
            catalog,
            rules,
            self.profile,
            business_date=date(2026, 9, 29),
            official_mapping_ready=False,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(len(result.lines), 2)
        self.assertEqual(
            [(line["商品代码"], line["规格代码"], line["数量"]) for line in result.lines],
            [
                ("JTBN1E1-EH", "6974768564842", 2),
                ("GMBN1", "6974768564804", 2),
            ],
        )
        self.assertTrue(all(line["买家会员"] == "samYM260929_848" for line in result.lines))
        self.assertTrue(all(line["价格"] == 0 for line in result.lines))
        self.assertTrue(all(line["支付金额"] == 0 for line in result.lines))
        self.assertTrue(all(line["运费"] == "" for line in result.lines))
        self.assertTrue(all(line["物流公司"] == "中通快递（重货）" for line in result.lines))
        self.assertEqual(
            [line["商品名称"] for line in result.lines],
            ["光明满元气 白糯玉米8棒家庭装", "光明满元气 白糯玉米单根装"],
        )

    def test_missing_official_mapping_blocks_before_product_question(self) -> None:
        result = build_custom_import_lines(
            [parsed_order()],
            self.catalog,
            confirmed_rules(),
            self.profile,
            official_mapping_ready=False,
            official_mapping_error="飞书用户未授权",
        )

        codes = {request.code for request in result.clarifications}
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.lines, [])
        self.assertIn("FEISHU_OFFICIAL_MAPPING_UNAVAILABLE", codes)
        self.assertNotIn("PROVIDE_GUANYI_PRODUCT_MAPPING", codes)
        self.assertNotIn("CONFIRM_GUANYI_PRODUCT_MAPPING", codes)
        request = next(
            item
            for item in result.clarifications
            if item.code == "FEISHU_OFFICIAL_MAPPING_UNAVAILABLE"
        )
        self.assertEqual(request.reason, "飞书用户未授权")

    def test_sam_standard_item_uses_readable_source_name_and_display_spec(self) -> None:
        order = parsed_order()
        order.items = [
            ParsedItem(
                source_product_code="EXT-1",
                source_product_name="SHOWA丝滑手感中厚型手套",
                source_spec="SWSH8072",
                source_display_spec="S/常规款/珍珠粉",
                quantity=1,
                source_line_no=2,
            )
        ]
        rules = confirmed_rules()
        rules["field_output_policy"]["product_name"]["strategy"] = (
            "source_name_with_display_spec_for_standard_items"
        )

        result = build_custom_import_lines(
            [order],
            self.catalog,
            rules,
            self.profile,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(
            result.lines[0]["商品名称"],
            "SHOWA丝滑手感中厚型手套/S/常规款/珍珠粉",
        )

    def test_customer_note_is_written_to_seller_remark(self) -> None:
        order = parsed_order()
        order.source_note = "周末送达"
        result = build_custom_import_lines(
            [order],
            self.catalog,
            confirmed_rules(),
            self.profile,
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.lines[0]["卖家备注"], "周末送达")

    def test_seller_remark_separator_is_read_from_configuration(self) -> None:
        order = parsed_order()
        order.source_note = "周末送达"
        rules = confirmed_rules()
        rules["seller_remark_policy"]["separator"] = "/"
        result = build_custom_import_lines(
            [order],
            self.catalog,
            rules,
            self.profile,
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.lines[0]["卖家备注"], "周末送达")

    def test_unconfirmed_field_output_policy_blocks_build(self) -> None:
        rules = confirmed_rules()
        rules["field_output_policy"]["order_created_at"]["confirmed"] = False
        result = build_custom_import_lines(
            [parsed_order()],
            self.catalog,
            rules,
            self.profile,
        )
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.lines, [])
        self.assertIn(
            "CONFIRM_GUANYI_FIELD_POLICY_ORDER_CREATED_AT",
            {request.code for request in result.clarifications},
        )

    def test_unmapped_product_store_blocks_build(self) -> None:
        rules = confirmed_rules()
        rules["store_assignment_policy"]["rules"][0]["match"] = {
            "product_codes": ["OTHER"]
        }
        result = build_custom_import_lines(
            [parsed_order()],
            self.catalog,
            rules,
            self.profile,
        )
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.lines, [])
        self.assertIn(
            "CONFIRM_GUANYI_STORE_ASSIGNMENT",
            {request.code for request in result.clarifications},
        )

    def test_unconfirmed_required_rules_block_build(self) -> None:
        rules = confirmed_rules()
        rules["defaults"]["payment_amount"]["confirmed"] = False
        result = build_custom_import_lines(
            [parsed_order()],
            self.catalog,
            rules,
            self.profile,
        )
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.lines, [])
        self.assertIn(
            "CONFIRM_GUANYI_PAYMENT_AMOUNT",
            {request.code for request in result.clarifications},
        )

    def test_renderer_removes_examples_and_preserves_template_headers(self) -> None:
        built = build_custom_import_lines(
            [parsed_order()],
            self.catalog,
            confirmed_rules(),
            self.profile,
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output.xlsx"
            render_custom_import(
                PROJECT_ROOT / "assets/templates/guanyi/自定义订单导入模板.xlsx",
                output,
                built.lines,
                self.profile,
            )
            workbook = load_workbook(output, read_only=False, data_only=True)
            sheet = workbook["Sheet1"]
            headers = [cell.value for cell in sheet[1] if cell.value is not None]
            self.assertEqual(headers, self.profile["columns"])
            self.assertEqual(sheet.max_row, 2)
            self.assertEqual(sheet["B2"].value, "SRC-001A")
            self.assertEqual(sheet["F2"].value, "P1")
            self.assertEqual(sheet["B2"].number_format, "@")
            self.assertEqual(sheet["P2"].value, "13800138000")
            self.assertEqual(sheet["Q2"].value, "13800138000")
            self.assertEqual(sheet["AA2"].value, "中通快递（重货）")
            workbook.close()


if __name__ == "__main__":
    unittest.main()
