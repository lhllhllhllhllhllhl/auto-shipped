import assert from "node:assert/strict";
import test from "node:test";

import {
  buildVerifiedTaskSnapshot,
  diffVerifiedTaskSnapshot,
  TaskReconciliationError,
} from "./task-reconciliation.mjs";


function result(ids, evidence = "task_record_ids_verified") {
  return {
    taskIdStatus: evidence,
    tasks: ids.map((taskId) => ({
      taskId,
      taskIdEvidence: "dom_and_record_model",
      taskName: "自定义订单导入",
      time: "2026-09-28 20:00:00",
      status: "执行完成",
    })),
  };
}


test("finds exactly one task id added after a verified baseline", () => {
  const baseline = buildVerifiedTaskSnapshot(
    result(["20", "19"]),
    "2026-09-28T12:00:00.000Z",
  );
  const diff = diffVerifiedTaskSnapshot(
    baseline,
    result(["21", "20", "19"]),
  );
  assert.equal(diff.status, "unique_new_task");
  assert.equal(diff.task.taskId, "21");
});


test("fails closed when multiple new tasks appear", () => {
  const baseline = buildVerifiedTaskSnapshot(result(["20"]));
  const diff = diffVerifiedTaskSnapshot(
    baseline,
    result(["22", "21", "20"]),
  );
  assert.equal(diff.status, "ambiguous_new_tasks");
  assert.equal(diff.newTasks.length, 2);
});


test("rejects task ids without record-model verification", () => {
  assert.throws(
    () => buildVerifiedTaskSnapshot(result(["20"], "task_record_ids_observed")),
    (error) =>
      error instanceof TaskReconciliationError &&
      error.code === "TASK_IDS_NOT_VERIFIED",
  );
});
