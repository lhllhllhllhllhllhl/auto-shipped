from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RulePackSchemaError(ValueError):
    """Raised when the stable core of a platform rule pack is invalid."""


RULE_STATUSES = {
    "implemented",
    "not_applicable",
    "pending_confirmation",
    "pending_implementation",
    "partial",
}


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RulePackSchemaError(f"规则包{key}必须是非空字符串")
    return value.strip()


def validate_rule_pack(payload: dict[str, Any]) -> None:
    """Validate only the stable rule-pack contract, leaving business fields extensible."""

    if not isinstance(payload, dict):
        raise RulePackSchemaError("规则包必须是JSON对象")
    if payload.get("schema_version") != "1.0":
        raise RulePackSchemaError("规则包schema_version必须为1.0")
    _required_text(payload, "profile_id")
    _required_text(payload, "source_profile_id")
    _required_text(payload, "target_profile_id")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise RulePackSchemaError("规则包version必须是大于等于1的整数")

    for key in ("policy_module_refs", "policy_module_overrides"):
        value = payload.get(key, {})
        if not isinstance(value, dict):
            raise RulePackSchemaError(f"规则包{key}必须是对象")

    coverage = payload.get("business_rule_coverage")
    if not isinstance(coverage, dict):
        raise RulePackSchemaError("规则包缺少business_rule_coverage对象")
    _required_text(coverage, "catalog_id")
    _required_text(coverage, "scope_id")
    declarations = coverage.get("rules")
    if not isinstance(declarations, dict) or not declarations:
        raise RulePackSchemaError("business_rule_coverage.rules必须是非空对象")

    for rule_id, declaration in declarations.items():
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise RulePackSchemaError("规则编号必须是非空字符串")
        if not isinstance(declaration, dict):
            raise RulePackSchemaError(f"规则{rule_id}声明必须是对象")
        status = declaration.get("status")
        if status not in RULE_STATUSES:
            raise RulePackSchemaError(f"规则{rule_id}状态无效：{status!r}")
        if not isinstance(declaration.get("confirmed"), bool):
            raise RulePackSchemaError(f"规则{rule_id}的confirmed必须是布尔值")
        implementation_ref = declaration.get("implementation_ref")
        if status == "implemented" and (
            not isinstance(implementation_ref, str) or not implementation_ref.strip()
        ):
            raise RulePackSchemaError(f"已实现规则{rule_id}缺少implementation_ref")
        if implementation_ref is not None and not isinstance(implementation_ref, str):
            raise RulePackSchemaError(f"规则{rule_id}的implementation_ref必须是字符串")


def load_rule_pack(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RulePackSchemaError(f"无法读取规则包{source.name}：{exc}") from exc
    validate_rule_pack(payload)
    return payload
