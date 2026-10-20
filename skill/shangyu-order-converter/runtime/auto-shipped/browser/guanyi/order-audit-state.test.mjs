import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { OperationState } from "./operation-state.mjs";
import {
  AuditOperationError,
  AuditOperationState,
  authorizeAuditOperation,
  createPreparedAuditOperation,
  loadAuditOperation,
  ORDER_AUDIT_AUTHORIZATION,
  recordAuditReconciliation,
  recordAuditWriteBoundary,
} from "./order-audit-state.mjs";


function policy() {
  return {
    schema_version: "guanyi-order-audit-policy/1.0",
    policy_id: "guanyi_order_audit_v1",
    version: 1,
    authorization: {
      mode: "separate_after_upload",
      execute_token: ORDER_AUDIT_AUTHORIZATION,
    },
    scope: {
      default: "all_pending",
      allowed: ["all_pending", "current_batch"],
    },
  };
}


async function writeImportOperation(directory, operationId, state) {
  await fs.mkdir(directory, { recursive: true });
  await fs.writeFile(
    path.join(directory, `${operationId}.json`),
    JSON.stringify({
      schemaVersion: "guanyi-operation/1.0",
      operationId,
      state,
      counts: { orders: 2, itemRows: 3 },
      auditScope: { platformOrderNumbers: ["BATCH-001", "BATCH-002"] },
      write: { postWriteVerified: state === OperationState.POST_WRITE_VERIFIED },
    }),
  );
}


test("prepares all pending by default and requires a new audit authorization", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-audit-state-"));
  const importDirectory = path.join(root, "imports");
  const auditDirectory = path.join(root, "audits");
  const importOperationId = "11111111-1111-4111-8111-111111111111";
  try {
    await writeImportOperation(
      importDirectory,
      importOperationId,
      OperationState.POST_WRITE_VERIFIED,
    );
    const prepared = await createPreparedAuditOperation(
      importOperationId,
      null,
      { directory: auditDirectory, importDirectory, policy: policy() },
    );
    assert.equal(prepared.record.state, AuditOperationState.PREPARED);
    assert.equal(prepared.record.scope.mode, "all_pending");
    assert.deepEqual(prepared.record.scope.platformOrderNumbers, []);
    assert.equal(prepared.record.authorization.granted, false);

    await assert.rejects(
      authorizeAuditOperation(
        prepared.record.auditOperationId,
        "CONFIRM_GUANYI_IMPORT_SUBMISSION",
        auditDirectory,
      ),
      (error) =>
        error instanceof AuditOperationError &&
        error.code === "ORDER_AUDIT_AUTHORIZATION_REQUIRED",
    );

    const authorized = await authorizeAuditOperation(
      prepared.record.auditOperationId,
      ORDER_AUDIT_AUTHORIZATION,
      auditDirectory,
    );
    assert.equal(authorized.record.state, AuditOperationState.AUTHORIZED);
    assert.equal(authorized.record.authorization.granted, true);
    const resumed = await authorizeAuditOperation(
      prepared.record.auditOperationId,
      ORDER_AUDIT_AUTHORIZATION,
      auditDirectory,
    );
    assert.equal(resumed.record.state, AuditOperationState.AUTHORIZED);
    assert.equal(
      resumed.record.authorization.grantedAt,
      authorized.record.authorization.grantedAt,
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});


test("current-batch special scope is bound to manifest platform order numbers", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-audit-state-"));
  const importDirectory = path.join(root, "imports");
  const auditDirectory = path.join(root, "audits");
  const importOperationId = "22222222-2222-4222-8222-222222222222";
  try {
    await writeImportOperation(
      importDirectory,
      importOperationId,
      OperationState.POST_WRITE_VERIFIED,
    );
    const prepared = await createPreparedAuditOperation(
      importOperationId,
      "current_batch",
      { directory: auditDirectory, importDirectory, policy: policy() },
    );
    assert.equal(prepared.record.scope.mode, "current_batch");
    assert.deepEqual(prepared.record.scope.platformOrderNumbers, [
      "BATCH-001",
      "BATCH-002",
    ]);
    const loaded = await loadAuditOperation(
      prepared.record.auditOperationId,
      auditDirectory,
    );
    assert.deepEqual(loaded.record.scope, prepared.record.scope);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});


test("does not prepare an audit before upload result verification", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-audit-state-"));
  const importDirectory = path.join(root, "imports");
  const auditDirectory = path.join(root, "audits");
  const importOperationId = "33333333-3333-4333-8333-333333333333";
  try {
    await writeImportOperation(
      importDirectory,
      importOperationId,
      OperationState.RECONCILING,
    );
    await assert.rejects(
      createPreparedAuditOperation(importOperationId, null, {
        directory: auditDirectory,
        importDirectory,
        policy: policy(),
      }),
      (error) =>
        error instanceof AuditOperationError &&
        error.code === "AUDIT_REQUIRES_VERIFIED_IMPORT",
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});


test("keeps uncertain write results reconciling until a read proves none remain", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-audit-state-"));
  const importDirectory = path.join(root, "imports");
  const auditDirectory = path.join(root, "audits");
  const importOperationId = "44444444-4444-4444-8444-444444444444";
  try {
    await writeImportOperation(
      importDirectory,
      importOperationId,
      OperationState.POST_WRITE_VERIFIED,
    );
    const prepared = await createPreparedAuditOperation(
      importOperationId,
      null,
      { directory: auditDirectory, importDirectory, policy: policy() },
    );
    await authorizeAuditOperation(
      prepared.record.auditOperationId,
      ORDER_AUDIT_AUTHORIZATION,
      auditDirectory,
    );
    await recordAuditWriteBoundary(
      prepared.record.auditOperationId,
      2,
      auditDirectory,
    );
    const pending = await recordAuditReconciliation(
      prepared.record.auditOperationId,
      2,
      auditDirectory,
    );
    assert.equal(pending.record.state, AuditOperationState.RECONCILING);
    const verified = await recordAuditReconciliation(
      prepared.record.auditOperationId,
      0,
      auditDirectory,
    );
    assert.equal(verified.record.state, AuditOperationState.VERIFIED);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
