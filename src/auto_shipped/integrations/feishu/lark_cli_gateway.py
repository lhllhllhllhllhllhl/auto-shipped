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
from .owner_publisher import (
    MappingPublishLogRecord,
    OfficialMappingRecord,
)
from .manual_store_routes import ManualStoreRouteRecord
from .item_expansion_registry import (
    ITEM_EXPANSION_HEADERS,
    ItemExpansionComponentRecord,
)
from .correction_feedback import CorrectionFeedbackRecord


CommandRunner = Callable[[Sequence[str]], dict[str, Any]]
_ROW_PREFIX = re.compile(r"(?m)^\[row=(\d+)\] ")
OFFICIAL_MAPPING_BASE_HEADERS = (
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
)
OFFICIAL_MAPPING_CANONICAL_HEADERS = (
    "canonical_source_brand",
    "canonical_source_product",
    "canonical_source_spec",
    "canonical_schema_version",
)
OFFICIAL_MAPPING_HEADERS = (
    *OFFICIAL_MAPPING_BASE_HEADERS,
    *OFFICIAL_MAPPING_CANONICAL_HEADERS,
)


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
        official_publish_log_sheet_id: str,
        official_manual_store_sheet_id: str = "",
        official_item_expansion_sheet_id: str = "",
        feedback_spreadsheet_token: str = "",
        feedback_sheet_id: str = "",
        feedback_sheet_title: str = "",
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
        self.official_publish_log_sheet_id = self._required(
            official_publish_log_sheet_id,
            "official_publish_log_sheet_id",
        )
        self.official_manual_store_sheet_id = str(
            official_manual_store_sheet_id or ""
        ).strip()
        self.official_item_expansion_sheet_id = str(
            official_item_expansion_sheet_id or ""
        ).strip()
        self.feedback_spreadsheet_token = str(feedback_spreadsheet_token or "").strip()
        self.feedback_sheet_id = str(feedback_sheet_id or "").strip()
        self.feedback_sheet_title = str(feedback_sheet_title or "").strip()
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
        feedback = config.get("correction_feedback_repository") or {}
        adapter_status = config.get("adapter_status") or {}
        if (
            adapter_status.get("item_expansion_rule_table") == "ready"
            and not str(official.get("item_expansion_sheet_id") or "").strip()
        ):
            raise LarkCliGatewayError(
                "飞书配置声明组合商品规则表已启用，但缺少item_expansion_sheet_id"
            )
        return cls(
            pending_spreadsheet_token=pending.get("spreadsheet_token"),
            route_sheet_id=pending.get("route_sheet_id"),
            template_sheet_id=pending.get("template_sheet_id"),
            official_spreadsheet_token=official.get("spreadsheet_token"),
            official_mapping_sheet_id=official.get("mapping_sheet_id"),
            official_metadata_sheet_id=official.get("metadata_sheet_id"),
            official_publish_log_sheet_id=official.get("publish_log_sheet_id"),
            official_manual_store_sheet_id=official.get("manual_store_sheet_id"),
            official_item_expansion_sheet_id=official.get(
                "item_expansion_sheet_id"
            ),
            feedback_spreadsheet_token=feedback.get("spreadsheet_token"),
            feedback_sheet_id=feedback.get("sheet_id"),
            feedback_sheet_title=feedback.get("sheet_title"),
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
        annotated_row_indices = [int(value) for value in _ROW_PREFIX.findall(annotated)]
        plain_csv = _ROW_PREFIX.sub("", annotated)
        rows = list(csv.reader(io.StringIO(plain_csv)))
        if (
            len(annotated_row_indices) == len(rows) + 1
            and annotated.rstrip("\n").endswith(
                f"[row={annotated_row_indices[-1]}] "
            )
        ):
            # csv.reader drops a final one-column empty record. Feishu still
            # reports its annotated row prefix, so restore that explicit blank.
            rows.append([""])
        response_row_indices = [int(value) for value in data.get("row_indices") or []]
        if len(rows) == len(response_row_indices):
            row_indices = response_row_indices
        elif len(rows) == len(annotated_row_indices):
            # Feishu may report the full requested row index vector while omitting
            # a trailing all-empty row from annotated_csv. The row prefixes remain
            # the authoritative coordinates for every returned logical record.
            row_indices = annotated_row_indices
        else:
            raise LarkCliGatewayError(
                "飞书CSV行号与内容数量不一致："
                f"response={len(response_row_indices)}, "
                f"annotated={len(annotated_row_indices)}, rows={len(rows)}"
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
        try:
            data = self._run("auth", "status", "--json", "--verify")
        except LarkCliGatewayError as auth_error:
            if not self._is_unknown_auth_command(auth_error):
                raise
            return self._get_current_identity_from_whoami(auth_error)
        user = (data.get("identities") or {}).get("user") or {}
        if not user.get("available") or user.get("status") != "ready":
            return FeishuIdentity(user_id="", identity_type="unavailable")
        return FeishuIdentity(
            user_id=str(user.get("openId") or ""),
            identity_type="user",
            display_name=str(user.get("userName") or ""),
        )

    @staticmethod
    def _is_unknown_auth_command(error: LarkCliGatewayError) -> bool:
        message = str(error).lower()
        return "unknown command" in message and "auth" in message

    def _get_current_identity_from_whoami(
        self,
        auth_error: LarkCliGatewayError,
    ) -> FeishuIdentity:
        try:
            data = self._run("whoami", "--as", "user")
        except LarkCliGatewayError as whoami_error:
            raise LarkCliGatewayError(
                "当前lark-cli不支持auth status，whoami兼容身份探针也失败："
                f"auth={auth_error}; whoami={whoami_error}"
            ) from whoami_error

        delegated = data.get("onBehalfOf") or data.get("on_behalf_of") or {}
        identity_type = str(data.get("identity") or "").strip().lower()
        token_status = str(
            data.get("tokenStatus") or data.get("token_status") or ""
        ).strip().lower()
        available = data.get("available") is True
        user_id = str(
            delegated.get("openId") or delegated.get("open_id") or ""
        ).strip()
        if (
            identity_type != "user"
            or not available
            or token_status != "ready"
            or not user_id
        ):
            return FeishuIdentity(user_id="", identity_type="unavailable")

        # Older lark-cli releases can expose whoami without auth status.  Verify
        # the reported user token against the configured workbook before that
        # identity is allowed to participate in a governed write.
        self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_metadata_sheet_id,
            cell_range="A1:B2",
        )
        return FeishuIdentity(
            user_id=user_id,
            identity_type="user",
            display_name=str(
                delegated.get("userName") or delegated.get("user_name") or ""
            ),
        )

    def _require_feedback_binding(self) -> tuple[str, str, str]:
        return (
            self._required(
                self.feedback_spreadsheet_token,
                "correction_feedback_repository.spreadsheet_token",
            ),
            self._required(
                self.feedback_sheet_id,
                "correction_feedback_repository.sheet_id",
            ),
            self._required(
                self.feedback_sheet_title,
                "correction_feedback_repository.sheet_title",
            ),
        )

    def list_correction_feedback(self) -> list[CorrectionFeedbackRecord]:
        spreadsheet_token, sheet_id, _ = self._require_feedback_binding()
        rows = self._read_rows(
            spreadsheet_token=spreadsheet_token,
            sheet_id=sheet_id,
            cell_range="A2:T5000",
        )
        records: list[CorrectionFeedbackRecord] = []
        for _, values in rows:
            padded = values + [""] * (20 - len(values))
            if not padded[0].strip():
                continue
            records.append(
                CorrectionFeedbackRecord(
                    feedback_id=padded[0],
                    issue_key=padded[1],
                    feedback_type=padded[2],
                    company_id=padded[3],
                    source_profile_id=padded[4],
                    source_field=padded[5],
                    source_value=padded[6],
                    source_spec=padded[7],
                    system_result=padded[8],
                    corrected_result=padded[9],
                    reuse_scope=padded[10],
                    related_proposal_id=padded[11],
                    evidence=padded[12],
                    skill_version=padded[13],
                    submitted_by=padded[14],
                    submitted_at=padded[15],
                    submission_operation_id=padded[16],
                    status=padded[17],
                    maintainer_note=padded[18],
                    fixed_version=padded[19],
                )
            )
        return records

    def append_correction_feedback(self, record: CorrectionFeedbackRecord) -> None:
        spreadsheet_token, _, sheet_title = self._require_feedback_binding()
        columns = [field.name for field in fields(CorrectionFeedbackRecord)]
        values = [
            record.feedback_id,
            record.issue_key,
            record.feedback_type,
            record.company_id,
            record.source_profile_id,
            record.source_field,
            record.source_value,
            record.source_spec,
            record.system_result,
            record.corrected_result,
            record.reuse_scope,
            record.related_proposal_id,
            record.evidence,
            record.skill_version,
            record.submitted_by,
            record.submitted_at,
            record.submission_operation_id,
            record.status,
            record.maintainer_note,
            record.fixed_version,
        ]
        self._run(
            "sheets",
            "+table-put",
            "--as",
            "user",
            "--spreadsheet-token",
            spreadsheet_token,
            "--sheets",
            json.dumps(
                {
                    "sheets": [
                        {
                            "name": sheet_title,
                            "mode": "append",
                            "header": False,
                            "columns": columns,
                            "data": [values],
                            "dtypes": {column: "object" for column in columns},
                            "formats": {column: "@" for column in columns},
                        }
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "--json",
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
            cell_range="A7:Q200",
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
            cell_range="A7:A200",
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

    def update_pending_proposal_status(
        self,
        sheet_id: str,
        proposal_id: str,
        status: str,
    ) -> None:
        rows = self._read_rows(
            spreadsheet_token=self.pending_spreadsheet_token,
            sheet_id=sheet_id,
            cell_range="A7:Q200",
        )
        matching = [
            row_number
            for row_number, values in rows
            if values and values[0].strip() == proposal_id
        ]
        if len(matching) != 1:
            raise LarkCliGatewayError(
                f"待确认proposal_id必须唯一，实际{len(matching)}条：{proposal_id}"
            )
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
            f"P{matching[0]}",
            "--cells",
            json.dumps([[{"value": status}]], ensure_ascii=False),
            "--json",
        )
        readback = [
            item
            for item in self.list_pending_proposals(sheet_id)
            if item.proposal_id == proposal_id
        ]
        if len(readback) != 1 or readback[0].proposal_status != status:
            raise LarkCliGatewayError("待确认提案状态写入后回读不一致。")

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

    def update_official_repository_metadata(
        self,
        updates: dict[str, str],
    ) -> None:
        if not updates:
            return
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_metadata_sheet_id,
            cell_range="A1:B50",
        )
        by_key = {
            values[0].strip(): row_number
            for row_number, values in rows
            if values and values[0].strip()
        }
        next_row = self._first_empty_row(rows, 2)
        for key, value in updates.items():
            row_number = by_key.get(key)
            if row_number is None:
                row_number = next_row
                next_row += 1
                by_key[key] = row_number
            self._write_row(
                spreadsheet_token=self.official_spreadsheet_token,
                sheet_id=self.official_metadata_sheet_id,
                row_number=row_number,
                end_column="B",
                values=[key, value],
            )
        metadata = self.get_official_repository_metadata()
        for key, value in updates.items():
            if metadata.get(key, "") != value:
                raise LarkCliGatewayError(
                    f"正式映射库元数据{key}写入后回读不一致。"
                )

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
            cell_range="A1:S5000",
        )
        if not rows:
            raise LarkCliGatewayError("正式映射Sheet为空。")
        raw_header = list(rows[0][1])
        while raw_header and not raw_header[-1].strip():
            raw_header.pop()
        if tuple(raw_header) == OFFICIAL_MAPPING_BASE_HEADERS:
            active_headers = OFFICIAL_MAPPING_BASE_HEADERS
        elif tuple(raw_header) == OFFICIAL_MAPPING_HEADERS:
            active_headers = OFFICIAL_MAPPING_HEADERS
        else:
            raise LarkCliGatewayError("正式映射Sheet表头与合同不一致。")
        records: list[dict[str, str]] = []
        for _, values in rows[1:]:
            padded = (values + [""] * len(OFFICIAL_MAPPING_HEADERS))[
                : len(OFFICIAL_MAPPING_HEADERS)
            ]
            if not padded[0].strip():
                continue
            record = dict(zip(OFFICIAL_MAPPING_HEADERS, padded))
            if active_headers == OFFICIAL_MAPPING_BASE_HEADERS:
                for field in OFFICIAL_MAPPING_CANONICAL_HEADERS:
                    record[field] = ""
            records.append(record)
        return records

    def official_mapping_schema_version(self) -> str:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_mapping_sheet_id,
            cell_range="A1:S1",
        )
        if not rows:
            raise LarkCliGatewayError("正式映射Sheet为空。")
        header = list(rows[0][1])
        while header and not header[-1].strip():
            header.pop()
        if tuple(header) == OFFICIAL_MAPPING_HEADERS:
            return "2.0"
        if tuple(header) == OFFICIAL_MAPPING_BASE_HEADERS:
            return "1.0"
        raise LarkCliGatewayError("正式映射Sheet表头与合同不一致。")

    def migrate_official_mapping_canonical_fields(
        self,
        updates: Sequence[dict[str, str]],
    ) -> None:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_mapping_sheet_id,
            cell_range="A1:S5000",
        )
        if not rows:
            raise LarkCliGatewayError("正式映射Sheet为空。")
        row_by_mapping_id = {
            values[0].strip(): row_number
            for row_number, values in rows[1:]
            if values and values[0].strip()
        }
        update_ids = [str(item.get("mapping_id") or "").strip() for item in updates]
        if not update_ids or len(update_ids) != len(set(update_ids)):
            raise LarkCliGatewayError("规范字段迁移记录为空或mapping_id重复。")
        if set(update_ids) != set(row_by_mapping_id):
            raise LarkCliGatewayError("规范字段迁移记录与正式映射行集合不一致。")

        self._write_row(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_mapping_sheet_id,
            row_number=1,
            end_column="S",
            values=list(OFFICIAL_MAPPING_HEADERS),
        )
        for update in updates:
            mapping_id = str(update.get("mapping_id") or "").strip()
            row_number = row_by_mapping_id[mapping_id]
            self._run(
                "sheets",
                "+cells-set",
                "--as",
                "user",
                "--spreadsheet-token",
                self.official_spreadsheet_token,
                "--sheet-id",
                self.official_mapping_sheet_id,
                "--range",
                f"P{row_number}:S{row_number}",
                "--cells",
                json.dumps(
                    [[
                        {"value": str(update.get(field) or "")}
                        for field in OFFICIAL_MAPPING_CANONICAL_HEADERS
                    ]],
                    ensure_ascii=False,
                ),
                "--json",
            )
        readback = {
            item["mapping_id"]: item
            for item in self.list_official_mapping_records()
        }
        for update in updates:
            actual = readback.get(str(update.get("mapping_id") or "")) or {}
            if any(
                str(actual.get(field) or "") != str(update.get(field) or "")
                for field in OFFICIAL_MAPPING_CANONICAL_HEADERS
            ):
                raise LarkCliGatewayError("正式映射规范字段迁移后回读不一致。")

    def list_manual_store_route_records(self) -> list[dict[str, str]]:
        if not self.official_manual_store_sheet_id:
            return []
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_manual_store_sheet_id,
            cell_range="A1:K5000",
        )
        expected = [
            "route_id",
            "route_key",
            "scope_id",
            "product_code",
            "spec_code",
            "store",
            "status",
            "route_version",
            "confirmed_by",
            "confirmed_at",
            "evidence",
        ]
        if not rows:
            raise LarkCliGatewayError("手工单商品店铺Sheet为空。")
        header = (rows[0][1] + [""] * len(expected))[: len(expected)]
        if header != expected:
            raise LarkCliGatewayError("手工单商品店铺Sheet表头与合同不一致。")
        records: list[dict[str, str]] = []
        for _, values in rows[1:]:
            padded = (values + [""] * len(expected))[: len(expected)]
            if padded[0].strip():
                records.append(dict(zip(expected, padded)))
        return records

    def list_item_expansion_component_records(self) -> list[dict[str, str]]:
        if not self.official_item_expansion_sheet_id:
            return []
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_item_expansion_sheet_id,
            cell_range="A1:R5000",
        )
        expected = list(ITEM_EXPANSION_HEADERS)
        if not rows:
            raise LarkCliGatewayError("组合商品规则Sheet为空。")
        header = (rows[0][1] + [""] * len(expected))[: len(expected)]
        if header != expected:
            raise LarkCliGatewayError("组合商品规则Sheet表头与合同不一致。")
        records: list[dict[str, str]] = []
        for _, values in rows[1:]:
            padded = (values + [""] * len(expected))[: len(expected)]
            if padded[0].strip():
                records.append(dict(zip(expected, padded)))
        return records

    def upsert_item_expansion_component_records(
        self,
        records: Sequence[ItemExpansionComponentRecord],
    ) -> None:
        existing = self.list_item_expansion_component_records()
        by_component_id = {
            item.get("component_id", "").strip(): item
            for item in existing
            if item.get("component_id", "").strip()
        }
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_item_expansion_sheet_id,
            cell_range="A2:A5000",
        )
        next_row = self._first_empty_row(rows, 2)
        for record in records:
            expected = record.to_dict()
            current = by_component_id.get(record.component_id)
            if current is not None:
                if current == expected:
                    continue
                raise LarkCliGatewayError(
                    "组合商品component_id已存在不同内容，拒绝覆盖："
                    f"{record.component_id}"
                )
            self._write_row(
                spreadsheet_token=self.official_spreadsheet_token,
                sheet_id=self.official_item_expansion_sheet_id,
                row_number=next_row,
                end_column="R",
                values=[
                    str(getattr(record, field_name))
                    for field_name in ITEM_EXPANSION_HEADERS
                ],
            )
            next_row += 1
            by_component_id[record.component_id] = expected
        readback = self.list_item_expansion_component_records()
        actual = {
            item.get("component_id", "").strip(): item
            for item in readback
            if item.get("component_id", "").strip()
        }
        for record in records:
            if actual.get(record.component_id) != record.to_dict():
                raise LarkCliGatewayError(
                    f"组合商品组件写入后回读不一致：{record.component_id}"
                )

    def update_item_expansion_rule_status(
        self,
        rule_key: str,
        status: str,
    ) -> None:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_item_expansion_sheet_id,
            cell_range="A2:R5000",
        )
        matching = [
            row_number
            for row_number, values in rows
            if len(values) >= 3 and values[2].strip() == rule_key
        ]
        if not matching:
            raise LarkCliGatewayError(
                f"组合商品rule_key不存在，无法更新状态：{rule_key}"
            )
        for row_number in matching:
            self._run(
                "sheets",
                "+cells-set",
                "--as",
                "user",
                "--spreadsheet-token",
                self.official_spreadsheet_token,
                "--sheet-id",
                self.official_item_expansion_sheet_id,
                "--range",
                f"N{row_number}",
                "--cells",
                json.dumps([[{"value": status}]], ensure_ascii=False),
                "--json",
            )
        readback = [
            item
            for item in self.list_item_expansion_component_records()
            if item.get("rule_key") == rule_key
        ]
        if len(readback) != len(matching) or any(
            item.get("status") != status for item in readback
        ):
            raise LarkCliGatewayError("组合商品规则状态写入后回读不一致。")

    def upsert_manual_store_route_record(
        self,
        record: ManualStoreRouteRecord,
    ) -> None:
        existing = self.list_manual_store_route_records()
        same_key = [
            item
            for item in existing
            if item.get("route_key", "").strip() == record.route_key
        ]
        if same_key:
            if same_key == [record.to_dict()]:
                return
            raise LarkCliGatewayError(
                f"手工单店铺route_key已存在不同内容，拒绝覆盖：{record.route_key}"
            )
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_manual_store_sheet_id,
            cell_range="A2:A5000",
        )
        row_number = self._first_empty_row(rows, 2)
        self._write_row(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_manual_store_sheet_id,
            row_number=row_number,
            end_column="K",
            values=[str(getattr(record, field.name)) for field in fields(record)],
        )
        readback = [
            item
            for item in self.list_manual_store_route_records()
            if item.get("route_key") == record.route_key
        ]
        if readback != [record.to_dict()]:
            raise LarkCliGatewayError("手工单商品店铺关联写入后回读不一致。")

    def update_manual_store_route_status(self, route_key: str, status: str) -> None:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_manual_store_sheet_id,
            cell_range="A2:K5000",
        )
        matching = [
            row_number
            for row_number, values in rows
            if len(values) >= 2 and values[1].strip() == route_key
        ]
        if len(matching) != 1:
            raise LarkCliGatewayError(
                f"手工单店铺route_key必须唯一，实际{len(matching)}条：{route_key}"
            )
        self._run(
            "sheets",
            "+cells-set",
            "--as",
            "user",
            "--spreadsheet-token",
            self.official_spreadsheet_token,
            "--sheet-id",
            self.official_manual_store_sheet_id,
            "--range",
            f"G{matching[0]}",
            "--cells",
            json.dumps([[{"value": status}]], ensure_ascii=False),
            "--json",
        )
        readback = [
            item
            for item in self.list_manual_store_route_records()
            if item.get("route_key") == route_key
        ]
        if len(readback) != 1 or readback[0].get("status") != status:
            raise LarkCliGatewayError("手工单商品店铺关联状态写入后回读不一致。")

    def upsert_official_mapping_record(self, record: OfficialMappingRecord) -> None:
        existing = self.list_official_mapping_records()
        schema_version = self.official_mapping_schema_version()
        active_headers = (
            OFFICIAL_MAPPING_HEADERS
            if schema_version == "2.0"
            else OFFICIAL_MAPPING_BASE_HEADERS
        )
        same_key = [
            item for item in existing
            if item.get("mapping_key", "").strip() == record.mapping_key
        ]
        if same_key:
            expected = {
                key: record.to_dict()[key]
                for key in active_headers
            }
            actual = [
                {key: item.get(key, "") for key in active_headers}
                for item in same_key
            ]
            if actual == [expected]:
                return
            raise LarkCliGatewayError(
                f"正式映射mapping_key已存在不同内容，拒绝覆盖：{record.mapping_key}"
            )
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_mapping_sheet_id,
            cell_range="A2:A5000",
        )
        row_number = self._first_empty_row(rows, 2)
        self._write_row(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_mapping_sheet_id,
            row_number=row_number,
            end_column="S" if schema_version == "2.0" else "O",
            values=[str(getattr(record, name)) for name in active_headers],
        )
        readback = [
            item for item in self.list_official_mapping_records()
            if item.get("mapping_key") == record.mapping_key
        ]
        expected_readback = {
            key: record.to_dict()[key]
            for key in active_headers
        }
        actual_readback = [
            {key: item.get(key, "") for key in active_headers}
            for item in readback
        ]
        if actual_readback != [expected_readback]:
            raise LarkCliGatewayError("正式映射写入后回读不一致。")

    def update_official_mapping_status(self, mapping_key: str, status: str) -> None:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_mapping_sheet_id,
            cell_range="A2:O5000",
        )
        matching = [
            row_number
            for row_number, values in rows
            if len(values) >= 2 and values[1].strip() == mapping_key
        ]
        if len(matching) != 1:
            raise LarkCliGatewayError(
                f"正式映射mapping_key必须唯一，实际{len(matching)}条：{mapping_key}"
            )
        self._run(
            "sheets",
            "+cells-set",
            "--as",
            "user",
            "--spreadsheet-token",
            self.official_spreadsheet_token,
            "--sheet-id",
            self.official_mapping_sheet_id,
            "--range",
            f"K{matching[0]}",
            "--cells",
            json.dumps([[{"value": status}]], ensure_ascii=False),
            "--json",
        )
        readback = [
            item for item in self.list_official_mapping_records()
            if item.get("mapping_key") == mapping_key
        ]
        if len(readback) != 1 or readback[0].get("status") != status:
            raise LarkCliGatewayError("正式映射状态写入后回读不一致。")

    def list_mapping_publish_logs(self) -> list[dict[str, str]]:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_publish_log_sheet_id,
            cell_range="A1:N5000",
        )
        expected = [field.name for field in fields(MappingPublishLogRecord)]
        if not rows:
            raise LarkCliGatewayError("正式映射发布日志Sheet为空。")
        header = (rows[0][1] + [""] * len(expected))[: len(expected)]
        if header != expected:
            raise LarkCliGatewayError("正式映射发布日志表头与合同不一致。")
        records: list[dict[str, str]] = []
        for _, values in rows[1:]:
            padded = (values + [""] * len(expected))[: len(expected)]
            if padded[0].strip():
                records.append(dict(zip(expected, padded)))
        return records

    def upsert_mapping_publish_log(self, record: MappingPublishLogRecord) -> None:
        existing = self.list_mapping_publish_logs()
        same_id = [item for item in existing if item.get("event_id") == record.event_id]
        if same_id:
            if same_id == [record.to_dict()]:
                return
            raise LarkCliGatewayError(
                f"正式映射发布日志event_id已存在不同内容：{record.event_id}"
            )
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_publish_log_sheet_id,
            cell_range="A2:A5000",
        )
        row_number = self._first_empty_row(rows, 2)
        self._write_row(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_publish_log_sheet_id,
            row_number=row_number,
            end_column="N",
            values=[str(getattr(record, field.name)) for field in fields(record)],
        )
        readback = [
            item for item in self.list_mapping_publish_logs()
            if item.get("event_id") == record.event_id
        ]
        if readback != [record.to_dict()]:
            raise LarkCliGatewayError("正式映射发布日志写入后回读不一致。")

    def update_mapping_publish_log_status(self, event_id: str, status: str) -> None:
        rows = self._read_rows(
            spreadsheet_token=self.official_spreadsheet_token,
            sheet_id=self.official_publish_log_sheet_id,
            cell_range="A2:N5000",
        )
        matching = [
            row_number
            for row_number, values in rows
            if values and values[0].strip() == event_id
        ]
        if len(matching) != 1:
            raise LarkCliGatewayError(
                f"发布日志event_id必须唯一，实际{len(matching)}条：{event_id}"
            )
        self._run(
            "sheets",
            "+cells-set",
            "--as",
            "user",
            "--spreadsheet-token",
            self.official_spreadsheet_token,
            "--sheet-id",
            self.official_publish_log_sheet_id,
            "--range",
            f"M{matching[0]}",
            "--cells",
            json.dumps([[{"value": status}]], ensure_ascii=False),
            "--json",
        )
        readback = [
            item for item in self.list_mapping_publish_logs()
            if item.get("event_id") == event_id
        ]
        if len(readback) != 1 or readback[0].get("status") != status:
            raise LarkCliGatewayError("正式映射发布日志状态写入后回读不一致。")
