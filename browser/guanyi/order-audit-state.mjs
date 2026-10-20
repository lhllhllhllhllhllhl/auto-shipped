import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { loadOrderAuditPolicy, resolveAuditScope } from "./audit-policy.mjs";
import { AUDIT_OPERATION_DIR, OPERATION_DIR } from "./config.mjs";
import { loadOperation, OperationState } from "./operation-state.mjs";


export const ORDER_AUDIT_AUTHORIZATION = "CONFIRM_GUANYI_ORDER_AUDIT";


export const AuditOperationState = Object.freeze({
  PREPARED: "prepared",
  AUTHORIZED: "authorized",
  WRITE_REQUEST_MAYBE_SENT: "write_request_maybe_sent",
  RECONCILING: "reconciling",
  VERIFIED: "verified",
  NOTHING_TO_AUDIT: "nothing_to_audit",
  NEEDS_MANUAL_REVIEW: "needs_manual_review",
  FAILED_CLOSED: "failed_closed",
});


const TERMINAL_STATES = new Set([
  AuditOperationState.VERIFIED,
  AuditOperationState.NOTHING_TO_AUDIT,
  AuditOperationState.NEEDS_MANUAL_REVIEW,
  AuditOperationState.FAILED_CLOSED,
]);


const TRANSITIONS = Object.freeze({
  [AuditOperationState.PREPARED]: new Set([
    AuditOperationState.AUTHORIZED,
    AuditOperationState.FAILED_CLOSED,
  ]),
  [AuditOperationState.AUTHORIZED]: new Set([
    AuditOperationState.WRITE_REQUEST_MAYBE_SENT,
    AuditOperationState.NOTHING_TO_AUDIT,
    AuditOperationState.FAILED_CLOSED,
  ]),
  [AuditOperationState.WRITE_REQUEST_MAYBE_SENT]: new Set([
    AuditOperationState.RECONCILING,
    AuditOperationState.NEEDS_MANUAL_REVIEW,
    AuditOperationState.FAILED_CLOSED,
  ]),
  [AuditOperationState.RECONCILING]: new Set([
    AuditOperationState.RECONCILING,
    AuditOperationState.VERIFIED,
    AuditOperationState.NEEDS_MANUAL_REVIEW,
    AuditOperationState.FAILED_CLOSED,
  ]),
  [AuditOperationState.VERIFIED]: new Set(),
  [AuditOperationState.NOTHING_TO_AUDIT]: new Set(),
  [AuditOperationState.NEEDS_MANUAL_REVIEW]: new Set(),
  [AuditOperationState.FAILED_CLOSED]: new Set(),
});


export class AuditOperationError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AuditOperationError";
    this.code = code;
  }
}


function receiptPath(operationId, directory = AUDIT_OPERATION_DIR) {
  if (!/^[0-9a-f-]{36}$/.test(operationId || "")) {
    throw new AuditOperationError(
      "AUDIT_OPERATION_ID_INVALID",
      "审核操作 ID 格式不正确。",
    );
  }
  return path.join(directory, `${operationId}.json`);
}


async function writeAtomic(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  await fs.rename(temporary, filePath);
}


async function findAuditOperations(sourceImportOperationId, directory) {
  let entries = [];
  try {
    entries = await fs.readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
  const records = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    try {
      const record = JSON.parse(
        await fs.readFile(path.join(directory, entry.name), "utf8"),
      );
      if (
        record?.schemaVersion === "guanyi-order-audit-operation/1.0" &&
        record?.sourceImportOperationId === sourceImportOperationId &&
        TRANSITIONS[record?.state]
      ) {
        records.push(record);
      }
    } catch {
      // Ignore unrelated or incomplete files; direct loads remain fail-closed.
    }
  }
  return records;
}


