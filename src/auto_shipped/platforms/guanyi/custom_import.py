from __future__ import annotations

import hashlib
from copy import copy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from auto_shipped.catalog import ProductCatalog
from auto_shipped.domain import (
    ClarificationRequest,
    ParsedOrder,
    deduplicate_clarifications,
)
from auto_shipped.platforms.guanyi.order_policies import (
    GuanyiOrderPolicyError,
    ResolvedOrderItem,
    compose_seller_remark,
    select_buyer_member,
    select_logistics_carrier,
)
from auto_shipped.platforms.guanyi.item_expansion import (
    ResolvedLineItem,
    expand_source_item,
)
from auto_shipped.platforms.guanyi.store_assignment import (
    GuanyiStoreAssignmentError,
    select_store,
)


class GuanyiCustomImportError(ValueError):
    """Raised when a blocked or incompatible batch reaches workbook rendering."""


@dataclass(slots=True)
class GuanyiBuildResult:
    lines: list[dict[str, Any]] = field(default_factory=list)
    clarifications: list[ClarificationRequest] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "needs_input" if self.clarifications else "ready"


def _rule_value(rules: dict[str, Any], key: str) -> Any:
    return rules["defaults"][key]["value"]


def _field_strategy(rules: dict[str, Any], key: str, default: str) -> str:
    policy = (rules.get("field_output_policy") or {}).get(key) or {}
    return str(policy.get("strategy") or default)


def _output_value(strategy: str, value: Any, value_strategy: str, field: str) -> Any:
    if strategy == value_strategy:
        return value or ""
    if strategy == "blank":
        return ""
    raise GuanyiCustomImportError(f"未知{field}输出策略: {strategy}")


def _product_name_output_value(strategy: str, line_item: ResolvedLineItem) -> str:
    product = line_item.product
    item = line_item.source_item
    if strategy == "catalog_product_name":
        return product.product_name or ""
    if strategy == "expansion_component_name_or_catalog":
        return line_item.output_product_name or product.product_name or ""
    if strategy == "blank":
        return ""
    if strategy == "source_name_with_display_spec_for_standard_items":
        if line_item.expansion_rule_id:
            return product.product_name or ""
        source_name = str(item.source_product_name or "").strip()
        source_display_spec = str(item.source_display_spec or "").strip().strip("/")
        if not source_name:
            return product.product_name or ""
        if not source_display_spec or source_display_spec in source_name:
            return source_name
        return f"{source_name.rstrip('/')}/{source_display_spec}"
    raise GuanyiCustomImportError(f"未知商品名称输出策略: {strategy}")


