#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from auto_shipped.rules.module_catalog import (  # noqa: E402
    audit_module_catalog,
    load_module_catalog,
    render_module_catalog_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="审计尚舆系统模块治理目录。")
    parser.add_argument("--write-doc", action="store_true", help="同步生成docs/MODULE_GOVERNANCE.md")
    args = parser.parse_args()
    catalog_path = PROJECT_ROOT / "config/governance/system_module_catalog_v1.json"
    result = audit_module_catalog(
        catalog_path,
        project_root=PROJECT_ROOT,
        implementation_registry_path=PROJECT_ROOT / "config/business_rules/implementation_registry_v1.json",
        policy_registry_path=PROJECT_ROOT / "config/platform_rules/guanyi/policy_modules_v1.json",
        rule_catalog_path=PROJECT_ROOT
        / "config/business_rules/shangyu_sop_rule_catalog_v1.json",
        check_generated_doc=not args.write_doc,
    )
    if args.write_doc and result["status"] == "ready":
        destination = PROJECT_ROOT / "docs/MODULE_GOVERNANCE.md"
        destination.write_text(
            render_module_catalog_markdown(load_module_catalog(catalog_path)),
            encoding="utf-8",
        )
        result["generated_doc"] = str(destination.relative_to(PROJECT_ROOT))
        result = audit_module_catalog(
            catalog_path,
            project_root=PROJECT_ROOT,
            implementation_registry_path=PROJECT_ROOT
            / "config/business_rules/implementation_registry_v1.json",
            policy_registry_path=PROJECT_ROOT
            / "config/platform_rules/guanyi/policy_modules_v1.json",
            rule_catalog_path=PROJECT_ROOT
            / "config/business_rules/shangyu_sop_rule_catalog_v1.json",
        ) | {"generated_doc": str(destination.relative_to(PROJECT_ROOT))}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
