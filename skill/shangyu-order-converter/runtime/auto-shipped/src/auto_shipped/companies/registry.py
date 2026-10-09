from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from auto_shipped.platforms.guanyi.policy_modules import (
    GuanyiPolicyModuleError,
    resolve_guanyi_policy_modules,
)


class CompanyRegistryError(ValueError):
    """Raised when the company registry itself is structurally unsafe to use."""


@dataclass(frozen=True, slots=True)
class CompanyContext:
    company_id: str
    display_name: str
    aliases: tuple[str, ...]
    source_profile_id: str
    registry_id: str
    registry_version: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        return data


@dataclass(frozen=True, slots=True)
class CompanyResolution:
    status: str
    company: CompanyContext | None = None
    code: str | None = None
    reason: str | None = None


def load_company_registry(path: str | Path) -> dict[str, Any]:
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    if registry.get("schema_version") != "1.0":
        raise CompanyRegistryError("公司库schema_version必须为1.0。")
    if not str(registry.get("registry_id") or "").strip():
        raise CompanyRegistryError("公司库缺少registry_id。")
    companies = registry.get("companies")
    if not isinstance(companies, list):
        raise CompanyRegistryError("公司库companies必须是数组。")

    company_ids: set[str] = set()
    source_owners: dict[str, str] = {}
    workflow_ids: set[str] = set()
    for company in companies:
        company_id = str(company.get("company_id") or "").strip()
        if not company_id:
            raise CompanyRegistryError("公司记录缺少company_id。")
        if company_id in company_ids:
            raise CompanyRegistryError(f"公司ID重复：{company_id}")
        company_ids.add(company_id)
        if not str(company.get("display_name") or "").strip():
            raise CompanyRegistryError(f"公司{company_id}缺少display_name。")

        source_profiles = company.get("source_profiles")
        if not isinstance(source_profiles, list) or not source_profiles:
            raise CompanyRegistryError(f"公司{company_id}至少需要一个source_profile引用。")
        for source in source_profiles:
            profile_id = str(source.get("source_profile_id") or "").strip()
            if not profile_id:
                raise CompanyRegistryError(f"公司{company_id}存在空source_profile_id。")
            owner = source_owners.get(profile_id)
            if owner and owner != company_id:
                raise CompanyRegistryError(
                    f"来源配置{profile_id}同时属于{owner}和{company_id}。"
                )
            source_owners[profile_id] = company_id

        workflows = company.get("workflows")
        if not isinstance(workflows, list):
            raise CompanyRegistryError(f"公司{company_id}的workflows必须是数组。")
        for workflow in workflows:
            workflow_id = str(workflow.get("workflow_id") or "").strip()
            if not workflow_id:
                raise CompanyRegistryError(f"公司{company_id}存在空workflow_id。")
            if workflow_id in workflow_ids:
                raise CompanyRegistryError(f"公司工作流ID重复：{workflow_id}")
            workflow_ids.add(workflow_id)
    return registry


def resolve_company_by_source_profile(
    registry: dict[str, Any],
    source_profile_id: str,
) -> CompanyResolution:
    matches: list[dict[str, Any]] = []
    for company in registry.get("companies", []):
        for source in company.get("source_profiles", []):
            if source.get("source_profile_id") != source_profile_id:
                continue
            if source.get("enabled", True) is False or source.get("status") == "retired":
                continue
            matches.append(company)
            break

    if not matches:
        return CompanyResolution(
            status="needs_input",
            code="COMPANY_NOT_REGISTERED",
            reason=f"来源配置{source_profile_id}尚未登记到公司库。",
        )
    if len(matches) > 1:
        return CompanyResolution(
            status="needs_input",
            code="COMPANY_REGISTRY_AMBIGUOUS",
            reason=f"来源配置{source_profile_id}同时属于多个公司。",
        )

    record = matches[0]
    if record.get("enabled", True) is False or record.get("status") != "active":
        return CompanyResolution(
            status="needs_input",
            code="COMPANY_DISABLED",
            reason=f"公司{record.get('display_name') or record.get('company_id')}当前未启用。",
        )
    return CompanyResolution(
        status="resolved",
        company=CompanyContext(
            company_id=str(record["company_id"]),
            display_name=str(record["display_name"]),
            aliases=tuple(str(value) for value in record.get("aliases", [])),
            source_profile_id=source_profile_id,
            registry_id=str(registry["registry_id"]),
            registry_version=str(registry["schema_version"]),
        ),
    )


