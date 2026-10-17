from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from auto_shipped.integrations.feishu.daily_sequence_ledger import (
    DAILY_SEQUENCE_SCHEMA_VERSION,
    DailySequenceLedgerError,
    DailySequenceLedgerGateway,
    DailySequenceLedgerRecord,
    LarkCliDailySequenceLedgerGateway,
)


LEDGER_SCHEMA_VERSION = "shangyu-order-number-allocations/1.0"
DAILY_CACHE_SCHEMA_VERSION = "shangyu-daily-sequence-confirmed-cache/1.0"
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_SECONDS = 60.0


class OrderNumberAllocationError(ValueError):
    """Raised when a sequence block cannot be allocated safely."""


@dataclass(frozen=True, slots=True)
class OrderNumberAllocation:
    allocation_id: str
    purpose: str
    profile_id: str
    prefix: str
    batch_minute: str
    stamp_format: str
    sequence_start: int
    sequence_count: int
    sequence_width: int
    reused: bool

    @property
    def sequence_end(self) -> int:
        return self.sequence_start + self.sequence_count - 1

    def to_public_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "sequence_end": self.sequence_end,
            "scope": "single_computer_local_minute_ledger",
        }


@dataclass(frozen=True, slots=True)
class DailySequenceAllocation:
    allocation_id: str
    purpose: str
    profile_id: str
    company_id: str
    prefix: str
    business_date: str
    namespace_key: str
    source_partition: str
    stamp_format: str
    sequence_separator: str
    sequence_start: int
    sequence_count: int
    sequence_width: int
    reused: bool
    authority: str

    @property
    def sequence_end(self) -> int:
        return self.sequence_start + self.sequence_count - 1

    def to_public_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "sequence_end": self.sequence_end,
            "scope": "feishu_central_daily_namespace",
        }


def default_allocation_state_dir() -> Path:
    configured = os.environ.get("SHANGYU_ORDER_NUMBER_STATE_ROOT")
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / ".codex"
        / "runtime"
        / "shangyu-order-converter"
        / "state"
        / "order-number-allocations"
    )


def default_daily_sequence_config_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "config"
        / "integrations"
        / "feishu_daily_sequence_v1.json"
    )


def _allocation_key(
    purpose: str,
    profile_id: str,
    source_sha256: str,
) -> str:
    if purpose == "platform_order_number":
        key_source = f"{profile_id}\0{source_sha256}"
    else:
        key_source = f"{purpose}\0{profile_id}\0{source_sha256}"
    return hashlib.sha256(
        key_source.encode("utf-8")
    ).hexdigest()


def _daily_allocation_key(
    purpose: str,
    profile_id: str,
    source_sha256: str,
    source_partition: str,
) -> str:
    return hashlib.sha256(
        f"{purpose}\0{profile_id}\0{source_sha256}\0{source_partition}".encode(
            "utf-8"
        )
    ).hexdigest()


def _daily_namespace_key(purpose: str, prefix: str, business_date: str) -> str:
    return f"{purpose}|{prefix}|{business_date}"


