import { prepareCustomImportForm } from "./navigation.mjs";
import {
  OperationStateError,
  recordImportTaskResult,
  recordTaskBaseline,
  reconcileOperationTask,
} from "./operation-state.mjs";
import { openApp } from "./session.mjs";
import { detectState } from "./states.mjs";
import {
  requireSubmitAuthorization,
  submitPreparedImport,
} from "./submission.mjs";
import {
  probeImportTaskSnapshot,
  readCompletedImportTaskResult,
} from "./task-center.mjs";


const DEFAULT_RECONCILIATION_TIMEOUT_MS = Number(
  process.env.GUANYI_WORKFLOW_RECONCILIATION_TIMEOUT_MS || 45_000,
);


const DEFAULT_DEPENDENCIES = Object.freeze({
  detectState,
  openApp,
  prepareCustomImportForm,
  probeImportTaskSnapshot,
  readCompletedImportTaskResult,
  recordImportTaskResult,
  recordTaskBaseline,
  reconcileOperationTask,
  submitPreparedImport,
});


function dependencies(overrides) {
  return { ...DEFAULT_DEPENDENCIES, ...(overrides || {}) };
}


function requireOperationId(operationId) {
  if (!operationId) {
    throw new OperationStateError(
      "OPERATION_ID_REQUIRED",
      "执行单批导入闭环需要操作 ID。",
    );
  }
}


function publicReconciliationResult(result, operationId) {
  return {
    status: result.status,
    reason: result.reason || null,
    operationId,
    state: result.record.state,
    task: result.task || null,
    candidates: result.candidates || null,
    quarantineAfterMs: result.quarantineAfterMs ?? null,
    elapsedMs: result.elapsedMs ?? null,
    retryAfterMs: result.retryAfterMs ?? null,
  };
}


export async function reconcileImportInCurrentSession(
  page,
  operationId,
  {
    directory,
    timeoutMs = 30_000,
    pollMs = 2_000,
    dependencies: overrides,
  } = {},
) {
  requireOperationId(operationId);
  const service = dependencies(overrides);
  const deadline = Date.now() + timeoutMs;
  let result;
  do {
    const tasks = await service.probeImportTaskSnapshot(page);
    result = await service.reconcileOperationTask(
      operationId,
      tasks,
      directory,
    );
    if (result.status === "result_required") {
      const importResult = await service.readCompletedImportTaskResult(
        page,
        result.task.taskId,
      );
      const recorded = await service.recordImportTaskResult(
        operationId,
        importResult,
        directory,
      );
      return {
        status: recorded.status,
        reason: recorded.reason || null,
        operationId,
        state: recorded.record.state,
        task: {
          taskId: importResult.taskId,
          taskName: importResult.taskName,
          taskStatus: importResult.taskStatus,
        },
        importResult: {
          summary: importResult.summary,
          failures: importResult.failures,
          visibleFailureCount: importResult.visibleFailureCount,
          detailsTruncated: importResult.detailsTruncated,
        },
        safety:
          "Recipient, phone, address, and raw platform-order content are omitted from this result.",
      };
    }
    if (result.status !== "reconciling") {
      return publicReconciliationResult(result, operationId);
    }
    if (Date.now() < deadline) {
      await page.waitForTimeout(Math.min(pollMs, deadline - Date.now()));
    }
  } while (Date.now() < deadline);

  return {
    status: "reconciling",
    reason: result?.reason || "task_not_yet_visible",
    operationId,
    state: result.record.state,
    safety:
      "No unique completed task is visible yet. Do not resubmit; run reconciliation again later.",
  };
}


export async function executeAuthorizedImportInSingleSession(
  page,
  operationId,
  authorization,
  {
    directory,
    taskCenterPage = page,
    reconciliationTimeoutMs = DEFAULT_RECONCILIATION_TIMEOUT_MS,
    pollMs = 2_000,
    onProgress = async () => {},
    dependencies: overrides,
  } = {},
) {
  requireOperationId(operationId);
  requireSubmitAuthorization(authorization);
  const service = dependencies(overrides);

  await service.openApp(page);
  const observed = await service.detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    return observed;
  }
  await onProgress({
    status: "session_ready",
    operationId,
    browserSession: "single_process",
  });

  const form = await service.prepareCustomImportForm(page);
  await onProgress({
    status: "form_prepared",
    operationId,
    orderStatus: form.orderStatus,
    importOptions: form.importOptions,
  });

  const baseline = await service.probeImportTaskSnapshot(taskCenterPage);
  const saved = await service.recordTaskBaseline(
    operationId,
    baseline,
    directory,
  );
  await onProgress({
    status: "task_baseline_captured",
    operationId,
    taskCount: saved.record.taskBaseline.taskIds.length,
    capturedAt: saved.record.taskBaseline.capturedAt,
  });

  const submitted = await service.submitPreparedImport(
    page,
    operationId,
    authorization,
    directory,
  );
  await onProgress(submitted);
  if (
    !["submit_maybe_sent", "file_selection_write_detected"].includes(
      submitted.status,
    )
  ) {
    return submitted;
  }

  return await reconcileImportInCurrentSession(taskCenterPage, operationId, {
    directory,
    timeoutMs: reconciliationTimeoutMs,
    pollMs,
    dependencies: service,
  });
}


export function workflowExitCode(result) {
  if (result?.status === "verified") return 0;
  if (
    ["needs_login", "needs_verification", "reconciling"].includes(
      result?.status || result?.state,
    )
  ) {
    return 2;
  }
  return 3;
}
