import fs from "node:fs/promises";
import path from "node:path";

import { PROJECT_ROOT } from "./config.mjs";


export const DEFAULT_AUDIT_POLICY_PATH = path.join(
  PROJECT_ROOT,
  "config/platform_profiles/guanyi_order_audit_v1.json",
);


export class AuditPolicyError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AuditPolicyError";
    this.code = code;
  }
}


function requirePolicy(condition, code, message) {
  if (!condition) {
    throw new AuditPolicyError(code, message);
  }
}


export function validateOrderAuditPolicy(policy) {
  requirePolicy(
    policy?.schema_version === "guanyi-order-audit-policy/1.0" &&
      policy?.policy_id === "guanyi_order_audit_v1" &&
      Number.isInteger(policy?.version) &&
      policy.version > 0,
    "ORDER_AUDIT_POLICY_INVALID",
    "订单审核策略身份或版本无效。",
  );
  const authorization = policy.authorization || {};
  requirePolicy(
    ["separate_after_upload", "combined_import_and_audit"].includes(
      authorization.mode,
    ) &&
      authorization.execute_token === "CONFIRM_GUANYI_ORDER_AUDIT",
    "ORDER_AUDIT_AUTHORIZATION_POLICY_INVALID",
    "订单审核授权策略无效。",
  );
  requirePolicy(
    authorization.mode !== "combined_import_and_audit" ||
      authorization.combined_mode_enabled === true,
    "ORDER_AUDIT_COMBINED_MODE_DISABLED",
    "上传与审核联动模式尚未启用；当前必须在上传核验成功后单独批准审核。",
  );
  const allowedScopes = policy.scope?.allowed;
  requirePolicy(
    Array.isArray(allowedScopes) &&
      allowedScopes.includes("all_pending") &&
      allowedScopes.includes("current_batch") &&
      allowedScopes.includes(policy.scope?.default),
    "ORDER_AUDIT_SCOPE_POLICY_INVALID",
    "订单审核范围策略无效。",
  );
  requirePolicy(
    Array.isArray(policy.ui?.pending_status_candidates) &&
      policy.ui.pending_status_candidates.length > 0,
    "ORDER_AUDIT_STATUS_POLICY_INVALID",
    "订单审核策略缺少待审核状态候选。",
  );
  const requiredEnvironments = new Set(
    policy.validation?.required_environments || [],
  );
  requirePolicy(
    requiredEnvironments.size === 2 &&
      requiredEnvironments.has("local") &&
      requiredEnvironments.has("doubao"),
    "ORDER_AUDIT_VALIDATION_ENVIRONMENTS_INVALID",
    "订单审核必须同时通过本地与豆包环境的只读页面探针。",
  );
  requirePolicy(
    authorization.mode !== "separate_after_upload" ||
      policy.safety?.requires_separate_user_authorization === true,
    "ORDER_AUDIT_SEPARATE_AUTHORIZATION_INVALID",
    "独立审核模式必须明确要求上传后的第二次用户授权。",
  );
  return policy;
}


export async function loadOrderAuditPolicy(
  policyPath = DEFAULT_AUDIT_POLICY_PATH,
) {
  let policy;
  try {
    policy = JSON.parse(await fs.readFile(policyPath, "utf8"));
  } catch {
    throw new AuditPolicyError(
      "ORDER_AUDIT_POLICY_UNREADABLE",
      "无法读取订单审核策略。",
    );
  }
  return validateOrderAuditPolicy(policy);
}


export function requireAuditUiValidation(policy) {
  const required = policy.validation?.required_environments || [];
  const validated = new Set(policy.validation?.validated_environments || []);
  const missing = required.filter((environment) => !validated.has(environment));
  if (policy.validation?.status !== "ready" || missing.length > 0) {
    throw new AuditPolicyError(
      "ORDER_AUDIT_UI_NOT_VALIDATED",
      `订单审核页面尚未完成全部只读探针验收：${missing.join(", ") || "状态未就绪"}。`,
    );
  }
  const pendingStatus = String(policy.ui?.confirmed_pending_status || "").trim();
  if (!pendingStatus) {
    throw new AuditPolicyError(
      "ORDER_AUDIT_PENDING_STATUS_UNCONFIRMED",
      "管易待审核筛选值尚未通过真实页面探针确认。",
    );
  }
  return pendingStatus;
}


export function resolveAuditScope(policy, requestedScope = null) {
  const scope = requestedScope || policy.scope.default;
  if (!policy.scope.allowed.includes(scope)) {
    throw new AuditPolicyError(
      "ORDER_AUDIT_SCOPE_NOT_ALLOWED",
      "订单审核范围不在允许列表中。",
    );
  }
  return scope;
}