def _rule_clarifications(rules: dict[str, Any]) -> list[ClarificationRequest]:
    requests: list[ClarificationRequest] = []
    platform_number = rules.get("platform_order_number", {})
    if not platform_number.get("confirmed", False):
        requests.append(
            ClarificationRequest(
                code="CONFIRM_PLATFORM_ORDER_NUMBER_STRATEGY",
                scope="batch",
                field="平台单号",
                question=platform_number.get("question")
                or "请确认当前来源应如何生成管易平台单号。",
                reason="平台单号是导入和防重的关键字段，不能使用未确认规则。",
                answer_type="confirmation",
            )
        )

    used_defaults = {
        "payment_amount": "支付金额",
        "unit_price": "价格",
        "freight": "运费",
        "warehouse_name": "仓库名称",
        "payment_method": "支付方式",
        "order_type": "订单类型",
        "is_mobile_order": "是否手机订单",
        "is_cod": "是否货到付款",
        "is_distributor_order": "是否分销商订单",
    }
    if not rules.get("buyer_member_policy"):
        used_defaults["buyer_member"] = "买家会员"
    store_policy = rules.get("store_assignment_policy") or {}
    if store_policy:
        if not store_policy.get("confirmed", False):
            requests.append(
                ClarificationRequest(
                    code="CONFIRM_GUANYI_STORE_ASSIGNMENT_POLICY",
                    scope="batch",
                    field="店铺",
                    question=store_policy.get("question")
                    or "请确认当前来源的管易店铺分配规则。",
                    reason="店铺不能仅由来源公司默认，当前商品店铺规则尚未确认。",
                    answer_type="confirmation",
                )
            )
    else:
        used_defaults["store"] = "店铺"
    defaults = rules.get("defaults", {})
    for key, column in used_defaults.items():
        rule = defaults.get(key)
        if not rule:
            requests.append(
                ClarificationRequest(
                    code="MISSING_GUANYI_REQUIRED_RULE",
                    scope="batch",
                    field=column,
                    question=f"请提供管易必填列“{column}”的取值规则。",
                    reason="平台规则配置中没有该必填字段。",
                    answer_type="text",
                )
            )
        elif not rule.get("confirmed", False):
            requests.append(
                ClarificationRequest(
                    code=f"CONFIRM_GUANYI_{key.upper()}",
                    scope="batch",
                    field=column,
                    question=rule.get("question") or f"请确认“{column}”的默认值。",
                    reason="转换过程会写入该平台字段，当前默认值尚未确认。",
                    answer_type="confirmation",
                )
            )

    used_field_policies = {
        "product_name": "商品名称",
        "spec_name": "规格名称",
        "order_created_at": "订单创建时间",
        "contact_columns": "联系电话/联系手机",
        "recipient_region": "省/市/区",
    }
    field_policies = rules.get("field_output_policy") or {}
    for key, column in used_field_policies.items():
        rule = field_policies.get(key)
        if rule and not rule.get("confirmed", False):
            requests.append(
                ClarificationRequest(
                    code=f"CONFIRM_GUANYI_FIELD_POLICY_{key.upper()}",
                    scope="batch",
                    field=column,
                    question=rule.get("question") or f"请确认“{column}”的输出规则。",
                    reason="该字段输出策略尚未确认。",
                    answer_type="confirmation",
                )
            )
    for policy_key, field_name in {
        "buyer_member_policy": "买家会员",
        "item_expansion_policy": "组合商品展开",
        "logistics_policy": "物流公司",
        "seller_remark_policy": "卖家备注",
    }.items():
        policy = rules.get(policy_key)
        if policy_key == "item_expansion_policy" and not policy:
            continue
        if not policy:
            requests.append(
                ClarificationRequest(
                    code=f"MISSING_GUANYI_{policy_key.upper()}",
                    scope="batch",
                    field=field_name,
                    question=f"请补充“{field_name}”的业务策略。",
                    reason=f"平台规则缺少 {policy_key}。",
                    answer_type="text",
                )
            )
        elif not policy.get("confirmed", False):
            requests.append(
                ClarificationRequest(
                    code=f"CONFIRM_GUANYI_{policy_key.upper()}",
                    scope="batch",
                    field=field_name,
                    question=policy.get("question") or f"请确认“{field_name}”的业务策略。",
                    reason=f"{policy_key} 尚未确认。",
                    answer_type="confirmation",
                )
            )
    return requests


def _platform_order_no(
    order: ParsedOrder,
    rules: dict[str, Any],
    platform_profile: dict[str, Any],
) -> str:
    number_rule = rules["platform_order_number"]
    strategy = number_rule["strategy"]
    if strategy == "source_order_no":
        base_number = order.source.source_order_no or ""
    elif strategy == "source_order_no_with_affixes":
        source_order_no = order.source.source_order_no or ""
        prefix = str(number_rule.get("prefix") or "")
        suffix = str(number_rule.get("suffix") or "")
        base_number = f"{prefix}{source_order_no}{suffix}"
    else:
        raise GuanyiCustomImportError(f"未知平台单号策略: {strategy}")

    identity = platform_profile.get("automation_identity_policy") or {}
    if not identity.get("confirmed", False):
        raise GuanyiCustomImportError("管易自动化订单标识策略尚未确认")
    identity_strategy = str(identity.get("strategy") or "")
    if identity_strategy != "append_suffix":
        raise GuanyiCustomImportError(
            f"未知管易自动化订单标识策略: {identity_strategy or '未配置'}"
        )
    automation_suffix = str(identity.get("suffix") or "")
    if not automation_suffix:
        raise GuanyiCustomImportError("管易自动化订单尾缀不能为空")
    return f"{base_number}{automation_suffix}"


def _product_resolution_key(order: ParsedOrder, item: Any) -> tuple[str, str, str, str]:
    return (
        order.source.profile_id,
        str(item.source_product_code or ""),
        str(item.source_product_name or ""),
        str(item.source_spec or ""),
    )


def _product_source_label(item: Any) -> str:
    return str(item.source_product_code or item.source_product_name or "未提供商品标识")


