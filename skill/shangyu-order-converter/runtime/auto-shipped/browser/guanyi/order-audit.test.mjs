import assert from "node:assert/strict";
import test from "node:test";

import {
  executeAuthorizedOrderAudit,
} from "./order-audit.mjs";
import { ORDER_AUDIT_AUTHORIZATION } from "./order-audit-state.mjs";


function readyPolicy() {
  return {
    policy_id: "guanyi_order_audit_v1",
    version: 1,
    authorization: { mode: "separate_after_upload" },
    ui: { confirmed_pending_status: "待审核" },
    validation: { status: "ready" },
  };
}


test("uses one separate authorization to audit every pending page in scope", async () => {
  const calls = [];
  const counts = [50, 12, 0];
  const baseRecord = {
    state: "authorized",
    scope: { mode: "all_pending", platformOrderNumbers: [] },
  };
  const result = await executeAuthorizedOrderAudit(
    {},
    "audit-operation-1",
    ORDER_AUDIT_AUTHORIZATION,
    {
      dependencies: {
        async loadOrderAuditPolicy() {
          return readyPolicy();
        },
        requireAuditUiValidation() {
          return "待审核";
        },
        async authorizeAuditOperation(id, authorization) {
          assert.equal(id, "audit-operation-1");
          assert.equal(authorization, ORDER_AUDIT_AUTHORIZATION);
          calls.push("authorize");
          return { record: baseRecord };
        },
        async preparePendingAuditSelection() {
          const selectedCount = counts.shift();
          calls.push(`select:${selectedCount}`);
          return {
            pendingCount: selectedCount,
            selectedCount,
            selectionSignature: selectedCount ? `page-${counts.length}` : null,
            frame: {},
            auditButton: {},
          };
        },
        async recordAuditWriteBoundary(id, selectedCount) {
          calls.push(`boundary:${selectedCount}`);
          return { record: { ...baseRecord, state: "write_request_maybe_sent" } };
        },
        async clickAuditAction() {
          calls.push("click_audit");
        },
        async loadAuditOperation() {
          return { record: { ...baseRecord, state: "write_request_maybe_sent" } };
        },
        async recordAuditReconciliation(id, remaining) {
          calls.push(`reconcile:${remaining}`);
          return { record: { ...baseRecord, state: "verified" } };
        },
        async recordNoPendingOrders() {
          throw new Error("not expected");
        },
      },
      environment: { GUANYI_AUDIT_RULES_READY: "1" },
    },
  );

  assert.equal(result.status, "audit_verified");
  assert.equal(result.auditedPasses, 2);
  assert.deepEqual(calls, [
    "authorize",
    "select:50",
    "boundary:50",
    "click_audit",
    "select:12",
    "click_audit",
    "select:0",
    "reconcile:0",
  ]);
});


test("treats an already empty pending scope as idempotent success without clicking", async () => {
  let clicked = false;
  const result = await executeAuthorizedOrderAudit(
    {},
    "audit-operation-2",
    ORDER_AUDIT_AUTHORIZATION,
    {
      dependencies: {
        async loadOrderAuditPolicy() {
          return readyPolicy();
        },
        requireAuditUiValidation() {
          return "待审核";
        },
        async authorizeAuditOperation() {
          return {
            record: {
              scope: { mode: "all_pending", platformOrderNumbers: [] },
            },
          };
        },
        async preparePendingAuditSelection() {
          return { pendingCount: 0, selectedCount: 0, selectionSignature: null };
        },
        async recordNoPendingOrders() {
          return { record: { state: "nothing_to_audit", scope: { mode: "all_pending" } } };
        },
        async clickAuditAction() {
          clicked = true;
        },
      },
      environment: { GUANYI_AUDIT_RULES_READY: "1" },
    },
  );

  assert.equal(result.status, "nothing_to_audit");
  assert.equal(clicked, false);
});


test("rejects execution until the audited browser rule gate is enabled", async () => {
  await assert.rejects(
    executeAuthorizedOrderAudit(
      {},
      "audit-operation-3",
      ORDER_AUDIT_AUTHORIZATION,
      {
        dependencies: {
          async loadOrderAuditPolicy() {
            throw new Error("must not load policy before rule gate");
          },
        },
        environment: {},
      },
    ),
    (error) => error.code === "ORDER_AUDIT_RULE_GATE_BLOCKED",
  );
});


test("allows a full next page when its non-sensitive selection signature changes", async () => {
  const pages = [
    { pendingCount: 50, selectedCount: 50, selectionSignature: "page-a" },
    { pendingCount: 50, selectedCount: 50, selectionSignature: "page-b" },
    { pendingCount: 0, selectedCount: 0, selectionSignature: null },
  ];
  let clicks = 0;
  const record = { scope: { mode: "all_pending", platformOrderNumbers: [] } };
  const result = await executeAuthorizedOrderAudit(
    {},
    "audit-operation-4",
    ORDER_AUDIT_AUTHORIZATION,
    {
      environment: { GUANYI_AUDIT_RULES_READY: "1" },
      dependencies: {
        async loadOrderAuditPolicy() {
          return readyPolicy();
        },
        requireAuditUiValidation() {
          return "待审核";
        },
        async authorizeAuditOperation() {
          return { record };
        },
        async preparePendingAuditSelection() {
          return { ...pages.shift(), frame: {}, auditButton: {} };
        },
        async recordAuditWriteBoundary() {
          return { record };
        },
        async clickAuditAction() {
          clicks += 1;
        },
        async loadAuditOperation() {
          return { record };
        },
        async recordAuditReconciliation(id, remaining) {
          assert.equal(remaining, 0);
          return { record: { ...record, state: "verified" } };
        },
      },
    },
  );
  assert.equal(result.status, "audit_verified");
  assert.equal(clicks, 2);
});


test("stops without a second audit click when the same pending page repeats", async () => {
  const pages = [
    { pendingCount: 50, selectedCount: 50, selectionSignature: "page-a" },
    { pendingCount: 50, selectedCount: 50, selectionSignature: "page-a" },
  ];
  let clicks = 0;
  const record = { scope: { mode: "all_pending", platformOrderNumbers: [] } };
  const result = await executeAuthorizedOrderAudit(
    {},
    "audit-operation-5",
    ORDER_AUDIT_AUTHORIZATION,
    {
      environment: { GUANYI_AUDIT_RULES_READY: "1" },
      dependencies: {
        async loadOrderAuditPolicy() {
          return readyPolicy();
        },
        requireAuditUiValidation() {
          return "待审核";
        },
        async authorizeAuditOperation() {
          return { record };
        },
        async preparePendingAuditSelection() {
          return { ...pages.shift(), frame: {}, auditButton: {} };
        },
        async recordAuditWriteBoundary() {
          return { record };
        },
        async clickAuditAction() {
          clicks += 1;
        },
        async loadAuditOperation() {
          return { record };
        },
        async recordAuditReconciliation(id, remaining) {
          assert.equal(remaining, 50);
          return { record: { ...record, state: "reconciling" } };
        },
      },
    },
  );
  assert.equal(result.status, "reconciling");
  assert.equal(result.reason, "pending_page_repeated");
  assert.equal(clicks, 1);
});
