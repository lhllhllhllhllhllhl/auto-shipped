const IMPORT_TASK_NAME = "自定义订单导入";


export class TaskReconciliationError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "TaskReconciliationError";
    this.code = code;
    this.details = details;
  }
}


function safeVerifiedTasks(result) {
  const tasks = Array.isArray(result?.tasks) ? result.tasks : [];
  if (tasks.length === 0) {
    if (
      result?.taskIdStatus &&
      !["verified_empty_list", "task_id_unavailable_in_list"].includes(
        result.taskIdStatus,
      )
    ) {
      throw new TaskReconciliationError(
        "TASK_SNAPSHOT_INVALID",
        "空任务快照的验证状态不正确。",
      );
    }
    return [];
  }
  if (result?.taskIdStatus !== "task_record_ids_verified") {
    throw new TaskReconciliationError(
      "TASK_IDS_NOT_VERIFIED",
      "任务中心记录 ID 未通过双重校验，不能建立上传基线。",
    );
  }
  const seen = new Set();
  return tasks.map((task) => {
    if (
      !/^\d+$/.test(task?.taskId || "") ||
      task?.taskIdEvidence !== "dom_and_record_model" ||
      task?.taskName !== IMPORT_TASK_NAME ||
      seen.has(task.taskId)
    ) {
      throw new TaskReconciliationError(
        "TASK_SNAPSHOT_INVALID",
        "任务中心快照包含无效、重复或未核验的任务记录。",
      );
    }
    seen.add(task.taskId);
    return {
      taskId: task.taskId,
      taskName: task.taskName,
      time: String(task.time || ""),
      status: String(task.status || ""),
    };
  });
}


export function buildVerifiedTaskSnapshot(result, capturedAt = new Date().toISOString()) {
  const tasks = safeVerifiedTasks(result);
  return {
    schemaVersion: "guanyi-task-baseline/1.0",
    capturedAt,
    taskName: IMPORT_TASK_NAME,
    evidence: tasks.length ? "verified_task_ids" : "verified_empty_list",
    taskIds: tasks.map((task) => task.taskId),
    tasks,
  };
}


export function diffVerifiedTaskSnapshot(baseline, currentResult) {
  if (
    baseline?.schemaVersion !== "guanyi-task-baseline/1.0" ||
    !Array.isArray(baseline.taskIds)
  ) {
    throw new TaskReconciliationError(
      "TASK_BASELINE_MISSING",
      "操作记录缺少有效的上传前任务基线。",
    );
  }
  const current = safeVerifiedTasks(currentResult);
  const previousIds = new Set(baseline.taskIds);
  const newTasks = current.filter((task) => !previousIds.has(task.taskId));
  if (newTasks.length === 0) {
    return { status: "no_new_task", newTasks: [] };
  }
  if (newTasks.length > 1) {
    return {
      status: "ambiguous_new_tasks",
      newTasks,
      safety: "Multiple new tasks were observed; no task was auto-associated.",
    };
  }
  return {
    status: "unique_new_task",
    task: newTasks[0],
    newTasks,
  };
}