export async function loadAuditOperation(
  auditOperationId,
  directory = AUDIT_OPERATION_DIR,
) {
  const filePath = receiptPath(auditOperationId, directory);
  let record;
  try {
    record = JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch {
    throw new AuditOperationError(
      "AUDIT_OPERATION_NOT_FOUND",
      "找不到对应的管易审核操作。",
    );
  }
  if (
    record.schemaVersion !== "guanyi-order-audit-operation/1.0" ||
    record.auditOperationId !== auditOperationId ||
    !TRANSITIONS[record.state]
  ) {
    throw new AuditOperationError(
      "AUDIT_OPERATION_RECORD_INVALID",
      "管易审核操作记录结构不正确。",
    );
  }
  return { record, receiptPath: filePath };
}


export async function createPreparedAuditOperation(
  sourceImportOperationId,
  requestedScope = null,
  {
    directory = AUDIT_OPERATION_DIR,
    importDirectory = OPERATION_DIR,
    policy: suppliedPolicy = null,
  } = {},
) {
  const source = await loadOperation(sourceImportOperationId, importDirectory);
  if (
    source.record.state !== OperationState.POST_WRITE_VERIFIED ||
    source.record.write?.postWriteVerified !== true
  ) {
    throw new AuditOperationError(
      "AUDIT_REQUIRES_VERIFIED_IMPORT",
      "只有任务结果确认全部导入成功后才能准备审核。",
    );
  }
  const policy = suppliedPolicy || (await loadOrderAuditPolicy());
  const scopeMode = resolveAuditScope(policy, requestedScope);
  const platformOrderNumbers =
    scopeMode === "current_batch"
      ? [...(source.record.auditScope?.platformOrderNumbers || [])]
      : [];
  if (
    scopeMode === "current_batch" &&
    (platformOrderNumbers.length !== source.record.counts?.orders ||
      platformOrderNumbers.length === 0)
  ) {
    throw new AuditOperationError(
      "CURRENT_BATCH_AUDIT_SCOPE_UNAVAILABLE",
      "当前上传操作没有完整的平台单号清单，不能安全使用“只审核本批”。",
    );
  }
  const existing = await findAuditOperations(sourceImportOperationId, directory);
  const sameScopeRecords = existing.filter(
    (record) => record.scope?.mode === scopeMode,
  );
  const activeSameScope = sameScopeRecords.find(
    (record) => !TERMINAL_STATES.has(record.state),
  );
  if (activeSameScope) {
    throw new AuditOperationError(
      "AUDIT_OPERATION_ALREADY_EXISTS",
      `该导入操作已经存在未闭环审核 ${activeSameScope.auditOperationId}。`,
    );
  }
  const completedSameScope = sameScopeRecords.find((record) =>
    [AuditOperationState.VERIFIED, AuditOperationState.NOTHING_TO_AUDIT].includes(
      record.state,
    ),
  );
  if (completedSameScope) {
    throw new AuditOperationError(
      "AUDIT_ALREADY_VERIFIED",
      `该导入操作的审核范围已由 ${completedSameScope.auditOperationId} 完成核验。`,
    );
  }

  const auditOperationId = randomUUID();
  const now = new Date().toISOString();
  const record = {
    schemaVersion: "guanyi-order-audit-operation/1.0",
    auditOperationId,
    sourceImportOperationId,
    state: AuditOperationState.PREPARED,
    createdAt: now,
    updatedAt: now,
    policy: {
      policyId: policy.policy_id,
      version: policy.version,
      authorizationMode: policy.authorization.mode,
    },
    scope: {
      mode: scopeMode,
      platformOrderNumbers,
    },
    authorization: {
      separateRequired: policy.authorization.mode === "separate_after_upload",
      granted: false,
      grantedAt: null,
    },
    write: {
      requestMaybeSent: false,
      selectedCount: 0,
      remainingPendingCount: null,
    },
    observations: [],
    history: [
      {
        at: now,
        from: null,
        to: AuditOperationState.PREPARED,
        reason: "verified_import_bound",
      },
    ],
  };
  const output = receiptPath(auditOperationId, directory);
  await writeAtomic(output, record);
  return { record, receiptPath: output };
}


async function transitionAuditOperation(
  auditOperationId,
  nextState,
  patch = {},
  directory = AUDIT_OPERATION_DIR,
) {
  const loaded = await loadAuditOperation(auditOperationId, directory);
  const current = loaded.record.state;
  if (!TRANSITIONS[current].has(nextState)) {
    throw new AuditOperationError(
      "AUDIT_OPERATION_TRANSITION_INVALID",
      `不允许从 ${current} 转换到 ${nextState}。`,
    );
  }
  const now = new Date().toISOString();
  const updated = {
    ...loaded.record,
    ...patch,
    state: nextState,
    updatedAt: now,
    history: [
      ...loaded.record.history,
      {
        at: now,
        from: current,
        to: nextState,
        reason: patch.reason || null,
      },
    ],
  };
  delete updated.reason;
  await writeAtomic(loaded.receiptPath, updated);
  return { record: updated, receiptPath: loaded.receiptPath };
}


export function requireOrderAuditAuthorization(authorization) {
  if (authorization !== ORDER_AUDIT_AUTHORIZATION) {
    throw new AuditOperationError(
      "ORDER_AUDIT_AUTHORIZATION_REQUIRED",
      "审核订单需要上传核验成功后的单独用户授权。",
    );
  }
}


export async function authorizeAuditOperation(
  auditOperationId,
  authorization,
  directory = AUDIT_OPERATION_DIR,
) {
  requireOrderAuditAuthorization(authorization);
  const loaded = await loadAuditOperation(auditOperationId, directory);
  if (loaded.record.policy.authorizationMode !== "separate_after_upload") {
    throw new AuditOperationError(
      "AUDIT_AUTHORIZATION_MODE_UNEXPECTED",
      "当前审核操作不是独立授权模式。",
    );
  }
  if (loaded.record.state === AuditOperationState.AUTHORIZED) {
    return loaded;
  }
  const now = new Date().toISOString();
  return await transitionAuditOperation(
    auditOperationId,
    AuditOperationState.AUTHORIZED,
    {
      reason: "separate_user_authorization_received",
      authorization: {
        ...loaded.record.authorization,
        granted: true,
        grantedAt: now,
      },
    },
    directory,
  );
}


export async function recordNoPendingOrders(
  auditOperationId,
  directory = AUDIT_OPERATION_DIR,
) {
  const loaded = await loadAuditOperation(auditOperationId, directory);
  return await transitionAuditOperation(
    auditOperationId,
    AuditOperationState.NOTHING_TO_AUDIT,
    {
      reason: "verified_no_pending_orders_in_scope",
      write: {
        ...loaded.record.write,
        selectedCount: 0,
        remainingPendingCount: 0,
      },
    },
    directory,
  );
}


export async function recordAuditWriteBoundary(
  auditOperationId,
  selectedCount,
  directory = AUDIT_OPERATION_DIR,
) {
  if (!Number.isInteger(selectedCount) || selectedCount <= 0) {
    throw new AuditOperationError(
      "AUDIT_SELECTION_COUNT_INVALID",
      "点击审核前必须确认至少选中一笔待审核订单。",
    );
  }
  const loaded = await loadAuditOperation(auditOperationId, directory);
  return await transitionAuditOperation(
    auditOperationId,
    AuditOperationState.WRITE_REQUEST_MAYBE_SENT,
    {
      reason: "audit_button_write_boundary_entered",
      write: {
        ...loaded.record.write,
        requestMaybeSent: true,
        selectedCount,
      },
    },
    directory,
  );
}


export async function startAuditReconciliation(
  auditOperationId,
  directory = AUDIT_OPERATION_DIR,
) {
  return await transitionAuditOperation(
    auditOperationId,
    AuditOperationState.RECONCILING,
    { reason: "audit_result_reconciliation_started" },
    directory,
  );
}


export async function recordAuditReconciliation(
  auditOperationId,
  remainingPendingCount,
  directory = AUDIT_OPERATION_DIR,
) {
  if (!Number.isInteger(remainingPendingCount) || remainingPendingCount < 0) {
    throw new AuditOperationError(
      "AUDIT_RECONCILIATION_COUNT_INVALID",
      "审核核验缺少可信的剩余待审核数量。",
    );
  }
  let loaded = await loadAuditOperation(auditOperationId, directory);
  if (loaded.record.state === AuditOperationState.WRITE_REQUEST_MAYBE_SENT) {
    loaded = await startAuditReconciliation(auditOperationId, directory);
  }
  if (loaded.record.state !== AuditOperationState.RECONCILING) {
    throw new AuditOperationError(
      "AUDIT_RECONCILIATION_STATE_INVALID",
      "当前审核操作状态不允许记录核验结果。",
    );
  }
  if (remainingPendingCount !== 0) {
    return await transitionAuditOperation(
      auditOperationId,
      AuditOperationState.RECONCILING,
      {
        reason: "pending_orders_still_visible_during_read_only_reconciliation",
        write: {
          ...loaded.record.write,
          remainingPendingCount,
        },
      },
      directory,
    );
  }
  return await transitionAuditOperation(
    auditOperationId,
    AuditOperationState.VERIFIED,
    {
      reason: "no_pending_orders_remain_in_scope",
      write: {
        ...loaded.record.write,
        remainingPendingCount: 0,
      },
    },
    directory,
  );
}
