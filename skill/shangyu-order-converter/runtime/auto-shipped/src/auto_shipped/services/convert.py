from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from auto_shipped.catalog import (
    ProductCatalog,
    WhiteLabelLibraryError,
    load_white_label_library,
)
from auto_shipped.companies import (
    CompanyRegistryError,
    load_company_registry,
    resolve_company_abbreviation_references,
    resolve_company_by_identity,
    resolve_company_by_source_profile,
    validate_company_workflow,
)
from auto_shipped.detection import DetectionResult, detect_source, load_source_profiles
from auto_shipped.domain import ClarificationRequest, deduplicate_clarifications
from auto_shipped.platforms.guanyi import (
    GuanyiCustomImportError,
    GuanyiPolicyModuleError,
    build_custom_import_lines,
    preflight_custom_import,
    render_custom_import,
    resolve_guanyi_policy_modules,
)
from auto_shipped.routing import RouteDecision, load_routing, resolve_route
from auto_shipped.rules import audit_rule_coverage, load_rule_catalog
from auto_shipped.source_adapters import (
    AdaptiveExcelParsedAdapter,
    AdaptiveExcelPlanError,
    ConfiguredExcelParsedAdapter,
    NddGiftOrderParsedAdapter,
    TiantianWarehouseParsedAdapter,
    load_adaptive_plan,
)
from auto_shipped.services.adaptive import (
    propose_adaptive_excel_plan,
    write_adaptive_plan,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


_COVERAGE_DUPLICATE_CLARIFICATION_CODES = {
    "GY-PAYMENT-VALUES": {
        "CONFIRM_GUANYI_PAYMENT_AMOUNT",
        "CONFIRM_GUANYI_UNIT_PRICE",
        "CONFIRM_GUANYI_FREIGHT",
    },
    "GY-WAREHOUSE-ASSIGNMENT": {"CONFIRM_GUANYI_WAREHOUSE_NAME"},
    "GY-PAYMENT-METHOD": {"CONFIRM_GUANYI_PAYMENT_METHOD"},
    "GY-ORDER-TYPE": {"CONFIRM_GUANYI_ORDER_TYPE"},
    "GY-ORDER-FLAGS": {
        "CONFIRM_GUANYI_IS_MOBILE_ORDER",
        "CONFIRM_GUANYI_IS_COD",
        "CONFIRM_GUANYI_IS_DISTRIBUTOR_ORDER",
    },
}


@dataclass(slots=True)
class ConversionResult:
    status: str
    source_file: str
    detection: DetectionResult | None = None
    route: RouteDecision | None = None
    parsed_order_count: int = 0
    output_row_count: int = 0
    outputs: list[str] = field(default_factory=list)
    upload_manifest: str | None = None
    clarifications: list[ClarificationRequest] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_file": self.source_file,
            "detection": self.detection.to_dict() if self.detection else None,
            "route": self.route.to_dict() if self.route else None,
            "parsed_order_count": self.parsed_order_count,
            "output_row_count": self.output_row_count,
            "outputs": self.outputs,
            "upload_manifest": self.upload_manifest,
            "clarifications": [item.to_dict() for item in self.clarifications],
            "errors": self.errors,
        }


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _find_profile(profiles: list[dict[str, Any]], profile_id: str) -> dict[str, Any]:
    for profile in profiles:
        if profile.get("profile_id") == profile_id:
            return profile
    raise KeyError(profile_id)


def _find_json_profile(directory: str | Path, profile_id: str) -> dict[str, Any] | None:
    for path in sorted(Path(directory).rglob("*.json")):
        data = _load_json(path)
        if data.get("profile_id") == profile_id:
            return data
    return None


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("._")
    return cleaned or "orders"


