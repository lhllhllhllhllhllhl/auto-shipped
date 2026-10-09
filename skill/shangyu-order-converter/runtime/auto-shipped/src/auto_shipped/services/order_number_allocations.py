from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


LEDGER_SCHEMA_VERSION = "shangyu-order-number-allocations/1.0"
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_SECONDS = 60.0


class OrderNumberAllocationError(ValueError):
    """Raised when a local sequence block cannot be allocated safely."""


@dataclass(frozen=True, slots=True)
class OrderNumberAllocation:
    allocation_id: str
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


def _allocation_key(profile_id: str, source_sha256: str) -> str:
    return hashlib.sha256(
        f"{profile_id}\0{source_sha256}".encode("utf-8")
    ).hexdigest()


def _validate_ledger(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise OrderNumberAllocationError("平台单号占号记录格式无效。")
    if payload.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise OrderNumberAllocationError("平台单号占号记录版本无法识别。")
    entries = payload.get("allocations")
    if not isinstance(entries, list):
        raise OrderNumberAllocationError("平台单号占号记录缺少allocations。")
    return payload


def _load_ledger(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"schema_version": LEDGER_SCHEMA_VERSION, "allocations": []}
    try:
        return _validate_ledger(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrderNumberAllocationError(
            f"无法安全读取平台单号占号记录：{exc}"
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
                    "平台单号占号记录正被另一个本机任务使用，请稍后重试。"
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def reserve_minute_sequence(
    *,
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
    if not profile_id or not prefix:
        raise OrderNumberAllocationError("平台单号占号缺少来源配置或公司前缀。")
    if not source_sha256 or len(source_sha256) != 64:
        raise OrderNumberAllocationError("平台单号占号缺少有效的来源SHA-256。")
    if sequence_count < 1 or sequence_width < 1 or sequence_start < 0:
        raise OrderNumberAllocationError("平台单号占号数量或序号配置无效。")
    if not batch_minute.isdigit() or len(batch_minute) not in {10, 12}:
        raise OrderNumberAllocationError("平台单号批次分钟格式无效。")

    root = (
        Path(state_dir).expanduser()
        if state_dir is not None
        else default_allocation_state_dir()
    )
    ledger_path = root / "minute-sequence-ledger.json"
    lock_path = root / "minute-sequence-ledger.lock"
    key = _allocation_key(profile_id, source_sha256)

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
                    "同一来源已经占用过平台单号，但本次订单数量发生变化；"
                    "为避免重号，请核对草稿后再决定是否建立新批次。"
                )
            return OrderNumberAllocation(
                allocation_id=str(existing["allocation_id"]),
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
            profile_id=profile_id,
            prefix=prefix,
            batch_minute=batch_minute,
            stamp_format=stamp_format,
            sequence_start=next_sequence,
            sequence_count=sequence_count,
            sequence_width=sequence_width,
            reused=False,
        )
