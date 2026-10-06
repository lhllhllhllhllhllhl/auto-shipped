from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_shipped.platforms.guanyi import (
    preflight_custom_import,
    render_custom_import,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class UploadManifestBridgeTests(unittest.TestCase):
    def test_python_preflight_manifest_is_accepted_by_browser_executor(self) -> None:
        platform_profile = json.loads(
            (PROJECT_ROOT / "config/platform_profiles/guanyi_order_import_v1.json").read_text(
                encoding="utf-8"
            )
        )
        line = {
            "店铺": "光明满元气",
            "平台单号": "BRIDGE-001A",
            "买家会员": "张",
            "支付金额": 0,
            "商品代码": "P1",
            "规格代码": "S1",
            "是否赠品": 0,
            "数量": 1,
            "价格": 0,
            "运费": 0,
            "收货人": "测试用户",
            "联系手机": "13800138000",
            "收货地址": "上海市宝山区测试路1号",
            "物流公司": "韵达快递",
            "是否手机订单": 0,
            "是否货到付款": 0,
            "支付方式": "网银在线",
            "仓库名称": "上海尚舆商贸有限公司",
            "订单类型": "销售订单",
            "是否分销商订单": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook_path = root / "orders.xlsx"
            render_custom_import(
                PROJECT_ROOT / "assets/templates/guanyi/自定义订单导入模板.xlsx",
                workbook_path,
                [line],
                platform_profile,
            )
            preflight = preflight_custom_import(workbook_path, platform_profile)
            self.assertEqual(preflight.status, "ready")
            manifest_path = preflight.write_manifest(root / "manifest.json")

            completed = subprocess.run(
                [
                    "node",
                    str(PROJECT_ROOT / "browser/guanyi/cli.mjs"),
                    "verify-manifest",
                    str(manifest_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "manifest_verified")
            self.assertEqual(payload["orders"], 1)
            self.assertEqual(payload["itemRows"], 1)


if __name__ == "__main__":
    unittest.main()
