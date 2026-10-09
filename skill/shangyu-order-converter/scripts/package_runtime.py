#!/usr/bin/env python3
"""Build the portable runtime snapshot stored inside this Skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from runtime_common import BUNDLED_PROJECT_ROOT, SKILL_ROOT


INCLUDED_PATHS = [
    "pyproject.toml",
    "uv.lock",
    "src",
    "config",
    "contracts",
    "assets",
    "browser/guanyi",
    "docs/ARCHITECTURE.md",
    "docs/DECISIONS.md",
    "docs/GUANYI_BROWSER_EXECUTOR.md",
    "docs/BUSINESS_RULE_COVERAGE.md",
    "docs/ORDER_AUTOMATION_RULES_V0.1.md",
    "docs/RULE_SOURCE_LEDGER.md",
    "docs/RULE_DECISION_FLOW_V0.1.md",
    "docs/CONVERSION_TEST_RECORD_TEMPLATE.md",
    "docs/FEISHU_MAPPING_GOVERNANCE.md",
    "docs/TEXT_ORDER_INTAKE_V0.1.md",
    "docs/RULE_MODULE_STRUCTURE.md",
    "docs/PACKAGE_SEMANTICS_GOVERNANCE.md",
]

LEGACY_RUNTIME_FILES = {
    "config/source_profiles/tiantian_warehouse_v1.json",
    "config/platform_rules/guanyi/tiantian_warehouse_v1.json",
}


def ignore(directory: str, names: list[str]) -> set[str]:
    blocked = {"__pycache__", ".pytest_cache", "node_modules", "runtime"}
    current = Path(directory)
    return {
        name
        for name in names
        if name in blocked
        or name.endswith(".pyc")
        or name.endswith(".egg-info")
        or any(
            current.as_posix().endswith(Path(relative).parent.as_posix())
            and name == Path(relative).name
            for relative in LEGACY_RUNTIME_FILES
        )
    }


def copy_path(source_root: Path, relative: str, destination_root: Path) -> None:
    source = source_root / relative
    destination = destination_root / relative
    if not source.exists():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, ignore=ignore)
    else:
        shutil.copy2(source, destination)


def package_catalog(source_root: Path, destination_root: Path) -> None:
    registry_path = source_root / "config" / "catalog" / "current_product_catalog.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    configured = Path(str(registry.get("path") or "")).expanduser()
    if not configured.is_absolute():
        configured = source_root / configured
    if not configured.is_file():
        raise FileNotFoundError("当前商品资料不存在，无法构建可迁移包。")
    target = destination_root / "assets" / "catalog" / "current_product_catalog.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(configured, target)
    packaged_registry = {
        **registry,
        "path": "assets/catalog/current_product_catalog.csv",
        "packaging": "portable_skill_snapshot",
    }
    destination_registry = destination_root / "config" / "catalog" / "current_product_catalog.json"
    destination_registry.write_text(
        json.dumps(packaged_registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_manifest(destination_root: Path) -> None:
    files = {}
    for path in sorted(destination_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(destination_root).as_posix()
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "shangyu-skill-runtime/1.0",
        "file_count": len(files),
        "files": files,
    }
    (SKILL_ROOT / "runtime-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Skill 内置运行时")
    parser.add_argument("--source", default=str(Path.cwd()))
    args = parser.parse_args()
    source_root = Path(args.source).expanduser().resolve()
    if BUNDLED_PROJECT_ROOT.exists():
        shutil.rmtree(BUNDLED_PROJECT_ROOT)
    BUNDLED_PROJECT_ROOT.mkdir(parents=True)
    for relative in INCLUDED_PATHS:
        copy_path(source_root, relative, BUNDLED_PROJECT_ROOT)
    package_catalog(source_root, BUNDLED_PROJECT_ROOT)
    write_manifest(BUNDLED_PROJECT_ROOT)
    print(json.dumps({"status": "packaged", "source": str(source_root), "destination": str(BUNDLED_PROJECT_ROOT), "skill": str(SKILL_ROOT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