def _needs_input(
    source_file: str,
    clarifications: list[ClarificationRequest],
    detection: DetectionResult | None = None,
    route: RouteDecision | None = None,
    parsed_order_count: int = 0,
) -> ConversionResult:
    return ConversionResult(
        status="needs_input",
        source_file=source_file,
        detection=detection,
        route=route,
        parsed_order_count=parsed_order_count,
        clarifications=deduplicate_clarifications(clarifications),
    )


def _adaptive_detection(
    plan: dict[str, Any],
    profiles: list[dict[str, Any]],
    *,
    company_hint: str | None,
) -> DetectionResult:
    company_id = str(plan.get("company_id") or "").strip()
    profile_id = str(plan.get("rule_source_profile_id") or "").strip()
    if not company_id or not profile_id:
        raise AdaptiveExcelPlanError(
            "自适应计划缺少company_id或rule_source_profile_id。"
        )
    if company_hint and company_hint != company_id:
        raise AdaptiveExcelPlanError("用户指定公司与自适应计划公司不一致。")
    profile = _find_profile(profiles, profile_id)
    if profile.get("company_id") != company_id:
        raise AdaptiveExcelPlanError("自适应计划公司与规则来源配置归属不一致。")
    return DetectionResult(
        status="matched",
        confidence="high",
        company_id=company_id,
        source_profile_id=profile_id,
        source_label=profile.get("source_label") or profile_id,
        order_type=profile.get("order_type") or "unknown",
        matched_features=[
            f"explicit_company:{company_id}",
            f"adaptive_plan:{plan.get('plan_id') or 'unversioned'}",
            f"rule_source_profile:{profile_id}",
        ],
        candidate_profile_ids=[profile_id],
    )


def _known_detection_for_company(
    source: Path,
    profiles: list[dict[str, Any]],
    company_id: str,
) -> tuple[DetectionResult | None, list[str]]:
    matched: list[DetectionResult] = []
    for profile in profiles:
        if profile.get("company_id") != company_id:
            continue
        profile_id = str(profile.get("profile_id") or "")
        if not profile_id:
            continue
        candidate = detect_source(source, profiles, source_profile_hint=profile_id)
        if candidate.status == "matched":
            matched.append(candidate)
    if len(matched) == 1:
        return matched[0], []
    return None, [
        str(item.source_profile_id)
        for item in matched
        if item.source_profile_id
    ]


