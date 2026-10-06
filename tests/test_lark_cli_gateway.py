from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

from auto_shipped.integrations.feishu import (
    LarkCliFeishuSheetGateway,
    LarkCliGatewayError,
    UserSheetRoute,
)


class FakeLarkRunner:
    def __init__(self) -> None:
        self.route_rows: dict[int, list[str]] = {}

    @staticmethod
    def _arg(command: Sequence[str], flag: str) -> str:
        return command[command.index(flag) + 1]

    @staticmethod
    def _csv_payload(rows: list[tuple[int, list[str]]]) -> dict[str, Any]:
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        for index, values in rows:
            row_stream = io.StringIO()
            csv.writer(row_stream, lineterminator="").writerow(values)
            stream.write(f"[row={index}] {row_stream.getvalue()}\n")
        return {
            "ok": True,
            "data": {
                "annotated_csv": stream.getvalue().rstrip("\n"),
                "row_indices": [index for index, _ in rows],
            },
        }

    def __call__(self, command: Sequence[str]) -> dict[str, Any]:
        if list(command[1:3]) == ["auth", "status"]:
            return {
                "identity": "user",
                "identities": {
                    "user": {
                        "available": True,
                        "status": "ready",
                        "openId": "ou-test",
                        "userName": "测试用户",
                    }
                },
            }
        if "+csv-get" in command:
            sheet_id = self._arg(command, "--sheet-id")
            cell_range = self._arg(command, "--range")
            if sheet_id == "meta" and cell_range == "A1:B50":
                return self._csv_payload(
                    [(1, ["key", "value"]), (2, ["mapping_revision", "7"])]
                )
            if sheet_id == "route" and cell_range == "A2:J2000":
                rows = [
                    (index, self.route_rows.get(index, [""] * 10))
                    for index in range(2, 5)
                ]
                return self._csv_payload(rows)
            raise AssertionError(f"unexpected csv read: {sheet_id} {cell_range}")
        if "+cells-set" in command:
            sheet_id = self._arg(command, "--sheet-id")
            if sheet_id != "route":
                raise AssertionError(f"unexpected write sheet: {sheet_id}")
            row_number = int(self._arg(command, "--range").split(":", 1)[0][1:])
            values = [cell["value"] for cell in json.loads(self._arg(command, "--cells"))[0]]
            self.route_rows[row_number] = values
            return {"ok": True, "data": {"revision": 3}}
        raise AssertionError(f"unexpected command: {command}")


def gateway(runner: FakeLarkRunner) -> LarkCliFeishuSheetGateway:
    return LarkCliFeishuSheetGateway(
        pending_spreadsheet_token="pending-token",
        route_sheet_id="route",
        template_sheet_id="template",
        official_spreadsheet_token="official-token",
        official_mapping_sheet_id="mapping",
        official_metadata_sheet_id="meta",
        runner=runner,
    )


class LarkCliGatewayTests(unittest.TestCase):
    def test_identity_revision_and_route_write_are_read_back(self) -> None:
        runner = FakeLarkRunner()
        client = gateway(runner)

        self.assertEqual(client.get_current_identity().user_id, "ou-test")
        self.assertEqual(client.get_official_mapping_revision(), "7")

        route = UserSheetRoute(
            feishu_user_id="ou-test",
            sheet_id="personal",
            sheet_title="待确认_测试用户",
            status="active",
            schema_version="1.0",
            provision_key="feishu-user-test",
            provision_operation_id="operation-1",
            created_at="2026-10-01T00:00:00+00:00",
            last_used_at="2026-10-01T00:01:00+00:00",
        )
        client.upsert_user_route(route)
        self.assertEqual(client.list_user_routes(), [route])

    def test_from_config_requires_all_stable_sheet_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "official_repository": {
                            "spreadsheet_token": "official",
                            "mapping_sheet_id": "mapping",
                            "metadata_sheet_id": None,
                        },
                        "pending_repository": {
                            "spreadsheet_token": "pending",
                            "route_sheet_id": "route",
                            "template_sheet_id": "template",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(LarkCliGatewayError):
                LarkCliFeishuSheetGateway.from_config(path)


if __name__ == "__main__":
    unittest.main()
