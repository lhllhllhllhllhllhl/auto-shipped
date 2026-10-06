#!/usr/bin/env python3
"""Stable Skill entrypoint for fail-closed multi-file order conversion."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from convert_order import _find_runtime, _json_error, _resolve_catalog
from runtime_common import STATE_ROOT, resolve_project_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="逐文件识别和预检后，把多个公司原始订单合并成一个管易导入Excel"
    )
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="公司原始订单Excel；重复提供，至少两个",
    )
    parser.add_argument("--catalog", help="可选：本次运行使用的管易商品信息CSV")
    parser.add_argument("--output-dir", required=True, help="本次批次输出目录")
    parser.add_argument("--batch-name", default="批量订单", help="输出文件名前缀")
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
    parser.add_argument("--template", help="可选的管易自定义订单导入模板")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        _json_error("找不到 Skill 内置或外部订单转换运行时。")
        return 1
    catalog = _resolve_catalog(project_root, args.catalog)
    sources = [Path(value).expanduser().resolve() for value in args.source]
    output_dir = Path(args.output_dir).expanduser().resolve()

    expected = [project_root / "pyproject.toml", project_root / "src/auto_shipped"]
    if not all(path.exists() for path in expected):
        _json_error(f"找不到订单转换项目：{project_root}")
        return 1
    missing = [str(path) for path in sources if not path.is_file()]
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

    output_dir.mkdir(parents=True, exist_ok=True)
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
    ]
    for source in sources:
        command.extend(["--source", str(source)])
    for value in args.source_profile_hint:
        command.extend(["--source-profile-hint", value])
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
