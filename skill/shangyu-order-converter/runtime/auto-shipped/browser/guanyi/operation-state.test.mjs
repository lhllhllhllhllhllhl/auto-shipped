import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createPreparedOperation,
  OperationState,
  OperationStateError,
  reconcileUniqueNewTask,
  recordTaskBaseline,
  transitionOperation,
} from "./operation-state.mjs";


function manifest() {
  return {
    status: "manifest_verified",
    manifestPath: "/tmp/preflight.json",
    workbookPath: "/tmp/orders.xlsx",
    artifactFileName: "orders.xlsx",
    artifactSizeBytes: 123,
    artifactSha256: "a".repeat(64),
    profileId: "guanyi_v1",
    orders: 2,
    itemRows: 3,
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


test("prepared operation records hash and blocks skipped write states", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-operation-"));
  try {
    const created = await createPreparedOperation(manifest(), directory);
    assert.equal(created.record.state, OperationState.PREPARED);
    assert.equal(created.record.artifact.sha256, "a".repeat(64));
    assert.equal(created.record.write.requestSent, false);

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
      OperationState.WRITE_REQUEST_SENT,
      { reason: "submit_clicked" },
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
        error instanceof OperationStateError && error.code === "TASK_ID_REQUIRED",
    );
    const observed = await transitionOperation(
      id,
      OperationState.TASK_ID_OBSERVED,
      { reason: "task_center_readback", taskId: "task-123" },
      directory,
    );
    assert.equal(observed.record.write.taskId, "task-123");
    const verified = await transitionOperation(
      id,
      OperationState.POST_WRITE_VERIFIED,
      { reason: "task_success", taskId: "task-123" },
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