def convert_order_file(
    source_path: str | Path,
    catalog_csv: str | Path,
    output_dir: str | Path,
    *,
    source_profiles_dir: str | Path = PROJECT_ROOT / "config/source_profiles",
    company_registry_path: str | Path = PROJECT_ROOT
    / "config/companies/company_registry_v1.json",
    routing_path: str | Path = PROJECT_ROOT / "config/routing/order_routes_v1.json",
    platform_profiles_dir: str | Path = PROJECT_ROOT / "config/platform_profiles",
    platform_rules_dir: str | Path = PROJECT_ROOT / "config/platform_rules",
    business_rule_catalog_path: str | Path = PROJECT_ROOT
    / "config/business_rules/shangyu_sop_rule_catalog_v1.json",
    mappings_path: str | Path = PROJECT_ROOT / "config/catalog/external_sku_mappings.json",
    package_semantics_path: str | Path = PROJECT_ROOT
    / "config/catalog/package_semantics_v1.json",
    white_label_library_path: str | Path = PROJECT_ROOT
    / "config/catalog/white_label_skus_v1.json",
    guanyi_template_path: str | Path = PROJECT_ROOT
    / "assets/templates/guanyi/自定义订单导入模板.xlsx",
    source_profile_hint: str | None = None,
    company_hint: str | None = None,
    adaptive_plan_path: str | Path | None = None,
    mapping_overlay_paths: tuple[str | Path, ...] = (),
    official_mapping_ready: bool = True,
    official_mapping_error: str = "",
) -> ConversionResult:
    source = Path(source_path)
    source_file = source.name
    profiles = load_source_profiles(source_profiles_dir)
    company_registry: dict[str, Any] | None = None
    if company_hint:
        try:
            company_registry = load_company_registry(company_registry_path)
        except (OSError, json.JSONDecodeError, CompanyRegistryError) as exc:
            return _needs_input(
                source_file,
                [
                    ClarificationRequest(
                        code="COMPANY_REGISTRY_UNAVAILABLE",
                        scope="batch",
                        question="公司库无法读取或结构不合法，请先恢复公司配置。",
                        reason=str(exc),
                        answer_type="file",
                    )
                ],
            )
        identity_resolution = resolve_company_by_identity(
            company_registry,
            company_hint,
        )
        if (
            identity_resolution.status != "resolved"
            or identity_resolution.company is None
        ):
            return _needs_input(
                source_file,
                [
                    ClarificationRequest(
                        code=identity_resolution.code or "COMPANY_NOT_REGISTERED",
                        scope="batch",
                        field="company_hint",
                        question="该公司尚未登记可执行订单流程，请先补充公司模板与业务规则。",
                        reason=identity_resolution.reason or company_hint,
                        answer_type="text",
                    )
                ],
            )
        company_hint = identity_resolution.company.company_id
    adaptive_plan: dict[str, Any] | None = None
    try:
        if adaptive_plan_path is not None:
            adaptive_plan = load_adaptive_plan(adaptive_plan_path)
            detection = _adaptive_detection(
                adaptive_plan,
                profiles,
                company_hint=company_hint,
            )
        else:
            detection = detect_source(
                source,
                profiles,
                source_profile_hint=source_profile_hint,
            )
    except (AdaptiveExcelPlanError, KeyError) as exc:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="ADAPTIVE_PLAN_INVALID",
                    scope="file",
                    source_ref=source_file,
                    question="自适应字段映射计划无法使用，请重新分析当前Excel。",
                    reason=str(exc),
                    answer_type="file",
                )
            ],
        )

    if adaptive_plan is None and company_hint:
        if detection.status == "matched" and detection.company_id != company_hint:
            return _needs_input(
                source_file,
                [
                    ClarificationRequest(
                        code="EXPLICIT_COMPANY_CONFLICTS_WITH_SOURCE",
                        scope="file",
                        source_ref=source_file,
                        question="用户指定公司与Excel中的已知公司证据冲突，请确认实际来源。",
                        reason=(
                            f"company_hint={company_hint}; "
                            f"detected_company={detection.company_id}"
                        ),
                        answer_type="text",
                    )
                ],
                detection=detection,
            )
        if detection.status != "matched":
            known_detection, ambiguous_profiles = _known_detection_for_company(
                source,
                profiles,
                company_hint,
            )
            if known_detection is not None:
                detection = known_detection
            elif ambiguous_profiles:
                return _needs_input(
                    source_file,
                    [
                        ClarificationRequest(
                            code="EXPLICIT_COMPANY_FORMAT_AMBIGUOUS",
                            scope="file",
                            source_ref=source_file,
                            question="该公司的Excel同时匹配多个已登记模板，请确认具体订单类型。",
                            reason="候选来源配置：" + ", ".join(ambiguous_profiles),
                            answer_type="single_choice",
                            choices=tuple(ambiguous_profiles),
                        )
                    ],
                    detection=detection,
                )
            else:
                try:
                    registry_for_adaptive = load_company_registry(company_registry_path)
                    proposal = propose_adaptive_excel_plan(
                        source,
                        company_hint,
                        registry_for_adaptive,
                    )
                except (
                    OSError,
                    json.JSONDecodeError,
                    CompanyRegistryError,
                    AdaptiveExcelPlanError,
                ) as exc:
                    return _needs_input(
                        source_file,
                        [
                            ClarificationRequest(
                                code="ADAPTIVE_SOURCE_ANALYSIS_FAILED",
                                scope="file",
                                source_ref=source_file,
                                question="无法分析这个新Excel格式，请检查文件和公司配置。",
                                reason=str(exc),
                                answer_type="file",
                            )
                        ],
                        detection=detection,
                    )
                if proposal.plan is None:
                    return _needs_input(
                        source_file,
                        proposal.clarifications,
                        detection=detection,
                    )
                plan_path = (
                    Path(output_dir).expanduser().resolve()
                    / f"{_safe_stem(source.stem)}_自适应来源计划.json"
                )
                write_adaptive_plan(plan_path, proposal.plan)
                if proposal.clarifications:
                    draft_detection = _adaptive_detection(
                        proposal.plan,
                        profiles,
                        company_hint=company_hint,
                    )
                    draft_detection.status = "needs_input"
                    draft_detection.confidence = "medium"
                    draft_detection.matched_features.append("adaptive_plan:draft")
                    next_action = (
                        f"根据用户回答更新计划文件{plan_path}，再通过"
                        "--adaptive-plan重新运行。"
                    )
                    questions = [
                        replace(item, next_action=next_action)
                        for item in proposal.clarifications
                    ]
                    return _needs_input(
                        source_file,
                        questions,
                        detection=draft_detection,
                    )
                adaptive_plan = proposal.plan
                detection = _adaptive_detection(
                    adaptive_plan,
                    profiles,
                    company_hint=company_hint,
                )
    if detection.status != "matched" or not detection.source_profile_id:
        return _needs_input(source_file, detection.clarifications, detection=detection)

    routing = load_routing(routing_path)
    route_result = resolve_route(
        routing,
        detection.source_profile_id,
        detection.order_type or "unknown",
    )
    if route_result.status != "resolved" or route_result.decision is None:
        return _needs_input(
            source_file,
            route_result.clarifications,
            detection=detection,
        )
    route = route_result.decision

    try:
        source_profile = _find_profile(profiles, detection.source_profile_id)
    except KeyError:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="SOURCE_PROFILE_NOT_LOADED",
                    scope="file",
                    question="来源已经识别，但配置无法加载，请检查来源配置。",
                    reason=detection.source_profile_id,
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
        )

    if company_registry is None:
        try:
            company_registry = load_company_registry(company_registry_path)
        except (OSError, json.JSONDecodeError, CompanyRegistryError) as exc:
            return _needs_input(
                source_file,
                [
                    ClarificationRequest(
                        code="COMPANY_REGISTRY_UNAVAILABLE",
                        scope="batch",
                        question="公司库无法读取或结构不合法，请先恢复公司配置。",
                        reason=str(exc),
                        answer_type="file",
                    )
                ],
                detection=detection,
                route=route,
            )
    company_resolution = resolve_company_by_source_profile(
        company_registry,
        detection.source_profile_id,
    )
    if company_resolution.status != "resolved" or company_resolution.company is None:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code=company_resolution.code or "COMPANY_NOT_REGISTERED",
                    scope="batch",
                    question="已经识别文件来源，但公司库没有可执行的唯一公司记录，请先登记或修复。",
                    reason=company_resolution.reason or detection.source_profile_id,
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
        )
    company = company_resolution.company
    if source_profile.get("company_id") != company.company_id:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="SOURCE_PROFILE_COMPANY_MISMATCH",
                    scope="batch",
                    question="来源配置与公司库归属不一致，请先修复配置引用。",
                    reason=(
                        f"source_profile.company_id={source_profile.get('company_id')!r}; "
                        f"registry.company_id={company.company_id!r}"
                    ),
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
        )
    workflow_resolution = validate_company_workflow(
        company_registry,
        company.company_id,
        source_profile_id=detection.source_profile_id,
        route_id=route.route_id,
        target_platform=route.target_platform,
        target_profile_id=route.target_profile_id,
        platform_rules_profile_id=route.platform_rules_profile_id,
    )
    if workflow_resolution.status != "resolved":
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code=workflow_resolution.code or "COMPANY_WORKFLOW_NOT_REGISTERED",
                    scope="batch",
                    question="公司库中的处理流程与实际路由不一致，请先修复引用。",
                    reason=workflow_resolution.reason or route.route_id,
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
        )

    if adaptive_plan is not None:
        try:
            parsed = AdaptiveExcelParsedAdapter().parse(
                source,
                source_profile,
                adaptive_plan,
            )
        except AdaptiveExcelPlanError as exc:
            return _needs_input(
                source_file,
                [
                    ClarificationRequest(
                        code="ADAPTIVE_PLAN_INVALID",
                        scope="file",
                        source_ref=source_file,
                        question="自适应字段映射计划无法使用，请重新分析当前Excel。",
                        reason=str(exc),
                        answer_type="file",
                    )
                ],
                detection=detection,
                route=route,
            )
    else:
        adapter_id = source_profile.get("adapter")
        adapter_types = {
            TiantianWarehouseParsedAdapter.adapter_id: TiantianWarehouseParsedAdapter,
            ConfiguredExcelParsedAdapter.adapter_id: ConfiguredExcelParsedAdapter,
            NddGiftOrderParsedAdapter.adapter_id: NddGiftOrderParsedAdapter,
        }
        adapter_type = adapter_types.get(str(adapter_id or ""))
        if adapter_type is None:
            return _needs_input(
                source_file,
                [
                    ClarificationRequest(
                        code="SOURCE_ADAPTER_NOT_IMPLEMENTED",
                        scope="file",
                        question="这个来源已经识别，但对应的解析器尚未实现。是否现在为它新增适配器？",
                        reason=f"adapter={adapter_id}",
                        answer_type="confirmation",
                    )
                ],
                detection=detection,
                route=route,
            )
        parsed = adapter_type().parse(source, source_profile)
    if parsed.clarifications:
        return _needs_input(
            source_file,
            parsed.clarifications,
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )

    if route.target_platform != "guanyi":
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="TARGET_PLATFORM_NOT_IMPLEMENTED",
                    scope="file",
                    question=f"目标平台“{route.target_platform}”尚未启用。是否先人工处理？",
                    reason="第一版只实现管易自定义订单导入。",
                    answer_type="confirmation",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )

    platform_profile = _find_json_profile(platform_profiles_dir, route.target_profile_id)
    if platform_profile is None:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="TARGET_PROFILE_NOT_FOUND",
                    scope="file",
                    question="管易平台配置缺失，请先恢复或确认平台配置。",
                    reason=route.target_profile_id,
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )

    if not route.platform_rules_profile_id:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="PLATFORM_RULES_NOT_ROUTED",
                    scope="batch",
                    question="已经确定进入管易，但还没有配置这个来源的管易业务规则。是否现在补充？",
                    reason=route.route_id,
                    answer_type="confirmation",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )
    rules = _find_json_profile(platform_rules_dir, route.platform_rules_profile_id)
    if rules is None:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="PLATFORM_RULES_NOT_FOUND",
                    scope="batch",
                    question="管易业务规则配置缺失，请先恢复或确认规则。",
                    reason=route.platform_rules_profile_id,
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )
    try:
        rules = resolve_guanyi_policy_modules(rules, platform_rules_dir)
        rules = resolve_company_abbreviation_references(rules, company)
    except GuanyiPolicyModuleError as exc:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="PLATFORM_POLICY_MODULE_INVALID",
                    scope="batch",
                    question="管易共享规则模块无法解析，请先修复规则配置。",
                    reason=str(exc),
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )
    except CompanyRegistryError as exc:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="PLATFORM_COMPANY_ABBREVIATION_INVALID",
                    scope="batch",
                    question="当前公司简称无法用于平台规则，请先修复公司库或规则引用。",
                    reason=str(exc),
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )

    if not route.business_rule_scope_id:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="BUSINESS_RULE_SCOPE_NOT_ROUTED",
                    scope="batch",
                    question="这个来源没有绑定SOP业务规则范围，请先补充规则覆盖配置。",
                    reason=route.route_id,
                    answer_type="text",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )
    try:
        business_rule_catalog = load_rule_catalog(business_rule_catalog_path)
    except (OSError, json.JSONDecodeError) as exc:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="BUSINESS_RULE_CATALOG_UNAVAILABLE",
                    scope="batch",
                    question="无法读取SOP业务规则目录，请先恢复规则文件。",
                    reason=str(exc),
                    answer_type="file",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )
    coverage = audit_rule_coverage(
        business_rule_catalog,
        rules,
        route.business_rule_scope_id,
        phase="conversion",
    )
    if coverage.status == "invalid":
        return _needs_input(
            source_file,
            coverage.clarifications,
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )

    try:
        catalog = ProductCatalog.from_files(
            catalog_csv,
            mappings_path,
            policy_paths=(package_semantics_path,),
            mapping_overlay_paths=mapping_overlay_paths,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="PRODUCT_CATALOG_UNAVAILABLE",
                    scope="batch",
                    question="无法读取管易商品资料或商品映射，请提供最新的商品信息CSV和映射配置。",
                    reason=str(exc),
                    answer_type="file",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )
    try:
        white_label_library = load_white_label_library(white_label_library_path)
    except WhiteLabelLibraryError as exc:
        return _needs_input(
            source_file,
            [
                ClarificationRequest(
                    code="WHITE_LABEL_LIBRARY_UNAVAILABLE",
                    scope="batch",
                    question="白标SKU库无法安全使用，请先修复版本化白标配置。",
                    reason=str(exc),
                    answer_type="file",
                )
            ],
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )
    built = build_custom_import_lines(
        parsed.orders,
        catalog,
        rules,
        platform_profile,
        white_label_library=white_label_library,
        official_mapping_ready=official_mapping_ready,
        official_mapping_error=official_mapping_error,
    )
    duplicate_codes = {
        code
        for rule_id in coverage.blocked_rule_ids
        for code in _COVERAGE_DUPLICATE_CLARIFICATION_CODES.get(rule_id, set())
    }
    if duplicate_codes:
        built.clarifications = [
            item for item in built.clarifications if item.code not in duplicate_codes
        ]
    built.clarifications = deduplicate_clarifications(
        coverage.clarifications + built.clarifications
    )
    if built.clarifications:
        built.lines = []
    if built.clarifications:
        return _needs_input(
            source_file,
            built.clarifications,
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
        )

    output_root = Path(output_dir)
    output_name = f"{_safe_stem(source.stem)}_管易自定义订单导入.xlsx"
    output_path = output_root / output_name
    try:
        rendered = render_custom_import(
            guanyi_template_path,
            output_path,
            built.lines,
            platform_profile,
        )
    except (GuanyiCustomImportError, OSError) as exc:
        return ConversionResult(
            status="blocked",
            source_file=source_file,
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
            errors=[{"code": "GUANYI_RENDER_FAILED", "message": str(exc)}],
        )

    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / f"{_safe_stem(source.stem)}_上传预检.json"
    preflight = preflight_custom_import(rendered, platform_profile)
    preflight.write_manifest(manifest_path)
    if preflight.status != "ready":
        return ConversionResult(
            status="blocked",
            source_file=source_file,
            detection=detection,
            route=route,
            parsed_order_count=len(parsed.orders),
            output_row_count=len(built.lines),
            upload_manifest=str(manifest_path),
            errors=[issue.to_dict() for issue in preflight.issues],
        )

    result = ConversionResult(
        status="ready",
        source_file=source_file,
        detection=detection,
        route=route,
        parsed_order_count=len(parsed.orders),
        output_row_count=len(built.lines),
        outputs=[str(rendered)],
        upload_manifest=str(manifest_path),
    )
    report_path = output_root / f"{_safe_stem(source.stem)}_处理报告.json"
    report_path.write_text(
        json.dumps(result.to_public_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result.outputs.append(str(report_path))
    return result
