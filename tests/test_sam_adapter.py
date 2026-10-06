from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from auto_shipped.detection import detect_source, load_source_profiles
from auto_shipped.routing import load_routing, resolve_route
from auto_shipped.source_adapters import ConfiguredExcelParsedAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads(
    (PROJECT_ROOT / "config/source_profiles/sam_order_v1.json").read_text(
        encoding="utf-8"
    )
)


def row() -> dict[str, object]:
    return {
        "物流公司": "",
        "物流单号": "",
        "来源单号（不存在则显示订单编号）": "SAM-SOURCE-1",
        "团标题": "测试团",
        "跟团号": "848",
        "原表格商品": "测试玉米",
        "原表格规格": "白糯8根+1根",
        "数量": 2,
        "收件人": "测试用户",
        "联系方式": "13800138000",
        "省市区详细地址": "上海市测试地址",
        "客户备注": "客户备注",
        "商铺名称": "sam哥的品牌号",
        "商品编码": "276388650",
        "规格编码": "光9-JTBN1T1-EH",
        "省": "上海",
        "市": "上海市",
        "区": "测试区",
        "子单号": "SUB-1",
        "身份证号": "",
        "真实姓名": "",
        "买家备注": "买家备注",
    }


class SamAdapterTests(unittest.TestCase):
    def test_sample_shape_detects_and_routes_to_sam_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sam.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "仓库订单"
            sample = row()
            sheet.append(list(sample))
            sheet.append(list(sample.values()))
            workbook.save(path)

            detected = detect_source(
                path,
                load_source_profiles(PROJECT_ROOT / "config/source_profiles"),
            )
            self.assertEqual(detected.status, "matched")
            self.assertEqual(detected.company_id, "sam")
            self.assertEqual(detected.source_profile_id, "sam_order_v1")
            self.assertIn(
                "company_column:仓库订单!商铺名称",
                detected.matched_features,
            )

            routed = resolve_route(
                load_routing(PROJECT_ROOT / "config/routing/order_routes_v1.json"),
                detected.source_profile_id,
                detected.order_type,
            )
            self.assertEqual(routed.status, "resolved")
            self.assertEqual(
                routed.decision.platform_rules_profile_id,
                "guanyi_sam_order_v1",
            )
            self.assertEqual(
                routed.decision.business_rule_scope_id,
                "sam_to_guanyi_v1",
            )

    def test_parser_preserves_group_number_and_source_spec_code(self) -> None:
        parsed = ConfiguredExcelParsedAdapter().parse_rows(
            [(2, row())],
            Path("sam.xlsx"),
            PROFILE,
        )
        self.assertEqual(parsed.status, "parsed")
        self.assertEqual(len(parsed.orders), 1)
        order = parsed.orders[0]
        self.assertEqual(order.source.source_order_no, "SAM-SOURCE-1")
        self.assertEqual(order.source_extensions["sam_group_number"], "848")
        self.assertEqual(order.buyer_message, "买家备注")
        self.assertEqual(order.source_note, "客户备注")
        self.assertEqual(order.items[0].source_product_code, "276388650")
        self.assertEqual(order.items[0].source_spec, "光9-JTBN1T1-EH")
        self.assertEqual(order.items[0].source_display_spec, "白糯8根+1根")
        self.assertEqual(order.items[0].quantity, 2)

    def test_blank_group_number_is_preserved_for_buyer_member_clarification(self) -> None:
        sample = row()
        sample["跟团号"] = ""
        parsed = ConfiguredExcelParsedAdapter().parse_rows(
            [(2, sample)],
            Path("sam.xlsx"),
            PROFILE,
        )
        self.assertEqual(parsed.status, "parsed")
        self.assertEqual(parsed.orders[0].source_extensions["sam_group_number"], "")

    def test_invalid_contact_and_numeric_long_order_id_are_blocked(self) -> None:
        invalid_contact = row()
        invalid_contact["联系方式"] = "abc"
        contact_result = ConfiguredExcelParsedAdapter().parse_rows(
            [(2, invalid_contact)],
            Path("sam.xlsx"),
            PROFILE,
        )
        self.assertIn(
            "INVALID_RECIPIENT_CONTACT",
            {request.code for request in contact_result.clarifications},
        )

        numeric_id = row()
        numeric_id["来源单号（不存在则显示订单编号）"] = 220722179066487320925437952
        id_result = ConfiguredExcelParsedAdapter().parse_rows(
            [(2, numeric_id)],
            Path("sam.xlsx"),
            PROFILE,
        )
        self.assertIn(
            "SOURCE_ORDER_NO_TEXT_REQUIRED",
            {request.code for request in id_result.clarifications},
        )


if __name__ == "__main__":
    unittest.main()
