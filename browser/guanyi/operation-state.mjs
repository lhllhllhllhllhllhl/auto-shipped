import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

import { OPERATION_DIR } from "./config.mjs";
import {
  buildVerifiedTaskSnapshot,
  diffVerifiedTaskSnapshot,
  TaskReconciliationError,
} from "./task-reconciliation.mjs";


export const OperationState = Object.freeze({
  PREPARED: "prepared",
  FILE_SELECTION_STARTED: "file_selection_started",
  WRITE_REQUEST_MAYBE_SENT: "write_request_maybe_sent",
  SUBMIT_AUTHORIZED: "submit_authorized",
  WRITE_REQUEST_SENT: "write_request_sent",
  RECONCILING: "reconciling",
  QUARANTINED: "quarantined",
  TASK_ID_OBSERVED: "task_id_observed",
  POST_WRITE_VERIFIED: "post_write_verified",
  NEEDS_MANUAL_REVIEW: "needs_manual_review",
  RETRYABLE_FAILED: "retryable_failed",
  FAILED_CLOSED: "failed_closed",
});


export const NO_TASK_RESOLUTION_AUTHORIZATION =
  "CONFIRM_CLOSE_NO_TASK_OPERATION";


const TRANSITIONS = Object.freeze({
  [OperationState.PREPARED]: new Set([
    OperationState.FILE_SELECTION_STARTED,
    OperationState.NEEDS_MANUAL_REVIEW,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.FILE_SELECTION_STARTED]: new Set([
    OperationState.WRITE_REQUEST_MAYBE_SENT,
    OperationState.NEEDS_MANUAL_REVIEW,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.WRITE_REQUEST_MAYBE_SENT]: new Set([
    OperationState.SUBMIT_AUTHORIZED,
    OperationState.RECONCILING,
    OperationState.NEEDS_MANUAL_REVIEW,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.SUBMIT_AUTHORIZED]: new Set([
    OperationState.WRITE_REQUEST_SENT,
    OperationState.NEEDS_MANUAL_REVIEW,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.WRITE_REQUEST_SENT]: new Set([
    OperationState.RECONCILING,
    OperationState.NEEDS_MANUAL_REVIEW,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.RECONCILING]: new Set([
    OperationState.QUARANTINED,
    OperationState.TASK_ID_OBSERVED,
    OperationState.NEEDS_MANUAL_REVIEW,
    OperationState.RETRYABLE_FAILED,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.QUARANTINED]: new Set([
    OperationState.TASK_ID_OBSERVED,
    OperationState.NEEDS_MANUAL_REVIEW,
  ]),
  [OperationState.TASK_ID_OBSERVED]: new Set([
    OperationState.POST_WRITE_VERIFIED,
    OperationState.NEEDS_MANUAL_REVIEW,
    OperationState.RETRYABLE_FAILED,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.POST_WRITE_VERIFIED]: new Set(),
  [OperationState.NEEDS_MANUAL_REVIEW]: new Set([
    OperationState.RETRYABLE_FAILED,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.RETRYABLE_FAILED]: new Set(),
  [OperationState.FAILED_CLOSED]: new Set(),
});


const IMPORT_TASK_NAME = "自定义订单导入";
export const DEFAULT_NO_TASK_QUARANTINE_MS = 5 * 60 * 1_000;
const VERIFIED_SUCCESS_STATUSES = new Set(["执行完成", "完成", "成功"]);
const RUNNING_STATUSES = new Set(["执行中", "等待执行", "排队中", "处理中"]);
const FAILED_STATUSES = new Set(["执行失败", "失败", "已失败"]);
const SAFE_IMPORT_FAILURE_MESSAGES = Object.freeze({
  STORE_NOT_FOUND: "无法匹配到管易店铺信息",
  PRODUCT_NOT_FOUND: "商品信息无法匹配",
  SKU_NOT_FOUND: "规格信息无法匹配",
  PLATFORM_ORDER_DUPLICATE: "平台单号重复或已存在",
  IMPORT_FAILURE_REDACTED:
    "管易返回了未登记的失败原因；详细内容已脱敏，请在结果窗口人工查看。",
});


export class OperationStateError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "OperationStateError";
    this.code = code;
  }
}


function normalizedImportResult(result) {
  const summary = result?.summary;
  const values = {
    total: summary?.total,
    successful: summary?.successful,
    formatErrors: summary?.formatErrors,
    illegalData: summary?.illegalData,
    failed: summary?.failed,
  };
  if (
    result?.status !== "import_task_result_ready" ||
    !/^\d+$/.test(result?.taskId || "") ||
    result?.taskName !== IMPORT_TASK_NAME ||
    Object.values(values).some(
      (value) => !Number.isInteger(value) || value < 0,
    ) ||
    values.failed !== values.formatErrors + values.illegalData ||
    values.total !== values.successful + values.failed
  ) {
    throw new OperationStateError(
      "IMPORT_RESULT_INVALID",
      "导入结果缺少可信的任务身份或数量统计。",
    );
  }
  const failures = Array.isArray(result.failures)
    ? result.failures.slice(0, 10).map((failure) => {
        const requestedCode = String(
          failure?.code || "IMPORT_FAILURE_REDACTED",
        );
        const code = Object.hasOwn(
          SAFE_IMPORT_FAILURE_MESSAGES,
          requestedCode,
        )
          ? requestedCode
          : "IMPORT_FAILURE_REDACTED";
        return {
          sourceRow:
            Number.isInteger(failure?.sourceRow) && failure.sourceRow > 0
              ? failure.sourceRow
              : null,
          code,
          message: SAFE_IMPORT_FAILURE_MESSAGES[code],
        };
      })
    : [];
  return {
    status: "import_task_result_ready",
    taskId: result.taskId,
    taskName: result.taskName,
    taskStatus: String(result.taskStatus || ""),
    summary: values,
    failures,
    visibleFailureCount: Number.isInteger(result.visibleFailureCount)
      ? result.visibleFailureCount
      : failures.length,
    detailsTruncated: Boolean(result.detailsTruncated),
  };
}


function receiptPath(operationId, directory = OPERATION_DIR) {
  if (!/^[0-9a-f-]{36}$/.test(operationId)) {
    throw new OperationStateError(
      "OPERATION_ID_INVALID",
      "操作 ID 格式不正确。",
    );
  }
  return path.join(directory, `${operationId}.json`);
}


async function writeAtomic(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(
    temporary,
    `${JSON.stringify(payload, null, 2)}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
  await fs.rename(temporary, filePath);
}


const RETRYABLE_TERMINAL_STATES = new Set([
  OperationState.RETRYABLE_FAILED,
  OperationState.FAILED_CLOSED,
]);


async function findArtifactOperations(artifactSha256, directory) {
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
        record?.schemaVersion === "guanyi-operation/1.0" &&
        record?.artifact?.sha256 === artifactSha256 &&
        TRANSITIONS[record?.state]
      ) {
        records.push(record);
      }
    } catch {
      // Ignore unrelated or incomplete files; their validity is checked when loaded directly.
    }
  }
  return records.sort(
    (left, right) =>
      Date.parse(right.updatedAt || right.createdAt || 0) -
      Date.parse(left.updatedAt || left.createdAt || 0),
  );
}


async function claimedTaskIdsByOtherOperations(operationId, directory) {
  let entries = [];
  try {
    entries = await fs.readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
  const claimed = new Set();
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    try {
      const record = JSON.parse(
        await fs.readFile(path.join(directory, entry.name), "utf8"),
      );
      if (
        record?.schemaVersion === "guanyi-operation/1.0" &&
        record?.operationId !== operationId &&
        /^\d+$/.test(record?.write?.taskId || "")
      ) {
        claimed.add(record.write.taskId);
      }
    } catch {
      // Ignore unrelated or incomplete files; direct loads still validate strictly.
    }
  }
  return [...claimed];
}


function requestSentAt(record) {
  const transition = [...(record.history || [])]
    .reverse()
    .find((entry) => entry?.to === OperationState.WRITE_REQUEST_SENT);
  const timestamp = Date.parse(transition?.at || "");
  return Number.isFinite(timestamp) ? timestamp : null;
}


export async function createPreparedOperation(
  verifiedManifest,
  directory = OPERATION_DIR,
) {
  if (verifiedManifest?.status !== "manifest_verified") {
    throw new OperationStateError(
      "MANIFEST_NOT_VERIFIED",
      "只有通过 manifest 校验的批次才能建立操作记录。",
    );
  }
  const existing = await findArtifactOperations(
    verifiedManifest.artifactSha256,
    directory,
  );
  const verified = existing.find(
    (record) => record.state === OperationState.POST_WRITE_VERIFIED,
  );
  if (verified) {
    throw new OperationStateError(
      "ARTIFACT_ALREADY_VERIFIED",
      `相同Excel已经由操作 ${verified.operationId} 完成核验，禁止重复上传。`,
    );
  }
  const active = existing.find(
    (record) => !RETRYABLE_TERMINAL_STATES.has(record.state),
  );
  if (active) {
    throw new OperationStateError(
      "ARTIFACT_OPERATION_ALREADY_EXISTS",
      `相同Excel已经存在未闭环操作 ${active.operationId}，请先核验该操作，不要盲目重试。`,
    );
  }
  const retryParent = existing.find((record) =>
    RETRYABLE_TERMINAL_STATES.has(record.state),
  );
  const highestRecordedAttempt = existing.reduce(
    (highest, record) =>
      Number.isInteger(record.attemptNumber)
        ? Math.max(highest, record.attemptNumber)
        : highest,
    0,
  );
  const operationId = randomUUID();
  const now = new Date().toISOString();
  const record = {
    schemaVersion: "guanyi-operation/1.0",
    operationId,
    retryOf: retryParent?.operationId || null,
    attemptNumber: existing.length
      ? Math.max(existing.length, highestRecordedAttempt) + 1
      : 1,
    platform: "guanyi",
    operation: "custom_order_import",
    state: OperationState.PREPARED,
    createdAt: now,
    updatedAt: now,
    manifest: {
      path: verifiedManifest.manifestPath,
      artifactPath: verifiedManifest.manifestArtifactPath,
      profileId: verifiedManifest.profileId,
    },
    artifact: {
      path: verifiedManifest.workbookPath,
      pathBinding: verifiedManifest.artifactPathBinding,
      fileName: verifiedManifest.artifactFileName,
      sizeBytes: verifiedManifest.artifactSizeBytes,
      sha256: verifiedManifest.artifactSha256,
    },
    counts: {
      orders: verifiedManifest.orders,
      itemRows: verifiedManifest.itemRows,
    },
    auditScope: {
      platformOrderNumbers: [...verifiedManifest.platformOrderNumbers],
    },
    write: {
      fileSelectionStarted: false,
      requestMaybeSent: false,
      submitAuthorized: false,
      requestSent: false,
      taskId: null,
      taskName: null,
      taskStatus: null,
      importResult: null,
      postWriteVerified: false,
    },
    taskBaseline: null,
    observations: [],
    history: [
      {
        at: now,
        from: null,
        to: OperationState.PREPARED,
        reason: retryParent
          ? "manifest_verified_retry"
          : "manifest_verified",
      },
    ],
  };
  const output = receiptPath(operationId, directory);
  await writeAtomic(output, record);
  return { record, receiptPath: output };
}


export async function recordTaskBaseline(
  operationId,
  taskResult,
  directory = OPERATION_DIR,
) {
  const loaded = await loadOperation(operationId, directory);
  if (loaded.record.state !== OperationState.PREPARED) {
    throw new OperationStateError(
      "TASK_BASELINE_TOO_LATE",
      "只能在文件选择开始前记录任务中心基线。",
    );
  }
  let baseline;
  try {
    baseline = buildVerifiedTaskSnapshot(taskResult);
  } catch (error) {
    if (error instanceof TaskReconciliationError) {
      throw new OperationStateError(error.code, error.message);
    }
    throw error;
  }
  const now = new Date().toISOString();
  const updated = {
    ...loaded.record,
    updatedAt: now,
    taskBaseline: baseline,
    observations: [
      ...(loaded.record.observations || []),
      {
        at: now,
        type: "task_baseline_captured",
        taskCount: baseline.taskIds.length,
      },
    ],
  };
  await writeAtomic(loaded.receiptPath, updated);
  return { record: updated, receiptPath: loaded.receiptPath };
}


export async function reconcileUniqueNewTask(
  operationId,
  currentTaskResult,
  directory = OPERATION_DIR,
) {
  const loaded = await loadOperation(operationId, directory);
  if (
    ![
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      OperationState.WRITE_REQUEST_SENT,
    ].includes(loaded.record.state)
  ) {
    throw new OperationStateError(
      "TASK_RECONCILIATION_STATE_INVALID",
      "只有文件选择或提交可能发生后才能关联新增任务。",
    );
  }
  let diff;
  try {
    diff = diffVerifiedTaskSnapshot(
      loaded.record.taskBaseline,
      currentTaskResult,
    );
  } catch (error) {
    if (error instanceof TaskReconciliationError) {
      throw new OperationStateError(error.code, error.message);
    }
    throw error;
  }
  if (diff.status !== "unique_new_task") {
    throw new OperationStateError(
      diff.status === "no_new_task"
        ? "NEW_TASK_NOT_FOUND"
        : "NEW_TASK_AMBIGUOUS",
      diff.status === "no_new_task"
        ? "任务中心尚未出现本次操作的新增任务。"
        : "任务中心出现多个新增任务，已停止自动关联。",
    );
  }
  await transitionOperation(
    operationId,
    OperationState.RECONCILING,
    { reason: "task_center_reconciliation_started" },
    directory,
  );
  return await transitionOperation(
    operationId,
    OperationState.TASK_ID_OBSERVED,
    {
      reason: "unique_new_task_from_verified_baseline",
      taskId: diff.task.taskId,
      taskName: diff.task.taskName,
      taskStatus: diff.task.status,
    },
    directory,
  );
}


export async function reconcileOperationTask(
  operationId,
  currentTaskResult,
  directory = OPERATION_DIR,
  {
    nowMs = Date.now(),
    noTaskQuarantineMs = DEFAULT_NO_TASK_QUARANTINE_MS,
  } = {},
) {
  let loaded = await loadOperation(operationId, directory);
  if (
    ![
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      OperationState.WRITE_REQUEST_SENT,
      OperationState.RECONCILING,
      OperationState.QUARANTINED,
      OperationState.TASK_ID_OBSERVED,
    ].includes(loaded.record.state)
  ) {
    throw new OperationStateError(
      "TASK_RECONCILIATION_STATE_INVALID",
      "当前操作状态不允许执行任务中心核验。",
    );
  }
  if (
    loaded.record.state !== OperationState.RECONCILING &&
    loaded.record.state !== OperationState.QUARANTINED &&
    loaded.record.state !== OperationState.TASK_ID_OBSERVED
  ) {
    loaded = await transitionOperation(
      operationId,
      OperationState.RECONCILING,
      { reason: "task_center_reconciliation_started" },
      directory,
    );
  }
  let diff;
  try {
    const excludedTaskIds = await claimedTaskIdsByOtherOperations(
      operationId,
      directory,
    );
    diff = diffVerifiedTaskSnapshot(
      loaded.record.taskBaseline,
      currentTaskResult,
      { excludedTaskIds },
    );
  } catch (error) {
    if (error instanceof TaskReconciliationError) {
      const reviewed = await transitionOperation(
        operationId,
        OperationState.NEEDS_MANUAL_REVIEW,
        { reason: `task_evidence_insufficient:${error.code}` },
        directory,
      );
      return {
        status: "needs_manual_review",
        reason: "task_evidence_insufficient",
        code: error.code,
        record: reviewed.record,
      };
    }
    throw error;
  }
  if (diff.status === "no_new_task") {
    if (
      !loaded.record.write.submitAuthorized &&
      !loaded.record.write.requestSent
    ) {
      const retryable = await transitionOperation(
        operationId,
        OperationState.RETRYABLE_FAILED,
        {
          reason: "file_staged_without_submit_task",
          failureCode: "NO_TASK_CREATED",
        },
        directory,
      );
      return {
        status: "retryable_failed",
        reason: "file_staged_without_submit_task",
        record: retryable.record,
      };
    }
    if (loaded.record.write.requestSent) {
      const sentAt = requestSentAt(loaded.record);
      if (sentAt === null) {
        const reviewed = await transitionOperation(
          operationId,
          OperationState.NEEDS_MANUAL_REVIEW,
          { reason: "write_request_timestamp_missing" },
          directory,
        );
        return {
          status: "needs_manual_review",
          reason: "write_request_timestamp_missing",
          record: reviewed.record,
        };
      }
      const elapsedMs = Math.max(0, nowMs - sentAt);
      if (
        elapsedMs >= noTaskQuarantineMs &&
        loaded.record.state !== OperationState.QUARANTINED
      ) {
        loaded = await transitionOperation(
          operationId,
          OperationState.QUARANTINED,
          { reason: "submitted_task_not_visible_after_quarantine_window" },
          directory,
        );
      }
      if (loaded.record.state === OperationState.QUARANTINED) {
        return {
          status: "quarantined",
          reason: "submitted_task_not_visible_after_quarantine_window",
          quarantineAfterMs: noTaskQuarantineMs,
          elapsedMs,
          record: loaded.record,
        };
      }
      return {
        status: "reconciling",
        reason: "no_new_task",
        retryAfterMs: Math.max(0, noTaskQuarantineMs - elapsedMs),
        record: loaded.record,
      };
    }
    return { status: "reconciling", reason: "no_new_task", record: loaded.record };
  }
  if (diff.status === "ambiguous_new_tasks") {
    const reviewed = await transitionOperation(
      operationId,
      OperationState.NEEDS_MANUAL_REVIEW,
      { reason: "multiple_verified_new_tasks" },
      directory,
    );
    return {
      status: "needs_manual_review",
      reason: "multiple_new_tasks",
      candidates: diff.newTasks,
      record: reviewed.record,
    };
  }

  const task = diff.task;
  if (loaded.record.state !== OperationState.TASK_ID_OBSERVED) {
    loaded = await transitionOperation(
      operationId,
      OperationState.TASK_ID_OBSERVED,
      {
        reason: "unique_new_task_from_verified_baseline",
        taskId: task.taskId,
        taskName: task.taskName,
        taskStatus: task.status,
      },
      directory,
    );
  }
  if (RUNNING_STATUSES.has(task.status)) {
    return { status: "reconciling", reason: "task_still_running", task, record: loaded.record };
  }
  if (VERIFIED_SUCCESS_STATUSES.has(task.status)) {
    return {
      status: "result_required",
      reason: "task_execution_completed_result_not_read",
      task,
      record: loaded.record,
    };
  }
  if (FAILED_STATUSES.has(task.status)) {
    const failed = await transitionOperation(
      operationId,
      OperationState.RETRYABLE_FAILED,
      {
        reason: "task_center_reported_failure",
        failureCode: "IMPORT_TASK_FAILED",
        taskId: task.taskId,
        taskName: task.taskName,
        taskStatus: task.status,
      },
      directory,
    );
    return { status: "retryable_failed", task, record: failed.record };
  }
  const reviewed = await transitionOperation(
    operationId,
    OperationState.NEEDS_MANUAL_REVIEW,
    {
      reason: "task_status_not_recognized",
      taskId: task.taskId,
      taskName: task.taskName,
      taskStatus: task.status,
    },
    directory,
  );
  return {
    status: "needs_manual_review",
    reason: "task_status_not_recognized",
    task,
    record: reviewed.record,
  };
}


export async function recordImportTaskResult(
  operationId,
  importResult,
  directory = OPERATION_DIR,
) {
  const loaded = await loadOperation(operationId, directory);
  if (loaded.record.state !== OperationState.TASK_ID_OBSERVED) {
    throw new OperationStateError(
      "IMPORT_RESULT_STATE_INVALID",
      "只有已经关联唯一任务 ID 的操作才能记录导入结果。",
    );
  }
  const result = normalizedImportResult(importResult);
  if (
    result.taskId !== loaded.record.write.taskId ||
    result.taskName !== loaded.record.write.taskName
  ) {
    throw new OperationStateError(
      "IMPORT_RESULT_TASK_MISMATCH",
      "导入结果与当前操作关联的任务不一致。",
    );
  }
  if (result.summary.total !== loaded.record.counts.orders) {
    const reviewed = await transitionOperation(
      operationId,
      OperationState.NEEDS_MANUAL_REVIEW,
      {
        reason: "task_result_order_count_mismatch",
        taskId: result.taskId,
        taskName: result.taskName,
        taskStatus: result.taskStatus,
        importResult: result,
      },
      directory,
    );
    return {
      status: "needs_manual_review",
      reason: "task_result_order_count_mismatch",
      expectedOrders: loaded.record.counts.orders,
      observedTotal: result.summary.total,
      result,
      record: reviewed.record,
    };
  }
  if (
    result.summary.total > 0 &&
    result.summary.successful === result.summary.total &&
    result.summary.failed === 0
  ) {
    const verified = await transitionOperation(
      operationId,
      OperationState.POST_WRITE_VERIFIED,
      {
        reason: "task_result_all_rows_succeeded",
        taskId: result.taskId,
        taskName: result.taskName,
        taskStatus: result.taskStatus,
        importResult: result,
      },
      directory,
    );
    return { status: "verified", result, record: verified.record };
  }
  if (result.summary.successful > 0 && result.summary.failed > 0) {
    const reviewed = await transitionOperation(
      operationId,
      OperationState.NEEDS_MANUAL_REVIEW,
      {
        reason: "task_result_partial_success",
        taskId: result.taskId,
        taskName: result.taskName,
        taskStatus: result.taskStatus,
        importResult: result,
      },
      directory,
    );
    return {
      status: "needs_manual_review",
      reason: "partial_success",
      result,
      record: reviewed.record,
    };
  }
  if (result.summary.successful === 0 && result.summary.failed > 0) {
    const failed = await transitionOperation(
      operationId,
      OperationState.RETRYABLE_FAILED,
      {
        reason: "task_result_all_rows_failed",
        failureCode: "IMPORT_RESULT_FAILED",
        taskId: result.taskId,
        taskName: result.taskName,
        taskStatus: result.taskStatus,
        importResult: result,
      },
      directory,
    );
    return { status: "retryable_failed", result, record: failed.record };
  }
  const reviewed = await transitionOperation(
    operationId,
    OperationState.NEEDS_MANUAL_REVIEW,
    {
      reason: "task_result_counts_not_actionable",
      taskId: result.taskId,
      taskName: result.taskName,
      taskStatus: result.taskStatus,
      importResult: result,
    },
    directory,
  );
  return {
    status: "needs_manual_review",
    reason: "result_counts_not_actionable",
    result,
    record: reviewed.record,
  };
}


export async function resolveNoTaskManualReview(
  operationId,
  currentTaskResult,
  authorization,
  directory = OPERATION_DIR,
) {
  if (authorization !== NO_TASK_RESOLUTION_AUTHORIZATION) {
    throw new OperationStateError(
      "NO_TASK_RESOLUTION_AUTHORIZATION_REQUIRED",
      "关闭人工复核操作需要本次明确授权。",
    );
  }
  const loaded = await loadOperation(operationId, directory);
  if (
    ![
      OperationState.RECONCILING,
      OperationState.NEEDS_MANUAL_REVIEW,
    ].includes(loaded.record.state)
  ) {
    throw new OperationStateError(
      "NO_TASK_RESOLUTION_STATE_INVALID",
      "只有 reconciling 或 needs_manual_review 状态才能按无新增任务关闭。",
    );
  }
  if (loaded.record.write.requestSent) {
    throw new OperationStateError(
      "NO_TASK_RESOLUTION_FINAL_SUBMIT_UNCERTAIN",
      "最终导入请求可能已经发出；即使暂时没有新增任务也不能关闭或重试，只能继续只读核验。",
    );
  }
  let diff;
  try {
    diff = diffVerifiedTaskSnapshot(
      loaded.record.taskBaseline,
      currentTaskResult,
    );
  } catch (error) {
    if (error instanceof TaskReconciliationError) {
      throw new OperationStateError(error.code, error.message);
    }
    throw error;
  }
  if (diff.status !== "no_new_task") {
    throw new OperationStateError(
      "NO_TASK_RESOLUTION_EVIDENCE_CONFLICT",
      "任务中心存在新增任务，不能按无任务关闭旧操作。",
    );
  }
  return await transitionOperation(
    operationId,
    OperationState.RETRYABLE_FAILED,
    {
      reason: "manual_review_resolved_no_new_task",
      failureCode: "NO_TASK_CREATED",
    },
    directory,
  );
}


export async function loadOperation(operationId, directory = OPERATION_DIR) {
  const filePath = receiptPath(operationId, directory);
  let record;
  try {
    record = JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch {
    throw new OperationStateError(
      "OPERATION_NOT_FOUND",
      "找不到对应的管易操作记录。",
    );
  }
  if (
    record.schemaVersion !== "guanyi-operation/1.0" ||
    record.operationId !== operationId ||
    !TRANSITIONS[record.state]
  ) {
    throw new OperationStateError(
      "OPERATION_RECORD_INVALID",
      "管易操作记录结构不正确。",
    );
  }
  return { record, receiptPath: filePath };
}


function writeFlagsFor(state, previous, patch) {
  return {
    ...previous,
    fileSelectionStarted:
      previous.fileSelectionStarted ||
      state === OperationState.FILE_SELECTION_STARTED,
    requestMaybeSent:
      previous.requestMaybeSent ||
      state === OperationState.WRITE_REQUEST_MAYBE_SENT ||
      state === OperationState.WRITE_REQUEST_SENT ||
      state === OperationState.RECONCILING ||
      state === OperationState.QUARANTINED ||
      state === OperationState.TASK_ID_OBSERVED ||
      state === OperationState.POST_WRITE_VERIFIED,
    submitAuthorized:
      previous.submitAuthorized || state === OperationState.SUBMIT_AUTHORIZED,
    requestSent:
      previous.requestSent ||
      state === OperationState.WRITE_REQUEST_SENT ||
      state === OperationState.TASK_ID_OBSERVED ||
      state === OperationState.POST_WRITE_VERIFIED,
    taskId: patch.taskId ?? previous.taskId,
    taskName: patch.taskName ?? previous.taskName,
    taskStatus: patch.taskStatus ?? previous.taskStatus,
    importResult: patch.importResult ?? previous.importResult ?? null,
    postWriteVerified:
      previous.postWriteVerified ||
      state === OperationState.POST_WRITE_VERIFIED,
  };
}


export async function transitionOperation(
  operationId,
  nextState,
  {
    reason,
    taskId = null,
    taskName = null,
    taskStatus = null,
    importResult = null,
    failureCode = null,
  } = {},
  directory = OPERATION_DIR,
) {
  const loaded = await loadOperation(operationId, directory);
  const current = loaded.record.state;
  if (!TRANSITIONS[current].has(nextState)) {
    throw new OperationStateError(
      "OPERATION_TRANSITION_INVALID",
      `不允许从 ${current} 转换到 ${nextState}。`,
    );
  }
  const effectiveTaskId = taskId || loaded.record.write.taskId;
  const effectiveTaskName = taskName || loaded.record.write.taskName;
  const effectiveTaskStatus = taskStatus || loaded.record.write.taskStatus;
  if (
    [OperationState.TASK_ID_OBSERVED, OperationState.POST_WRITE_VERIFIED]
      .includes(nextState) &&
    (!/^\d+$/.test(effectiveTaskId || "") ||
      effectiveTaskName !== IMPORT_TASK_NAME)
  ) {
    throw new OperationStateError(
      "TASK_EVIDENCE_REQUIRED",
      "进入任务回读状态前必须获得经过核验的任务 ID 和任务类型。",
    );
  }
  if (
    nextState === OperationState.POST_WRITE_VERIFIED &&
    !VERIFIED_SUCCESS_STATUSES.has(effectiveTaskStatus)
  ) {
    throw new OperationStateError(
      "TASK_SUCCESS_STATUS_REQUIRED",
      "只有任务中心明确返回成功状态时才能标记导入成功。",
    );
  }
  const effectiveImportResult = importResult
    ? normalizedImportResult(importResult)
    : loaded.record.write.importResult;
  if (
    nextState === OperationState.POST_WRITE_VERIFIED &&
    (!effectiveImportResult ||
      effectiveImportResult.taskId !== effectiveTaskId ||
      effectiveImportResult.summary.total <= 0 ||
      effectiveImportResult.summary.successful !==
        effectiveImportResult.summary.total ||
      effectiveImportResult.summary.failed !== 0)
  ) {
    throw new OperationStateError(
      "IMPORT_RESULT_SUCCESS_REQUIRED",
      "只有结果窗口确认全部订单成功后才能标记导入成功。",
    );
  }
  const now = new Date().toISOString();
  const updated = {
    ...loaded.record,
    state: nextState,
    updatedAt: now,
    write: writeFlagsFor(nextState, loaded.record.write, {
      taskId,
      taskName,
      taskStatus,
      importResult: effectiveImportResult,
    }),
    failure:
      [OperationState.RETRYABLE_FAILED, OperationState.FAILED_CLOSED]
        .includes(nextState)
        ? { code: failureCode || "FAILED_CLOSED", reason: reason || null }
        : loaded.record.failure,
    history: [
      ...loaded.record.history,
      { at: now, from: current, to: nextState, reason: reason || null },
    ],
  };
  await writeAtomic(loaded.receiptPath, updated);
  return { record: updated, receiptPath: loaded.receiptPath };
}