def validate_company_workflow(
    registry: dict[str, Any],
    company_id: str,
    *,
    source_profile_id: str,
    route_id: str,
    target_platform: str,
    target_profile_id: str,
    platform_rules_profile_id: str | None,
) -> CompanyResolution:
    company = next(
        (
            item
            for item in registry.get("companies", [])
            if item.get("company_id") == company_id
        ),
        None,
    )
    if company is None:
        return CompanyResolution(
            status="needs_input",
            code="COMPANY_NOT_REGISTERED",
            reason=f"公司ID {company_id}不存在。",
        )
    workflows = [
        item
        for item in company.get("workflows", [])
        if item.get("source_profile_id") == source_profile_id
        and item.get("route_id") == route_id
    ]
    if len(workflows) != 1:
        return CompanyResolution(
            status="needs_input",
            code=(
                "COMPANY_WORKFLOW_NOT_REGISTERED"
                if not workflows
                else "COMPANY_WORKFLOW_AMBIGUOUS"
            ),
            reason=f"公司{company_id}没有唯一登记工作流：{source_profile_id} / {route_id}。",
        )
    workflow = workflows[0]
    expected = {
        "target_platform": target_platform,
        "target_profile_id": target_profile_id,
        "platform_rules_profile_id": platform_rules_profile_id,
    }
    mismatches = [
        f"{key}={workflow.get(key)!r}，路由={value!r}"
        for key, value in expected.items()
        if workflow.get(key) != value
    ]
    if mismatches:
        return CompanyResolution(
            status="needs_input",
            code="COMPANY_WORKFLOW_REFERENCE_MISMATCH",
            reason="；".join(mismatches),
        )
    if workflow.get("runtime_enabled", False) is not True:
        return CompanyResolution(
            status="needs_input",
            code="COMPANY_WORKFLOW_DISABLED",
            reason=f"公司工作流{workflow.get('workflow_id')}尚未允许运行。",
        )
    resolved = resolve_company_by_source_profile(registry, source_profile_id)
    return resolved


def _profiles_by_id(directory: str | Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(directory).rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        profile_id = payload.get("profile_id")
        if profile_id:
            result[str(profile_id)] = payload
    return result


def audit_company_registry(
    registry: dict[str, Any],
    *,
    source_profiles_dir: str | Path,
    routing_path: str | Path,
    platform_profiles_dir: str | Path,
    platform_rules_dir: str | Path,
    mappings_path: str | Path,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    source_profiles = _profiles_by_id(source_profiles_dir)
    platform_profiles = _profiles_by_id(platform_profiles_dir)
    platform_rules = _profiles_by_id(platform_rules_dir)
    routing = json.loads(Path(routing_path).read_text(encoding="utf-8"))
    routes = {str(item.get("route_id")): item for item in routing.get("routes", [])}
    mappings = json.loads(Path(mappings_path).read_text(encoding="utf-8"))
    mapping_scopes = set((mappings.get("source_policies") or {}).keys())

    def add(code: str, location: str, message: str) -> None:
        issues.append({"code": code, "location": location, "message": message})

    for company in registry.get("companies", []):
        company_id = str(company["company_id"])
        for source in company.get("source_profiles", []):
            profile_id = str(source["source_profile_id"])
            profile = source_profiles.get(profile_id)
            location = f"companies.{company_id}.source_profiles.{profile_id}"
            if profile is None:
                add("SOURCE_PROFILE_REFERENCE_MISSING", location, "来源配置文件不存在。")
                continue
            if profile.get("company_id") != company_id:
                add(
                    "SOURCE_PROFILE_COMPANY_MISMATCH",
                    location,
                    f"来源配置company_id={profile.get('company_id')!r}。",
                )

        for workflow in company.get("workflows", []):
            workflow_id = str(workflow["workflow_id"])
            location = f"companies.{company_id}.workflows.{workflow_id}"
            route = routes.get(str(workflow.get("route_id")))
            if route is None:
                add("ROUTE_REFERENCE_MISSING", location, "路由不存在。")
                continue
            for key in (
                "source_profile_id",
                "target_platform",
                "target_profile_id",
                "platform_rules_profile_id",
            ):
                if workflow.get(key) != route.get(key):
                    add(
                        "ROUTE_REFERENCE_MISMATCH",
                        f"{location}.{key}",
                        f"公司库={workflow.get(key)!r}，路由={route.get(key)!r}。",
                    )
            target_profile_id = str(workflow.get("target_profile_id") or "")
            if target_profile_id not in platform_profiles:
                add("TARGET_PROFILE_REFERENCE_MISSING", location, target_profile_id)
            rules_profile_id = workflow.get("platform_rules_profile_id")
            if rules_profile_id and str(rules_profile_id) not in platform_rules:
                add("PLATFORM_RULES_REFERENCE_MISSING", location, str(rules_profile_id))
            elif rules_profile_id:
                try:
                    resolve_guanyi_policy_modules(
                        platform_rules[str(rules_profile_id)],
                        platform_rules_dir,
                    )
                except GuanyiPolicyModuleError as exc:
                    add("PLATFORM_POLICY_MODULE_INVALID", location, str(exc))
            mapping_scope = str(workflow.get("product_mapping_scope_id") or "")
            if mapping_scope and mapping_scope not in mapping_scopes:
                add("PRODUCT_MAPPING_SCOPE_MISSING", location, mapping_scope)

    return {
        "status": "ready" if not issues else "invalid",
        "registry_id": registry.get("registry_id"),
        "company_count": len(registry.get("companies", [])),
        "workflow_count": sum(
            len(company.get("workflows", []))
            for company in registry.get("companies", [])
        ),
        "issues": issues,
    }
