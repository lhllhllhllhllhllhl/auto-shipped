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
  WRITE_REQUEST_SENT: "write_request_sent",
  TASK_ID_OBSERVED: "task_id_observed",
  POST_WRITE_VERIFIED: "post_write_verified",
  FAILED_CLOSED: "failed_closed",
});


const TRANSITIONS = Object.freeze({
  [OperationState.PREPARED]: new Set([
    OperationState.FILE_SELECTION_STARTED,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.FILE_SELECTION_STARTED]: new Set([
    OperationState.WRITE_REQUEST_MAYBE_SENT,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.WRITE_REQUEST_MAYBE_SENT]: new Set([
    OperationState.WRITE_REQUEST_SENT,
    OperationState.TASK_ID_OBSERVED,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.WRITE_REQUEST_SENT]: new Set([
    OperationState.TASK_ID_OBSERVED,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.TASK_ID_OBSERVED]: new Set([
    OperationState.POST_WRITE_VERIFIED,
    OperationState.FAILED_CLOSED,
  ]),
  [OperationState.POST_WRITE_VERIFIED]: new Set(),
  [OperationState.FAILED_CLOSED]: new Set(),
});


export class OperationStateError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "OperationStateError";
    this.code = code;
  }
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


async function findActiveArtifactOperation(artifactSha256, directory) {
  let entries = [];
  try {
    entries = await fs.readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    try {
      const record = JSON.parse(
        await fs.readFile(path.join(directory, entry.name), "utf8"),
      );
      if (
        record?.schemaVersion === "guanyi-operation/1.0" &&
        record?.artifact?.sha256 === artifactSha256 &&
        record?.state !== OperationState.FAILED_CLOSED
      ) {
        return record;
      }
    } catch {
      // Ignore unrelated or incomplete files; their validity is checked when loaded directly.
    }
  }
  return null;
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
  const existing = await findActiveArtifactOperation(
    verifiedManifest.artifactSha256,
    directory,
  );
  if (existing) {
    throw new OperationStateError(
      "ARTIFACT_OPERATION_ALREADY_EXISTS",
      `相同Excel已经存在管易操作记录 ${existing.operationId}，请继续原操作，不要重复准备或上传。`,
    );
  }
  const operationId = randomUUID();
  const now = new Date().toISOString();
  const record = {
    schemaVersion: "guanyi-operation/1.0",
    operationId,
    platform: "guanyi",
    operation: "custom_order_import",
    state: OperationState.PREPARED,
    createdAt: now,
    updatedAt: now,
    manifest: {
      path: verifiedManifest.manifestPath,
      profileId: verifiedManifest.profileId,
    },
    artifact: {
      path: verifiedManifest.workbookPath,
      fileName: verifiedManifest.artifactFileName,
      sizeBytes: verifiedManifest.artifactSizeBytes,
      sha256: verifiedManifest.artifactSha256,
    },
    counts: {
      orders: verifiedManifest.orders,
      itemRows: verifiedManifest.itemRows,
    },
    write: {
      fileSelectionStarted: false,
      requestMaybeSent: false,
      requestSent: false,
      taskId: null,
      postWriteVerified: false,
    },
    taskBaseline: null,
    observations: [],
    history: [
      {
        at: now,
        from: null,
        to: OperationState.PREPARED,
        reason: "manifest_verified",
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
  return await transitionOperation(
    operationId,
    OperationState.TASK_ID_OBSERVED,
    {
      reason: "unique_new_task_from_verified_baseline",
      taskId: diff.task.taskId,
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
      state === OperationState.TASK_ID_OBSERVED ||
      state === OperationState.POST_WRITE_VERIFIED,
    requestSent:
      previous.requestSent ||
      state === OperationState.WRITE_REQUEST_SENT ||
      state === OperationState.TASK_ID_OBSERVED ||
      state === OperationState.POST_WRITE_VERIFIED,
    taskId: patch.taskId ?? previous.taskId,
    postWriteVerified:
      previous.postWriteVerified ||
      state === OperationState.POST_WRITE_VERIFIED,
  };
}


export async function transitionOperation(
  operationId,
  nextState,
  { reason, taskId = null, failureCode = null } = {},
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
  if (
    [OperationState.TASK_ID_OBSERVED, OperationState.POST_WRITE_VERIFIED]
      .includes(nextState) &&
    !(taskId || loaded.record.write.taskId)
  ) {
    throw new OperationStateError(
      "TASK_ID_REQUIRED",
      "进入任务回读状态前必须获得真实任务 ID。",
    );
  }
  const now = new Date().toISOString();
  const updated = {
    ...loaded.record,
    state: nextState,
    updatedAt: now,
    write: writeFlagsFor(nextState, loaded.record.write, { taskId }),
    failure:
      nextState === OperationState.FAILED_CLOSED
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
