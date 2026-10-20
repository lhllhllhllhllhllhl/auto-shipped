import assert from "node:assert/strict";
import test from "node:test";

import {
  AuditPolicyError,
  loadOrderAuditPolicy,
  requireAuditUiValidation,
  resolveAuditScope,
  validateOrderAuditPolicy,
} from "./audit-policy.mjs";


test("loads the separate-authorization policy with all-pending default", async () => {
  const policy = await loadOrderAuditPolicy();
  assert.equal(policy.authorization.mode, "separate_after_upload");
  assert.equal(policy.scope.default, "all_pending");
  assert.equal(resolveAuditScope(policy), "all_pending");
  assert.equal(resolveAuditScope(policy, "current_batch"), "current_batch");
});


test("keeps execution locked until both read-only probes confirm the UI vocabulary", async () => {
  const policy = await loadOrderAuditPolicy();
  assert.throws(
    () => requireAuditUiValidation(policy),
    (error) =>
      error instanceof AuditPolicyError &&
      error.code === "ORDER_AUDIT_UI_NOT_VALIDATED",
  );
});


test("does not enable combined upload and audit by changing only the mode label", async () => {
  const policy = await loadOrderAuditPolicy();
  const unsafe = structuredClone(policy);
  unsafe.authorization.mode = "combined_import_and_audit";
  assert.throws(
    () => validateOrderAuditPolicy(unsafe),
    (error) =>
      error instanceof AuditPolicyError &&
      error.code === "ORDER_AUDIT_COMBINED_MODE_DISABLED",
  );
});


test("does not allow policy edits to skip either execution environment probe", async () => {
  const policy = await loadOrderAuditPolicy();
  const unsafe = structuredClone(policy);
  unsafe.validation.required_environments = ["local"];
  assert.throws(
    () => validateOrderAuditPolicy(unsafe),
    (error) =>
      error instanceof AuditPolicyError &&
      error.code === "ORDER_AUDIT_VALIDATION_ENVIRONMENTS_INVALID",
  );
});