def build_custom_import_lines(
    orders: list[ParsedOrder],
    catalog: ProductCatalog,
    rules: dict[str, Any],
    platform_profile: dict[str, Any],
    *,
    business_date: date | None = None,
    official_mapping_ready: bool = True,
    official_mapping_error: str = "",
) -> GuanyiBuildResult:
    result = GuanyiBuildResult()
    result.clarifications.extend(_rule_clarifications(rules))

    resolved_products: dict[tuple[str, str, str, str], Any] = {}
    resolved_lines_by_order: dict[int, list[ResolvedLineItem]] = {}
    official_mapping_clarification_added = False
    for order in orders:
        order_lines: list[ResolvedLineItem] = []
        for item in order.items:
            expansion = expand_source_item(item, catalog, rules)
            if expansion.failed:
                result.clarifications.append(
                    ClarificationRequest(
                        code=expansion.error_code,
                        scope="batch",
                        source_ref=f"商品:{_product_source_label(item)}",
                        field="组合商品展开",
                        question="当前组合商品无法安全展开，请先修正规则或商品主档。",
                        reason=expansion.reason,
                        answer_type="text",
                    )
                )
                continue
            if expansion.applied:
                order_lines.extend(expansion.lines)
                continue

            if not official_mapping_ready:
                if not official_mapping_clarification_added:
                    result.clarifications.append(
                        ClarificationRequest(
                            code="FEISHU_OFFICIAL_MAPPING_UNAVAILABLE",
                            scope="batch",
                            field="飞书正式商品映射",
                            question=(
                                "无法读取并验证飞书正式商品映射。请先完成飞书授权或正式映射同步后重试。"
                            ),
                            reason=(
                                official_mapping_error
                                or "正式映射快照不存在、读取失败或未通过完整性校验。"
                            ),
                            answer_type="text",
                            next_action="sync_feishu_official_mappings",
                        )
                    )
                    official_mapping_clarification_added = True
                continue

            resolution_key = _product_resolution_key(order, item)
            resolution = resolved_products.get(resolution_key)
            if resolution is None:
                resolution = catalog.resolve(
                    item.source_product_code,
                    source_profile_id=order.source.profile_id,
                    source_product_name=item.source_product_name,
                    source_spec=item.source_spec,
                )
                resolved_products[resolution_key] = resolution
            source_label = _product_source_label(item)
            source_ref = f"商品:{source_label}"
            if resolution.status == "unconfirmed" and resolution.product:
                result.clarifications.append(
                    ClarificationRequest(
                        code="CONFIRM_GUANYI_PRODUCT_MAPPING",
                        scope="batch",
                        source_ref=source_ref,
                        field="商品代码/规格代码",
                        question=(
                            f"请确认：来源商品“{source_label}”是否对应管易商品代码"
                            f"“{resolution.product.product_code}”、规格代码"
                            f"“{resolution.product.spec_code}”？"
                        ),
                        reason=resolution.reason or "当前商品映射被标记为待确认。",
                        answer_type="confirmation",
                    )
                )
            elif resolution.status != "confirmed":
                result.clarifications.append(
                    ClarificationRequest(
                        code="PROVIDE_GUANYI_PRODUCT_MAPPING",
                        scope="batch",
                        source_ref=source_ref,
                        field="商品代码/规格代码",
                        question=f"请提供来源商品“{source_label}”对应的管易商品代码和规格代码。",
                        reason=resolution.reason or "商品主数据中没有唯一匹配。",
                        answer_type="text",
                    )
                )
            elif resolution.product:
                order_lines.append(
                    ResolvedLineItem(
                        source_item=item,
                        product=resolution.product,
                        quantity=item.quantity,
                    )
                )
        resolved_lines_by_order[id(order)] = order_lines

    result.clarifications = deduplicate_clarifications(result.clarifications)
    if result.clarifications:
        return result

    columns = platform_profile["columns"]
    product_name_strategy = _field_strategy(
        rules, "product_name", "catalog_product_name"
    )
    spec_name_strategy = _field_strategy(rules, "spec_name", "catalog_spec_name")
    order_created_at_strategy = _field_strategy(
        rules, "order_created_at", "source_ordered_at"
    )
    contact_strategy = _field_strategy(
        rules, "contact_columns", "mobile_to_both_phone_preserved"
    )
    recipient_region_strategy = _field_strategy(
        rules, "recipient_region", "source_components"
    )
    seen_platform_numbers: dict[str, str] = {}
    for order in orders:
        platform_no = _platform_order_no(order, rules, platform_profile)
        previous = seen_platform_numbers.get(platform_no)
        if previous and previous != order.record_key:
            result.clarifications.append(
                ClarificationRequest(
                    code="DUPLICATE_GUANYI_PLATFORM_ORDER_NO",
                    scope="batch",
                    field="平台单号",
                    question=f"平台单号“{platform_no}”对应多张订单，请确认单号生成规则。",
                    reason="管易平台单号必须唯一标识一张订单。",
                    answer_type="text",
                )
            )
            continue
        seen_platform_numbers[platform_no] = order.record_key
        shipment = order.shipment_facts
        resolved_line_items = resolved_lines_by_order[id(order)]
        resolved_order_items = [
            ResolvedOrderItem(
                quantity=line_item.quantity,
                product_code=line_item.product.product_code,
                spec_code=line_item.product.spec_code,
                product_name=line_item.product.product_name,
                spec_name=line_item.product.spec_name,
            )
            for line_item in resolved_line_items
        ]
        try:
            store_name = (
                select_store(resolved_order_items, rules)
                if rules.get("store_assignment_policy")
                else _rule_value(rules, "store")
            )
        except GuanyiStoreAssignmentError as exc:
            result.clarifications.append(
                ClarificationRequest(
                    code="CONFIRM_GUANYI_STORE_ASSIGNMENT",
                    scope="order",
                    source_ref=f"订单:{platform_no}",
                    field="店铺",
                    question="当前商品没有唯一、已确认的管易店铺，请确认应使用哪个店铺。",
                    reason=str(exc),
                    answer_type="text",
                )
            )
            continue
        try:
            carrier_name = select_logistics_carrier(order, resolved_order_items, rules)
        except GuanyiOrderPolicyError as exc:
            result.clarifications.append(
                ClarificationRequest(
                    code="GUANYI_LOGISTICS_POLICY_INVALID",
                    scope="batch",
                    source_ref=f"订单:{platform_no}",
                    field="物流公司",
                    question="当前物流规则无法安全执行，请先修正规则配置。",
                    reason=str(exc),
                    answer_type="text",
                )
            )
            continue
        try:
            buyer_member = select_buyer_member(
                order,
                rules,
                resolved_order_items,
                business_date=business_date,
            )
        except GuanyiOrderPolicyError as exc:
            result.clarifications.append(
                ClarificationRequest(
                    code="GUANYI_BUYER_MEMBER_POLICY_INVALID",
                    scope="order",
                    source_ref=f"订单:{platform_no}",
                    field="买家会员",
                    question="当前买家会员规则无法安全执行，请先修正规则配置。",
                    reason=str(exc),
                    answer_type="text",
                )
            )
            continue
        seller_policy = rules.get("seller_remark_policy") or {}
        seller_strategy = str(seller_policy.get("strategy") or "")
        if seller_strategy != "source_note_then_system_notes_deduplicated":
            result.clarifications.append(
                ClarificationRequest(
                    code="GUANYI_SELLER_REMARK_POLICY_INVALID",
                    scope="batch",
                    source_ref=f"订单:{platform_no}",
                    field="卖家备注",
                    question="当前卖家备注规则无法安全执行，请先修正规则配置。",
                    reason=f"未知卖家备注策略: {seller_strategy or '未配置'}",
                    answer_type="text",
                )
            )
            continue
        separator = str(seller_policy.get("separator") or "；")
        seller_remark = compose_seller_remark(order.source_note, separator=separator)
        if contact_strategy == "mobile_to_both_phone_preserved":
            contact_phone = order.recipient.mobile or order.recipient.phone
            contact_mobile = order.recipient.mobile
        elif contact_strategy == "source_split":
            contact_phone = order.recipient.phone
            contact_mobile = order.recipient.mobile
        else:
            raise GuanyiCustomImportError(
                f"未知联系电话/联系手机输出策略: {contact_strategy}"
            )
        if recipient_region_strategy == "source_components":
            province = order.recipient.province
            city = order.recipient.city
            district = order.recipient.district
        elif recipient_region_strategy == "blank":
            province = city = district = ""
        else:
            raise GuanyiCustomImportError(
                f"未知省/市/区输出策略: {recipient_region_strategy}"
            )
        for line_item in resolved_line_items:
            item = line_item.source_item
            product = line_item.product
            line = {column: "" for column in columns}
            line.update(
                {
                    "店铺": store_name,
                    "平台单号": platform_no,
                    "买家会员": buyer_member,
                    "支付金额": _rule_value(rules, "payment_amount"),
                    "商品名称": _product_name_output_value(
                        product_name_strategy,
                        line_item,
                    ),
                    "商品代码": product.product_code,
                    "规格代码": product.spec_code,
                    "规格名称": _output_value(
                        spec_name_strategy,
                        product.spec_name,
                        "catalog_spec_name",
                        "规格名称",
                    ),
                    "是否赠品": 0,
                    "数量": line_item.quantity,
                    "价格": (
                        item.unit_price
                        if item.unit_price is not None
                        and not line_item.expansion_rule_id
                        else _rule_value(rules, "unit_price")
                    ),
                    "运费": (
                        order.amounts.freight
                        if order.amounts and order.amounts.freight is not None
                        else _rule_value(rules, "freight")
                    ),
                    "买家留言": order.buyer_message or "",
                    "收货人": order.recipient.name,
                    "联系电话": contact_phone,
                    "联系手机": contact_mobile,
                    "收货地址": order.recipient.full_address,
                    "省": province,
                    "市": city,
                    "区": district,
                    "订单创建时间": _output_value(
                        order_created_at_strategy,
                        order.ordered_at,
                        "source_ordered_at",
                        "订单创建时间",
                    ),
                    "订单付款时间": order.paid_at or "",
                    "物流单号": shipment.tracking_no if shipment else "",
                    "物流公司": carrier_name,
                    "卖家备注": seller_remark,
                    "是否手机订单": _rule_value(rules, "is_mobile_order"),
                    "是否货到付款": _rule_value(rules, "is_cod"),
                    "支付方式": _rule_value(rules, "payment_method"),
                    "仓库名称": _rule_value(rules, "warehouse_name"),
                    "订单类型": _rule_value(rules, "order_type"),
                    "是否分销商订单": _rule_value(rules, "is_distributor_order"),
                }
            )
            result.lines.append(line)

    result.clarifications = deduplicate_clarifications(result.clarifications)
    if result.clarifications:
        result.lines = []
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_custom_import(
    template_path: str | Path,
    output_path: str | Path,
    lines: list[dict[str, Any]],
    platform_profile: dict[str, Any],
) -> Path:
    if not lines:
        raise GuanyiCustomImportError("没有可导出的管易订单行")

    template = Path(template_path)
    expected_hash = platform_profile.get("template_sha256")
    if expected_hash and _sha256(template) != expected_hash:
        raise GuanyiCustomImportError("管易模板文件与已确认版本不一致")

    workbook = load_workbook(template, read_only=False, data_only=False)
    sheet_name = platform_profile["sheet_name"]
    if sheet_name not in workbook.sheetnames:
        workbook.close()
        raise GuanyiCustomImportError(f"管易模板缺少工作表: {sheet_name}")
    sheet = workbook[sheet_name]
    header_row = int(platform_profile["header_row"])
    headers = [cell.value for cell in sheet[header_row] if cell.value is not None]
    if headers != platform_profile["columns"]:
        workbook.close()
        raise GuanyiCustomImportError("管易模板表头与平台配置不一致")

    donor_row = header_row + 1
    donor_styles = [copy(sheet.cell(donor_row, col)._style) for col in range(1, len(headers) + 1)]
    donor_height = sheet.row_dimensions[donor_row].height
    if sheet.max_row > header_row:
        sheet.delete_rows(header_row + 1, sheet.max_row - header_row)

    identifier_columns = set(platform_profile.get("identifier_columns", []))
    for row_offset, line in enumerate(lines, start=1):
        row_no = header_row + row_offset
        if donor_height is not None:
            sheet.row_dimensions[row_no].height = donor_height
        for col_no, column in enumerate(headers, start=1):
            cell = sheet.cell(row_no, col_no)
            cell._style = copy(donor_styles[col_no - 1])
            value = line.get(column, "")
            if column in identifier_columns and value not in (None, ""):
                value = str(value)
                cell.number_format = "@"
            cell.value = value

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    workbook.close()

    verification = load_workbook(output, read_only=False, data_only=False)
    try:
        sheet = verification[sheet_name]
        saved_headers = [cell.value for cell in sheet[header_row] if cell.value is not None]
        if saved_headers != headers:
            raise GuanyiCustomImportError("保存后的管易模板表头发生变化")
        if sheet.max_row != header_row + len(lines):
            raise GuanyiCustomImportError("保存后的管易订单行数不正确")
    finally:
        verification.close()
    return output
