from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from auto_shipped.catalog import (
    BatchMappingReplacementError,
    PackageSemanticsStoreError,
    ProductCatalog,
    ProductMappingStoreError,
    export_package_semantics_proposals,
    list_package_semantics_proposals,
    save_package_semantics_proposal,
    save_batch_mapping_replacement,
    save_confirmed_mapping,
)
from auto_shipped.companies import audit_company_registry, load_company_registry
from auto_shipped.integrations.feishu import (
    ALLOWED_FEEDBACK_TYPES,
    ALLOWED_REUSE_SCOPES,
    CORRECTION_FEEDBACK_CONFIRMATION_TOKEN,
    LarkCliFeishuSheetGateway,
    LarkCliGatewayError,
    OfficialMappingSyncError,
    build_official_mapping_snapshot,
    add_confirmed_mapping_direct,
    build_correction_feedback_record,
    build_pending_mapping_proposal,
    load_official_mapping_snapshot,
    plan_pending_mapping_publish,
    publish_pending_mappings,
    resolve_or_provision_user_sheet,
    submit_pending_mapping_proposal,
    submit_correction_feedback,
    collect_correction_feedback,
    write_official_mapping_snapshot,
)
from auto_shipped.services.convert import convert_order_file
from auto_shipped.services.batch_convert import convert_order_batch
from auto_shipped.services.adaptive import (
    propose_adaptive_excel_plan,
    write_adaptive_plan,
)
from auto_shipped.services.ingest import ingest_file
from auto_shipped.services.redaction import redact_order
from auto_shipped.services.text_intake import (
    TextOrderDraftError,
    prepare_text_order_draft,
    validate_text_order_draft,
)
from auto_shipped.platforms.guanyi import preflight_custom_import
from auto_shipped.rules import (
    RulePackSchemaError,
    audit_rule_coverage,
    load_rule_catalog,
    load_rule_pack,
)
from auto_shipped.source_adapters import AdaptiveExcelPlanError, inspect_excel_structure


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEISHU_MAPPING_CONFIG = (
    PROJECT_ROOT / "config/integrations/feishu_mapping_governance_v1.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auto-shipped")
    subparsers = parser.add_subparsers(dest="command", required=True)
    preview = subparsers.add_parser("preview", help="解析来源文件并输出离线预览")
    preview.add_argument("--source", required=True)
    preview.add_argument("--catalog", required=True)
    preview.add_argument(
        "--profile",
        required=True,
        help="仅用于开发诊断的显式来源配置；正式转换请使用convert或convert-batch",
    )
    preview.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    preview.add_argument("--output", help="将预览 JSON 写入指定路径")
    preview.add_argument(
        "--include-pii",
        action="store_true",
        help="显式输出未脱敏收件信息；默认关闭",
    )

    convert = subparsers.add_parser(
        "convert",
        help="自动识别来源并生成目标平台Excel；不确定时输出结构化追问",
    )
    convert.add_argument("--source", required=True)
    convert.add_argument("--catalog", required=True)
    convert.add_argument("--output-dir", required=True)
    convert.add_argument(
        "--source-profile",
        help="用户已明确确认来源公司时提供来源配置ID；仍会校验Excel格式",
    )
    convert.add_argument(
        "--company",
        help="用户明确提供的稳定公司ID；未知模板将进入自适应来源流程",
    )
    convert.add_argument(
        "--adaptive-plan",
        help="当前Excel对应的自适应来源字段映射计划JSON",
    )

    convert_batch = subparsers.add_parser(
        "convert-batch",
        help="逐文件识别和预检后，把同一目标模板的多个订单文件合并成一个Excel",
    )
    convert_batch.add_argument(
        "--source",
        action="append",
        required=True,
        help="可重复提供的公司原始订单Excel；至少两个",
    )
    convert_batch.add_argument("--catalog", required=True)
    convert_batch.add_argument("--output-dir", required=True)
    convert_batch.add_argument("--batch-name", default="批量订单")
    convert_batch.add_argument(
        "--source-profile-hint",
        action="append",
        default=[],
        metavar="SOURCE=PROFILE_ID",
        help="按文件路径或文件名指定来源配置；可重复提供",
    )
    convert_batch.add_argument(
        "--company-hint",
        action="append",
        default=[],
        metavar="SOURCE=COMPANY_ID",
        help="按文件路径或文件名提供用户确认的公司ID；可重复提供",
    )
    convert_batch.add_argument(
        "--adaptive-plan-hint",
        action="append",
        default=[],
        metavar="SOURCE=PLAN_JSON",
        help="按文件路径或文件名提供自适应来源计划；可重复提供",
    )
    convert_batch.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    convert_batch.add_argument(
        "--mapping-overlay",
        action="append",
        default=[],
        help="可重复提供的已确认商品映射覆盖层JSON",
    )
    convert_batch.add_argument(
        "--mapping-replacement",
        action="append",
        default=[],
        help="可重复提供的当前批次正式映射临时替换记录JSON",
    )
    convert_batch.add_argument(
        "--template",
        default=str(PROJECT_ROOT / "assets/templates/guanyi/自定义订单导入模板.xlsx"),
    )
    convert.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    convert.add_argument(
        "--mapping-overlay",
        action="append",
        default=[],
        help="可重复提供的已确认商品映射覆盖层JSON",
    )
    convert.add_argument(
        "--mapping-replacement",
        action="append",
        default=[],
        help="可重复提供的当前批次正式映射临时替换记录JSON",
    )
    for conversion_parser in (convert, convert_batch):
        conversion_parser.add_argument(
            "--official-mapping-status",
            choices=["auto", "ready", "unavailable"],
            default="auto",
            help="正式映射读取状态；ready仍会校验快照，默认从overlay自动判定",
        )
        conversion_parser.add_argument(
            "--official-mapping-error",
            default="",
            help="正式映射不可用时供结构化追问展示的诊断信息",
        )
    convert.add_argument(
        "--template",
        default=str(PROJECT_ROOT / "assets/templates/guanyi/自定义订单导入模板.xlsx"),
    )

    inspect_source = subparsers.add_parser(
        "inspect-source-structure",
        help="只读输出Excel工作表、表头和类型统计，不输出任何订单数据值",
    )
    inspect_source.add_argument("--source", required=True)

    propose_adaptive = subparsers.add_parser(
        "propose-adaptive-plan",
        help="根据用户确认的公司和Excel结构生成一次性字段映射草案",
    )
    propose_adaptive.add_argument("--source", required=True)
    propose_adaptive.add_argument("--company", required=True)
    propose_adaptive.add_argument(
        "--registry",
        default=str(PROJECT_ROOT / "config/companies/company_registry_v1.json"),
    )
    propose_adaptive.add_argument("--output", required=True)

    validate_text = subparsers.add_parser(
        "validate-text-order-draft",
        help="校验Agent从文字或截图提取的订单草稿，并只输出脱敏预览和集中追问",
    )
    validate_text.add_argument("--draft", required=True)
    validate_text.add_argument(
        "--registry",
        default=str(PROJECT_ROOT / "config/companies/company_registry_v1.json"),
    )

    prepare_text = subparsers.add_parser(
        "prepare-text-order-draft",
        help="在用户确认后把文字订单草稿生成标准化Excel和一次性字段计划",
    )
    prepare_text.add_argument("--draft", required=True)
    prepare_text.add_argument("--output-dir", required=True)
    prepare_text.add_argument(
        "--order-number-state-dir",
        help="可选的平台单号本机占号目录；默认使用Skill跨平台状态目录",
    )
    prepare_text.add_argument(
        "--registry",
        default=str(PROJECT_ROOT / "config/companies/company_registry_v1.json"),
    )

    preflight = subparsers.add_parser(
        "preflight",
        help="校验管易待上传Excel并生成不含收件信息的批次清单",
    )
    preflight.add_argument("--file", required=True)
    preflight.add_argument(
        "--profile",
        default=str(
            PROJECT_ROOT / "config/platform_profiles/guanyi_order_import_v1.json"
        ),
    )
    preflight.add_argument("--output", help="将上传预检 manifest 写入指定路径")

    audit_rules = subparsers.add_parser(
        "audit-rules",
        help="检查来源规则包是否完整覆盖SOP规则目录",
    )
    audit_rules.add_argument(
        "--catalog",
        default=str(
            PROJECT_ROOT / "config/business_rules/shangyu_sop_rule_catalog_v1.json"
        ),
    )
    audit_rules.add_argument(
        "--rules",
        default=str(
            PROJECT_ROOT
            / "config/platform_rules/guanyi/tiantian_warehouse_v2.json"
        ),
    )
    audit_rules.add_argument("--scope", help="规则范围；默认读取规则包中的scope_id")
    audit_rules.add_argument(
        "--phase",
        choices=["conversion", "upload", "post_upload", "post_fulfillment", "manual_entry", "all"],
        default="all",
    )

    audit_companies = subparsers.add_parser(
        "audit-companies",
        help="检查公司库与来源、路由、平台规则和商品映射的引用完整性",
    )
    audit_companies.add_argument(
        "--registry",
        default=str(PROJECT_ROOT / "config/companies/company_registry_v1.json"),
    )

    build_proposal = subparsers.add_parser(
        "build-mapping-proposal",
        help="为飞书待确认映射生成确定性proposal_id和mapping_key；不写入飞书",
    )
    build_proposal.add_argument("--submitted-by", required=True)
    build_proposal.add_argument("--company-id", required=True)
    build_proposal.add_argument("--source-profile", required=True)
    build_proposal.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    build_proposal.add_argument("--source-value", required=True)
    build_proposal.add_argument("--source-spec", default="")
    build_proposal.add_argument("--target-platform", default="guanyi")
    build_proposal.add_argument("--product-code", required=True)
    build_proposal.add_argument("--spec-code", default="")
    build_proposal.add_argument("--evidence", required=True)
    build_proposal.add_argument("--official-revision", required=True)
    build_proposal.add_argument("--operation-id")

    feishu_status = subparsers.add_parser(
        "feishu-mapping-status",
        help="只读检查飞书映射工作簿绑定、当前用户身份和正式库版本",
    )
    feishu_status.add_argument(
        "--config",
        default=str(DEFAULT_FEISHU_MAPPING_CONFIG),
    )

    sync_official = subparsers.add_parser(
        "sync-feishu-official-mappings",
        help="只读拉取飞书正式映射，校验商品主档后原子更新本机快照",
    )
    sync_official.add_argument(
        "--config",
        default=str(DEFAULT_FEISHU_MAPPING_CONFIG),
    )
    sync_official.add_argument("--catalog", required=True)
    sync_official.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    sync_official.add_argument("--output", required=True)

    sync_official_native = subparsers.add_parser(
        "sync-feishu-official-mappings-from-payload",
        help="接收Agent原生飞书能力读取的标准载荷，校验后原子更新本机正式映射快照",
    )
    sync_official_native.add_argument("--payload", required=True)
    sync_official_native.add_argument("--catalog", required=True)
    sync_official_native.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    sync_official_native.add_argument("--output", required=True)

    provision_pending = subparsers.add_parser(
        "provision-pending-sheet",
        help="按当前飞书用户自动发现、修复或创建个人待确认Sheet",
    )
    provision_pending.add_argument(
        "--config",
        default=str(DEFAULT_FEISHU_MAPPING_CONFIG),
    )
    provision_pending.add_argument("--operation-id")

    submit_proposal = subparsers.add_parser(
        "submit-mapping-proposal",
        help="把用户确认需长期沿用的商品映射追加到当前飞书用户的待确认Sheet",
    )
    submit_proposal.add_argument(
        "--config",
        default=str(DEFAULT_FEISHU_MAPPING_CONFIG),
    )
    submit_proposal.add_argument("--company-id", required=True)
    submit_proposal.add_argument("--source-profile", required=True)
    submit_proposal.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    submit_proposal.add_argument("--source-value", required=True)
    submit_proposal.add_argument("--source-spec", default="")
    submit_proposal.add_argument("--target-platform", default="guanyi")
    submit_proposal.add_argument("--product-code", required=True)
    submit_proposal.add_argument("--spec-code", default="")
    submit_proposal.add_argument("--evidence", required=True)
    submit_proposal.add_argument("--operation-id")
    submit_proposal.add_argument("--confirmation-token", required=True)

    build_feedback = subparsers.add_parser(
        "build-correction-feedback",
        help="生成确定性的用户纠错反馈记录；不写入飞书",
    )
    build_feedback.add_argument("--submitted-by", required=True)
    build_feedback.add_argument(
        "--feedback-type", required=True, choices=sorted(ALLOWED_FEEDBACK_TYPES)
    )
    build_feedback.add_argument("--company-id", required=True)
    build_feedback.add_argument("--source-profile", required=True)
    build_feedback.add_argument("--source-field", required=True)
    build_feedback.add_argument("--source-value", required=True)
    build_feedback.add_argument("--source-spec", default="")
    build_feedback.add_argument("--system-result", required=True)
    build_feedback.add_argument("--corrected-result", required=True)
    build_feedback.add_argument(
        "--reuse-scope", required=True, choices=sorted(ALLOWED_REUSE_SCOPES)
    )
    build_feedback.add_argument("--related-proposal-id", default="")
    build_feedback.add_argument("--evidence", required=True)
    build_feedback.add_argument("--skill-version", required=True)
    build_feedback.add_argument("--operation-id")

    submit_feedback = subparsers.add_parser(
        "submit-correction-feedback",
        help="经用户确认后把纠错写入独立反馈箱；不会修改正式规则或映射",
    )
    submit_feedback.add_argument(
        "--config", default=str(DEFAULT_FEISHU_MAPPING_CONFIG)
    )
    submit_feedback.add_argument(
        "--feedback-type", required=True, choices=sorted(ALLOWED_FEEDBACK_TYPES)
    )
    submit_feedback.add_argument("--company-id", required=True)
    submit_feedback.add_argument("--source-profile", required=True)
    submit_feedback.add_argument("--source-field", required=True)
    submit_feedback.add_argument("--source-value", required=True)
    submit_feedback.add_argument("--source-spec", default="")
    submit_feedback.add_argument("--system-result", required=True)
    submit_feedback.add_argument("--corrected-result", required=True)
    submit_feedback.add_argument(
        "--reuse-scope", required=True, choices=sorted(ALLOWED_REUSE_SCOPES)
    )
    submit_feedback.add_argument("--related-proposal-id", default="")
    submit_feedback.add_argument("--evidence", required=True)
    submit_feedback.add_argument("--skill-version", required=True)
    submit_feedback.add_argument("--operation-id")
    submit_feedback.add_argument("--confirmation-token", required=True)

    collect_feedback = subparsers.add_parser(
        "collect-correction-feedback",
        help="只读汇总纠错反馈，供开发维护线程定期审核",
    )
    collect_feedback.add_argument(
        "--config", default=str(DEFAULT_FEISHU_MAPPING_CONFIG)
    )
    collect_feedback.add_argument("--status", default="new")

    plan_publish = subparsers.add_parser(
        "plan-pending-mapping-publish",
        help="只读预检所有待确认商品映射，输出去重、冲突和正式版本计划",
    )
    plan_publish.add_argument(
        "--config",
        default=str(DEFAULT_FEISHU_MAPPING_CONFIG),
    )
    plan_publish.add_argument("--catalog", required=True)
    plan_publish.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    plan_publish.add_argument(
        "--registry",
        default=str(PROJECT_ROOT / "config/companies/company_registry_v1.json"),
    )
    plan_publish.add_argument("--operation-id")

    publish_pending = subparsers.add_parser(
        "publish-pending-mappings",
        help="所有者专用：经冲突预检后把全部pending映射发布到正式库",
    )
    publish_pending.add_argument(
        "--config",
        default=str(DEFAULT_FEISHU_MAPPING_CONFIG),
    )
    publish_pending.add_argument("--catalog", required=True)
    publish_pending.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    publish_pending.add_argument(
        "--registry",
        default=str(PROJECT_ROOT / "config/companies/company_registry_v1.json"),
    )
    publish_pending.add_argument("--operation-id")
    publish_pending.add_argument("--confirmation-token", required=True)

    direct_add = subparsers.add_parser(
        "direct-add-official-mapping",
        help="用户明确以后沿用后，受控新增一条无冲突正式商品映射",
    )
    direct_add.add_argument(
        "--config", default=str(DEFAULT_FEISHU_MAPPING_CONFIG)
    )
    direct_add.add_argument("--catalog", required=True)
    direct_add.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    direct_add.add_argument(
        "--registry",
        default=str(PROJECT_ROOT / "config/companies/company_registry_v1.json"),
    )
    direct_add.add_argument("--company-id", required=True)
    direct_add.add_argument("--source-profile", required=True)
    direct_add.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    direct_add.add_argument("--source-value", required=True)
    direct_add.add_argument("--source-spec", default="")
    direct_add.add_argument("--target-platform", default="guanyi")
    direct_add.add_argument("--product-code", required=True)
    direct_add.add_argument("--spec-code", default="")
    direct_add.add_argument("--evidence", required=True)
    direct_add.add_argument("--operation-id")
    direct_add.add_argument("--output")
    direct_add.add_argument("--confirmation-token", required=True)

    save_mapping = subparsers.add_parser(
        "save-product-mapping",
        help="在用户明确授权后，把已确认商品映射写入覆盖层",
    )
    save_mapping.add_argument("--catalog", required=True)
    save_mapping.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    save_mapping.add_argument("--overlay", required=True)
    save_mapping.add_argument("--source-profile", required=True)
    save_mapping.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    save_mapping.add_argument("--source-value", required=True)
    save_mapping.add_argument("--source-spec", default="")
    save_mapping.add_argument("--product-code", required=True)
    save_mapping.add_argument("--spec-code", default="")
    save_mapping.add_argument("--reason", default="用户确认")
    save_mapping.add_argument(
        "--reuse-scope",
        choices=["future", "current_batch"],
        default="future",
    )
    save_mapping.add_argument("--source-fingerprint", default="")
    save_mapping.add_argument("--confirmation-token", required=True)

    save_replacement = subparsers.add_parser(
        "save-batch-mapping-replacement",
        help="用户否决已有正式映射后，保存仅绑定当前来源文件的临时替换",
    )
    save_replacement.add_argument("--catalog", required=True)
    save_replacement.add_argument(
        "--mappings",
        default=str(PROJECT_ROOT / "config/catalog/external_sku_mappings.json"),
    )
    save_replacement.add_argument(
        "--registry",
        default=str(PROJECT_ROOT / "config/companies/company_registry_v1.json"),
    )
    save_replacement.add_argument(
        "--mapping-overlay", action="append", default=[], required=True
    )
    save_replacement.add_argument("--output", required=True)
    save_replacement.add_argument("--company-id", required=True)
    save_replacement.add_argument("--source-profile", required=True)
    save_replacement.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    save_replacement.add_argument("--source-value", required=True)
    save_replacement.add_argument("--source-spec", default="")
    save_replacement.add_argument("--expected-product-code", required=True)
    save_replacement.add_argument("--expected-spec-code", default="")
    save_replacement.add_argument("--replacement-product-code", required=True)
    save_replacement.add_argument("--replacement-spec-code", default="")
    save_replacement.add_argument("--source-fingerprint", required=True)
    save_replacement.add_argument("--reason", required=True)
    save_replacement.add_argument("--confirmed-by", required=True)
    save_replacement.add_argument("--confirmation-token", required=True)

    save_package_semantics = subparsers.add_parser(
        "save-package-semantics-proposal",
        help="在用户明确要求以后沿用后，把包装含义保存到本机待登记库",
    )
    save_package_semantics.add_argument("--state-dir", required=True)
    save_package_semantics.add_argument("--company-id", required=True)
    save_package_semantics.add_argument("--source-profile", required=True)
    save_package_semantics.add_argument("--product-family", required=True)
    save_package_semantics.add_argument("--source-expression", required=True)
    save_package_semantics.add_argument(
        "--semantic-type",
        required=True,
        choices=[
            "single_stick_packaged",
            "eight_stick_family_pack",
            "pack_content_count",
            "bundle",
        ],
    )
    save_package_semantics.add_argument(
        "--quantity-strategy",
        required=True,
        choices=[
            "same_as_source",
            "divide_by_sticks_per_target_unit",
            "requires_bundle_expansion",
        ],
    )
    save_package_semantics.add_argument("--sticks-per-target-unit", type=int)
    save_package_semantics.add_argument("--confirmation-token", required=True)

    list_package_semantics = subparsers.add_parser(
        "list-package-semantics-proposals",
        help="列出本机待回收的包装含义提案，不返回订单或收件信息",
    )
    list_package_semantics.add_argument("--state-dir", required=True)

    export_package_semantics = subparsers.add_parser(
        "export-package-semantics-proposals",
        help="导出本机待回收包装含义提案，供主系统所有者审核",
    )
    export_package_semantics.add_argument("--state-dir", required=True)
    export_package_semantics.add_argument("--output", required=True)
    for catalog_parser in (
        preview,
        convert,
        convert_batch,
        sync_official,
        sync_official_native,
        plan_publish,
        publish_pending,
        direct_add,
        save_mapping,
        save_replacement,
    ):
        catalog_parser.add_argument(
            "--preferred-catalog",
            action="append",
            default=[],
            help="可重复提供的业务优先商品目录CSV；按参数顺序从高到低匹配",
        )
    return parser


