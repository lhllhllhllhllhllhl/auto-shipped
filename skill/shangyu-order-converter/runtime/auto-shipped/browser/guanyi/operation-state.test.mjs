import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createPreparedOperation,
  NO_TASK_RESOLUTION_AUTHORIZATION,
  OperationState,
  OperationStateError,
  recordImportTaskResult,
  reconcileOperationTask,
  reconcileUniqueNewTask,
  recordTaskBaseline,
  resolveNoTaskManualReview,
  transitionOperation,
} from "./operation-state.mjs";


function manifest() {
  return {
    status: "manifest_verified",
    manifestPath: "/tmp/preflight.json",
    manifestArtifactPath: "/original-machine/exports/orders.xlsx",
    workbookPath: "/tmp/orders.xlsx",
    artifactPathBinding: "explicit_current_path",
    artifactFileName: "orders.xlsx",
    artifactSizeBytes: 123,
    artifactSha256: "a".repeat(64),
    profileId: "guanyi_v1",
    orders: 2,
    itemRows: 3,
    platformOrderNumbers: ["TEST-001", "TEST-002"],
  };
}


function taskResult(ids) {
  return {
    taskIdStatus: "task_record_ids_verified",
    tasks: ids.map((taskId, index) => ({
      taskId,
      taskIdEvidence: "dom_and_record_model",
      taskName: "自定义订单导入",
      time: `2026-09-28 20:00:0${index}`,
      status: "执行完成",
    })),
  };
}


function importResult(
  taskId,
  { total = 2, successful = 2, formatErrors = 0, illegalData = 0 } = {},
) {
  return {
    status: "import_task_result_ready",
    taskId,
    taskName: "自定义订单导入",
    taskStatus: "执行完成",
    summary: {
      total,
      successful,
      formatErrors,
      illegalData,
      failed: formatErrors + illegalData,
    },
    failures: [],
    visibleFailureCount: 0,
    detailsTruncated: false,
  };
}


