from __future__ import annotations

from typing import Any

from auto_shipped.catalog import clean_identifier


READABLE_REPOSITORY_STATUSES = frozenset({"active", "publishing"})
VERSIONED_RECORD_STATUSES = frozenset({"active", "inactive", "staged"})


class VersionedRepositoryError(ValueError):
    """Raised when a versioned Feishu repository row cannot be classified safely."""


def record_is_visible(
    *,
    status: Any,
    record_version: Any,
    repository_revision: Any,
    record_label: str,
) -> bool:
    """Return whether a row belongs to the repository's committed read revision.

    ``mapping_revision`` is the atomic read pointer. New rows are written as
    ``staged`` at the next revision and remain invisible until that pointer moves.
    Once the pointer moves, staged rows are readable even if final status cleanup
    was interrupted. ``inactive`` remains a durable tombstone and is never read.
    """

    normalized_status = clean_identifier(status)
    if normalized_status not in VERSIONED_RECORD_STATUSES:
        raise VersionedRepositoryError(
            f"{record_label}状态不是active/inactive/staged。"
        )
    if normalized_status == "inactive":
        return False

    version = clean_identifier(record_version)
    revision = clean_identifier(repository_revision)
    if not version:
        raise VersionedRepositoryError(f"{record_label}缺少版本号。")
    if not revision:
        raise VersionedRepositoryError("正式库缺少mapping_revision。")
    if version.isdigit() and revision.isdigit():
        return int(version) <= int(revision)
    if version != revision:
        raise VersionedRepositoryError(
            f"{record_label}版本无法与正式库版本安全比较。"
        )
    return True

