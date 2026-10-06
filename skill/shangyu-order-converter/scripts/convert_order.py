#!/usr/bin/env python3
"""Stable Skill entrypoint for the Shangyu order conversion runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from runtime_common import STATE_ROOT, resolve_project_root, state_python_candidates


def _json_error(message: str) -> None:
    print(
        json.dumps(
            {
                "status": "failed",
                "errors": [{"code": "SKILL_ENTRYPOINT_ERROR", "message": message}],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _candidate_executable(value: str) -> str | None:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path) if path.is_file() else None
    return shutil.which(value)


def _find_runtime(project_root: Path) -> str | None:
    candidates = [
        *state_python_candidates(project_root),
        sys.executable,
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        executable = _candidate_executable(candidate)
        if not executable or executable in seen:
            continue
        seen.add(executable)
        probe = subprocess.run(
            [
                executable,
                "-c",
                "import sys, openpyxl; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return executable
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将公司原始订单转换为管易自定义订单导入Excel"
    )
    parser.add_argument("--source", required=True, help="公司原始订单Excel")
    parser.add_argument("--catalog", help="可选：本次运行使用的管易商品信息CSV")
    parser.add_argument("--output-dir", required=True, help="本次转换输出目录")
    parser.add_argument(
        "--project-root",
        default=None,
        help="auto-shipped项目目录",
    )
    parser.add_argument("--mappings", help="可选的商品映射JSON")
    parser.add_argument(
        "--mapping-overlay",
        action="append",
        default=[],
        help="可重复提供的已确认商品映射覆盖层JSON",
    )
    parser.add_argument(
        "--source-profile",
        help="用户已明确确认来源公司时提供来源配置ID",
    )
    parser.add_argument("--template", help="可选的管易自定义订单导入模板")
    return parser


def _resolve_catalog(project_root: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    registry_path = project_root / "config/catalog/current_product_catalog.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = registry.get("path")
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        _json_error("找不到 Skill 内置或外部订单转换运行时。")
        return 1
    source = Path(args.source).expanduser().resolve()
    catalog = _resolve_catalog(project_root, args.catalog)
    output_dir = Path(args.output_dir).expanduser().resolve()

    expected = [project_root / "pyproject.toml", project_root / "src/auto_shipped"]
    if not all(path.exists() for path in expected):
        _json_error(f"找不到订单转换项目：{project_root}")
        return 1
    if not source.is_file():
        _json_error(f"找不到原始订单文件：{source}")
        return 1
    if catalog is None or not catalog.is_file():
        _json_error("找不到系统登记的管易商品资料，请提供最新商品信息CSV。")
        return 1

    runtime = _find_runtime(project_root)
    if runtime is None:
        _json_error("找不到 Python 3.11+ 且包含 openpyxl 的运行环境。")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
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
    ]
    if args.mappings:
        command.extend(["--mappings", str(Path(args.mappings).expanduser().resolve())])
    overlay_candidates = [
        *args.mapping_overlay,
        str(STATE_ROOT / "config" / "feishu-official-product-mappings.json"),
    ]
    seen_overlays: set[str] = set()
    for value in overlay_candidates:
        overlay = Path(value).expanduser().resolve()
        key = str(overlay)
        if key in seen_overlays or not overlay.is_file():
            continue
        seen_overlays.add(key)
        command.extend(["--mapping-overlay", key])
    if args.source_profile:
        command.extend(["--source-profile", args.source_profile])
    if args.template:
        command.extend(["--template", str(Path(args.template).expanduser().resolve())])

    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root
        if not existing_pythonpath
        else os.pathsep.join([source_root, existing_pythonpath])
    )
    completed = subprocess.run(command, env=environment, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