test("prepared operation records hash and blocks skipped write states", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    assert.equal(created.record.state, OperationState.PREPARED);
    assert.equal(created.record.retryOf, null);
    assert.equal(created.record.attemptNumber, 1);
    assert.equal(
      created.record.manifest.artifactPath,
      "/original-machine/exports/orders.xlsx",
    );
    assert.equal(created.record.artifact.path, "/tmp/orders.xlsx");
    assert.equal(
      created.record.artifact.pathBinding,
      "explicit_current_path",
    );
    assert.equal(created.record.artifact.sha256, "a".repeat(64));
    assert.equal(created.record.write.requestSent, false);
    assert.deepEqual(created.record.auditScope.platformOrderNumbers, [
      "TEST-001",
      "TEST-002",
    ]);

    await assert.rejects(
      transitionOperation(
        created.record.operationId,
        OperationState.WRITE_REQUEST_SENT,
        { reason: "invalid_skip" },
        directory,
      ),
      (error) =>
        error instanceof OperationStateError &&
        error.code === "OPERATION_TRANSITION_INVALID",
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("rejects a second active operation for the same artifact hash", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    await assert.rejects(
      createPreparedOperation(manifest(), directory),
      (error) =>
        error instanceof OperationStateError &&
        error.code === "ARTIFACT_OPERATION_ALREADY_EXISTS" &&
        error.message.includes(created.record.operationId),
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("closes manual review only when the verified task snapshot has no additions", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.NEEDS_MANUAL_REVIEW,
      { reason: "selection_result_uncertain" },
      directory,
    );
    const closed = await resolveNoTaskManualReview(
      id,
      taskResult(["100"]),
      NO_TASK_RESOLUTION_AUTHORIZATION,
      directory,
    );
    assert.equal(closed.record.state, OperationState.RETRYABLE_FAILED);
    assert.equal(closed.record.failure.code, "NO_TASK_CREATED");
    const retried = await createPreparedOperation(manifest(), directory);
    assert.notEqual(retried.record.operationId, id);
    assert.equal(retried.record.retryOf, id);
    assert.equal(retried.record.attemptNumber, 2);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("does not close manual review when a verified new task exists", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.NEEDS_MANUAL_REVIEW,
      { reason: "selection_result_uncertain" },
      directory,
    );
    await assert.rejects(
      resolveNoTaskManualReview(
        id,
        taskResult(["101", "100"]),
        NO_TASK_RESOLUTION_AUTHORIZATION,
        directory,
      ),
      (error) =>
        error instanceof OperationStateError &&
        error.code === "NO_TASK_RESOLUTION_EVIDENCE_CONFLICT",
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("does not close an attempt after the final import request may have been sent", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    for (const [state, reason] of [
      [OperationState.FILE_SELECTION_STARTED, "authorized_file_selection"],
      [OperationState.WRITE_REQUEST_MAYBE_SENT, "file_input_changed"],
      [OperationState.SUBMIT_AUTHORIZED, "first_confirm_allowed"],
      [OperationState.WRITE_REQUEST_SENT, "final_confirm_clicked"],
      [OperationState.NEEDS_MANUAL_REVIEW, "final_result_uncertain"],
    ]) {
      await transitionOperation(id, state, { reason }, directory);
    }
    await assert.rejects(
      resolveNoTaskManualReview(
        id,
        taskResult(["100"]),
        NO_TASK_RESOLUTION_AUTHORIZATION,
        directory,
      ),
      (error) =>
        error instanceof OperationStateError &&
        error.code === "NO_TASK_RESOLUTION_FINAL_SUBMIT_UNCERTAIN",
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("closes a reconciling operation after a verified no-task result", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_staging_observed" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.RECONCILING,
      { reason: "task_center_reconciliation_started" },
      directory,
    );
    const closed = await resolveNoTaskManualReview(
      id,
      taskResult(["100"]),
      NO_TASK_RESOLUTION_AUTHORIZATION,
      directory,
    );
    assert.equal(closed.record.state, OperationState.RETRYABLE_FAILED);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("allows separate operation ids for distinct converted batches", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const first = await createPreparedOperation(manifest(), directory);
    const secondManifest = {
      ...manifest(),
      manifestPath: "/tmp/preflight-2.json",
      workbookPath: "/tmp/orders-2.xlsx",
      artifactFileName: "orders-2.xlsx",
      artifactSha256: "b".repeat(64),
    };
    const second = await createPreparedOperation(secondManifest, directory);
    assert.notEqual(first.record.operationId, second.record.operationId);
    assert.equal(first.record.artifact.sha256, "a".repeat(64));
    assert.equal(second.record.artifact.sha256, "b".repeat(64));
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("post-write verification requires a real task id", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.SUBMIT_AUTHORIZED,
      { reason: "current_batch_authorized" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_SENT,
      { reason: "submit_clicked_once" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.RECONCILING,
      { reason: "task_center_reconciliation_started" },
      directory,
    );
    await assert.rejects(
      transitionOperation(
        id,
        OperationState.TASK_ID_OBSERVED,
        { reason: "missing_task_id" },
        directory,
      ),
      (error) =>
        error instanceof OperationStateError &&
        error.code === "TASK_EVIDENCE_REQUIRED",
    );
    const observed = await transitionOperation(
      id,
      OperationState.TASK_ID_OBSERVED,
      {
        reason: "task_center_readback",
        taskId: "123",
        taskName: "自定义订单导入",
        taskStatus: "执行中",
      },
      directory,
    );
    assert.equal(observed.record.write.taskId, "123");
    await assert.rejects(
      transitionOperation(
        id,
        OperationState.POST_WRITE_VERIFIED,
        {
          reason: "task_status_without_result",
          taskId: "123",
          taskName: "自定义订单导入",
          taskStatus: "执行完成",
        },
        directory,
      ),
      (error) =>
        error instanceof OperationStateError &&
        error.code === "IMPORT_RESULT_SUCCESS_REQUIRED",
    );
    const verified = await transitionOperation(
      id,
      OperationState.POST_WRITE_VERIFIED,
      {
        reason: "task_success",
        taskId: "123",
        taskName: "自定义订单导入",
        taskStatus: "执行完成",
        importResult: importResult("123"),
      },
      directory,
    );
    assert.equal(verified.record.write.postWriteVerified, true);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("captures a verified task baseline only before file selection", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const captured = await recordTaskBaseline(
      created.record.operationId,
      taskResult(["100", "99"]),
      directory,
    );
    assert.deepEqual(captured.record.taskBaseline.taskIds, ["100", "99"]);
    await transitionOperation(
      created.record.operationId,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await assert.rejects(
      recordTaskBaseline(
        created.record.operationId,
        taskResult(["101", "100"]),
        directory,
      ),
      (error) =>
        error instanceof OperationStateError &&
        error.code === "TASK_BASELINE_TOO_LATE",
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("associates only one verified task added after the baseline", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100", "99"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    const matched = await reconcileUniqueNewTask(
      id,
      taskResult(["101", "100", "99"]),
      directory,
    );
    assert.equal(matched.record.state, OperationState.TASK_ID_OBSERVED);
    assert.equal(matched.record.write.taskId, "101");
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("marks an operation verified only for one successful verified task", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    const resultRequired = await reconcileOperationTask(
      id,
      taskResult(["101", "100"]),
      directory,
    );
    assert.equal(resultRequired.status, "result_required");
    const result = await recordImportTaskResult(
      id,
      importResult("101"),
      directory,
    );
    assert.equal(result.status, "verified");
    assert.equal(result.record.state, OperationState.POST_WRITE_VERIFIED);
    assert.equal(result.record.write.taskId, "101");
    assert.equal(result.record.write.taskStatus, "执行完成");
    await assert.rejects(
      createPreparedOperation(manifest(), directory),
      (error) =>
        error instanceof OperationStateError &&
        error.code === "ARTIFACT_ALREADY_VERIFIED",
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("keeps a partially successful import in manual review with redacted result counts", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    const required = await reconcileOperationTask(
      id,
      taskResult(["101", "100"]),
      directory,
    );
    assert.equal(required.status, "result_required");
    const partial = importResult("101", {
      total: 2,
      successful: 1,
      formatErrors: 1,
      illegalData: 0,
    });
    partial.failures = [
      {
        sourceRow: 1,
        code: "STORE_NOT_FOUND",
        message: "张三 13800000000 上海市某路：无法匹配到系统中店铺信息",
      },
    ];
    partial.visibleFailureCount = 1;
    const result = await recordImportTaskResult(id, partial, directory);
    assert.equal(result.status, "needs_manual_review");
    assert.equal(result.reason, "partial_success");
    assert.equal(result.record.state, OperationState.NEEDS_MANUAL_REVIEW);
    assert.equal(result.record.write.postWriteVerified, false);
    assert.deepEqual(result.record.write.importResult.summary, partial.summary);
    assert.deepEqual(result.record.write.importResult.failures, [
      {
        sourceRow: 1,
        code: "STORE_NOT_FOUND",
        message: "无法匹配到管易店铺信息",
      },
    ]);
    assert.equal(
      JSON.stringify(result.record).includes("13800000000"),
      false,
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("allows a linked retry after the task center reports failure", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    const failedTask = taskResult(["101", "100"]);
    failedTask.tasks[0].status = "执行失败";
    const failed = await reconcileOperationTask(id, failedTask, directory);
    assert.equal(failed.status, "retryable_failed");
    assert.equal(failed.record.state, OperationState.RETRYABLE_FAILED);
    const retried = await createPreparedOperation(manifest(), directory);
    assert.equal(retried.record.retryOf, id);
    assert.equal(retried.record.attemptNumber, 2);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("routes ambiguous new tasks to manual review", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    const result = await reconcileOperationTask(
      id,
      taskResult(["102", "101", "100"]),
      directory,
    );
    assert.equal(result.status, "needs_manual_review");
    assert.equal(result.record.state, OperationState.NEEDS_MANUAL_REVIEW);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("marks file-staging-only attempt retryable when no task was created", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    const result = await reconcileOperationTask(
      id,
      taskResult(["100"]),
      directory,
    );
    assert.equal(result.status, "retryable_failed");
    assert.equal(result.record.state, OperationState.RETRYABLE_FAILED);
    assert.equal(result.record.write.requestMaybeSent, true);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("keeps a submitted operation reconciling when no new task is visible yet", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.SUBMIT_AUTHORIZED,
      { reason: "current_batch_authorized" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_SENT,
      { reason: "submit_clicked_once" },
      directory,
    );
    const result = await reconcileOperationTask(
      id,
      taskResult(["100"]),
      directory,
      { nowMs: Date.now() + 4 * 60 * 1_000 },
    );
    assert.equal(result.status, "reconciling");
    assert.equal(result.record.state, OperationState.RECONCILING);
    assert.equal(result.record.write.requestSent, true);
    assert.ok(result.retryAfterMs > 0);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("quarantines a submitted operation after five minutes without a task", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    for (const [state, reason] of [
      [OperationState.FILE_SELECTION_STARTED, "authorized_file_selection"],
      [OperationState.WRITE_REQUEST_MAYBE_SENT, "file_input_changed"],
      [OperationState.SUBMIT_AUTHORIZED, "current_batch_authorized"],
      [OperationState.WRITE_REQUEST_SENT, "submit_clicked_once"],
    ]) {
      await transitionOperation(id, state, { reason }, directory);
    }
    const result = await reconcileOperationTask(
      id,
      taskResult(["100"]),
      directory,
      { nowMs: Date.now() + 5 * 60 * 1_000 },
    );
    assert.equal(result.status, "quarantined");
    assert.equal(result.record.state, OperationState.QUARANTINED);
    assert.equal(result.record.write.requestSent, true);
    assert.equal(result.quarantineAfterMs, 5 * 60 * 1_000);
    assert.equal(
      result.record.history.at(-1).reason,
      "submitted_task_not_visible_after_quarantine_window",
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("a quarantined operation ignores a task already claimed by a later batch", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const first = await createPreparedOperation(manifest(), directory);
    await recordTaskBaseline(first.record.operationId, taskResult(["100"]), directory);
    for (const [state, reason] of [
      [OperationState.FILE_SELECTION_STARTED, "authorized_file_selection"],
      [OperationState.WRITE_REQUEST_MAYBE_SENT, "file_input_changed"],
      [OperationState.SUBMIT_AUTHORIZED, "current_batch_authorized"],
      [OperationState.WRITE_REQUEST_SENT, "submit_clicked_once"],
    ]) {
      await transitionOperation(first.record.operationId, state, { reason }, directory);
    }
    await reconcileOperationTask(
      first.record.operationId,
      taskResult(["100"]),
      directory,
      { nowMs: Date.now() + 5 * 60 * 1_000 },
    );

    const secondManifest = {
      ...manifest(),
      manifestPath: "/tmp/preflight-2.json",
      workbookPath: "/tmp/orders-2.xlsx",
      artifactFileName: "orders-2.xlsx",
      artifactSha256: "b".repeat(64),
    };
    const second = await createPreparedOperation(secondManifest, directory);
    await recordTaskBaseline(second.record.operationId, taskResult(["100"]), directory);
    for (const [state, reason] of [
      [OperationState.FILE_SELECTION_STARTED, "authorized_file_selection"],
      [OperationState.WRITE_REQUEST_MAYBE_SENT, "file_input_changed"],
      [OperationState.RECONCILING, "task_center_reconciliation_started"],
      [OperationState.TASK_ID_OBSERVED, "unique_new_task_from_verified_baseline"],
    ]) {
      await transitionOperation(
        second.record.operationId,
        state,
        {
          reason,
          ...(state === OperationState.TASK_ID_OBSERVED
            ? {
                taskId: "101",
                taskName: "自定义订单导入",
                taskStatus: "执行完成",
              }
            : {}),
        },
        directory,
      );
    }

    const result = await reconcileOperationTask(
      first.record.operationId,
      taskResult(["101", "100"]),
      directory,
      { nowMs: Date.now() + 6 * 60 * 1_000 },
    );
    assert.equal(result.status, "quarantined");
    assert.equal(result.record.write.taskId, null);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("a quarantined operation can still claim its later unique task", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    for (const [state, reason] of [
      [OperationState.FILE_SELECTION_STARTED, "authorized_file_selection"],
      [OperationState.WRITE_REQUEST_MAYBE_SENT, "file_input_changed"],
      [OperationState.SUBMIT_AUTHORIZED, "current_batch_authorized"],
      [OperationState.WRITE_REQUEST_SENT, "submit_clicked_once"],
    ]) {
      await transitionOperation(id, state, { reason }, directory);
    }
    await reconcileOperationTask(
      id,
      taskResult(["100"]),
      directory,
      { nowMs: Date.now() + 5 * 60 * 1_000 },
    );
    const result = await reconcileOperationTask(
      id,
      taskResult(["101", "100"]),
      directory,
      { nowMs: Date.now() + 6 * 60 * 1_000 },
    );
    assert.equal(result.status, "result_required");
    assert.equal(result.record.state, OperationState.TASK_ID_OBSERVED);
    assert.equal(result.record.write.taskId, "101");
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("requires task result order count to match the manifest batch", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    for (const [state, reason] of [
      [OperationState.FILE_SELECTION_STARTED, "authorized_file_selection"],
      [OperationState.WRITE_REQUEST_MAYBE_SENT, "file_input_changed"],
      [OperationState.RECONCILING, "task_center_reconciliation_started"],
    ]) {
      await transitionOperation(id, state, { reason }, directory);
    }
    await transitionOperation(
      id,
      OperationState.TASK_ID_OBSERVED,
      {
        reason: "unique_new_task_from_verified_baseline",
        taskId: "101",
        taskName: "自定义订单导入",
        taskStatus: "执行完成",
      },
      directory,
    );
    const result = await recordImportTaskResult(
      id,
      importResult("101", { total: 1, successful: 1 }),
      directory,
    );
    assert.equal(result.status, "needs_manual_review");
    assert.equal(result.reason, "task_result_order_count_mismatch");
    assert.equal(result.record.state, OperationState.NEEDS_MANUAL_REVIEW);
    assert.equal(result.expectedOrders, 2);
    assert.equal(result.observedTotal, 1);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("routes insufficient task-id evidence to manual review", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    const id = created.record.operationId;
    await recordTaskBaseline(id, taskResult(["100"]), directory);
    await transitionOperation(
      id,
      OperationState.FILE_SELECTION_STARTED,
      { reason: "authorized_file_selection" },
      directory,
    );
    await transitionOperation(
      id,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_changed" },
      directory,
    );
    const result = await reconcileOperationTask(
      id,
      {
        taskIdStatus: "task_record_ids_observed",
        tasks: [
          {
            taskId: "101",
            taskIdEvidence: "dom_consistent",
            taskName: "自定义订单导入",
            status: "执行完成",
          },
        ],
      },
      directory,
    );
    assert.equal(result.status, "needs_manual_review");
    assert.equal(result.reason, "task_evidence_insufficient");
    assert.equal(result.record.state, OperationState.NEEDS_MANUAL_REVIEW);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});
