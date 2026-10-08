from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from auto_shipped.domain import ClarificationRequest
from auto_shipped.source_adapters import inspect_excel_structure, propose_field_mapping


@dataclass(slots=True)
class AdaptivePlanProposal:
    status: str
    source_file: str
    company_id: str
    plan: dict[str, Any] | None = None
    structure: dict[str, Any] | None = None
    clarifications: list[ClarificationRequest] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_file": self.source_file,
            "company_id": self.company_id,
            "plan": self.plan,
            "structure": self.structure,
            "clarifications": [asdict(item) for item in self.clarifications],
        }


def _active_company_workflows(
    registry: dict[str, Any],
    company_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    company = next(
        (
            item
            for item in registry.get("companies", [])
            if item.get("company_id") == company_id
        ),
        None,
    )
    if company is None:
        return None, []
    if company.get("enabled", True) is False or company.get("status") != "active":
        return company, []
    workflows = [
        item
        for item in company.get("workflows", [])
        if item.get("runtime_enabled") is True
        and item.get("target_platform")
        and item.get("source_profile_id")
    ]
    return company, workflows


def _column_has_values(plan_structure: dict[str, Any], spec: dict[str, Any]) -> bool:
    counts = {
        str(column.get("column")): int(column.get("nonempty_data_rows") or 0)
        for column in plan_structure.get("columns") or []
    }
    return any(counts.get(str(value), 0) > 0 for value in spec.get("columns") or [])


def propose_adaptive_excel_plan(
    source_path: str | Path,
    company_id: str,
    company_registry: dict[str, Any],
) -> AdaptivePlanProposal:
    path = Path(source_path).expanduser().resolve()
    structure = inspect_excel_structure(path)
    company, workflows = _active_company_workflows(company_registry, company_id)
    if company is None:
        return AdaptivePlanProposal(
            status="needs_input",
            source_file=path.name,
            company_id=company_id,
            structure=structure,
            clarifications=[
                ClarificationRequest(
                    code="ADAPTIVE_COMPANY_NOT_REGISTERED",
                    scope="file",
                    source_ref=path.name,
                    question="用户提供的公司尚未登记到公司库，请先确认公司ID或新增公司配置。",
                    reason=f"company_id={company_id}",
                    answer_type="text",
                )
            ],
        )
    if not workflows:
        return AdaptivePlanProposal(
            status="needs_input",
            source_file=path.name,
            company_id=company_id,
            structure=structure,
            clarifications=[
                ClarificationRequest(
                    code="ADAPTIVE_COMPANY_WORKFLOW_UNAVAILABLE",
                    scope="batch",
                    source_ref=path.name,
                    question="该公司还没有启用可复用的目标平台业务规则，暂时不能自适应转换。",
                    reason=f"company_id={company_id}",
                    answer_type="text",
                )
            ],
        )
    distinct_sources = {
        str(workflow.get("source_profile_id")) for workflow in workflows
    }
    if len(distinct_sources) != 1:
        choices = tuple(sorted(distinct_sources))
        return AdaptivePlanProposal(
            status="needs_input",
            source_file=path.name,
            company_id=company_id,
            structure=structure,
            clarifications=[
                ClarificationRequest(
                    code="ADAPTIVE_COMPANY_WORKFLOW_AMBIGUOUS",
                    scope="batch",
                    source_ref=path.name,
                    question="该公司存在多个业务规则范围，请确认本文件应使用哪一种订单流程。",
                    reason="候选来源规则：" + ", ".join(choices),
                    answer_type="single_choice",
                    choices=choices,
                )
            ],
        )

    sheets = list(structure.get("sheets") or [])
    if not sheets:
        return AdaptivePlanProposal(
            status="needs_input",
            source_file=path.name,
            company_id=company_id,
            structure=structure,
            clarifications=[
                ClarificationRequest(
                    code="ADAPTIVE_WORKBOOK_EMPTY",
                    scope="file",
                    source_ref=path.name,
                    question="Excel没有可分析的工作表，请重新提供文件。",
                    reason="workbook.sheets为空。",
                    answer_type="file",
                )
            ],
        )
    ranked = sorted(
        sheets,
        key=lambda item: (
            sum(
                1
                for column in item.get("columns") or []
                if column.get("header")
            ),
            int(item.get("row_count") or 0),
        ),
        reverse=True,
    )
    sheet = ranked[0]
    field_mapping = propose_field_mapping(sheet)
    rule_source_profile_id = next(iter(distinct_sources))
    plan_seed = (
        f"{structure['source_sha256']}:{company_id}:"
        f"{rule_source_profile_id}:{sheet['structure_signature']}"
    )
    plan = {
        "schema_version": "1.0",
        "plan_type": "adaptive_excel_source",
        "plan_id": f"adaptive-{hashlib.sha256(plan_seed.encode('utf-8')).hexdigest()[:20]}",
        "status": "draft",
        "source_file": path.name,
        "source_sha256": structure["source_sha256"],
        "company_id": company_id,
        "rule_source_profile_id": rule_source_profile_id,
        "sheet_name": sheet["sheet_name"],
        "header_row": sheet["candidate_header_row"],
        "structure_signature": sheet["structure_signature"],
        "field_mapping": field_mapping,
        "batch_defaults": {},
        "order_number": {"strategy": "source_column"},
        "unresolved_fields": [],
    }
    unresolved: list[str] = []
    for field in ("recipient_name", "contact", "address"):
        spec = field_mapping.get(field)
        if spec is None or not _column_has_values(sheet, spec):
            unresolved.append(field)
    order_spec = field_mapping.get("source_order_no")
    if order_spec is None or not _column_has_values(sheet, order_spec):
        unresolved.append("source_order_no")
    product_specs = [
        field_mapping.get("product_code"),
        field_mapping.get("product_name"),
    ]
    if not any(
        spec is not None and _column_has_values(sheet, spec)
        for spec in product_specs
    ):
        unresolved.append("product_identity")
    quantity_spec = field_mapping.get("quantity")
    if quantity_spec is None or not _column_has_values(sheet, quantity_spec):
        unresolved.append("quantity")
    plan["unresolved_fields"] = unresolved

    questions: list[ClarificationRequest] = []
    if any(field in unresolved for field in ("recipient_name", "contact", "address")):
        questions.append(
            ClarificationRequest(
                code="ADAPTIVE_RECIPIENT_MAPPING_REQUIRED",
                scope="file",
                source_ref=path.name,
                question="收件人、联系方式或地址没有形成唯一字段映射，请确认对应列。",
                reason="缺少字段："
                + ", ".join(
                    field
                    for field in ("recipient_name", "contact", "address")
                    if field in unresolved
                ),
                answer_type="text",
            )
        )
    if "source_order_no" in unresolved:
        questions.append(
            ClarificationRequest(
                code="ADAPTIVE_ORDER_NUMBER_REQUIRED",
                scope="batch",
                source_ref=path.name,
                field="平台单号",
                question="原表没有可用订单号。请确认是否按日期加顺序号为这批订单生成唯一来源单号。",
                reason="现有订单号列不存在或全部为空。",
                answer_type="confirmation",
            )
        )
    if "product_identity" in unresolved:
        questions.append(
            ClarificationRequest(
                code="ADAPTIVE_PRODUCT_REQUIRED",
                scope="batch",
                source_ref=path.name,
                field="商品",
                question="原表没有商品信息。请说明这批订单对应的商品或套餐；如果全部相同，只需确认一次。",
                reason="没有可用的商品编码或商品名称列。",
                answer_type="text",
            )
        )
    if "quantity" in unresolved:
        questions.append(
            ClarificationRequest(
                code="ADAPTIVE_QUANTITY_REQUIRED",
                scope="batch",
                source_ref=path.name,
                field="数量",
                question="原表没有可确认的商品数量。请说明每单数量；如果全部相同，只需确认一次。",
                reason="没有可用的数量列。",
                answer_type="text",
            )
        )
    return AdaptivePlanProposal(
        status="needs_input" if questions else "ready",
        source_file=path.name,
        company_id=company_id,
        plan=plan,
        structure=structure,
        clarifications=questions,
    )


def write_adaptive_plan(path: str | Path, plan: dict[str, Any]) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination
