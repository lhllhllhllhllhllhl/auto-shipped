from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence


DAILY_SEQUENCE_SCHEMA_VERSION = "shangyu-daily-sequence-ledger/1.0"
DAILY_SEQUENCE_HEADERS = (
    "allocation_id",
    "allocation_key",
    "business_date",
    "purpose",
    "prefix",
    "namespace_key",
    "company_id",
    "source_profile_id",
    "source_sha256",
    "sequence_start",
    "sequence_end",
    "sequence_count",
    "sequence_width",
    "operation_id",
    "allocated_by",
    "allocated_at",
    "status",
    "schema_version",
    "source_partition",
    "stamp_format",
    "sequence_separator",
)
_ROW_PREFIX = re.compile(r"(?m)^\[row=(\d+)\] ")


class DailySequenceLedgerError(RuntimeError):
    """Raised when the central daily-sequence ledger cannot be trusted."""


@dataclass(frozen=True, slots=True)
class DailySequenceLedgerRecord:
    allocation_id: str
    allocation_key: str
    business_date: str
    purpose: str
    prefix: str
    namespace_key: str
    company_id: str
    source_profile_id: str
    source_sha256: str
    sequence_start: int
    sequence_end: int
    sequence_count: int
    sequence_width: int
    operation_id: str
    allocated_by: str
    allocated_at: str
    status: str = "allocated"
    schema_version: str = DAILY_SEQUENCE_SCHEMA_VERSION
    source_partition: str = ""
    stamp_format: str = "%Y%m%d"
    sequence_separator: str = ""
    sheet_name: str = ""
    row_number: int = 0

    def to_row(self) -> list[str | int]:
        return [
            self.allocation_id,
            self.allocation_key,
            self.business_date,
            self.purpose,
            self.prefix,
            self.namespace_key,
            self.company_id,
            self.source_profile_id,
            self.source_sha256,
            self.sequence_start,
            self.sequence_end,
            self.sequence_count,
            self.sequence_width,
            self.operation_id,
            self.allocated_by,
            self.allocated_at,
            self.status,
            self.schema_version,
            self.source_partition,
            self.stamp_format,
            self.sequence_separator,
        ]

    @classmethod
    def from_row(
        cls,
        values: Sequence[str],
        *,
        sheet_name: str,
        row_number: int,
    ) -> "DailySequenceLedgerRecord":
        padded = [str(value) for value in values] + [""] * (
            len(DAILY_SEQUENCE_HEADERS) - len(values)
        )
        try:
            record = cls(
                allocation_id=padded[0].strip(),
                allocation_key=padded[1].strip(),
                business_date=padded[2].strip(),
                purpose=padded[3].strip(),
                prefix=padded[4].strip(),
                namespace_key=padded[5].strip(),
                company_id=padded[6].strip(),
                source_profile_id=padded[7].strip(),
                source_sha256=padded[8].strip(),
                sequence_start=int(padded[9]),
                sequence_end=int(padded[10]),
                sequence_count=int(padded[11]),
                sequence_width=int(padded[12]),
                operation_id=padded[13].strip(),
                allocated_by=padded[14].strip(),
                allocated_at=padded[15].strip(),
                status=padded[16].strip(),
                schema_version=padded[17].strip(),
                source_partition=padded[18].strip(),
                stamp_format=padded[19].strip(),
                sequence_separator=padded[20],
                sheet_name=sheet_name,
                row_number=row_number,
            )
        except (TypeError, ValueError) as exc:
            raise DailySequenceLedgerError(
                f"飞书日序号台账{sheet_name}!{row_number}的数值字段无效。"
            ) from exc
        record.validate()
        return record

    def validate(self) -> None:
        if self.schema_version != DAILY_SEQUENCE_SCHEMA_VERSION:
            raise DailySequenceLedgerError(
                f"飞书日序号台账版本无法识别：{self.schema_version!r}。"
            )
        if not re.fullmatch(r"\d{8}", self.business_date):
            raise DailySequenceLedgerError("飞书日序号台账业务日期格式无效。")
        if not all(
            (
                self.allocation_id,
                self.allocation_key,
                self.purpose,
                self.prefix,
                self.namespace_key,
                self.source_profile_id,
                self.source_sha256,
                self.operation_id,
                self.allocated_by,
                self.allocated_at,
                self.status,
            )
        ):
            raise DailySequenceLedgerError("飞书日序号台账存在必填字段空值。")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise DailySequenceLedgerError("飞书日序号台账来源SHA-256无效。")
        if not re.fullmatch(r"[0-9a-f]{20}", self.allocation_id):
            raise DailySequenceLedgerError("飞书日序号台账分配ID无效。")
        if not re.fullmatch(r"[0-9a-f]{64}", self.allocation_key):
            raise DailySequenceLedgerError("飞书日序号台账幂等键无效。")
        if not re.fullmatch(r"[0-9a-f]{32}", self.operation_id):
            raise DailySequenceLedgerError("飞书日序号台账操作ID无效。")
        if not re.fullmatch(r"[0-9A-Za-z_-]{1,24}", self.prefix):
            raise DailySequenceLedgerError("飞书日序号台账前缀无效。")
        if self.namespace_key != (
            f"{self.purpose}|{self.prefix}|{self.business_date}"
        ):
            raise DailySequenceLedgerError("飞书日序号台账命名空间与字段不一致。")
        if self.status != "allocated":
            raise DailySequenceLedgerError("飞书日序号台账存在未知状态。")
        if self.sequence_start < 1 or self.sequence_count < 1:
            raise DailySequenceLedgerError("飞书日序号台账序号范围无效。")
        if self.sequence_end != self.sequence_start + self.sequence_count - 1:
            raise DailySequenceLedgerError("飞书日序号台账序号起止与数量不一致。")
        if self.sequence_width < 1:
            raise DailySequenceLedgerError("飞书日序号台账最小序号位数无效。")
        if self.stamp_format not in {"%Y%m%d", "%y%m%d"}:
            raise DailySequenceLedgerError("飞书日序号台账日期格式无效。")
        if not re.fullmatch(r"[0-9A-Za-z_-]{0,24}", self.source_partition):
            raise DailySequenceLedgerError("飞书日序号台账来源分区格式无效。")
        if not re.fullmatch(r"[_-]?", self.sequence_separator):
            raise DailySequenceLedgerError("飞书日序号台账序号分隔符无效。")


