#!/usr/bin/env python3
"""Primary Skill entrypoint: merge multiple sources unless separation is explicit."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from convert_order import _find_runtime, _json_error, _resolve_catalog
from runtime_common import STATE_ROOT, resolve_project_root


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("._")
    return cleaned or "orders"


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
    parser.add_argument("--project-root", default=None, help="auto-shipped项目目录")
    parser.add_argument("--mappings", help="可选的商品映射JSON")
    parser.add_argument(
        "--mapping-overlay",
        action="append",
        default=[],
        help="可重复提供的已确认商品映射覆盖层JSON",
    )
    parser.add_argument(
        "--official-mapping-snapshot",
        help="Agent原生飞书能力已经读取并校验生成的正式映射快照；提供后不再调用lark-cli同步",
    )
    parser.add_argument("--template", help="可选的管易自定义订单导入模板")
    return parser


def _common_cli_args(
    args: argparse.Namespace,
    official_mapping_snapshot: Path | None,
    official_mapping_error: str,
) -> list[str]:
    values: list[str] = []
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
    if official_mapping_snapshot is not None:
        values.extend(["--official-mapping-status", "auto"])
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
) -> tuple[Path | None, str]:
    if args.official_mapping_snapshot:
        snapshot = Path(args.official_mapping_snapshot).expanduser().resolve()
        if snapshot.is_file():
            return snapshot, ""
        return None, f"Agent原生飞书正式映射快照不存在：{snapshot}"

    snapshot = STATE_ROOT / "config" / "feishu-official-product-mappings.json"
    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "sync-feishu-official-mappings",
        "--catalog",
        str(catalog),
        "--output",
        str(snapshot),
    ]
    if args.mappings:
        command.extend(
            ["--mappings", str(Path(args.mappings).expanduser().resolve())]
        )
    completed = subprocess.run(
        command,
        env=_environment(project_root),
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode == 0 and snapshot.is_file():
        return snapshot, ""
    message = completed.stderr.strip()
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("code") or message)
    return None, message or "飞书正式映射同步失败。"


def _environment(project_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root
        if not existing_pythonpath
        else os.pathsep.join([source_root, existing_pythonpath])
    )
    return environment


def _run_single(
    runtime: str,
    project_root: Path,
    source: Path,
    catalog: Path,
    output_dir: Path,
    hint: str | None,
    common_args: list[str],
    *,
    capture: bool,
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
    return subprocess.run(
        command,
        env=_environment(project_root),
        check=False,
        text=True,
        capture_output=capture,
    )


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        _json_error("找不到 Skill 内置或外部订单转换运行时。")
        return 1
    catalog = _resolve_catalog(project_root, args.catalog)
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
    except ValueError as exc:
        _json_error(str(exc))
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    official_mapping_snapshot, official_mapping_error = _prepare_official_mapping(
        args,
        runtime,
        project_root,
        catalog,
    )
    common_args = _common_cli_args(
        args,
        official_mapping_snapshot,
        official_mapping_error,
    )
    if len(sources) == 1:
        completed = _run_single(
            runtime,
            project_root,
            sources[0],
            catalog,
            output_dir,
            _hint_for_source(sources[0], hints),
            common_args,
            capture=False,
        )
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
        completed = subprocess.run(
            command,
            env=_environment(project_root),
            check=False,
        )
        return completed.returncode

    separate_results: list[dict[str, Any]] = []
    exit_codes: list[int] = []
    for index, source in enumerate(sources, start=1):
        source_output = output_dir / f"{index:03d}_{_safe_stem(source.stem)}"
        source_output.mkdir(parents=True, exist_ok=True)
        completed = _run_single(
            runtime,
            project_root,
            source,
            catalog,
            source_output,
            _hint_for_source(source, hints),
            common_args,
            capture=True,
        )
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

    if all(code == 0 for code in exit_codes):
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
