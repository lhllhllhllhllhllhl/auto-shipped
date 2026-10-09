from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


class GuanyiPolicyModuleError(ValueError):
    """Raised when a platform rule references an invalid shared policy module."""


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_registries(directory: str | Path) -> dict[str, dict[str, Any]]:
    registries: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(directory).rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GuanyiPolicyModuleError(f"无法读取管易规则文件{path.name}：{exc}") from exc
        registry_id = str(payload.get("registry_id") or "").strip()
        if not registry_id:
            continue
        if registry_id in registries:
            raise GuanyiPolicyModuleError(f"管易共享策略库ID重复：{registry_id}")
        registries[registry_id] = payload
    return registries


def resolve_guanyi_policy_modules(
    rules: dict[str, Any],
    platform_rules_dir: str | Path,
) -> dict[str, Any]:
    """Materialize shared policy modules into one source rule pack.

    Source rule packs own only source-specific choices and references. Shared
    executable policies have a single authority in a versioned registry.
    """

    resolved = copy.deepcopy(rules)
    references = resolved.get("policy_module_refs") or {}
    overrides = resolved.get("policy_module_overrides") or {}
    if not isinstance(references, dict) or not isinstance(overrides, dict):
        raise GuanyiPolicyModuleError("policy_module_refs和policy_module_overrides必须是对象")
    if not references:
        return resolved

    registries = _load_registries(platform_rules_dir)
    trace: dict[str, str] = {}
    for target_key, reference in references.items():
        if target_key in resolved:
            raise GuanyiPolicyModuleError(
                f"规则包同时内联并引用{target_key}，存在两个权威来源"
            )
        if not isinstance(reference, dict):
            raise GuanyiPolicyModuleError(f"{target_key}的共享策略引用无效")
        registry_id = str(reference.get("registry_id") or "").strip()
        module_id = str(reference.get("module_id") or "").strip()
        registry = registries.get(registry_id)
        if registry is None:
            raise GuanyiPolicyModuleError(f"共享策略库不存在：{registry_id or '未填写'}")
        module = (registry.get("modules") or {}).get(module_id)
        if not isinstance(module, dict):
            raise GuanyiPolicyModuleError(f"共享策略模块不存在：{module_id or '未填写'}")
        if str(module.get("target_key") or "") != target_key:
            raise GuanyiPolicyModuleError(
                f"共享策略模块{module_id}不能写入{target_key}"
            )
        value = module.get("value")
        if not isinstance(value, dict):
            raise GuanyiPolicyModuleError(f"共享策略模块{module_id}缺少对象值")
        target_overrides = overrides.get(target_key) or {}
        if not isinstance(target_overrides, dict):
            raise GuanyiPolicyModuleError(f"{target_key}的策略覆盖必须是对象")
        resolved[target_key] = _deep_merge(value, target_overrides)
        trace[target_key] = f"{registry_id}:{module_id}"

    unknown_overrides = set(overrides) - set(references)
    if unknown_overrides:
        raise GuanyiPolicyModuleError(
            "存在没有对应共享策略引用的覆盖：" + ", ".join(sorted(unknown_overrides))
        )
    resolved["resolved_policy_modules"] = trace
    return resolved