class DailySequenceLedgerGateway(Protocol):
    def current_user_id(self) -> str: ...

    def list_records(self) -> list[DailySequenceLedgerRecord]: ...

    def append_record(self, record: DailySequenceLedgerRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class DailySequenceLedgerConfig:
    spreadsheet_token: str
    annual_sheet_title_pattern: str
    annual_sheet_row_count: int
    annual_sheet_column_count: int
    auto_provision_annual_sheet: bool

    @classmethod
    def load(cls, path: str | Path) -> "DailySequenceLedgerConfig":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DailySequenceLedgerError(
                f"无法读取飞书日序号配置：{exc}"
            ) from exc
        columns = tuple(str(value) for value in payload.get("columns") or [])
        if columns != DAILY_SEQUENCE_HEADERS:
            raise DailySequenceLedgerError("飞书日序号配置列定义与程序Schema不一致。")
        config = cls(
            spreadsheet_token=str(payload.get("spreadsheet_token") or "").strip(),
            annual_sheet_title_pattern=str(
                payload.get("annual_sheet_title_pattern") or ""
            ).strip(),
            annual_sheet_row_count=int(payload.get("annual_sheet_row_count") or 0),
            annual_sheet_column_count=int(
                payload.get("annual_sheet_column_count") or 0
            ),
            auto_provision_annual_sheet=(
                payload.get("auto_provision_annual_sheet") is True
            ),
        )
        if (
            not config.spreadsheet_token
            or "{year}" not in config.annual_sheet_title_pattern
            or config.annual_sheet_row_count < 2
            or config.annual_sheet_column_count != len(DAILY_SEQUENCE_HEADERS)
        ):
            raise DailySequenceLedgerError("飞书日序号配置不完整或无效。")
        return config


class LarkCliDailySequenceLedgerGateway:
    """Central append-only daily sequence ledger using the user's Feishu identity."""

    def __init__(
        self,
        config: DailySequenceLedgerConfig,
        *,
        executable: str = "lark-cli",
    ) -> None:
        self.config = config
        self.executable = executable

    @classmethod
    def from_config(
        cls,
        path: str | Path,
        *,
        executable: str = "lark-cli",
    ) -> "LarkCliDailySequenceLedgerGateway":
        return cls(DailySequenceLedgerConfig.load(path), executable=executable)

    def _run(self, *args: str) -> dict[str, Any]:
        executable = shutil.which(self.executable)
        if executable is None:
            raise DailySequenceLedgerError(
                "未找到lark-cli，无法从飞书中央台账分配新日序号。"
            )
        environment = dict(os.environ)
        environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise DailySequenceLedgerError(
                f"lark-cli执行失败（{completed.returncode}）：{message}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DailySequenceLedgerError("lark-cli未返回有效JSON。") from exc
        if not isinstance(payload, dict):
            raise DailySequenceLedgerError("lark-cli返回值不是对象。")
        if payload.get("ok") is False:
            raise DailySequenceLedgerError(str(payload.get("error") or payload))
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def current_user_id(self) -> str:
        data = self._run("whoami", "--as", "user")
        delegated = data.get("onBehalfOf") or data.get("on_behalf_of") or {}
        user_id = str(
            delegated.get("openId") or delegated.get("open_id") or ""
        ).strip()
        if (
            str(data.get("identity") or "").strip().lower() != "user"
            or data.get("available") is not True
            or str(data.get("tokenStatus") or "").strip().lower() != "ready"
            or not user_id
        ):
            raise DailySequenceLedgerError(
                "飞书用户身份不可用，不能用机器人或未知身份占用日序号。"
            )
        return user_id

    def _workbook_info(self) -> dict[str, Any]:
        return self._run(
            "sheets",
            "+workbook-info",
            "--spreadsheet-token",
            self.config.spreadsheet_token,
            "--as",
            "user",
            "--json",
        )

    def _read_rows(
        self,
        *,
        sheet_id: str,
        cell_range: str,
    ) -> list[tuple[int, list[str]]]:
        data = self._run(
            "sheets",
            "+csv-get",
            "--spreadsheet-token",
            self.config.spreadsheet_token,
            "--sheet-id",
            sheet_id,
            "--range",
            cell_range,
            "--as",
            "user",
            "--json",
        )
        annotated = str(data.get("annotated_csv") or "")
        if not annotated:
            return []
        row_indices = [int(value) for value in _ROW_PREFIX.findall(annotated)]
        rows = list(csv.reader(io.StringIO(_ROW_PREFIX.sub("", annotated))))
        if (
            len(row_indices) == len(rows) + 1
            and annotated.rstrip("\n").endswith(f"[row={row_indices[-1]}] ")
        ):
            rows.append([""])
        if len(row_indices) != len(rows):
            raise DailySequenceLedgerError("飞书日序号台账行号与内容数量不一致。")
        return list(zip(row_indices, rows))

    def _sheet_title(self, year: int) -> str:
        return self.config.annual_sheet_title_pattern.format(year=year)

    def _find_annual_sheets(self) -> list[tuple[str, str]]:
        data = self._workbook_info()
        prefix, suffix = self.config.annual_sheet_title_pattern.split("{year}", 1)
        pattern = re.compile(
            rf"^{re.escape(prefix)}(?P<year>\d{{4}}){re.escape(suffix)}$"
        )
        sheets: list[tuple[str, str]] = []
        for sheet in data.get("sheets") or []:
            title = str(sheet.get("sheet_name") or "")
            if pattern.fullmatch(title):
                sheets.append((str(sheet.get("sheet_id") or ""), title))
        return sheets

    def _verify_header(self, sheet_id: str, sheet_name: str) -> None:
        rows = self._read_rows(sheet_id=sheet_id, cell_range="A1:U1")
        values = rows[0][1] if rows else []
        if tuple(values[: len(DAILY_SEQUENCE_HEADERS)]) != DAILY_SEQUENCE_HEADERS:
            raise DailySequenceLedgerError(
                f"飞书日序号台账{sheet_name}表头与程序Schema不一致。"
            )

    def _ensure_annual_sheet(self, year: int) -> tuple[str, str]:
        title = self._sheet_title(year)
        for sheet_id, sheet_name in self._find_annual_sheets():
            if sheet_name == title:
                self._verify_header(sheet_id, sheet_name)
                return sheet_id, sheet_name
        if not self.config.auto_provision_annual_sheet:
            raise DailySequenceLedgerError(f"飞书缺少年度占号页：{title}。")
        created = self._run(
            "sheets",
            "+sheet-create",
            "--spreadsheet-token",
            self.config.spreadsheet_token,
            "--title",
            title,
            "--row-count",
            str(self.config.annual_sheet_row_count),
            "--col-count",
            str(self.config.annual_sheet_column_count),
            "--as",
            "user",
            "--json",
        )
        sheet_id = str(created.get("sheet_id") or "").strip()
        if not sheet_id:
            raise DailySequenceLedgerError("飞书创建年度占号页后未返回sheet_id。")
        header_cells = [
            {
                "value": header,
                "cell_styles": {
                    "background_color": "#4472C4",
                    "font_color": "#FFFFFF",
                    "font_weight": "bold",
                    "horizontal_alignment": "center",
                    "vertical_alignment": "middle",
                    "number_format": "@",
                },
            }
            for header in DAILY_SEQUENCE_HEADERS
        ]
        self._run(
            "sheets",
            "+cells-set",
            "--spreadsheet-token",
            self.config.spreadsheet_token,
            "--sheet-id",
            sheet_id,
            "--range",
            "A1:U1",
            "--cells",
            json.dumps([header_cells], ensure_ascii=False, separators=(",", ":")),
            "--as",
            "user",
            "--json",
        )
        self._run(
            "sheets",
            "+dim-freeze",
            "--spreadsheet-token",
            self.config.spreadsheet_token,
            "--sheet-id",
            sheet_id,
            "--rows",
            "1",
            "--cols",
            "0",
            "--as",
            "user",
            "--json",
        )
        self._verify_header(sheet_id, title)
        return sheet_id, title

    def list_records(self) -> list[DailySequenceLedgerRecord]:
        records: list[DailySequenceLedgerRecord] = []
        for sheet_id, sheet_name in self._find_annual_sheets():
            self._verify_header(sheet_id, sheet_name)
            for row_number, values in self._read_rows(
                sheet_id=sheet_id,
                cell_range=f"A2:U{self.config.annual_sheet_row_count}",
            ):
                if not values or not str(values[0]).strip():
                    continue
                records.append(
                    DailySequenceLedgerRecord.from_row(
                        values,
                        sheet_name=sheet_name,
                        row_number=row_number,
                    )
                )
        return records

    def append_record(self, record: DailySequenceLedgerRecord) -> None:
        record.validate()
        sheet_id, sheet_name = self._ensure_annual_sheet(
            int(record.business_date[:4])
        )
        rows = self._read_rows(
            sheet_id=sheet_id,
            cell_range=f"A2:U{self.config.annual_sheet_row_count}",
        )
        row_number = next(
            (
                number
                for number, values in rows
                if not values or not str(values[0]).strip()
            ),
            self.config.annual_sheet_row_count + 1,
        )
        if row_number > self.config.annual_sheet_row_count:
            raise DailySequenceLedgerError(f"飞书年度占号页{sheet_name}已满。")
        cells = [[{"value": value} for value in record.to_row()]]
        self._run(
            "sheets",
            "+cells-set",
            "--spreadsheet-token",
            self.config.spreadsheet_token,
            "--sheet-id",
            sheet_id,
            "--range",
            f"A{row_number}:U{row_number}",
            "--cells",
            json.dumps(cells, ensure_ascii=False, separators=(",", ":")),
            "--allow-overwrite=false",
            "--as",
            "user",
            "--json",
        )
        readback = self._read_rows(
            sheet_id=sheet_id,
            cell_range=f"A{row_number}:U{row_number}",
        )
        if len(readback) != 1:
            raise DailySequenceLedgerError("飞书日序号写入后无法回读唯一记录。")
        stored = DailySequenceLedgerRecord.from_row(
            readback[0][1],
            sheet_name=sheet_name,
            row_number=row_number,
        )
        if stored.to_row() != record.to_row():
            raise DailySequenceLedgerError("飞书日序号写入后回读内容不一致。")
