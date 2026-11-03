#!/usr/bin/env python3
"""Primary Skill entrypoint: merge multiple sources unless separation is explicit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from convert_order import (
    _find_runtime,
    _json_error,
    _resolve_catalog,
    _resolve_preferred_catalogs,
)
from runtime_common import (
    BUNDLE_MANIFEST,
    STATE_ROOT,
    resolve_project_root,
    runtime_release_identity,
)


class StageTimeoutError(RuntimeError):
    def __init__(self, stage: str, timeout_seconds: float) -> None:
        super().__init__(stage)
        self.stage = stage
        self.timeout_seconds = timeout_seconds


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("._")
    return cleaned or "orders"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_hints(values: list[str]) -> dict[str, str]:
    hints: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("来源提示必须使用 SOURCE=PROFILE_ID 格式")
        source, profile_id = value.rsplit("=", 1)
        source = source.strip()
        profile_id = profile_id.strip()
        if not source or not profile_id:
            raise ValueError("来源提示中的文件和配置ID都不能为空")
        if source in hints and hints[source] != profile_id:
            raise ValueError(f"同一文件配置了多个来源：{source}")
        hints[source] = profile_id
    return hints


def _hint_for_source(source: Path, hints: dict[str, str]) -> str | None:
    for key in (str(source), str(source.resolve()), source.name):
        if key in hints:
            return hints[key]
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="尚舆订单统一入口：多个文件默认合并为一个管易Excel"
    )
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="公司原始订单Excel；可重复提供",
    )
    parser.add_argument("--catalog", help="可选：本次运行使用的管易商品信息CSV")
    parser.add_argument(
        "--preferred-catalog",
        action="append",
        default=[],
        help="可选：业务优先商品目录CSV；可重复提供，按参数顺序匹配",
    )
    parser.add_argument("--output-dir", required=True, help="本次输出目录")
    parser.add_argument("--batch-name", default="批量订单", help="合并输出文件名前缀")
    parser.add_argument(
        "--separate",
        action="store_true",
        help="仅在用户明确要求分开输出时启用",
    )
    parser.add_argument(
        "--source-profile-hint",
        action="append",
        default=[],
        metavar="SOURCE=PROFILE_ID",
        help="用户已明确公司时，按路径或文件名指定来源配置；可重复提供",
    )
    parser.add_argument(
        "--company-hint",
        action="append",
        default=[],
        metavar="SOURCE=COMPANY_ID",
        help="用户明确公司但模板可能未知时，按文件提供公司ID；可重复提供",
    )
    parser.add_argument(
        "--adaptive-plan-hint",
        action="append",
        default=[],
        metavar="SOURCE=PLAN_JSON",
        help="按文件提供已经补充确认的自适应来源计划；可重复提供",
    )
    parser.add_argument(
        "--text-intake-receipt",
        action="append",
        default=[],
        metavar="SOURCE=RECEIPT_JSON",
        help="文字/截图订单准备入口签发的路由凭证；TextOrders来源必须提供",
    )
    parser.add_argument("--project-root", default=None, help="auto-shipped项目目录")
    parser.add_argument("--mappings", help="可选的商品映射JSON")
    parser.add_argument(
        "--mapping-overlay",
        action="append",
        default=[],
        help="可重复提供的已确认商品映射覆盖层JSON",
    )
    parser.add_argument(
        "--mapping-replacement",
        action="append",
        default=[],
        help="可重复提供的当前批次正式映射临时替换记录JSON",
    )
    parser.add_argument(
        "--official-mapping-snapshot",
        help="Agent原生飞书能力为当前任务读取并校验生成的正式映射快照；必须同时提供同步凭证",
    )
    parser.add_argument(
        "--official-mapping-receipt",
        help="sync-native返回的当前任务正式映射同步凭证",
    )
    parser.add_argument("--template", help="可选的管易自定义订单导入模板")
    parser.add_argument(
        "--stage-timeout-seconds",
        type=float,
        default=120.0,
        help="飞书同步或单次转换的最长等待时间",
    )
    return parser


def _source_profile_sha256(project_root: Path, profile_id: str) -> str:
    for path in sorted((project_root / "config" / "source_profiles").rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("profile_id") == profile_id:
            return _sha256(path)
    return ""


def _verify_text_intake_receipts(
    *,
    project_root: Path,
    sources: list[Path],
    company_hints: dict[str, str],
    adaptive_plan_hints: dict[str, str],
    receipt_hints: dict[str, str],
) -> str | None:
    for source in sources:
        plan_value = _hint_for_source(source, adaptive_plan_hints)
        if not plan_value:
            continue
        plan_path = Path(plan_value).expanduser().resolve()
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if plan.get("sheet_name") != "TextOrders":
            continue
        receipt_value = _hint_for_source(source, receipt_hints)
        if not receipt_value:
            return f"TEXT_ORDER_INTAKE_RECEIPT_REQUIRED：{source.name}缺少程序签发的文字订单路由凭证。"
        receipt_path = Path(receipt_value).expanduser().resolve()
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return f"TEXT_ORDER_INTAKE_RECEIPT_INVALID：{source.name}的路由凭证无法读取。"
        if (
            receipt.get("schema_version") != "text-intake-receipt/1.0"
            or receipt.get("source_kind") != "TextOrders"
            or receipt.get("privacy") != "no_recipient_pii"
        ):
            return f"TEXT_ORDER_INTAKE_RECEIPT_INVALID：{source.name}的路由凭证合同不正确。"
        receipt_source = receipt.get("source") or {}
        receipt_plan = receipt.get("adaptive_plan") or {}
        route = receipt.get("route") or {}
        if (
            receipt_source.get("file_name") != source.name
            or receipt_source.get("sha256") != _sha256(source)
        ):
            return f"TEXT_ORDER_INTAKE_RECEIPT_SOURCE_MISMATCH：{source.name}与路由凭证哈希不一致。"
        if (
            receipt_plan.get("file_name") != plan_path.name
            or receipt_plan.get("sha256") != _sha256(plan_path)
        ):
            return f"TEXT_ORDER_INTAKE_RECEIPT_PLAN_MISMATCH：{source.name}的字段计划与路由凭证不一致。"
        company_hint = _hint_for_source(source, company_hints)
        if company_hint and company_hint != route.get("company_id"):
            return f"TEXT_ORDER_INTAKE_RECEIPT_ROUTE_MISMATCH：{source.name}的公司提示被改写。"
        if (
            plan.get("company_id") != route.get("company_id")
            or plan.get("rule_source_profile_id")
            != route.get("rule_source_profile_id")
        ):
            return f"TEXT_ORDER_INTAKE_RECEIPT_ROUTE_MISMATCH：{source.name}的来源路由被改写。"
        profile_id = str(route.get("rule_source_profile_id") or "")
        if (
            not profile_id
            or route.get("source_profile_sha256")
            != _source_profile_sha256(project_root, profile_id)
        ):
            return f"TEXT_ORDER_INTAKE_RECEIPT_PROFILE_MISMATCH：{source.name}的来源配置版本已变化。"
    return None


def _common_cli_args(
    args: argparse.Namespace,
    official_mapping_snapshot: Path | None,
    official_mapping_receipt: Path | None,
    official_mapping_error: str,
    preferred_catalogs: tuple[Path, ...],
) -> list[str]:
    values: list[str] = []
    for preferred_catalog in preferred_catalogs:
        values.extend(["--preferred-catalog", str(preferred_catalog)])
    if args.mappings:
        values.extend(["--mappings", str(Path(args.mappings).expanduser().resolve())])
    overlay_candidates = list(args.mapping_overlay)
    if official_mapping_snapshot is not None:
        overlay_candidates.append(str(official_mapping_snapshot))
    seen: set[str] = set()
    for value in overlay_candidates:
        overlay = Path(value).expanduser().resolve()
        key = str(overlay)
        if key in seen or not overlay.is_file():
            continue
        seen.add(key)
        values.extend(["--mapping-overlay", key])
    replacement_candidates = [
        *args.mapping_replacement,
        str(STATE_ROOT / "config" / "current-batch-mapping-replacements.json"),
    ]
    seen_replacements: set[str] = set()
    for value in replacement_candidates:
        replacement = Path(value).expanduser().resolve()
        key = str(replacement)
        if key in seen_replacements or not replacement.is_file():
            continue
        seen_replacements.add(key)
        values.extend(["--mapping-replacement", key])
    if official_mapping_snapshot is not None:
        values.extend(
            [
                "--official-mapping-status",
                "auto",
                "--official-mapping-receipt",
                str(official_mapping_receipt),
            ]
        )
    else:
        values.extend(
            [
                "--official-mapping-status",
                "unavailable",
                "--official-mapping-error",
                official_mapping_error[:1000]
                or "无法读取并验证飞书正式商品映射。",
            ]
        )
    if args.template:
        values.extend(["--template", str(Path(args.template).expanduser().resolve())])
    return values


def _prepare_official_mapping(
    args: argparse.Namespace,
    runtime: str,
    project_root: Path,
    catalog: Path,
    preferred_catalogs: tuple[Path, ...],
) -> tuple[Path | None, Path | None, str]:
    operation_root = (
        STATE_ROOT
        / "operations"
        / "official-mapping-sync"
        / uuid.uuid4().hex
    )
    operation_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    if args.official_mapping_snapshot:
        snapshot = Path(args.official_mapping_snapshot).expanduser().resolve()
        receipt_value = str(args.official_mapping_receipt or "").strip()
        receipt = (
            Path(receipt_value).expanduser().resolve()
            if receipt_value
            else None
        )
        if not snapshot.is_file():
            return None, None, f"Agent原生飞书正式映射快照不存在：{snapshot}"
        if receipt is None or not receipt.is_file():
            return (
                None,
                None,
                "Agent原生飞书快照缺少当前任务同步凭证；请重新运行sync-native，"
                "并同时传入返回的sync_receipt。",
            )
        try:
            receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, None, f"Agent原生飞书同步凭证无法读取：{exc}"
        if not isinstance(receipt_payload, dict):
            return None, None, "Agent原生飞书同步凭证根节点不是对象。"
        receipt_id = str(receipt_payload.get("receipt_id") or "").strip()
        if len(receipt_id) != 32 or any(
            character not in "0123456789abcdef" for character in receipt_id
        ):
            return None, None, "Agent原生飞书同步凭证ID无效。"
        claims_root = (
            STATE_ROOT / "operations" / "official-mapping-sync-receipt-claims"
        )
        claims_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        claim_path = claims_root / f"{receipt_id}.json"
        try:
            descriptor = os.open(
                claim_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return (
                None,
                None,
                "该正式映射同步凭证已被另一个转换任务使用；"
                "请为本任务重新读取正式库并运行sync-native。",
            )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": "official-mapping-sync-receipt-claim/1.0",
                    "receipt_id": receipt_id,
                    "operation_id": operation_root.name,
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                    "privacy": "no_order_or_recipient_data",
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
        bound_snapshot = operation_root / "official-mappings.json"
        bound_receipt = operation_root / "official-mappings.sync-receipt.json"
        shutil.copyfile(snapshot, bound_snapshot)
        shutil.copyfile(receipt, bound_receipt)
        return bound_snapshot, bound_receipt, ""

    if args.official_mapping_receipt:
        return (
            None,
            None,
            "只提供了正式映射同步凭证但没有快照；两个参数必须成对提供。",
        )

    snapshot = operation_root / "official-mappings.json"
    receipt = operation_root / "official-mappings.sync-receipt.json"
    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "sync-feishu-official-mappings",
        "--catalog",
        str(catalog),
        "--output",
        str(snapshot),
        "--receipt-output",
        str(receipt),
    ]
    for preferred_catalog in preferred_catalogs:
        command.extend(["--preferred-catalog", str(preferred_catalog)])
    if args.mappings:
        command.extend(
            ["--mappings", str(Path(args.mappings).expanduser().resolve())]
        )
    try:
        completed = subprocess.run(
            command,
            env=_environment(project_root),
            check=False,
            text=True,
            capture_output=True,
            timeout=min(args.stage_timeout_seconds, 60.0),
        )
    except subprocess.TimeoutExpired as exc:
        raise StageTimeoutError(
            "official_mapping_sync", min(args.stage_timeout_seconds, 60.0)
        ) from exc
    if (
        completed.returncode == 0
        and snapshot.is_file()
        and receipt.is_file()
    ):
        return snapshot, receipt, ""
    message = completed.stderr.strip()
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("code") or message)
    return None, None, message or "飞书正式映射同步失败。"


def _environment(project_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root
        if not existing_pythonpath
        else os.pathsep.join([source_root, existing_pythonpath])
    )
    if BUNDLE_MANIFEST.is_file():
        environment["SHANGYU_SKILL_RUNTIME_MANIFEST_SHA256"] = hashlib.sha256(
            BUNDLE_MANIFEST.read_bytes()
        ).hexdigest()
    environment["SHANGYU_RELEASE_ID"] = str(
        runtime_release_identity(project_root)["release_id"]
    )
    return environment


def _run_single(
    runtime: str,
    project_root: Path,
    source: Path,
    catalog: Path,
    output_dir: Path,
    hint: str | None,
    company_hint: str | None,
    adaptive_plan: str | None,
    common_args: list[str],
    *,
    capture: bool,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "convert",
        "--source",
        str(source),
        "--catalog",
        str(catalog),
        "--output-dir",
        str(output_dir),
        *common_args,
    ]
    if hint:
        command.extend(["--source-profile", hint])
    if company_hint:
        command.extend(["--company", company_hint])
    if adaptive_plan:
        command.extend(
            ["--adaptive-plan", str(Path(adaptive_plan).expanduser().resolve())]
        )
    try:
        return subprocess.run(
            command,
            env=_environment(project_root),
            check=False,
            text=True,
            capture_output=capture,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise StageTimeoutError("conversion", timeout_seconds) from exc


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        _json_error("找不到 Skill 内置或外部订单转换运行时。")
        return 1
    catalog = _resolve_catalog(project_root, args.catalog)
    try:
        preferred_catalogs = _resolve_preferred_catalogs(
            project_root,
            tuple(args.preferred_catalog),
        )
    except FileNotFoundError as exc:
        _json_error(str(exc))
        return 1
    sources = [Path(value).expanduser().resolve() for value in args.source]
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not all(path.exists() for path in (project_root / "pyproject.toml", project_root / "src/auto_shipped")):
        _json_error(f"找不到订单转换项目：{project_root}")
        return 1
    missing = [str(source) for source in sources if not source.is_file()]
    if missing:
        _json_error("找不到原始订单文件：" + "、".join(missing))
        return 1
    if catalog is None or not catalog.is_file():
        _json_error("找不到系统登记的管易商品资料，请提供最新商品信息CSV。")
        return 1
    runtime = _find_runtime(project_root)
    if runtime is None:
        _json_error("找不到 Python 3.11+ 且包含 openpyxl 的运行环境。")
        return 1
    try:
        hints = _parse_hints(args.source_profile_hint)
        company_hints = _parse_hints(args.company_hint)
        adaptive_plan_hints = _parse_hints(args.adaptive_plan_hint)
        receipt_hints = _parse_hints(args.text_intake_receipt)
    except ValueError as exc:
        _json_error(str(exc))
        return 1

    receipt_error = _verify_text_intake_receipts(
        project_root=project_root,
        sources=sources,
        company_hints=company_hints,
        adaptive_plan_hints=adaptive_plan_hints,
        receipt_hints=receipt_hints,
    )
    if receipt_error:
        _json_error(receipt_error)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    release = runtime_release_identity(project_root)
    try:
        (
            official_mapping_snapshot,
            official_mapping_receipt,
            official_mapping_error,
        ) = _prepare_official_mapping(
            args,
            runtime,
            project_root,
            catalog,
            preferred_catalogs,
        )
    except StageTimeoutError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "TEXT_ORDER_STAGE_TIMEOUT",
                    "stage": exc.stage,
                    "timeout_seconds": exc.timeout_seconds,
                    "release_id": release["release_id"],
                    "message": "正式映射同步超时，已停止转换且未更换来源规则。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    common_args = _common_cli_args(
        args,
        official_mapping_snapshot,
        official_mapping_receipt,
        official_mapping_error,
        preferred_catalogs,
    )
    if len(sources) == 1:
        try:
            completed = _run_single(
                runtime,
                project_root,
                sources[0],
                catalog,
                output_dir,
                _hint_for_source(sources[0], hints),
                _hint_for_source(sources[0], company_hints),
                _hint_for_source(sources[0], adaptive_plan_hints),
                common_args,
                capture=False,
                timeout_seconds=args.stage_timeout_seconds,
            )
        except StageTimeoutError as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "code": "TEXT_ORDER_STAGE_TIMEOUT",
                        "stage": exc.stage,
                        "timeout_seconds": exc.timeout_seconds,
                        "release_id": release["release_id"],
                        "message": "统一转换超时，已停止且未更换来源规则。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        return completed.returncode

    if not args.separate:
        command = [
            runtime,
            "-m",
            "auto_shipped.cli",
            "convert-batch",
            "--catalog",
            str(catalog),
            "--output-dir",
            str(output_dir),
            "--batch-name",
            args.batch_name,
            *common_args,
        ]
        for source in sources:
            command.extend(["--source", str(source)])
        for value in args.source_profile_hint:
            command.extend(["--source-profile-hint", value])
        for value in args.company_hint:
            command.extend(["--company-hint", value])
        for value in args.adaptive_plan_hint:
            command.extend(["--adaptive-plan-hint", value])
        try:
            completed = subprocess.run(
                command,
                env=_environment(project_root),
                check=False,
                timeout=args.stage_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "code": "TEXT_ORDER_STAGE_TIMEOUT",
                        "stage": "batch_conversion",
                        "timeout_seconds": args.stage_timeout_seconds,
                        "release_id": release["release_id"],
                        "message": "批量统一转换超时，已停止且未更换来源规则。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        return completed.returncode

    separate_results: list[dict[str, Any]] = []
    exit_codes: list[int] = []
    for index, source in enumerate(sources, start=1):
        source_output = output_dir / f"{index:03d}_{_safe_stem(source.stem)}"
        source_output.mkdir(parents=True, exist_ok=True)
        try:
            completed = _run_single(
                runtime,
                project_root,
                source,
                catalog,
                source_output,
                _hint_for_source(source, hints),
                _hint_for_source(source, company_hints),
                _hint_for_source(source, adaptive_plan_hints),
                common_args,
                capture=True,
                timeout_seconds=args.stage_timeout_seconds,
            )
        except StageTimeoutError as exc:
            separate_results.append(
                {
                    "status": "failed",
                    "source_file": source.name,
                    "errors": [
                        {
                            "code": "TEXT_ORDER_STAGE_TIMEOUT",
                            "stage": exc.stage,
                            "message": "单文件转换超时。",
                        }
                    ],
                }
            )
            exit_codes.append(1)
            continue
        exit_codes.append(completed.returncode)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {
                "status": "failed",
                "source_file": source.name,
                "errors": [
                    {
                        "code": "SEPARATE_RESULT_INVALID",
                        "message": completed.stderr.strip() or "单文件转换没有返回有效JSON。",
                    }
                ],
            }
        separate_results.append(payload)

    result_statuses = {str(item.get("status") or "failed") for item in separate_results}
    if all(code == 0 for code in exit_codes) and "preview_ready" in result_statuses:
        status = "preview_ready"
        exit_code = 0
    elif all(code == 0 for code in exit_codes):
        status = "ready"
        exit_code = 0
    elif all(code in {0, 2} for code in exit_codes):
        status = "needs_input"
        exit_code = 2
    else:
        status = "failed"
        exit_code = 1
    print(
        json.dumps(
            {
                "status": status,
                "mode": "separate",
                "source_count": len(sources),
                "results": separate_results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