def _validate_ledger(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise OrderNumberAllocationError("分钟序号占号记录格式无效。")
    if payload.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise OrderNumberAllocationError("分钟序号占号记录版本无法识别。")
    entries = payload.get("allocations")
    if not isinstance(entries, list):
        raise OrderNumberAllocationError("分钟序号占号记录缺少allocations。")
    return payload


def _load_ledger(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"schema_version": LEDGER_SCHEMA_VERSION, "allocations": []}
    try:
        return _validate_ledger(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrderNumberAllocationError(
            f"无法安全读取分钟序号占号记录：{exc}"
        ) from exc


def _write_ledger(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _daily_cache_path(state_dir: str | Path | None) -> Path:
    root = (
        Path(state_dir).expanduser()
        if state_dir is not None
        else default_allocation_state_dir()
    )
    return root / "daily-sequence-confirmed-cache.json"


def _load_daily_cache(path: Path) -> list[DailySequenceLedgerRecord]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrderNumberAllocationError(
            f"无法安全读取日序号确认缓存：{exc}"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != DAILY_CACHE_SCHEMA_VERSION
        or not isinstance(payload.get("allocations"), list)
    ):
        raise OrderNumberAllocationError("日序号确认缓存格式或版本无效。")
    records: list[DailySequenceLedgerRecord] = []
    for index, item in enumerate(payload["allocations"], start=1):
        if not isinstance(item, dict):
            raise OrderNumberAllocationError("日序号确认缓存包含无效记录。")
        try:
            record = DailySequenceLedgerRecord(**item)
            record.validate()
            records.append(record)
        except (TypeError, ValueError, DailySequenceLedgerError) as exc:
            raise OrderNumberAllocationError(
                f"日序号确认缓存第{index}条记录无效：{exc}"
            ) from exc
    return records


def _write_daily_cache(
    path: Path,
    records: list[DailySequenceLedgerRecord],
) -> None:
    payload = {
        "schema_version": DAILY_CACHE_SCHEMA_VERSION,
        "allocations": [
            {
                key: value
                for key, value in asdict(record).items()
                if key not in {"sheet_name", "row_number"}
            }
            for record in records
        ],
    }
    _write_ledger(path, payload)


def _cache_confirmed_record(
    path: Path,
    record: DailySequenceLedgerRecord,
) -> None:
    lock_path = path.with_suffix(".lock")
    with _exclusive_lock(lock_path):
        records = _load_daily_cache(path)
        existing = next(
            (
                item
                for item in records
                if item.allocation_key == record.allocation_key
            ),
            None,
        )
        if existing is not None:
            if existing.to_row() != record.to_row():
                raise OrderNumberAllocationError(
                    "本机确认缓存中的同一来源日序号与飞书回读结果冲突。"
                )
            return
        records.append(record)
        _write_daily_cache(path, records)


def _daily_allocation_from_record(
    record: DailySequenceLedgerRecord,
    *,
    reused: bool,
    authority: str,
) -> DailySequenceAllocation:
    return DailySequenceAllocation(
        allocation_id=record.allocation_id,
        purpose=record.purpose,
        profile_id=record.source_profile_id,
        company_id=record.company_id,
        prefix=record.prefix,
        business_date=record.business_date,
        namespace_key=record.namespace_key,
        source_partition=record.source_partition,
        stamp_format=record.stamp_format,
        sequence_separator=record.sequence_separator,
        sequence_start=record.sequence_start,
        sequence_count=record.sequence_count,
        sequence_width=record.sequence_width,
        reused=reused,
        authority=authority,
    )


def _validate_existing_daily_records(
    records: list[DailySequenceLedgerRecord],
) -> None:
    by_namespace: dict[str, list[DailySequenceLedgerRecord]] = {}
    for record in records:
        record.validate()
        if record.status != "allocated":
            continue
        by_namespace.setdefault(record.namespace_key, []).append(record)
    for namespace_key, namespace_records in by_namespace.items():
        ordered = sorted(
            namespace_records,
            key=lambda item: (item.sequence_start, item.sequence_end, item.row_number),
        )
        previous: DailySequenceLedgerRecord | None = None
        for current in ordered:
            if previous is not None and current.sequence_start <= previous.sequence_end:
                if (
                    current.allocation_id == previous.allocation_id
                    and current.sequence_start == previous.sequence_start
                    and current.sequence_end == previous.sequence_end
                    and current.allocation_key == previous.allocation_key
                ):
                    continue
                raise OrderNumberAllocationError(
                    "飞书日序号台账存在重叠区间，已停止新占号："
                    f"{namespace_key} {previous.sequence_start}-{previous.sequence_end} "
                    f"与{current.sequence_start}-{current.sequence_end}。"
                )
            previous = current


def reserve_daily_sequence(
    *,
    purpose: str = "platform_order_number",
    profile_id: str,
    company_id: str,
    prefix: str,
    business_date: str,
    source_sha256: str,
    sequence_count: int,
    sequence_width: int = 3,
    stamp_format: str = "%Y%m%d",
    sequence_separator: str = "",
    source_partition: str = "",
    sequence_start: int = 1,
    state_dir: str | Path | None = None,
    gateway: DailySequenceLedgerGateway | None = None,
    config_path: str | Path | None = None,
) -> DailySequenceAllocation:
    """Reserve a daily range in Feishu; only confirmed cache reuse may work offline."""

    if not purpose or not profile_id or not prefix:
        raise OrderNumberAllocationError("日序号占号缺少用途、来源配置或前缀。")
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,24}", prefix):
        raise OrderNumberAllocationError("日序号前缀格式无效。")
    if not re.fullmatch(r"\d{8}", business_date):
        raise OrderNumberAllocationError("日序号业务日期必须是YYYYMMDD。")
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise OrderNumberAllocationError("日序号占号缺少有效的来源SHA-256。")
    if sequence_count < 1 or sequence_width < 3 or sequence_start < 1:
        raise OrderNumberAllocationError(
            "日序号占号数量无效，最小位数必须不少于3位。"
        )
    if stamp_format not in {"%Y%m%d", "%y%m%d"}:
        raise OrderNumberAllocationError("日序号日期格式仅支持YYYYMMDD或YYMMDD。")
    if not re.fullmatch(r"[_-]?", sequence_separator):
        raise OrderNumberAllocationError("日序号分隔符仅支持空、下划线或短横线。")
    if not re.fullmatch(r"[0-9A-Za-z_-]{0,24}", source_partition):
        raise OrderNumberAllocationError("日序号来源分区格式无效。")

    allocation_key = _daily_allocation_key(
        purpose,
        profile_id,
        source_sha256,
        source_partition,
    )
    cache_path = _daily_cache_path(state_dir)
    ledger_gateway = gateway
    if ledger_gateway is None:
        try:
            ledger_gateway = LarkCliDailySequenceLedgerGateway.from_config(
                config_path or default_daily_sequence_config_path()
            )
        except DailySequenceLedgerError as exc:
            raise OrderNumberAllocationError(
                f"飞书日序号配置无法加载：{exc}"
            ) from exc

    try:
        records = ledger_gateway.list_records()
    except DailySequenceLedgerError as exc:
        cached = next(
            (
                record
                for record in _load_daily_cache(cache_path)
                if record.allocation_key == allocation_key
            ),
            None,
        )
        if cached is None:
            raise OrderNumberAllocationError(
                "飞书日序号台账不可用；新批次禁止从001离线起号。"
                f"原始错误：{exc}"
            ) from exc
        if cached.sequence_count != sequence_count:
            raise OrderNumberAllocationError(
                "同一来源已有确认日序号，但本次订单数量发生变化。"
            )
        return _daily_allocation_from_record(
            cached,
            reused=True,
            authority="local_confirmed_cache",
        )

    _validate_existing_daily_records(records)
    existing = [
        record for record in records if record.allocation_key == allocation_key
    ]
    if existing:
        first = existing[0]
        first_identity = (
            first.allocation_id,
            first.business_date,
            first.purpose,
            first.prefix,
            first.namespace_key,
            first.company_id,
            first.source_profile_id,
            first.source_sha256,
            first.sequence_start,
            first.sequence_end,
            first.sequence_count,
            first.sequence_width,
            first.status,
            first.schema_version,
            first.source_partition,
            first.stamp_format,
            first.sequence_separator,
        )
        if any(
            (
                record.allocation_id,
                record.business_date,
                record.purpose,
                record.prefix,
                record.namespace_key,
                record.company_id,
                record.source_profile_id,
                record.source_sha256,
                record.sequence_start,
                record.sequence_end,
                record.sequence_count,
                record.sequence_width,
                record.status,
                record.schema_version,
                record.source_partition,
                record.stamp_format,
                record.sequence_separator,
            )
            != first_identity
            for record in existing[1:]
        ):
            raise OrderNumberAllocationError(
                "飞书日序号台账中同一来源存在不一致的重复占号。"
            )
        if first.sequence_count != sequence_count:
            raise OrderNumberAllocationError(
                "同一来源已经占用过日序号，但本次订单数量发生变化；"
                "请核对草稿后再决定是否建立新批次。"
            )
        _cache_confirmed_record(cache_path, first)
        return _daily_allocation_from_record(
            first,
            reused=True,
            authority="feishu_central_ledger",
        )

    namespace_key = _daily_namespace_key(purpose, prefix, business_date)
    namespace_records = [
        record
        for record in records
        if record.namespace_key == namespace_key and record.status == "allocated"
    ]
    next_sequence = max(
        [sequence_start, *(record.sequence_end + 1 for record in namespace_records)]
    )
    allocation_id = hashlib.sha256(
        f"{allocation_key}\0{business_date}\0{next_sequence}\0{sequence_count}".encode(
            "utf-8"
        )
    ).hexdigest()[:20]
    try:
        allocated_by = ledger_gateway.current_user_id()
    except DailySequenceLedgerError as exc:
        raise OrderNumberAllocationError(
            f"无法验证飞书占号用户身份：{exc}"
        ) from exc
    candidate = DailySequenceLedgerRecord(
        allocation_id=allocation_id,
        allocation_key=allocation_key,
        business_date=business_date,
        purpose=purpose,
        prefix=prefix,
        namespace_key=namespace_key,
        company_id=company_id,
        source_profile_id=profile_id,
        source_sha256=source_sha256,
        sequence_start=next_sequence,
        sequence_end=next_sequence + sequence_count - 1,
        sequence_count=sequence_count,
        sequence_width=sequence_width,
        operation_id=uuid.uuid4().hex,
        allocated_by=allocated_by,
        allocated_at=datetime.now(timezone.utc).isoformat(),
        status="allocated",
        schema_version=DAILY_SEQUENCE_SCHEMA_VERSION,
        source_partition=source_partition,
        stamp_format=stamp_format,
        sequence_separator=sequence_separator,
    )
    try:
        ledger_gateway.append_record(candidate)
        readback_records = ledger_gateway.list_records()
    except DailySequenceLedgerError as exc:
        raise OrderNumberAllocationError(
            f"飞书日序号占号或回读失败，未生成可用编号：{exc}"
        ) from exc
    _validate_existing_daily_records(readback_records)
    readback = next(
        (
            record
            for record in readback_records
            if record.operation_id == candidate.operation_id
        ),
        None,
    )
    if readback is None or readback.to_row() != candidate.to_row():
        raise OrderNumberAllocationError("飞书日序号写入后未回读到本次完整占号记录。")
    _cache_confirmed_record(cache_path, readback)
    return _daily_allocation_from_record(
        readback,
        reused=False,
        authority="feishu_central_ledger",
    )


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + DEFAULT_LOCK_TIMEOUT_SECONDS
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(
                descriptor,
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).encode("utf-8"),
            )
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > STALE_LOCK_SECONDS:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise OrderNumberAllocationError(
                    "分钟序号占号记录正被另一个本机任务使用，请稍后重试。"
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def reserve_minute_sequence(
    *,
    purpose: str = "platform_order_number",
    profile_id: str,
    prefix: str,
    batch_minute: str,
    source_sha256: str,
    sequence_count: int,
    sequence_width: int,
    stamp_format: str = "%Y%m%d%H%M",
    sequence_start: int = 1,
    state_dir: str | Path | None = None,
) -> OrderNumberAllocation:
    if not purpose or not profile_id or not prefix:
        raise OrderNumberAllocationError("分钟序号占号缺少用途、来源配置或前缀。")
    if not source_sha256 or len(source_sha256) != 64:
        raise OrderNumberAllocationError("分钟序号占号缺少有效的来源SHA-256。")
    if sequence_count < 1 or sequence_width < 1 or sequence_start < 0:
        raise OrderNumberAllocationError("分钟序号占号数量或序号配置无效。")
    if not batch_minute.isdigit() or len(batch_minute) not in {10, 12}:
        raise OrderNumberAllocationError("批次分钟格式无效。")

    root = (
        Path(state_dir).expanduser()
        if state_dir is not None
        else default_allocation_state_dir()
    )
    ledger_path = root / "minute-sequence-ledger.json"
    lock_path = root / "minute-sequence-ledger.lock"
    key = _allocation_key(purpose, profile_id, source_sha256)

    with _exclusive_lock(lock_path):
        ledger = _load_ledger(ledger_path)
        entries = ledger["allocations"]
        assert isinstance(entries, list)
        existing = next(
            (
                item
                for item in entries
                if isinstance(item, dict) and item.get("allocation_key") == key
            ),
            None,
        )
        if existing is not None:
            existing_count = int(existing.get("sequence_count") or 0)
            if existing_count != sequence_count:
                raise OrderNumberAllocationError(
                    "同一来源已经占用过分钟序号，但本次订单数量发生变化；"
                    "为避免重号，请核对草稿后再决定是否建立新批次。"
                )
            return OrderNumberAllocation(
                allocation_id=str(existing["allocation_id"]),
                purpose=str(existing.get("purpose") or "platform_order_number"),
                profile_id=str(existing["profile_id"]),
                prefix=str(existing["prefix"]),
                batch_minute=str(existing["batch_minute"]),
                stamp_format=str(existing.get("stamp_format") or stamp_format),
                sequence_start=int(existing["sequence_start"]),
                sequence_count=existing_count,
                sequence_width=int(existing["sequence_width"]),
                reused=True,
            )

        next_sequence = sequence_start
        for item in entries:
            if not isinstance(item, dict):
                continue
            if (
                item.get("profile_id") != profile_id
                or item.get("prefix") != prefix
                or item.get("batch_minute") != batch_minute
                or str(item.get("purpose") or "platform_order_number") != purpose
            ):
                continue
            item_start = int(item.get("sequence_start") or 0)
            item_count = int(item.get("sequence_count") or 0)
            next_sequence = max(next_sequence, item_start + item_count)

        allocation_id = hashlib.sha256(
            f"{key}\0{batch_minute}\0{next_sequence}\0{sequence_count}".encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        entry = {
            "allocation_id": allocation_id,
            "allocation_key": key,
            "purpose": purpose,
            "profile_id": profile_id,
            "prefix": prefix,
            "batch_minute": batch_minute,
            "stamp_format": stamp_format,
            "sequence_start": next_sequence,
            "sequence_count": sequence_count,
            "sequence_width": sequence_width,
            "source_sha256": source_sha256,
            "allocated_at": datetime.now(timezone.utc).isoformat(),
        }
        entries.append(entry)
        _write_ledger(ledger_path, ledger)
        return OrderNumberAllocation(
            allocation_id=allocation_id,
            purpose=purpose,
            profile_id=profile_id,
            prefix=prefix,
            batch_minute=batch_minute,
            stamp_format=stamp_format,
            sequence_start=next_sequence,
            sequence_count=sequence_count,
            sequence_width=sequence_width,
            reused=False,
        )
