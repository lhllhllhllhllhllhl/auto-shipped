from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from auto_shipped import __version__
from auto_shipped.services.conversion_workflow import WORKFLOW_SCHEMA_VERSION


DELIVERY_RECEIPT_SCHEMA_VERSION = "conversion-delivery-receipt/1.0"


class DeliveryReceiptError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            **({"details": self.details} if self.details else {}),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DeliveryReceiptError(
            "DELIVERY_ARTIFACT_MISSING",
            "转换交付物不存在，不能签发交付凭证。",
            details={"file_name": path.name},
        )
    return {
        "path": str(path.resolve()),
        "file_name": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _read_json(path: Path, *, artifact_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryReceiptError(
            "DELIVERY_ARTIFACT_JSON_INVALID",
            f"{artifact_name}不是有效JSON，不能签发或验证交付凭证。",
            details={"file_name": path.name, "error": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise DeliveryReceiptError(
            "DELIVERY_ARTIFACT_JSON_INVALID",
            f"{artifact_name}必须是JSON对象。",
            details={"file_name": path.name},
        )
    return payload


def _output_sha256(workflow_execution: dict[str, Any]) -> str:
    for stage in workflow_execution.get("stages") or []:
        if stage.get("stage_id") == "output_render_readback":
            return str(stage.get("output_sha256") or "")
    return ""


def _release_identity() -> dict[str, Any]:
    bundle_sha256 = str(
        os.environ.get("SHANGYU_SKILL_RUNTIME_MANIFEST_SHA256") or ""
    ).strip()
    return {
        "package": "auto-shipped",
        "package_version": __version__,
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "release_id": str(os.environ.get("SHANGYU_RELEASE_ID") or "").strip()
        or None,
        "runtime_manifest_sha256": bundle_sha256 or None,
    }


def _official_mapping_identity(report_payload: dict[str, Any]) -> dict[str, Any]:
    value = report_payload.get("official_mapping")
    if isinstance(value, dict) and value:
        return dict(value)
    return {
        "status": "unavailable",
        "provider": None,
        "mapping_revision": None,
        "synced_at": None,
        "snapshot_sha256": None,
        "receipt_id": None,
        "receipt_issued_at": None,
        "read_consistency": None,
        "read_attempts": 0,
        "error": "处理报告未携带当前任务正式映射同步证据。",
    }


def write_delivery_receipt(
    output_path: str | Path,
    *,
    delivery_status: str,
    workbook_path: str | Path,
    report_path: str | Path,
    upload_manifest_path: str | Path | None,
    workflow_execution: dict[str, Any],
) -> Path:
    """Create the program-owned proof required before an Excel may be delivered."""

    if delivery_status not in {"ready", "preview_ready"}:
        raise DeliveryReceiptError(
            "DELIVERY_STATUS_INVALID",
            "只有ready或preview_ready结果可以签发交付凭证。",
            details={"delivery_status": delivery_status},
        )
    if workflow_execution.get("status") != "complete":
        raise DeliveryReceiptError(
            "DELIVERY_WORKFLOW_INCOMPLETE",
            "内部必做判断目录尚未完成，不能签发交付凭证。",
        )

    receipt_path = Path(output_path).expanduser().resolve()
    workbook = Path(workbook_path).expanduser().resolve()
    report = Path(report_path).expanduser().resolve()
    workbook_artifact = _artifact(workbook)
    report_artifact = _artifact(report)
    report_payload = _read_json(report, artifact_name="处理报告")

    readback_sha256 = _output_sha256(workflow_execution)
    if not readback_sha256 or readback_sha256 != workbook_artifact["sha256"]:
        raise DeliveryReceiptError(
            "DELIVERY_WORKBOOK_READBACK_MISMATCH",
            "待交付Excel与内部回读校验结果不一致。",
            details={
                "workflow_sha256": readback_sha256,
                "workbook_sha256": workbook_artifact["sha256"],
            },
        )
    if report_payload.get("status") != delivery_status:
        raise DeliveryReceiptError(
            "DELIVERY_REPORT_STATUS_MISMATCH",
            "处理报告状态与交付状态不一致。",
        )
    report_workflow = report_payload.get("workflow_execution") or {}
    if (
        report_workflow.get("status") != "complete"
        or report_workflow.get("workflow_id") != workflow_execution.get("workflow_id")
    ):
        raise DeliveryReceiptError(
            "DELIVERY_REPORT_WORKFLOW_MISMATCH",
            "处理报告没有记录同一个已完成工作流。",
        )
    report_receipt_value = str(report_payload.get("delivery_receipt") or "").strip()
    report_receipt_path = (
        Path(report_receipt_value).expanduser().resolve()
        if report_receipt_value
        else None
    )
    if report_receipt_path != receipt_path:
        raise DeliveryReceiptError(
            "DELIVERY_REPORT_RECEIPT_PATH_MISMATCH",
            "处理报告没有绑定本次交付凭证路径。",
        )

    manifest_artifact: dict[str, Any] | None = None
    if delivery_status == "ready":
        if upload_manifest_path is None:
            raise DeliveryReceiptError(
                "DELIVERY_MANIFEST_REQUIRED",
                "ready成品缺少上传预检manifest，不能交付。",
            )
        manifest = Path(upload_manifest_path).expanduser().resolve()
        manifest_artifact = _artifact(manifest)
        manifest_payload = _read_json(manifest, artifact_name="上传预检manifest")
        manifest_sha256 = str(
            (manifest_payload.get("artifact") or {}).get("sha256") or ""
        )
        if manifest_payload.get("status") != "ready" or manifest_sha256 != workbook_artifact["sha256"]:
            raise DeliveryReceiptError(
                "DELIVERY_MANIFEST_WORKBOOK_MISMATCH",
                "上传预检manifest没有绑定当前Excel。",
            )
    elif upload_manifest_path is not None or report_payload.get("upload_manifest") is not None:
        raise DeliveryReceiptError(
            "DELIVERY_PREVIEW_MANIFEST_FORBIDDEN",
            "待确认预览不得生成上传manifest。",
        )

    payload = {
        "schema_version": DELIVERY_RECEIPT_SCHEMA_VERSION,
        "status": "verified",
        "delivery_status": delivery_status,
        "converter": _release_identity(),
        "official_mapping": _official_mapping_identity(report_payload),
        "workflow": {
            "workflow_id": workflow_execution.get("workflow_id"),
            "status": workflow_execution.get("status"),
            "output_sha256": readback_sha256,
        },
        "artifacts": {
            "workbook": workbook_artifact,
            "processing_report": report_artifact,
            "upload_manifest": manifest_artifact,
        },
        "checks": {
            "workflow_complete": True,
            "workbook_matches_readback": True,
            "report_matches_workflow": True,
            "manifest_matches_workbook": True,
            "preview_has_no_manifest": True,
        },
        "safety": {
            "receipt_contains_recipient_pii": False,
            "workbook_contains_recipient_pii": True,
            "upload_authorized": False,
        },
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt_path


def verify_delivery_receipt(path: str | Path) -> dict[str, Any]:
    """Recompute every receipt hash before an Agent claims a verified delivery."""

    receipt_path = Path(path).expanduser().resolve()
    receipt = _read_json(receipt_path, artifact_name="交付凭证")
    required_checks = {
        "workflow_complete",
        "workbook_matches_readback",
        "report_matches_workflow",
        "manifest_matches_workbook",
        "preview_has_no_manifest",
    }
    receipt_checks = receipt.get("checks") or {}
    if (
        receipt.get("schema_version") != DELIVERY_RECEIPT_SCHEMA_VERSION
        or receipt.get("status") != "verified"
        or not required_checks.issubset(receipt_checks)
        or not all(receipt_checks.get(name) is True for name in required_checks)
    ):
        raise DeliveryReceiptError(
            "DELIVERY_RECEIPT_INVALID",
            "交付凭证版本或状态无效。",
        )
    artifacts = receipt.get("artifacts") or {}
    expected_release = _release_identity()
    actual_release = receipt.get("converter") or {}
    release_fields = ("package", "package_version", "workflow_schema_version")
    if any(actual_release.get(field) != expected_release.get(field) for field in release_fields):
        raise DeliveryReceiptError(
            "DELIVERY_RECEIPT_RUNTIME_MISMATCH",
            "交付凭证不是由当前订单转换运行时签发。",
        )
    expected_bundle = expected_release.get("runtime_manifest_sha256")
    if expected_bundle and actual_release.get("runtime_manifest_sha256") != expected_bundle:
        raise DeliveryReceiptError(
            "DELIVERY_RECEIPT_RUNTIME_MISMATCH",
            "交付凭证与当前Skill运行时清单不一致。",
        )
    for key in ("workbook", "processing_report"):
        artifact = artifacts.get(key) or {}
        artifact_path = Path(str(artifact.get("path") or "")).expanduser().resolve()
        actual = _artifact(artifact_path)
        if (
            actual["sha256"] != artifact.get("sha256")
            or actual["size_bytes"] != artifact.get("size_bytes")
        ):
            raise DeliveryReceiptError(
                "DELIVERY_RECEIPT_ARTIFACT_CHANGED",
                "交付凭证绑定的文件在签发后发生变化。",
                details={"artifact": key, "file_name": actual["file_name"]},
            )

    delivery_status = str(receipt.get("delivery_status") or "")
    workbook_artifact = artifacts.get("workbook") or {}
    report_artifact = artifacts.get("processing_report") or {}
    report_path = Path(str(report_artifact.get("path") or "")).expanduser().resolve()
    report_payload = _read_json(report_path, artifact_name="处理报告")
    report_workflow = report_payload.get("workflow_execution") or {}
    receipt_workflow = receipt.get("workflow") or {}
    report_official_mapping = _official_mapping_identity(report_payload)
    report_receipt_value = str(report_payload.get("delivery_receipt") or "").strip()
    if (
        report_payload.get("status") != delivery_status
        or report_workflow.get("status") != "complete"
        or report_workflow.get("workflow_id") != receipt_workflow.get("workflow_id")
        or _output_sha256(report_workflow) != workbook_artifact.get("sha256")
        or not report_receipt_value
        or Path(report_receipt_value).expanduser().resolve() != receipt_path
        or receipt.get("official_mapping") != report_official_mapping
    ):
        raise DeliveryReceiptError(
            "DELIVERY_RECEIPT_REPORT_MISMATCH",
            "交付凭证、处理报告和已完成工作流不一致。",
        )
    manifest_artifact = artifacts.get("upload_manifest")
    if delivery_status == "ready":
        if not isinstance(manifest_artifact, dict):
            raise DeliveryReceiptError(
                "DELIVERY_RECEIPT_MANIFEST_MISSING",
                "ready交付凭证缺少上传manifest。",
            )
        manifest_path = Path(str(manifest_artifact.get("path") or "")).expanduser().resolve()
        actual_manifest = _artifact(manifest_path)
        if (
            actual_manifest["sha256"] != manifest_artifact.get("sha256")
            or actual_manifest["size_bytes"] != manifest_artifact.get("size_bytes")
        ):
            raise DeliveryReceiptError(
                "DELIVERY_RECEIPT_ARTIFACT_CHANGED",
                "交付凭证绑定的上传manifest在签发后发生变化。",
                details={"artifact": "upload_manifest"},
            )
        manifest_payload = _read_json(manifest_path, artifact_name="上传预检manifest")
        report_manifest_value = str(report_payload.get("upload_manifest") or "").strip()
        report_manifest_path = (
            Path(report_manifest_value).expanduser().resolve()
            if report_manifest_value
            else None
        )
        if (
            manifest_payload.get("status") != "ready"
            or str((manifest_payload.get("artifact") or {}).get("sha256") or "")
            != str(workbook_artifact.get("sha256") or "")
            or report_manifest_path != manifest_path
        ):
            raise DeliveryReceiptError(
                "DELIVERY_RECEIPT_MANIFEST_WORKBOOK_MISMATCH",
                "交付凭证中的manifest与Excel哈希不一致。",
            )
    elif delivery_status == "preview_ready":
        if manifest_artifact is not None or report_payload.get("upload_manifest") is not None:
            raise DeliveryReceiptError(
                "DELIVERY_RECEIPT_PREVIEW_MANIFEST_FORBIDDEN",
                "待确认预览凭证不得包含上传manifest。",
            )
    else:
        raise DeliveryReceiptError(
            "DELIVERY_STATUS_INVALID",
            "交付凭证包含未知交付状态。",
        )

    return {
        "status": "verified",
        "delivery_status": delivery_status,
        "receipt": str(receipt_path),
        "workbook": (artifacts.get("workbook") or {}).get("path"),
        "processing_report": (artifacts.get("processing_report") or {}).get("path"),
        "upload_manifest": (
            manifest_artifact.get("path") if isinstance(manifest_artifact, dict) else None
        ),
        "workflow_id": (receipt.get("workflow") or {}).get("workflow_id"),
        "converter": receipt.get("converter"),
        "official_mapping": receipt.get("official_mapping"),
    }
