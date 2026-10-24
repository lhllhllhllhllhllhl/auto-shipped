import assert from "node:assert/strict";
import test from "node:test";

import {
  executeAuthorizedImportInSingleSession,
  reconcileImportInCurrentSession,
  workflowExitCode,
} from "./workflow.mjs";


function fakePage() {
  return {
    waits: [],
    async waitForTimeout(milliseconds) {
      this.waits.push(milliseconds);
    },
  };
}


test("runs one authorized batch through one browser page", async () => {
  const page = fakePage();
  const calls = [];
  const progress = [];
  let taskProbeCount = 0;
  const importResult = {
    taskId: "705700001",
    taskName: "自定义订单导入",
    taskStatus: "执行完成",
    summary: {
      total: 2,
      successful: 2,
      formatErrors: 0,
      illegalData: 0,
      failed: 0,
    },
    failures: [],
    visibleFailureCount: 0,
    detailsTruncated: false,
  };
  const dependencies = {
    async openApp(candidate) {
      assert.equal(candidate, page);
      calls.push("open_app");
    },
    async detectState(candidate) {
      assert.equal(candidate, page);
      calls.push("detect_state");
      return { state: "dashboard", url: "https://www.guanyierp.com/index" };
    },
    async prepareCustomImportForm(candidate) {
      assert.equal(candidate, page);
      calls.push("prepare_form");
      return { orderStatus: "待审核", importOptions: { detailMode: "单据" } };
    },
    async probeImportTaskSnapshot(candidate) {
      assert.equal(candidate, page);
      taskProbeCount += 1;
      calls.push(`probe_tasks_${taskProbeCount}`);
      return taskProbeCount === 1
        ? { evidence: "verified_task_ids", taskIds: ["old-task"] }
        : { evidence: "verified_task_ids", taskIds: ["old-task", "705700001"] };
    },
    async recordTaskBaseline(operationId) {
      calls.push("record_baseline");
      assert.equal(operationId, "operation-1");
      return {
        record: {
          taskBaseline: {
            taskIds: ["old-task"],
            capturedAt: "2026-10-15T10:00:00.000Z",
          },
        },
      };
    },
    async submitPreparedImport(candidate, operationId, authorization) {
      assert.equal(candidate, page);
      assert.equal(operationId, "operation-1");
      assert.equal(authorization, "CONFIRM_GUANYI_IMPORT_SUBMISSION");
      calls.push("submit");
      return { status: "submit_maybe_sent", operationId };
    },
    async reconcileOperationTask(operationId) {
      assert.equal(operationId, "operation-1");
      calls.push("reconcile");
      return {
        status: "result_required",
        task: { taskId: "705700001" },
        record: { state: "task_id_observed" },
      };
    },
    async readCompletedImportTaskResult(candidate, taskId) {
      assert.equal(candidate, page);
      assert.equal(taskId, "705700001");
      calls.push("read_result");
      return importResult;
    },
    async recordImportTaskResult(operationId, result) {
      assert.equal(operationId, "operation-1");
      assert.equal(result, importResult);
      calls.push("record_result");
      return { status: "verified", record: { state: "post_write_verified" } };
    },
  };

  const result = await executeAuthorizedImportInSingleSession(
    page,
    "operation-1",
    "CONFIRM_GUANYI_IMPORT_SUBMISSION",
    {
      dependencies,
      onProgress: async (event) => progress.push(event.status),
    },
  );

  assert.deepEqual(calls, [
    "open_app",
    "detect_state",
    "prepare_form",
    "probe_tasks_1",
    "record_baseline",
    "submit",
    "probe_tasks_2",
    "reconcile",
    "read_result",
    "record_result",
  ]);
  assert.deepEqual(progress, [
    "session_ready",
    "form_prepared",
    "task_baseline_captured",
    "submit_maybe_sent",
  ]);
  assert.equal(result.status, "verified");
  assert.equal(result.importResult.summary.successful, 2);
  assert.equal(workflowExitCode(result), 0);
});


test("keeps upload and task-center work on separate pages when an observer is supplied", async () => {
  const uploadPage = fakePage();
  const taskCenterPage = fakePage();
  const observedPages = [];
  const dependencies = {
    async openApp(candidate) {
      assert.equal(candidate, uploadPage);
    },
    async detectState() {
      return { state: "dashboard" };
    },
    async prepareCustomImportForm(candidate) {
      assert.equal(candidate, uploadPage);
      return { orderStatus: "待审核", importOptions: {} };
    },
    async probeImportTaskSnapshot(candidate) {
      observedPages.push(candidate);
      return { taskIdStatus: "verified_empty_list", tasks: [] };
    },
    async recordTaskBaseline() {
      return { record: { taskBaseline: { taskIds: [], capturedAt: "now" } } };
    },
    async submitPreparedImport(candidate) {
      assert.equal(candidate, uploadPage);
      return { status: "submit_maybe_sent" };
    },
    async reconcileOperationTask() {
      return {
        status: "reconciling",
        reason: "no_new_task",
        record: { state: "reconciling" },
      };
    },
  };

  const result = await executeAuthorizedImportInSingleSession(
    uploadPage,
    "operation-observer",
    "CONFIRM_GUANYI_IMPORT_SUBMISSION",
    {
      taskCenterPage,
      reconciliationTimeoutMs: 0,
      dependencies,
    },
  );

  assert.equal(result.status, "reconciling");
  assert.deepEqual(observedPages, [taskCenterPage, taskCenterPage]);
});


test("stops the single-session workflow before the form when login is required", async () => {
  const page = fakePage();
  let formPrepared = false;
  const result = await executeAuthorizedImportInSingleSession(
    page,
    "operation-2",
    "CONFIRM_GUANYI_IMPORT_SUBMISSION",
    {
      dependencies: {
        async openApp() {},
        async detectState() {
          return { state: "needs_login", url: "https://login.guanyierp.com/login" };
        },
        async prepareCustomImportForm() {
          formPrepared = true;
        },
      },
    },
  );

  assert.equal(result.state, "needs_login");
  assert.equal(formPrepared, false);
  assert.equal(workflowExitCode(result), 2);
});


test("rejects missing submission authorization before opening the application", async () => {
  const page = fakePage();
  let appOpened = false;

  await assert.rejects(
    executeAuthorizedImportInSingleSession(
      page,
      "operation-no-authorization",
      "",
      {
        dependencies: {
          async openApp() {
            appOpened = true;
          },
        },
      },
    ),
    (error) => error?.code === "SUBMIT_AUTHORIZATION_REQUIRED",
  );
  assert.equal(appOpened, false);
});


test("keeps an uncertain submitted batch in reconciliation without resubmitting", async () => {
  const page = fakePage();
  let probes = 0;
  const result = await reconcileImportInCurrentSession(
    page,
    "operation-3",
    {
      timeoutMs: 0,
      dependencies: {
        async probeImportTaskSnapshot() {
          probes += 1;
          return { evidence: "verified_empty_list", taskIds: [] };
        },
        async reconcileOperationTask() {
          return {
            status: "reconciling",
            reason: "no_new_task",
            record: { state: "reconciling" },
          };
        },
      },
    },
  );

  assert.equal(probes, 1);
  assert.equal(result.status, "reconciling");
  assert.equal(result.state, "reconciling");
  assert.equal(workflowExitCode(result), 2);
});
