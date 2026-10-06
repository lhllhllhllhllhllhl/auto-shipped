from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable, Sequence

from .pending_mappings import (
    FeishuIdentity,
    ManagedPendingSheet,
    PendingMappingProposal,
    PendingSheetMetadata,
    UserSheetRoute,
)


CommandRunner = Callable[[Sequence[str]], dict[str, Any]]
_ROW_PREFIX = re.compile(r"(?m)^\[row=\d+\] ")


class LarkCliGatewayError(RuntimeError):
    """Raised when the lark-cli adapter cannot prove a read or write result."""


class LarkCliFeishuSheetGateway:
    """FeishuSheetGateway backed by the user's authenticated lark-cli identity."""

    def __init__(
        self,
        *,
        pending_spreadsheet_token: str,
        route_sheet_id: str,
        template_sheet_id: str,
        official_spreadsheet_token: str,
        official_mapping_sheet_id: str,
        official_metadata_sheet_id: str,
        executable: str = "lark-cli",
        runner: CommandRunner | None = None,
    ) -> None:
        self.pending_spreadsheet_token = self._required(
            pending_spreadsheet_token,
            "pending_spreadsheet_token",
        )
        self.route_sheet_id = self._required(route_sheet_id, "route_sheet_id")
        self.template_sheet_id = self._required(
            template_sheet_id,
            "template_sheet_id",
        )
        self.official_spreadsheet_token = self._required(
            official_spreadsheet_token,
            "official_spreadsheet_token",
        )
        self.official_mapping_sheet_id = self._required(
            official_mapping_sheet_id,
            "official_mapping_sheet_id",
        )
        self.official_metadata_sheet_id = self._required(
            official_metadata_sheet_id,
            "official_metadata_sheet_id",
        )
        self.executable = executable
        self._runner = runner or self._subprocess_runner

    @classmethod
    def from_config(
        cls,
        path: str | Path,
        *,
        executable: str = "lark-cli",
        runner: CommandRunner | None = None,
    ) -> "LarkCliFeishuSheetGateway":
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        official = config.get("official_repository") or {}
        pending = config.get("pending_repository") or {}
        return cls(
            pending_spreadsheet_token=pending.get("spreadsheet_token"),
            route_sheet_id=pending.get("route_sheet_id"),
            template_sheet_id=pending.get("template_sheet_id"),
            official_spreadsheet_token=official.get("spreadsheet_token"),
            official_mapping_sheet_id=official.get("mapping_sheet_id"),
            official_metadata_sheet_id=official.get("metadata_sheet_id"),
            executable=executable,
            runner=runner,
        )

    @staticmethod
    def _required(value: Any, field_name: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise LarkCliGatewayError(f"飞书配置缺少{field_name}")
        return cleaned

    def _subprocess_runner(self, command: Sequence[str]) -> dict[str, Any]:
        executable = shutil.which(command[0])
        if executable is None:
            raise LarkCliGatewayError(
                "未找到lark-cli；请安装并用飞书用户身份登录后重试。"
            )
        env = dict(os.environ)
        env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        completed = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise LarkCliGatewayError(
                f"lark-cli执行失败（{completed.returncode}）：{message}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LarkCliGatewayError("lark-cli未返回有效JSON。") from exc
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise LarkCliGatewayError(str(payload.get("error") or payload))
        return payload

    def _run(self, *args: str) -> dict[str, Any]:
        payload = self._runner([self.executable, *args])
        if not isinstance(payload, dict):
            raise LarkCliGatewayError("lark-cli适配器返回值不是对象。")
        if payload.get("ok") is False:
            raise LarkCliGatewayError(str(payload.get("error") or payload))
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def _read_rows(
        self,
        *,
        spreadsheet_token: str,
        sheet_id: str,
        cell_range: str,
    ) -> list[tuple[int, list[str]]]:
        data = self._run(
            "sheets",
            "+csv-get",
            "--as",
            "user",
            "--spreadsheet-token",
            spreadsheet_token,
            "--sheet-id",
            sheet_id,
            "--range",
            cell_range,
            "--json",
        )
        annotated = str(data.get("annotated_csv") or "")
        if not annotated:
            return []
        plain_csv = _ROW_PREFIX.sub("", annotated)
        rows = list(csv.reader(io.StringIO(plain_csv)))
        row_indices = data.get("row_indices") or []
        if len(rows) != len(row_indices):
            raise LarkCliGatewayError(
                f"飞书CSV行号与内容数量不一致：{len(row_indices)} != {len(rows)}"
            )
        return [(int(index), [str(value) for value in row]) for index, row in zip(row_indices, rows)]

    def _write_row(
        self,
        *,
        spreadsheet_token: str,
        sheet_id: str,
        row_number: int,
        end_column: str,
        values: Sequence[str],
    ) -> None:
        cells = [[{"value": str(value)} for value in values]]
        self._run(
            "sheets",
            "+cells-set",
            "--as",
            "user",
            "--spreadsheet-token",
            spreadsheet_token,
            "--sheet-id",
            sheet_id,
            "--range",
            f"A{row_number}:{end_column}{row_number}",
            "--cells",
            json.dumps(cells, ensure_ascii=False, separators=(",", ":")),
            "--json",
        )

    @staticmethod
    def _first_empty_row(rows: list[tuple[int, list[str]]], start: int) -> int:
        for row_number, values in rows:
            if not values or not str(values[0]).strip():
                return row_number
        if rows:
            return max(row_number for row_number, _ in rows) + 1
        return start

    def get_current_identity(self) -> FeishuIdentity:
        data = self._run("auth", "status", "--json", "--verify")
        user = (data.get("identities") or {}).get("user") or {}
        if not user.get("available") or user.get("status") != "ready":
            return FeishuIdentity(user_id="", identity_type="unavailable")
        return FeishuIdentity(
            user_id=str(user.get("openId") or ""),
            identity_type="user",
            display_name=str(user.get("userName") or ""),
        )

    def list_user_routes(self) -> list[UserSheetRoute]:
        rows = self._read_rows(
            spreadsheet_token=self.pending_spreadsheet_token,
            sheet_id=self.route_sheet_id,
            cell_range="A2:J2000",
        )
        routes: list[UserSheetRoute] = []
        for _, values in rows:
            padded = values + [""] * (10 - len(values))
            if not padded[0].strip():
                continue
            try:
                conflicts = tuple(json.loads(padded[9] or "[]"))
            except (json.JSONDecodeError, TypeError):
                conflicts = tuple(
                    value for value in padded[9].split("|") if value.strip()
                )
            routes.append(
                UserSheetRoute(
                    feishu_user_id=padded[0],
                    sheet_id=padded[1],
                    sheet_title=padded[2],
                    status=padded[3],
                    schema_version=padded[4],
                    provision_key=padded[5],
                    provision_operation_id=padded[6],
                    created_at=padded[7],
                    last_used_at=padded[8],
                    conflict_sheet_ids=conflicts,
                )
            )
        return routes

    def upsert_user_route(self, route: UserSheetRoute) -> None:
        rows = self._read_rows(
            spreadsheet_token=self.pending_spreadsheet_token,
            sheet_id=self.route_sheet_id,
            cell_range="A2:J2000",
        )
        matching = [
            row_number
            for row_number, values in rows
            if values and values[0].strip() == route.feishu_user_id
        ]
        if len(matching) > 1:
            raise LarkCliGatewayError("用户路由表存在重复user_id，拒绝覆盖。")
        target_row = matching[0] if matching else self._first_empty_row(rows, 2)
        self._write_row(
            spreadsheet_token=self.pending_spreadsheet_token,
            sheet_id=self.route_sheet_id,
            row_number=target_row,
            end_column="J",
            values=[
                route.feishu_user_id,
                route.sheet_id,
                route.sheet_title,
                route.status,
                route.schema_version,
                route.provision_key,
                route.provision_operation_id,
                route.created_at,
                route.last_used_at,
                json.dumps(list(route.conflict_sheet_ids), ensure_ascii=False),
            ],
        )
        readback = [
            item for item in self.list_user_routes()
            if item.feishu_user_id == route.feishu_user_id
        ]
        if readback != [route]:
            raise LarkCliGatewayError("用户路由写入后回读不一致。")

    def _workbook_sheets(self) -> list[dict[str, Any]]:
        data = self._run(
            "sheets",
            "+workbook-info",
            "--as",
            "user",
            "--spreadsheet-token",
            self.pending_spreadsheet_token,
            "--json",
        )
        sheets = data.get("sheets") or []
        if not isinstance(sheets, list):
            raise LarkCliGatewayError("无法读取待确认工作簿Sheet列表。")
        return [item for item in sheets if isinstance(item, dict)]

    def _read_sheet_metadata(self, sheet_id: str) -> PendingSheetMetadata | None:
        rows = self._read_rows(
            spreadsheet_token=self.pending_spreadsheet_token,
            sheet_id=sheet_id,
            cell_range="A1:B5",
        )
        values = {
            row[0].strip(): row[1].strip()
            for _, row in rows
            if len(row) >= 2 and row[0].strip()
        }
        required = {
            "owner_user_id",
            "schema_version",
            "provision_key",
            "created_by_operation",
            "status",
        }
        if not required.issubset(values) or not values["owner_user_id"]:
            return None
        return PendingSheetMetadata(
            owner_user_id=values["owner_user_id"],
            schema_version=values["schema_version"],
            provision_key=values["provision_key"],
            created_by_operation=values["created_by_operation"],
            status=values["status"],
        )

    def list_managed_pending_sheets(self) -> list[ManagedPendingSheet]:
        excluded = {self.route_sheet_id, self.template_sheet_id}
        managed: list[ManagedPendingSheet] = []
        for sheet in self._workbook_sheets():
            sheet_id = str(sheet.get("sheet_id") or "")
            if not sheet_id or sheet_id in excluded:
                continue
            managed.append(
                ManagedPendingSheet(
                    sheet_id=sheet_id,
                    title=str(sheet.get("sheet_name") or ""),
                    metadata=self._read_sheet_metadata(sheet_id),
                )
            )
        return managed

    def create_pending_sheet_from_template(
        self,
        *,
        title: str,
        template_sheet_id: str,
        provision_operation_id: str,
    ) -> ManagedPendingSheet:
        if template_sheet_id != self.template_sheet_id:
            raise LarkCliGatewayError("请求的模板Sheet ID与绑定配置不一致。")
        data = self._run(
            "sheets",
            "+sheet-copy",
            "--as",
            "user",
            "--spreadsheet-token",
            self.pending_spreadsheet_token,
            "--sheet-id",
            template_sheet_id,
            "--title",
            title,
            "--json",
        )
        sheet_id = self._required(data.get("sheet_id"), "created_sheet_id")
        return ManagedPendingSheet(sheet_id=sheet_id, title=title)

    def write_pending_sheet_metadata(
        self,
        sheet_id: str,
        metadata: PendingSheetMetadata,
    ) -> None:
        cells = [
            [{"value": "owner_user_id"}, {"value": metadata.owner_user_id}],
            [{"value": "schema_version"}, {"value": metadata.schema_version}],
            [{"value": "provision_key"}, {"value": metadata.provision_key}],
            [
                {"value": "created_by_operation"},
                {"value": metadata.created_by_operation},
            ],
            [{"value": "status"}, {"value": metadata.status}],
        ]
        self._run(
            "sheets",
            "+cells-set",
            "--as",
            "user",
            "--spreadsheet-token",
            self.pending_spreadsheet_token,
            "--sheet-id",
            sheet_id,
            "--range",
            "A1:B5",
            "--cells",
            json.dumps(cells, ensure_ascii=False, separators=(",", ":")),
            "--json",
        )
        if self._read_sheet_metadata(sheet_id) != metadata:
            raise LarkCliGatewayError("个人Sheet元数据写入后回读不一致。")

    def list_pending_proposals(self, sheet_id: str) -> list[PendingMappingProposal]:
        rows = self._read_rows(
            spreadsheet_token=self.pending_spreadsheet_token,
            sheet_id=sheet_id,
            cell_range="A7:Q5000",
        )
        field_names = [field.name for field in fields(PendingMappingProposal)]
        proposals: list[PendingMappingProposal] = []
        for _, values in rows:
            padded = values + [""] * (len(field_names) - len(values))
            if not padded[0].strip():
                continue
            proposals.append(
                PendingMappingProposal(
                    **dict(zip(field_names, padded[: len(field_names)]))
                )
            )
        return proposals

    def append_pending_proposal(
        self,
        sheet_id: str,
        proposal: PendingMappingProposal,
    ) -> None:
        rows = self._read_rows(
            spreadsheet_token=self.pending_spreadsheet_token,
            sheet_id=sheet_id,
            cell_range="A7:A5000",
        )
        target_row = self._first_empty_row(rows, 7)
        values = [str(getattr(proposal, field.name)) for field in fields(proposal)]
        self._write_row(
            spreadsheet_token=self.pending_spreadsheet_token,
            sheet_id=sheet_id,
            row_number=target_row,
            end_column="Q",
            values=values,
        )

    def get_official_repository_metadata(self) -> dict[str, str]:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_metadata_sheet_id,
            cell_range="A1:B50",
        )
        return {
            values[0].strip(): values[1].strip()
            for _, values in rows
            if len(values) >= 2 and values[0].strip()
        }
    def get_official_mapping_revision(self) -> str:
        revision = self.get_official_repository_metadata().get(
            "mapping_revision",
            "",
        ).strip()
        if not revision:
            raise LarkCliGatewayError("正式映射库缺少mapping_revision。")
        return revision

    def list_official_mapping_records(self) -> list[dict[str, str]]:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_mapping_sheet_id,
            cell_range="A1:O5000",
        )
        if not rows:
            raise LarkCliGatewayError("正式映射Sheet为空。")
        expected = [
            "mapping_id",
            "mapping_key",
            "company_id",
            "source_profile_id",
            "identifier_type",
            "source_value",
            "source_spec",
            "target_platform",
            "product_code",
            "spec_code",
            "status",
            "mapping_version",
            "confirmed_by",
            "confirmed_at",
            "evidence",
        ]
        header = (rows[0][1] + [""] * len(expected))[: len(expected)]
        if header != expected:
            raise LarkCliGatewayError("正式映射Sheet表头与合同不一致。")
        records: list[dict[str, str]] = []
        for _, values in rows[1:]:
            padded = (values + [""] * len(expected))[: len(expected)]
            if not padded[0].strip():
                continue
            records.append(dict(zip(expected, padded)))
        return records