def run_preview(args: argparse.Namespace) -> int:
    result = ingest_file(
        args.source,
        args.catalog,
        args.profile,
        args.mappings,
        preferred_catalog_paths=tuple(args.preferred_catalog),
    )
    payload = {
        "summary": result.summary(),
        "batch_issues": [asdict(issue) for issue in result.batch_issues],
        "orders": [
            order.to_dict() if args.include_pii else redact_order(order)
            for order in result.orders
        ],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


def _official_mapping_runtime_state(
    args: argparse.Namespace,
) -> tuple[bool, str]:
    if args.official_mapping_status == "unavailable":
        return False, (
            args.official_mapping_error
            or "正式映射提供器未能完成读取与校验。"
        )
    try:
        catalog = ProductCatalog.from_files(
            args.catalog,
            args.mappings,
            preferred_catalog_paths=tuple(args.preferred_catalog),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"正式映射校验无法加载商品主档：{exc}"

    official_candidates: list[Path] = []
    errors: list[str] = []
    for value in args.mapping_overlay:
        path = Path(value).expanduser().resolve()
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if isinstance(document, dict) and document.get("source") == "feishu_official_mapping_repository":
            official_candidates.append(path)

    for path in official_candidates:
        try:
            load_official_mapping_snapshot(path, catalog)
        except OfficialMappingSyncError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        return True, ""

    if errors:
        return False, "；".join(errors)
    return False, (
        args.official_mapping_error
        or "没有提供经过验证的飞书正式映射快照。"
    )


def run_convert(args: argparse.Namespace) -> int:
    official_mapping_ready, official_mapping_error = _official_mapping_runtime_state(args)
    result = convert_order_file(
        source_path=args.source,
        catalog_csv=args.catalog,
        output_dir=args.output_dir,
        preferred_catalog_paths=tuple(args.preferred_catalog),
        mappings_path=args.mappings,
        guanyi_template_path=args.template,
        source_profile_hint=args.source_profile,
        company_hint=args.company,
        adaptive_plan_path=args.adaptive_plan,
        mapping_overlay_paths=tuple(args.mapping_overlay),
        mapping_replacement_paths=tuple(args.mapping_replacement),
        official_mapping_ready=official_mapping_ready,
        official_mapping_error=official_mapping_error,
    )
    print(json.dumps(result.to_public_dict(), ensure_ascii=False, indent=2))
    if result.status == "ready":
        return 0
    if result.status == "needs_input":
        return 2
    return 1


def _parse_keyed_hints(values: list[str], option_name: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(
                f"{option_name}必须使用 SOURCE=VALUE 格式"
            )
        source, profile_id = value.rsplit("=", 1)
        source = source.strip()
        profile_id = profile_id.strip()
        if not source or not profile_id:
            raise ValueError(
                f"{option_name}的文件和值都不能为空"
            )
        if source in parsed and parsed[source] != profile_id:
            raise ValueError(f"同一文件配置了多个来源：{source}")
        parsed[source] = profile_id
    return parsed


def _parse_source_profile_hints(values: list[str]) -> dict[str, str]:
    return _parse_keyed_hints(values, "--source-profile-hint")


def run_convert_batch(args: argparse.Namespace) -> int:
    try:
        hints = _parse_source_profile_hints(args.source_profile_hint)
        company_hints = _parse_keyed_hints(args.company_hint, "--company-hint")
        adaptive_plan_hints = _parse_keyed_hints(
            args.adaptive_plan_hint,
            "--adaptive-plan-hint",
        )
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "errors": [
                        {"code": "SOURCE_PROFILE_HINT_INVALID", "message": str(exc)}
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    official_mapping_ready, official_mapping_error = _official_mapping_runtime_state(args)
    result = convert_order_batch(
        source_paths=args.source,
        catalog_csv=args.catalog,
        output_dir=args.output_dir,
        preferred_catalog_paths=tuple(args.preferred_catalog),
        mappings_path=args.mappings,
        guanyi_template_path=args.template,
        source_profile_hints=hints,
        company_hints=company_hints,
        adaptive_plan_hints=adaptive_plan_hints,
        mapping_overlay_paths=tuple(args.mapping_overlay),
        mapping_replacement_paths=tuple(args.mapping_replacement),
        batch_name=args.batch_name,
        official_mapping_ready=official_mapping_ready,
        official_mapping_error=official_mapping_error,
    )
    print(json.dumps(result.to_public_dict(), ensure_ascii=False, indent=2))
    if result.status == "ready":
        return 0
    if result.status == "needs_input":
        return 2
    return 1


def run_inspect_source_structure(args: argparse.Namespace) -> int:
    try:
        payload = inspect_excel_structure(args.source)
    except (OSError, ValueError, AdaptiveExcelPlanError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "ADAPTIVE_SOURCE_INSPECTION_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps({"status": "ready", **payload}, ensure_ascii=False, indent=2))
    return 0


def run_propose_adaptive_plan(args: argparse.Namespace) -> int:
    try:
        registry = load_company_registry(args.registry)
        proposal = propose_adaptive_excel_plan(
            args.source,
            args.company,
            registry,
        )
        output = None
        if proposal.plan is not None:
            output = write_adaptive_plan(args.output, proposal.plan)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        AdaptiveExcelPlanError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "ADAPTIVE_PLAN_PROPOSAL_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    payload = proposal.to_public_dict()
    payload["output"] = str(output) if output else None
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if proposal.status == "ready" else 2


def run_validate_text_order_draft(args: argparse.Namespace) -> int:
    try:
        result, _ = validate_text_order_draft(
            args.draft,
            company_registry_path=args.registry,
            require_confirmation=True,
        )
    except (OSError, ValueError, json.JSONDecodeError, TextOrderDraftError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "TEXT_ORDER_DRAFT_INVALID",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result.to_public_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "ready" else 2


def run_prepare_text_order_draft(args: argparse.Namespace) -> int:
    try:
        result = prepare_text_order_draft(
            args.draft,
            args.output_dir,
            company_registry_path=args.registry,
            order_number_state_dir=args.order_number_state_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError, TextOrderDraftError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "TEXT_ORDER_PREPARATION_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result.to_public_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "ready" else 2


def run_preflight(args: argparse.Namespace) -> int:
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    result = preflight_custom_import(args.file, profile)
    payload = result.to_manifest()
    if args.output:
        result.write_manifest(args.output)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status == "ready" else 2


def run_audit_rules(args: argparse.Namespace) -> int:
    try:
        catalog = load_rule_catalog(args.catalog)
        rules = load_rule_pack(args.rules)
    except (OSError, json.JSONDecodeError, RulePackSchemaError) as exc:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "code": "BUSINESS_RULE_PACK_SCHEMA_INVALID",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    coverage = rules.get("business_rule_coverage") or {}
    scope_id = args.scope or coverage.get("scope_id")
    if not scope_id:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "code": "BUSINESS_RULE_SCOPE_MISSING",
                    "message": "请通过--scope提供规则范围，或在规则包中配置scope_id。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    result = audit_rule_coverage(
        catalog,
        rules,
        str(scope_id),
        phase=None if args.phase == "all" else args.phase,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "ready" else 2


def run_audit_companies(args: argparse.Namespace) -> int:
    try:
        registry = load_company_registry(args.registry)
        result = audit_company_registry(
            registry,
            source_profiles_dir=PROJECT_ROOT / "config/source_profiles",
            routing_path=PROJECT_ROOT / "config/routing/order_routes_v1.json",
            platform_profiles_dir=PROJECT_ROOT / "config/platform_profiles",
            platform_rules_dir=PROJECT_ROOT / "config/platform_rules",
            mappings_path=PROJECT_ROOT / "config/catalog/external_sku_mappings.json",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "status": "invalid",
            "code": "COMPANY_REGISTRY_AUDIT_FAILED",
            "message": str(exc),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ready" else 2


def run_build_mapping_proposal(args: argparse.Namespace) -> int:
    try:
        proposal = build_pending_mapping_proposal(
            company_id=args.company_id,
            source_profile_id=args.source_profile,
            identifier_type=args.identifier_type,
            source_value=args.source_value,
            source_spec=args.source_spec,
            target_platform=args.target_platform,
            candidate_product_code=args.product_code,
            candidate_spec_code=args.spec_code,
            evidence=args.evidence,
            base_official_revision=args.official_revision,
            submitted_by=args.submitted_by,
            submission_operation_id=args.operation_id,
        )
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "code": "PENDING_MAPPING_PROPOSAL_INVALID",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {"status": "ready", "proposal": proposal.to_dict()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _lark_gateway(config_path: str) -> LarkCliFeishuSheetGateway:
    return LarkCliFeishuSheetGateway.from_config(config_path)


def run_feishu_mapping_status(args: argparse.Namespace) -> int:
    try:
        gateway = _lark_gateway(args.config)
        identity = gateway.get_current_identity()
        revision = gateway.get_official_mapping_revision()
        routes = gateway.list_user_routes()
        sheets = gateway.list_managed_pending_sheets()
    except (OSError, ValueError, json.JSONDecodeError, LarkCliGatewayError) as exc:
        print(
            json.dumps(
                {
                    "status": "needs_setup",
                    "code": "FEISHU_MAPPING_GATEWAY_NOT_READY",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "ready",
                "identity": {
                    "identity_type": identity.identity_type,
                    "user_id": identity.user_id,
                    "display_name": identity.display_name,
                },
                "official_mapping_revision": revision,
                "route_count": len(routes),
                "managed_pending_sheet_count": len(sheets),
                "template_sheet_id": gateway.template_sheet_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_sync_feishu_official_mappings(args: argparse.Namespace) -> int:
    try:
        gateway = _lark_gateway(args.config)
        catalog = ProductCatalog.from_files(
            args.catalog,
            args.mappings,
            preferred_catalog_paths=tuple(args.preferred_catalog),
        )
        records = gateway.list_official_mapping_records()
        metadata = gateway.get_official_repository_metadata()
        snapshot = build_official_mapping_snapshot(records, metadata, catalog)
        output = write_official_mapping_snapshot(args.output, snapshot)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        LarkCliGatewayError,
        OfficialMappingSyncError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "FEISHU_OFFICIAL_MAPPING_SYNC_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "synced",
                "mapping_revision": snapshot["mapping_revision"],
                "mapping_count": len(snapshot["mappings"]),
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_sync_feishu_official_mappings_from_payload(
    args: argparse.Namespace,
) -> int:
    try:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise OfficialMappingSyncError("Agent原生飞书载荷根节点不是对象。")
        records = payload.get("records")
        metadata = payload.get("metadata")
        if not isinstance(records, list) or not isinstance(metadata, dict):
            raise OfficialMappingSyncError(
                "Agent原生飞书载荷必须包含records列表和metadata对象。"
            )
        catalog = ProductCatalog.from_files(
            args.catalog,
            args.mappings,
            preferred_catalog_paths=tuple(args.preferred_catalog),
        )
        snapshot = build_official_mapping_snapshot(records, metadata, catalog)
        output = write_official_mapping_snapshot(args.output, snapshot)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        OfficialMappingSyncError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "FEISHU_NATIVE_OFFICIAL_MAPPING_SYNC_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "synced",
                "provider": "agent_native_feishu",
                "mapping_revision": snapshot["mapping_revision"],
                "mapping_count": len(snapshot["mappings"]),
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_provision_pending_sheet(args: argparse.Namespace) -> int:
    try:
        gateway = _lark_gateway(args.config)
        result = resolve_or_provision_user_sheet(
            gateway,
            template_sheet_id=gateway.template_sheet_id,
            provision_operation_id=args.operation_id,
        )
    except (OSError, ValueError, json.JSONDecodeError, LarkCliGatewayError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "FEISHU_PENDING_SHEET_PROVISION_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    payload = asdict(result)
    if result.status in {"created", "already_exists"} and args.output:
        try:
            snapshot = build_official_mapping_snapshot(
                gateway.list_official_mapping_records(),
                gateway.get_official_repository_metadata(),
                catalog,
            )
            output = write_official_mapping_snapshot(args.output, snapshot)
            payload["snapshot_output"] = str(output)
            payload["snapshot_revision"] = snapshot["mapping_revision"]
        except (OSError, ValueError, OfficialMappingSyncError, LarkCliGatewayError) as exc:
            payload["status"] = "created_but_snapshot_sync_failed"
            payload["code"] = "DIRECT_MAPPING_SNAPSHOT_SYNC_FAILED"
            payload["reason"] = (
                "正式映射写入已完成，但本机快照同步失败；转换前必须重新sync："
                f"{exc}"
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status == "ready" else 2


def run_submit_mapping_proposal(args: argparse.Namespace) -> int:
    if args.confirmation_token != "CONFIRM_SUBMIT_PENDING_MAPPING":
        print(
            json.dumps(
                {
                    "status": "needs_confirmation",
                    "code": "PENDING_MAPPING_CONFIRMATION_REQUIRED",
                    "message": "只有用户明确确认该映射需以后沿用，才可写入飞书待确认库。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    try:
        gateway = _lark_gateway(args.config)
        identity = gateway.get_current_identity()
        proposal = build_pending_mapping_proposal(
            company_id=args.company_id,
            source_profile_id=args.source_profile,
            identifier_type=args.identifier_type,
            source_value=args.source_value,
            source_spec=args.source_spec,
            target_platform=args.target_platform,
            candidate_product_code=args.product_code,
            candidate_spec_code=args.spec_code,
            evidence=args.evidence,
            base_official_revision=gateway.get_official_mapping_revision(),
            submitted_by=identity.user_id,
            submission_operation_id=args.operation_id,
        )
        result = submit_pending_mapping_proposal(
            gateway,
            proposal,
            template_sheet_id=gateway.template_sheet_id,
        )
    except (OSError, ValueError, json.JSONDecodeError, LarkCliGatewayError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "PENDING_MAPPING_SUBMISSION_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                **asdict(result),
                "mapping_key": proposal.mapping_key,
                "base_official_revision": proposal.base_official_revision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status in {"submitted", "already_submitted", "already_pending"} else 2


def _build_feedback_from_args(
    args: argparse.Namespace,
    *,
    submitted_by: str,
):
    return build_correction_feedback_record(
        feedback_type=args.feedback_type,
        company_id=args.company_id,
        source_profile_id=args.source_profile,
        source_field=args.source_field,
        source_value=args.source_value,
        source_spec=args.source_spec,
        system_result=args.system_result,
        corrected_result=args.corrected_result,
        reuse_scope=args.reuse_scope,
        related_proposal_id=args.related_proposal_id,
        evidence=args.evidence,
        skill_version=args.skill_version,
        submitted_by=submitted_by,
        submission_operation_id=args.operation_id,
    )


def run_build_correction_feedback(args: argparse.Namespace) -> int:
    try:
        record = _build_feedback_from_args(args, submitted_by=args.submitted_by)
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "code": "CORRECTION_FEEDBACK_INVALID",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {"status": "ready", "feedback": record.to_dict()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_submit_correction_feedback(args: argparse.Namespace) -> int:
    if args.confirmation_token != CORRECTION_FEEDBACK_CONFIRMATION_TOKEN:
        print(
            json.dumps(
                {
                    "status": "needs_confirmation",
                    "code": "CORRECTION_FEEDBACK_CONFIRMATION_REQUIRED",
                    "message": "只有用户明确确认后，才可把纠错写入飞书反馈箱。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    try:
        gateway = _lark_gateway(args.config)
        identity = gateway.get_current_identity()
        record = _build_feedback_from_args(args, submitted_by=identity.user_id)
        result = submit_correction_feedback(gateway, record)
    except (OSError, ValueError, json.JSONDecodeError, LarkCliGatewayError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "CORRECTION_FEEDBACK_SUBMISSION_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.status in {"submitted", "already_submitted"} else 2


def run_collect_correction_feedback(args: argparse.Namespace) -> int:
    try:
        gateway = _lark_gateway(args.config)
        payload = collect_correction_feedback(
            gateway.list_correction_feedback(),
            status=args.status,
        )
    except (OSError, ValueError, json.JSONDecodeError, LarkCliGatewayError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "CORRECTION_FEEDBACK_READ_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _load_mapping_publisher_context(args: argparse.Namespace) -> tuple[
    LarkCliFeishuSheetGateway,
    ProductCatalog,
    list[str],
    list[str],
    list[str],
]:
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    registry = load_company_registry(args.registry)
    publisher_policy = config.get("publisher_policy") or {}
    owners = [
        str(value).strip()
        for value in publisher_policy.get("allowed_user_ids") or []
        if str(value).strip()
    ]
    if not owners:
        raise ValueError("飞书映射治理配置缺少publisher_policy.allowed_user_ids。")
    companies: list[str] = []
    source_profiles: list[str] = []
    for company in registry.get("companies", []):
        if company.get("status") != "active" or company.get("enabled", True) is False:
            continue
        companies.append(str(company.get("company_id") or ""))
        source_profiles.extend(
            str(item.get("source_profile_id") or "")
            for item in company.get("source_profiles", [])
            if item.get("status") == "active" and item.get("enabled", True) is not False
        )
    catalog = ProductCatalog.from_files(
        args.catalog,
        args.mappings,
        preferred_catalog_paths=tuple(args.preferred_catalog),
    )
    return _lark_gateway(args.config), catalog, owners, companies, source_profiles


def run_plan_pending_mapping_publish(args: argparse.Namespace) -> int:
    try:
        gateway, catalog, owners, companies, source_profiles = (
            _load_mapping_publisher_context(args)
        )
        plan = plan_pending_mapping_publish(
            gateway,
            catalog,
            allowed_owner_user_ids=owners,
            allowed_company_ids=companies,
            allowed_source_profile_ids=source_profiles,
            operation_id=args.operation_id,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        LarkCliGatewayError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "MAPPING_PUBLISH_PLAN_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(plan.to_public_dict(), ensure_ascii=False, indent=2))
    return 0 if plan.status in {"ready", "no_changes"} else 2


def run_publish_pending_mappings(args: argparse.Namespace) -> int:
    try:
        gateway, catalog, owners, companies, source_profiles = (
            _load_mapping_publisher_context(args)
        )
        result = publish_pending_mappings(
            gateway,
            catalog,
            allowed_owner_user_ids=owners,
            allowed_company_ids=companies,
            allowed_source_profile_ids=source_profiles,
            confirmation_token=args.confirmation_token,
            operation_id=args.operation_id,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        LarkCliGatewayError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "MAPPING_PUBLISH_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.status in {"published", "no_changes"} else 2


def run_direct_add_official_mapping(args: argparse.Namespace) -> int:
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        direct_policy = config.get("direct_add_policy") or {}
        if direct_policy.get("enabled") is not True:
            raise ValueError("飞书映射治理配置未启用受控直接新增。")
        gateway, catalog, _, companies, source_profiles = (
            _load_mapping_publisher_context(args)
        )
        result = add_confirmed_mapping_direct(
            gateway,
            catalog,
            allowed_company_ids=companies,
            allowed_source_profile_ids=source_profiles,
            company_id=args.company_id,
            source_profile_id=args.source_profile,
            identifier_type=args.identifier_type,
            source_value=args.source_value,
            source_spec=args.source_spec,
            target_platform=args.target_platform,
            product_code=args.product_code,
            spec_code=args.spec_code,
            evidence=args.evidence,
            confirmation_token=args.confirmation_token,
            operation_id=args.operation_id,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        LarkCliGatewayError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "DIRECT_MAPPING_ADD_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.status in {"created", "already_exists"} else 2


def run_save_product_mapping(args: argparse.Namespace) -> int:
    try:
        catalog = ProductCatalog.from_files(
            args.catalog,
            args.mappings,
            preferred_catalog_paths=tuple(args.preferred_catalog),
        )
        saved = save_confirmed_mapping(
            args.overlay,
            catalog,
            source_profile_id=args.source_profile,
            identifier_type=args.identifier_type,
            source_value=args.source_value,
            source_spec=args.source_spec,
            product_code=args.product_code,
            spec_code=args.spec_code,
            reason=args.reason,
            reuse_scope=args.reuse_scope,
            source_fingerprint=args.source_fingerprint,
            confirmation_token=args.confirmation_token,
        )
    except (OSError, ValueError, json.JSONDecodeError, ProductMappingStoreError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "PRODUCT_MAPPING_NOT_SAVED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "saved",
                "overlay": str(saved),
                "source_profile_id": args.source_profile,
                "identifier_type": args.identifier_type,
                "source_value": args.source_value,
                "product_code": args.product_code,
                "spec_code": args.spec_code,
                "scope": args.reuse_scope,
                "source_fingerprint": args.source_fingerprint,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_save_batch_mapping_replacement(args: argparse.Namespace) -> int:
    try:
        registry = load_company_registry(args.registry)
        matching_companies = [
            company
            for company in registry.get("companies", [])
            if str(company.get("company_id") or "").strip() == args.company_id
            and company.get("status") == "active"
            and company.get("enabled", True) is not False
        ]
        if len(matching_companies) != 1:
            raise BatchMappingReplacementError("公司未在活动公司库中唯一登记")
        profiles = {
            str(item.get("source_profile_id") or "").strip()
            for item in matching_companies[0].get("source_profiles", [])
            if item.get("status") == "active" and item.get("enabled", True) is not False
        }
        if args.source_profile not in profiles:
            raise BatchMappingReplacementError("来源配置不属于指定公司或未启用")
        catalog = ProductCatalog.from_files(
            args.catalog,
            args.mappings,
            preferred_catalog_paths=tuple(args.preferred_catalog),
            mapping_overlay_paths=tuple(args.mapping_overlay),
        )
        saved = save_batch_mapping_replacement(
            args.output,
            catalog,
            company_id=args.company_id,
            source_profile_id=args.source_profile,
            identifier_type=args.identifier_type,
            source_value=args.source_value,
            source_spec=args.source_spec,
            expected_product_code=args.expected_product_code,
            expected_spec_code=args.expected_spec_code,
            replacement_product_code=args.replacement_product_code,
            replacement_spec_code=args.replacement_spec_code,
            source_fingerprint=args.source_fingerprint,
            reason=args.reason,
            confirmed_by=args.confirmed_by,
            confirmation_token=args.confirmation_token,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        BatchMappingReplacementError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "BATCH_MAPPING_REPLACEMENT_NOT_SAVED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "saved",
                "output": str(saved),
                "scope": "current_batch",
                "source_fingerprint": args.source_fingerprint,
                "formal_mapping_changed": False,
                "admin_action_required": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_save_package_semantics_proposal(args: argparse.Namespace) -> int:
    try:
        result = save_package_semantics_proposal(
            args.state_dir,
            company_id=args.company_id,
            source_profile_id=args.source_profile,
            product_family=args.product_family,
            source_expression=args.source_expression,
            semantic_type=args.semantic_type,
            quantity_strategy=args.quantity_strategy,
            sticks_per_target_unit=args.sticks_per_target_unit,
            confirmation_token=args.confirmation_token,
        )
    except (OSError, ValueError, json.JSONDecodeError, PackageSemanticsStoreError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "PACKAGE_SEMANTICS_PROPOSAL_NOT_SAVED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps({"status": "saved", **result}, ensure_ascii=False, indent=2))
    return 0


def run_list_package_semantics_proposals(args: argparse.Namespace) -> int:
    try:
        proposals = list_package_semantics_proposals(args.state_dir)
    except (OSError, ValueError, json.JSONDecodeError, PackageSemanticsStoreError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "PACKAGE_SEMANTICS_PROPOSALS_UNREADABLE",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "ready",
                "proposal_count": len(proposals),
                "proposals": proposals,
                "privacy": "contains_no_order_recipient_or_address_data",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_export_package_semantics_proposals(args: argparse.Namespace) -> int:
    try:
        result = export_package_semantics_proposals(args.state_dir, args.output)
    except (OSError, ValueError, json.JSONDecodeError, PackageSemanticsStoreError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "PACKAGE_SEMANTICS_EXPORT_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "preview":
        return run_preview(args)
    if args.command == "convert":
        return run_convert(args)
    if args.command == "convert-batch":
        return run_convert_batch(args)
    if args.command == "inspect-source-structure":
        return run_inspect_source_structure(args)
    if args.command == "propose-adaptive-plan":
        return run_propose_adaptive_plan(args)
    if args.command == "validate-text-order-draft":
        return run_validate_text_order_draft(args)
    if args.command == "prepare-text-order-draft":
        return run_prepare_text_order_draft(args)
    if args.command == "preflight":
        return run_preflight(args)
    if args.command == "audit-rules":
        return run_audit_rules(args)
    if args.command == "audit-companies":
        return run_audit_companies(args)
    if args.command == "build-mapping-proposal":
        return run_build_mapping_proposal(args)
    if args.command == "feishu-mapping-status":
        return run_feishu_mapping_status(args)
    if args.command == "sync-feishu-official-mappings":
        return run_sync_feishu_official_mappings(args)
    if args.command == "sync-feishu-official-mappings-from-payload":
        return run_sync_feishu_official_mappings_from_payload(args)
    if args.command == "provision-pending-sheet":
        return run_provision_pending_sheet(args)
    if args.command == "submit-mapping-proposal":
        return run_submit_mapping_proposal(args)
    if args.command == "build-correction-feedback":
        return run_build_correction_feedback(args)
    if args.command == "submit-correction-feedback":
        return run_submit_correction_feedback(args)
    if args.command == "collect-correction-feedback":
        return run_collect_correction_feedback(args)
    if args.command == "plan-pending-mapping-publish":
        return run_plan_pending_mapping_publish(args)
    if args.command == "publish-pending-mappings":
        return run_publish_pending_mappings(args)
    if args.command == "direct-add-official-mapping":
        return run_direct_add_official_mapping(args)
    if args.command == "save-product-mapping":
        return run_save_product_mapping(args)
    if args.command == "save-batch-mapping-replacement":
        return run_save_batch_mapping_replacement(args)
    if args.command == "save-package-semantics-proposal":
        return run_save_package_semantics_proposal(args)
    if args.command == "list-package-semantics-proposals":
        return run_list_package_semantics_proposals(args)
    if args.command == "export-package-semantics-proposals":
        return run_export_package_semantics_proposals(args)
    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
