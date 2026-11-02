#!/usr/bin/env python3
"""Stable Skill entrypoint for Agent-extracted text or screenshot orders."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from convert_order import _find_runtime, _json_error
from runtime_common import resolve_project_root, runtime_release_identity


DEFAULT_STAGE_TIMEOUT_SECONDS = 120.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校验文字/截图订单草稿，并在用户确认后生成标准化转换输入"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--draft", required=True)
    validate.add_argument("--defer-confirmation", action="store_true")
    validate.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_STAGE_TIMEOUT_SECONDS
    )
    validate.add_argument("--project-root")
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--draft", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--defer-confirmation", action="store_true")
    prepare.add_argument("--order-number-state-dir")
    prepare.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_STAGE_TIMEOUT_SECONDS
    )
    prepare.add_argument("--project-root")
    process = subparsers.add_parser(
        "process",
        help="一次完成文字草稿准备、正式映射同步和统一转换",
    )
    process.add_argument("--draft", required=True)
    process.add_argument("--output-dir", required=True)
    process.add_argument("--defer-confirmation", action="store_true")
    process.add_argument("--order-number-state-dir")
    process.add_argument("--project-root")
    process.add_argument("--catalog")
    process.add_argument("--preferred-catalog", action="append", default=[])
    process.add_argument("--mappings")
    process.add_argument("--mapping-overlay", action="append", default=[])
    process.add_argument("--mapping-replacement", action="append", default=[])
    process.add_argument("--official-mapping-snapshot")
    process.add_argument("--template")
    process.add_argument("--batch-name", default="文字订单")
    process.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_STAGE_TIMEOUT_SECONDS
    )
    return parser


def _environment(project_root: Path) -> tuple[dict[str, str], dict[str, str | None]]:
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join([source_root, existing])
    )
    release = runtime_release_identity(project_root)
    environment["SHANGYU_RELEASE_ID"] = str(release["release_id"])
    manifest_sha256 = release.get("runtime_manifest_sha256")
    if manifest_sha256:
        environment["SHANGYU_SKILL_RUNTIME_MANIFEST_SHA256"] = str(manifest_sha256)
    return environment, release


def _timeout_payload(
    *,
    stage: str,
    timeout_seconds: float,
    release: dict[str, str | None],
    trace_id: str | None = None,
) -> dict[str, object]:
    return {
        "status": "failed",
        "code": "TEXT_ORDER_STAGE_TIMEOUT",
        "stage": stage,
        "timeout_seconds": timeout_seconds,
        "trace_id": trace_id,
        "release_id": release.get("release_id"),
        "message": f"文字订单阶段{stage}超过{timeout_seconds:g}秒，已停止且未改换来源规则。",
    }


def _run_process(
    args: argparse.Namespace,
    *,
    project_root: Path,
    runtime: str,
    draft: Path,
    environment: dict[str, str],
    release: dict[str, str | None],
) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    prepared_dir = output_dir / "text-intake"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    prepare_command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "prepare-text-order-draft",
        "--draft",
        str(draft),
        "--output-dir",
        str(prepared_dir),
    ]
    if args.defer_confirmation:
        prepare_command.append("--defer-confirmation")
    if args.order_number_state_dir:
        prepare_command.extend(
            [
                "--order-number-state-dir",
                str(Path(args.order_number_state_dir).expanduser().resolve()),
            ]
        )
    try:
        prepared = subprocess.run(
            prepare_command,
            env=environment,
            check=False,
            text=True,
            capture_output=True,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                _timeout_payload(
                    stage="preparation",
                    timeout_seconds=args.timeout_seconds,
                    release=release,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    try:
        intake = json.loads(prepared.stdout)
    except json.JSONDecodeError:
        intake = {
            "status": "failed",
            "code": "TEXT_ORDER_PREPARATION_RESULT_INVALID",
            "message": prepared.stderr.strip() or "文字订单准备阶段没有返回有效JSON。",
            "release_id": release.get("release_id"),
        }
    if prepared.returncode != 0 or intake.get("status") != "ready":
        print(json.dumps(intake, ensure_ascii=False, indent=2))
        return prepared.returncode if prepared.returncode in {1, 2} else 1

    conversion_inputs = intake.get("conversion_inputs") or []
    if not conversion_inputs:
        intake.update(
            {
                "status": "failed",
                "code": "TEXT_ORDER_CONVERSION_INPUTS_MISSING",
                "message": "文字订单准备完成但没有签发转换输入。",
            }
        )
        print(json.dumps(intake, ensure_ascii=False, indent=2))
        return 1

    convert_script = Path(__file__).with_name("convert_orders.py")
    convert_command = [
        sys.executable,
        str(convert_script),
        "--output-dir",
        str(output_dir / "converted"),
        "--batch-name",
        args.batch_name,
        "--project-root",
        str(project_root),
        "--stage-timeout-seconds",
        str(args.timeout_seconds),
    ]
    for item in conversion_inputs:
        source = str(item["source"])
        convert_command.extend(["--source", source])
        convert_command.extend(
            ["--company-hint", f"{source}={item['company_id']}"]
        )
        convert_command.extend(
            ["--adaptive-plan-hint", f"{source}={item['adaptive_plan']}"]
        )
        convert_command.extend(
            [
                "--text-intake-receipt",
                f"{source}={item['text_intake_receipt']}",
            ]
        )
    for option_name, option_value in (
        ("--catalog", args.catalog),
        ("--mappings", args.mappings),
        ("--official-mapping-snapshot", args.official_mapping_snapshot),
        ("--template", args.template),
    ):
        if option_value:
            convert_command.extend([option_name, str(option_value)])
    for value in args.preferred_catalog:
        convert_command.extend(["--preferred-catalog", str(value)])
    for value in args.mapping_overlay:
        convert_command.extend(["--mapping-overlay", str(value)])
    for value in args.mapping_replacement:
        convert_command.extend(["--mapping-replacement", str(value)])

    conversion_started = time.perf_counter()
    try:
        converted = subprocess.run(
            convert_command,
            env=environment,
            check=False,
            text=True,
            capture_output=True,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                _timeout_payload(
                    stage="official_mapping_and_conversion",
                    timeout_seconds=args.timeout_seconds,
                    release=release,
                    trace_id=str(intake.get("trace_id") or "") or None,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    try:
        conversion = json.loads(converted.stdout)
    except json.JSONDecodeError:
        conversion = {
            "status": "failed",
            "code": "TEXT_ORDER_CONVERSION_RESULT_INVALID",
            "message": converted.stderr.strip() or "统一转换阶段没有返回有效JSON。",
        }
    phase_durations = dict(intake.get("phase_durations_ms") or {})
    phase_durations["official_mapping_and_conversion"] = round(
        (time.perf_counter() - conversion_started) * 1000,
        3,
    )
    conversion.update(
        {
            "trace_id": intake.get("trace_id"),
            "release_id": release.get("release_id"),
            "phase_durations_ms": phase_durations,
            "text_intake": {
                key: intake.get(key)
                for key in (
                    "draft_file",
                    "order_count",
                    "masked_preview",
                    "clarifications",
                    "routed_groups",
                    "order_number_allocation",
                    "order_number_allocations",
                )
            },
        }
    )
    print(json.dumps(conversion, ensure_ascii=False, indent=2))
    return converted.returncode if converted.returncode in {0, 1, 2} else 1


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        _json_error("找不到 Skill 内置或外部订单转换运行时。")
        return 1
    runtime = _find_runtime(project_root)
    if runtime is None:
        _json_error("找不到 Python 3.11+ 且包含 openpyxl 的运行环境。")
        return 1
    draft = Path(args.draft).expanduser().resolve()
    if not draft.is_file():
        _json_error(f"找不到文字订单草稿：{draft}")
        return 1

    environment, release = _environment(project_root)
    if args.command == "process":
        return _run_process(
            args,
            project_root=project_root,
            runtime=runtime,
            draft=draft,
            environment=environment,
            release=release,
        )

    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        (
            "validate-text-order-draft"
            if args.command == "validate"
            else "prepare-text-order-draft"
        ),
        "--draft",
        str(draft),
    ]
    if args.command == "prepare":
        command.extend(
            ["--output-dir", str(Path(args.output_dir).expanduser().resolve())]
        )
        if args.order_number_state_dir:
            command.extend(
                [
                    "--order-number-state-dir",
                    str(Path(args.order_number_state_dir).expanduser().resolve()),
                ]
            )
    if args.defer_confirmation:
        command.append("--defer-confirmation")
    try:
        return subprocess.run(
            command,
            env=environment,
            check=False,
            timeout=args.timeout_seconds,
        ).returncode
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                _timeout_payload(
                    stage=("validation" if args.command == "validate" else "preparation"),
                    timeout_seconds=args.timeout_seconds,
                    release=release,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
