from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from auto_shipped.domain import ClarificationRequest, deduplicate_clarifications


READY_STATUSES = {"implemented", "not_applicable"}
BLOCKING_STATUSES = {"pending_confirmation", "pending_implementation", "partial"}


@dataclass(slots=True)
class RuleCoverageResult:
    status: str
    catalog_id: str
    scope_id: str
    phase: str | None
    required_rule_ids: list[str] = field(default_factory=list)
    ready_rule_ids: list[str] = field(default_factory=list)
    blocked_rule_ids: list[str] = field(default_factory=list)
    clarifications: list[ClarificationRequest] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["clarifications"] = [item.to_dict() for item in self.clarifications]
        return payload


def load_rule_catalog(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _code_suffix(rule_id: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", rule_id.upper()).strip("_")


def _clarification_for_rule(
    rule_id: str,
    definition: dict[str, Any],
    declaration: dict[str, Any] | None,
    *,
    code: str,
    default_question: str,
    default_reason: str,
) -> ClarificationRequest:
    declaration = declaration or {}
    choices = tuple(declaration.get("choices") or definition.get("choices") or ())
    answer_type = declaration.get("answer_type") or definition.get("answer_type")
    if not answer_type:
        answer_type = "single_choice" if len(choices) >= 2 else "confirmation"
    return ClarificationRequest(
        code=f"{code}_{_code_suffix(rule_id)}",
        scope="batch",
        source_ref=rule_id,
        field=definition.get("title") or rule_id,
        question=declaration.get("question") or definition.get("question") or default_question,
        reason=declaration.get("reason") or definition.get("reason") or default_reason,
        answer_type=answer_type,
        choices=choices,
        next_action=declaration.get("next_action") or definition.get("next_action"),
    )


def audit_rule_coverage(
    catalog: dict[str, Any],
    platform_rules: dict[str, Any],
    scope_id: str,
    *,
    phase: str | None = None,
) -> RuleCoverageResult:
    catalog_id = str(catalog.get("catalog_id") or "")
    result = RuleCoverageResult(
        status="ready",
        catalog_id=catalog_id,
        scope_id=scope_id,
        phase=phase,
    )
    scopes = catalog.get("scopes") or {}
    scope = scopes.get(scope_id)
    coverage = platform_rules.get("business_rule_coverage") or {}
    definitions = catalog.get("rules") or {}

    if scope is None:
        result.status = "invalid"
        result.clarifications.append(
            ClarificationRequest(
                code="BUSINESS_RULE_SCOPE_NOT_FOUND",
                scope="batch",
                source_ref=scope_id,
                field="业务规则范围",
                question="业务规则范围不存在，请先恢复规则目录后再转换。",
                reason=f"规则目录 {catalog_id or 'unknown'} 中找不到 {scope_id}",
                answer_type="text",
            )
        )
        return result

    declared_catalog = str(coverage.get("catalog_id") or "")
    declared_scope = str(coverage.get("scope_id") or "")
    if declared_catalog != catalog_id or declared_scope != scope_id:
        result.status = "invalid"
        result.clarifications.append(
            ClarificationRequest(
                code="BUSINESS_RULE_PACK_MISMATCH",
                scope="batch",
                source_ref=scope_id,
                field="业务规则包",
                question="当前平台规则包与SOP规则目录不匹配，请先修复配置。",
                reason=(
                    f"期望 catalog={catalog_id}, scope={scope_id}; "
                    f"实际 catalog={declared_catalog or 'missing'}, scope={declared_scope or 'missing'}"
                ),
                answer_type="text",
            )
        )
        return result

    required_by_phase = scope.get("required_rules") or {}
    if phase is None:
        required = [
            rule_id
            for phase_rule_ids in required_by_phase.values()
            for rule_id in phase_rule_ids
        ]
    else:
        required = list(required_by_phase.get(phase) or [])
    result.required_rule_ids = list(dict.fromkeys(required))
    declarations = coverage.get("rules") or {}

    for rule_id in result.required_rule_ids:
        definition = definitions.get(rule_id)
        declaration = declarations.get(rule_id)
        if definition is None:
            result.blocked_rule_ids.append(rule_id)
            result.clarifications.append(
                ClarificationRequest(
                    code=f"BUSINESS_RULE_DEFINITION_MISSING_{_code_suffix(rule_id)}",
                    scope="batch",
                    source_ref=rule_id,
                    field="业务规则目录",
                    question="SOP规则目录缺少规则定义，请先补全目录。",
                    reason=f"范围 {scope_id} 引用了未定义规则 {rule_id}",
                    answer_type="text",
                )
            )
            continue
        if declaration is None:
            result.blocked_rule_ids.append(rule_id)
            result.clarifications.append(
                _clarification_for_rule(
                    rule_id,
                    definition,
                    None,
                    code="BUSINESS_RULE_DECLARATION_MISSING",
                    default_question="该业务规则尚未进入当前来源的规则包，请先补充。",
                    default_reason="SOP要求的规则没有实现状态，不能证明转换结果完整。",
                )
            )
            continue

        status = str(declaration.get("status") or "")
        confirmed = declaration.get("confirmed") is True
        implementation_ref = str(declaration.get("implementation_ref") or "")
        allowed_refs = set(definition.get("allowed_implementation_refs") or [])

        if status in READY_STATUSES and confirmed:
            if status == "implemented" and (
                not implementation_ref
                or (allowed_refs and implementation_ref not in allowed_refs)
            ):
                result.blocked_rule_ids.append(rule_id)
                result.clarifications.append(
                    _clarification_for_rule(
                        rule_id,
                        definition,
                        declaration,
                        code="BUSINESS_RULE_IMPLEMENTATION_INVALID",
                        default_question="该规则标记为已实现，但实现引用无法核验，请先修复配置。",
                        default_reason="覆盖门禁不接受没有可追踪实现引用的已实现规则。",
                    )
                )
            else:
                result.ready_rule_ids.append(rule_id)
            continue

        result.blocked_rule_ids.append(rule_id)
        if status in {"pending_implementation", "partial"}:
            result.clarifications.append(
                _clarification_for_rule(
                    rule_id,
                    definition,
                    declaration,
                    code="BUSINESS_RULE_NOT_IMPLEMENTED",
                    default_question="这条已知业务规则尚未完整实现，当前流程必须停止。",
                    default_reason="系统不能在缺少适用SOP规则时生成可上传文件。",
                )
            )
        elif status == "pending_confirmation" or not confirmed:
            result.clarifications.append(
                _clarification_for_rule(
                    rule_id,
                    definition,
                    declaration,
                    code="BUSINESS_RULE_PENDING_CONFIRMATION",
                    default_question="请确认这条业务规则后再继续转换。",
                    default_reason="该规则会影响管易订单内容，当前尚未获得业务确认。",
                )
            )
        else:
            result.clarifications.append(
                _clarification_for_rule(
                    rule_id,
                    definition,
                    declaration,
                    code="BUSINESS_RULE_STATUS_INVALID",
                    default_question="业务规则状态无效，请先修复规则包。",
                    default_reason=f"不支持的状态：{status or 'missing'}",
                )
            )

    result.clarifications = deduplicate_clarifications(result.clarifications)
    if result.clarifications:
        result.status = "needs_input"
    return result
