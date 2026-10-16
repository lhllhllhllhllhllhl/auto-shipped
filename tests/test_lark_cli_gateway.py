from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

from auto_shipped.integrations.feishu import (
    CorrectionFeedbackRecord,
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
        official_publish_log_sheet_id="publish-log",
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

    def test_identity_falls_back_to_whoami_when_auth_command_is_unavailable(self) -> None:
        commands: list[list[str]] = []

        def runner(command: Sequence[str]) -> dict[str, Any]:
            commands.append(list(command))
            if list(command[1:3]) == ["auth", "status"]:
                raise LarkCliGatewayError(
                    'lark-cli执行失败（1）：Error: unknown command "auth" for "lark-cli"'
                )
            if command[1] == "whoami":
                return {
                    "identity": "user",
                    "available": True,
                    "tokenStatus": "ready",
                    "onBehalfOf": {
                        "openId": "ou-legacy-user",
                        "userName": "旧版CLI用户",
                    },
                }
            if "+csv-get" in command:
                return FakeLarkRunner._csv_payload(
                    [(1, ["key", "value"]), (2, ["mapping_revision", "23"])]
                )
            raise AssertionError(f"unexpected command: {command}")

        client = gateway(runner)  # type: ignore[arg-type]

        identity = client.get_current_identity()

        self.assertEqual(identity.user_id, "ou-legacy-user")
        self.assertEqual(identity.identity_type, "user")
        self.assertEqual(identity.display_name, "旧版CLI用户")
        self.assertIn(
            ["lark-cli", "whoami", "--as", "user"],
            commands,
        )
        self.assertTrue(
            any(
                "+csv-get" in command and "A1:B2" in command
                for command in commands
            )
        )

    def test_identity_fallback_does_not_accept_user_without_stable_open_id(self) -> None:
        def runner(command: Sequence[str]) -> dict[str, Any]:
            if list(command[1:3]) == ["auth", "status"]:
                raise LarkCliGatewayError(
                    'lark-cli执行失败（1）：Error: unknown command "auth" for "lark-cli"'
                )
            if command[1] == "whoami":
                return {
                    "identity": "user",
                    "available": True,
                    "tokenStatus": "ready",
                    "onBehalfOf": {"userName": "缺少ID"},
                }
            raise AssertionError(f"unexpected command: {command}")

        identity = gateway(runner).get_current_identity()  # type: ignore[arg-type]

        self.assertEqual(identity.identity_type, "unavailable")
        self.assertEqual(identity.user_id, "")

    def test_identity_does_not_hide_non_compatibility_auth_failures(self) -> None:
        def runner(command: Sequence[str]) -> dict[str, Any]:
            if list(command[1:3]) == ["auth", "status"]:
                raise LarkCliGatewayError("lark-cli执行失败（1）：token verification timeout")
            raise AssertionError(f"unexpected fallback command: {command}")

        with self.assertRaisesRegex(LarkCliGatewayError, "verification timeout"):
            gateway(runner).get_current_identity()  # type: ignore[arg-type]

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

    def test_enabled_item_expansion_table_requires_stable_sheet_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "adapter_status": {"item_expansion_rule_table": "ready"},
                        "official_repository": {
                            "spreadsheet_token": "official",
                            "mapping_sheet_id": "mapping",
                            "metadata_sheet_id": "meta",
                            "publish_log_sheet_id": "log",
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

    def test_annotated_row_prefixes_recover_when_response_has_extra_blank_index(self) -> None:
        def runner(command: Sequence[str]) -> dict[str, Any]:
            if "+csv-get" not in command:
                raise AssertionError(f"unexpected command: {command}")
            return {
                "ok": True,
                "data": {
                    "annotated_csv": "[row=7] value\n[row=8] ",
                    "row_indices": [7, 8, 9],
                },
            }

        client = gateway(runner)  # type: ignore[arg-type]
        self.assertEqual(
            client._read_rows(
                spreadsheet_token="pending-token",
                sheet_id="personal",
                cell_range="A7:A5000",
            ),
            [(7, ["value"]), (8, [""])],
        )

    def test_final_one_column_blank_row_is_not_dropped(self) -> None:
        def runner(command: Sequence[str]) -> dict[str, Any]:
            if "+csv-get" not in command:
                raise AssertionError(f"unexpected command: {command}")
            return {
                "ok": True,
                "data": {
                    "annotated_csv": "[row=7] value\n[row=8] ",
                    "row_indices": [7, 8],
                },
            }

        client = gateway(runner)  # type: ignore[arg-type]
        self.assertEqual(
            client._read_rows(
                spreadsheet_token="pending-token",
                sheet_id="personal",
                cell_range="A7:A200",
            ),
            [(7, ["value"]), (8, [""])],
        )

    def test_feedback_record_is_appended_to_dedicated_sheet(self) -> None:
        stored: dict[int, list[str]] = {}

        def runner(command: Sequence[str]) -> dict[str, Any]:
            if "+csv-get" in command:
                rows = [
                    (index, stored.get(index, [""] * 20))
                    for index in range(2, 5)
                ]
                return FakeLarkRunner._csv_payload(rows)
            if "+table-put" in command:
                payload = json.loads(FakeLarkRunner._arg(command, "--sheets"))
                values = payload["sheets"][0]["data"][0]
                row_number = max(stored, default=1) + 1
                stored[row_number] = values
                return {"ok": True, "data": {"revision": 1}}
            raise AssertionError(f"unexpected command: {command}")

        client = LarkCliFeishuSheetGateway(
            pending_spreadsheet_token="pending-token",
            route_sheet_id="route",
            template_sheet_id="template",
            official_spreadsheet_token="official-token",
            official_mapping_sheet_id="mapping",
            official_metadata_sheet_id="meta",
            official_publish_log_sheet_id="publish-log",
            feedback_spreadsheet_token="feedback-token",
            feedback_sheet_id="feedback-sheet",
            feedback_sheet_title="Sheet1",
            runner=runner,
        )
        record = CorrectionFeedbackRecord(
            feedback_id="feedback-1",
            issue_key="issue-1",
            feedback_type="mapping_corrected",
            company_id="sam",
            source_profile_id="sam_order_v1",
            source_field="商品编码+规格编码",
            source_value="84292384",
            source_spec="SWSH8096",
            system_result="旧结果",
            corrected_result="新结果",
            reuse_scope="long_term",
            related_proposal_id="",
            evidence="用户确认",
            skill_version="1.0.0",
            submitted_by="ou-user",
            submitted_at="2026-10-12T00:00:00+00:00",
            submission_operation_id="feedback-op",
        )
        client.append_correction_feedback(record)
        self.assertEqual(client.list_correction_feedback(), [record])


if __name__ == "__main__":
    unittest.main()
