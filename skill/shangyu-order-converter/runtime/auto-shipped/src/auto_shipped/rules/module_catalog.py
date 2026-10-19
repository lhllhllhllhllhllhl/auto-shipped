from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ModuleCatalogError(ValueError):
    """Raised when the AI-facing module catalog is incomplete or contradictory."""


def load_module_catalog(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ModuleCatalogError("模块目录schema_version必须为1.0。")
    if not str(payload.get("catalog_id") or "").strip():
        raise ModuleCatalogError("模块目录缺少catalog_id。")
    if not isinstance(payload.get("modules"), list) or not payload["modules"]:
        raise ModuleCatalogError("模块目录modules必须是非空数组。")
    return payload


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _has_dependency_cycle(modules: dict[str, dict[str, Any]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module_id: str, trail: tuple[str, ...]) -> list[str]:
        if module_id in visiting:
            return [*trail, module_id]
        if module_id in visited:
            return []
        visiting.add(module_id)
        for dependency in modules[module_id].get("dependencies") or []:
            cycle = visit(str(dependency), (*trail, module_id))
            if cycle:
                return cycle
        visiting.remove(module_id)
        visited.add(module_id)
        return []

    for module_id in modules:
        cycle = visit(module_id, ())
        if cycle:
            return cycle
    return []


def audit_module_catalog(
    catalog_path: str | Path,
    *,
    project_root: str | Path,
    implementation_registry_path: str | Path,
    policy_registry_path: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    catalog = load_module_catalog(catalog_path)
    implementations = json.loads(
        Path(implementation_registry_path).read_text(encoding="utf-8")
    ).get("implementations") or {}
    policies = json.loads(Path(policy_registry_path).read_text(encoding="utf-8")).get(
        "modules"
    ) or {}
    issues: list[dict[str, str]] = []

    def add(code: str, location: str, message: str) -> None:
        issues.append({"code": code, "location": location, "message": message})

    raw_modules = catalog["modules"]
    module_ids = [str(module.get("module_id") or "").strip() for module in raw_modules]
    for duplicate in _duplicates(module_ids):
        add("MODULE_ID_DUPLICATED", duplicate, "模块ID重复。")
    modules = {
        str(module.get("module_id") or "").strip(): module
        for module in raw_modules
        if str(module.get("module_id") or "").strip()
    }
    for module_id, module in modules.items():
        for field in ("title", "responsibility", "status"):
            if not str(module.get(field) or "").strip():
                add("MODULE_FIELD_REQUIRED", f"{module_id}.{field}", "字段不能为空。")
        for authority in module.get("authority_paths") or []:
            if not (root / str(authority)).exists():
                add(
                    "MODULE_AUTHORITY_PATH_MISSING",
                    f"{module_id}.authority_paths",
                    str(authority),
                )
        for dependency in module.get("dependencies") or []:
            if dependency not in modules:
                add(
                    "MODULE_DEPENDENCY_MISSING",
                    f"{module_id}.dependencies",
                    str(dependency),
                )
    cycle = _has_dependency_cycle(modules) if len(modules) == len(raw_modules) else []
    if cycle:
        add("MODULE_DEPENDENCY_CYCLE", "dependencies", " -> ".join(cycle))

    implementation_refs = [
        str(ref)
        for module in raw_modules
        for ref in module.get("implementation_refs") or []
    ]
    legacy_implementation_refs = [
        str(ref)
        for module in raw_modules
        for ref in module.get("legacy_implementation_refs") or []
    ]
    all_owned_implementation_refs = [
        *implementation_refs,
        *legacy_implementation_refs,
    ]
    for duplicate in _duplicates(all_owned_implementation_refs):
        add("IMPLEMENTATION_REF_DUPLICATED", duplicate, "实现引用被多个模块认领。")
    missing_impl = sorted(set(implementations) - set(all_owned_implementation_refs))
    unknown_impl = sorted(set(all_owned_implementation_refs) - set(implementations))
    for ref in missing_impl:
        add("IMPLEMENTATION_REF_UNOWNED", ref, "实现注册表条目没有模块归属。")
    for ref in unknown_impl:
        add("IMPLEMENTATION_REF_UNKNOWN", ref, "模块引用不存在于实现注册表。")
    for ref in sorted(set(implementation_refs) & set(implementations)):
        if implementations[ref].get("status") == "legacy_rerun_only":
            add(
                "LEGACY_IMPLEMENTATION_MARKED_ACTIVE",
                ref,
                "仅历史复跑实现必须放入legacy_implementation_refs。",
            )
    for ref in sorted(set(legacy_implementation_refs) & set(implementations)):
        if implementations[ref].get("status") != "legacy_rerun_only":
            add(
                "ACTIVE_IMPLEMENTATION_MARKED_LEGACY",
                ref,
                "活动实现不得放入legacy_implementation_refs。",
            )

    policy_ids = [
        str(policy_id)
        for module in raw_modules
        for policy_id in module.get("policy_module_ids") or []
    ]
    for duplicate in _duplicates(policy_ids):
        add("POLICY_MODULE_DUPLICATED", duplicate, "共享策略被多个模块认领。")
    for policy_id in sorted(set(policies) - set(policy_ids)):
        add("POLICY_MODULE_UNOWNED", policy_id, "共享策略没有模块归属。")
    for policy_id in sorted(set(policy_ids) - set(policies)):
        add("POLICY_MODULE_UNKNOWN", policy_id, "模块引用不存在的共享策略。")

    deprecated_refs = {
        ref
        for module in raw_modules
        if module.get("status") == "deprecated"
        for ref in module.get("implementation_refs") or []
    }
    for ref in sorted(deprecated_refs):
        add("DEPRECATED_IMPLEMENTATION_OWNED", ref, "已弃用模块仍认领活动实现。")

    return {
        "status": "ready" if not issues else "invalid",
        "catalog_id": catalog.get("catalog_id"),
        "module_count": len(raw_modules),
        "implementation_ref_count": len(all_owned_implementation_refs),
        "legacy_implementation_ref_count": len(legacy_implementation_refs),
        "policy_module_count": len(policy_ids),
        "issues": issues,
    }


def render_module_catalog_markdown(catalog: dict[str, Any]) -> str:
    lines = [
        "# 系统模块治理目录",
        "",
        "> 本文由 `config/governance/system_module_catalog_v1.json` 生成；JSON 是唯一权威来源。",
        "",
        f"当前共 **{len(catalog['modules'])} 个一级模块**。",
        "",
        "## 修改前必读",
        "",
    ]
    lines.extend(f"- {item}" for item in catalog.get("design_principles") or [])
    lines.extend(
        [
            "",
            "## 模块清单",
            "",
            "| 模块 | 职责 | 边界 | 依赖 |",
            "|---|---|---|---|",
        ]
    )
    for module in catalog["modules"]:
        dependencies = "、".join(module.get("dependencies") or []) or "无"
        lines.append(
            "| `{}` | {} | {} | {} |".format(
                module["module_id"],
                str(module["responsibility"]).replace("|", "\\|"),
                str(module.get("boundary") or "").replace("|", "\\|"),
                dependencies,
            )
        )
    lines.extend(["", "## 权威来源与实现归属", ""])
    for module in catalog["modules"]:
        lines.extend([f"### {module['title']} (`{module['module_id']}`)", ""])
        lines.append("权威路径：" + "、".join(f"`{path}`" for path in module["authority_paths"]))
        refs = module.get("implementation_refs") or []
        legacy_refs = module.get("legacy_implementation_refs") or []
        policies = module.get("policy_module_ids") or []
        lines.append("实现引用：" + ("、".join(f"`{ref}`" for ref in refs) or "无"))
        lines.append(
            "历史兼容实现："
            + ("、".join(f"`{ref}`" for ref in legacy_refs) or "无")
        )
        lines.append("共享策略：" + ("、".join(f"`{ref}`" for ref in policies) or "无"))
        lines.append("")
    lines.extend(
        [
            "## 审计",
            "",
            "```bash",
            "python3 scripts/audit_modules.py",
            "```",
            "",
            "审计必须确保：所有实现和共享策略恰好归属一个模块、权威路径存在、依赖无环、弃用模块不污染活动规则。",
            "",
        ]
    )
    return "\n".join(lines)
