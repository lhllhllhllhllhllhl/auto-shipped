import { ensurePageGeometry } from "./geometry.mjs";
import { dismissKnownPopups } from "./popup.mjs";
import { firstVisible, firstVisibleInFrames } from "./states.mjs";


const TASK_CENTER_HEADERS = [
  "任务时间",
  "任务名称",
  "任务类型",
  "任务状态",
  "执行状态",
  "创建时间",
  "完成时间",
  "时间",
  "状态",
  "当前进度",
  "操作人",
  "操作",
];

const COMPLETED_IMPORT_TASK_NAME = "自定义订单导入";
const MAX_COMPLETED_IMPORT_SUMMARIES = 20;


export class TaskCenterError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "TaskCenterError";
    this.code = code;
    this.details = details;
  }
}


async function visibleExact(scope, text) {
  return await firstVisible(scope.getByText(text, { exact: true }));
}


export async function findTaskCenterFrame(page) {
  for (const frame of page.frames()) {
    let anchorCount = 0;
    for (const header of TASK_CENTER_HEADERS) {
      if (await visibleExact(frame, header).catch(() => null)) {
        anchorCount += 1;
      }
    }
    if (
      anchorCount >= 2 &&
      (/\/task\/task_center/i.test(frame.url()) || anchorCount >= 3)
    ) {
      return frame;
    }
  }
  return null;
}


async function waitForTaskCenter(page, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const frame = await findTaskCenterFrame(page);
    if (frame) {
      return frame;
    }
    await page.waitForTimeout(250);
  }
  return null;
}


async function waitForExactEntry(page, text, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const entry = await firstVisibleInFrames(
      page,
      (frame) => frame.getByText(text, { exact: true }),
    );
    if (entry) {
      return entry;
    }
    await page.waitForTimeout(200);
  }
  return null;
}


async function clickTaskCenterEntry(page) {
  const existingTab = await firstVisibleInFrames(
    page,
    (frame) => frame.getByRole("tab", { name: /任务中心/ }),
  );
  if (existingTab) {
    await existingTab.click();
    return "existing_task_center_tab";
  }

  const commonPagesIcon = await firstVisibleInFrames(
    page,
    (frame) => frame.locator('[aria-label="read"]:visible'),
  );
  if (commonPagesIcon) {
    await commonPagesIcon.click();
    const taskCenter = await waitForExactEntry(page, "任务中心");
    if (taskCenter) {
      await taskCenter.click();
      return "common_pages_icon_task_center";
    }
  }

  const commonPages = await firstVisibleInFrames(
    page,
    (frame) => frame.getByText("常用页面", { exact: true }),
  );
  if (commonPages) {
    await commonPages.click();
    const taskCenter = await waitForExactEntry(page, "任务中心");
    if (taskCenter) {
      await taskCenter.click();
      return "common_pages_task_center";
    }
  }

  const menu = await firstVisibleInFrames(
    page,
    (frame) => frame.getByText("菜单", { exact: true }),
  );
  if (menu) {
    await menu.click();
    const taskCenter = await waitForExactEntry(page, "任务中心");
    if (taskCenter) {
      await taskCenter.click();
      return "main_menu_task_center";
    }
  }
  return null;
}


export async function navigateToTaskCenter(page) {
  await page.waitForFunction(
    () => document.body?.innerText?.trim().length > 0,
    null,
    { timeout: 30_000 },
  );
  await page
    .getByText("店铺授权到期提醒", { exact: false })
    .first()
    .waitFor({ state: "visible", timeout: 4_000 })
    .catch(() => null);
  const initialPopups = await dismissKnownPopups(page);
  if (initialPopups.unknown.length) {
    throw new TaskCenterError(
      "POPUP_UNKNOWN",
      "发现未登记的阻断弹窗，已停止任务中心导航。",
      { popupSummaries: initialPopups.unknown },
    );
  }
  const existing = await findTaskCenterFrame(page);
  if (existing) {
    await ensurePageGeometry(page, "task_center_already_ready");
    return {
      frame: existing,
      route: "already_on_task_center",
      dismissedPopups: initialPopups.dismissed,
    };
  }

  const route = await clickTaskCenterEntry(page);
  if (!route) {
    throw new TaskCenterError(
      "TASK_CENTER_ENTRY_NOT_FOUND",
      "没有找到任务中心入口；需要在已登录页面重新探查菜单结构。",
    );
  }
  const frame = await waitForTaskCenter(page);
  if (!frame) {
    throw new TaskCenterError(
      "TASK_CENTER_NOT_REACHED",
      "已点击任务中心入口，但没有回读到任务中心标题和状态表头。",
      { route },
    );
  }
  const finalPopups = await dismissKnownPopups(page);
  if (finalPopups.unknown.length) {
    throw new TaskCenterError(
      "POPUP_UNKNOWN",
      "任务中心出现未登记的阻断弹窗，已停止读取。",
      { popupSummaries: finalPopups.unknown },
    );
  }
  await ensurePageGeometry(page, "task_center_ready");
  return {
    frame,
    route,
    dismissedPopups: [
      ...initialPopups.dismissed,
      ...finalPopups.dismissed,
    ],
  };
}


export async function readTaskCenterStructure(frame) {
  const headers = [];
  for (const header of TASK_CENTER_HEADERS) {
    if (await visibleExact(frame, header).catch(() => null)) {
      headers.push(header);
    }
  }
  const semanticHeaders = frame.locator('[role="columnheader"]:visible');
  for (let index = 0; index < (await semanticHeaders.count()); index += 1) {
    const text = (await semanticHeaders.nth(index).innerText().catch(() => ""))
      .replace(/\s+/g, " ")
      .trim();
    if (text && text.length <= 30 && !headers.includes(text)) {
      headers.push(text);
    }
  }
  const tableHeaders = frame.locator("th:visible");
  for (let index = 0; index < (await tableHeaders.count()); index += 1) {
    const text = (await tableHeaders.nth(index).innerText().catch(() => ""))
      .replace(/\s+/g, " ")
      .trim();
    if (text && text.length <= 30 && !headers.includes(text)) {
      headers.push(text);
    }
  }
  const semanticRows = await frame.locator('[role="row"]:visible').count();
  const tableRows = await frame.locator("tbody tr:visible").count();
  return {
    status: "task_center_structure_ready",
    headers: headers.slice(0, 30),
    visibleRowCount: Math.max(
      tableRows,
      semanticRows > 0 ? semanticRows - 1 : 0,
    ),
    safety: "No task row was opened and no write action was performed.",
  };
}


async function waitForTaskTable(frame, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  let previousCount = -1;
  let stableReads = 0;
  while (Date.now() < deadline) {
    const visibleRows = frame.locator("tbody tr:visible");
    const count = await visibleRows.count();
    const emptyState = await firstVisible(
      frame.getByText(/暂无数据|暂无任务|没有数据/, { exact: false }),
    ).catch(() => null);
    if (emptyState) {
      return;
    }
    if (count > 0 && count === previousCount) {
      stableReads += 1;
      if (stableReads >= 2) {
        return;
      }
    } else {
      stableReads = 0;
    }
    previousCount = count;
    await frame.waitForTimeout(250);
  }
  throw new TaskCenterError(
    "COMPLETED_TASK_TABLE_TIMEOUT",
    "切换到已完成任务后，任务表格未在限定时间内稳定。",
  );
}


function normalizedText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}


async function readTaskRecordIdentity(row) {
  const identity = await row.evaluate((element) => {
    const recordId = element.getAttribute("data-recordid");
    const rowId = element.getAttribute("id");
    const viewId = element.getAttribute("data-boundview");
    const view = globalThis.Ext?.getCmp?.(viewId);
    const record = view?.getRecord?.(element);
    const modelId = record?.data?.id;
    return {
      recordId,
      rowId,
      modelId:
        typeof modelId === "string" || typeof modelId === "number"
          ? String(modelId)
          : null,
    };
  });
  if (!/^\d+$/.test(identity.recordId || "")) {
    return { taskId: null, taskIdEvidence: "unavailable" };
  }
  if (
    identity.rowId &&
    !identity.rowId.endsWith(`-record-${identity.recordId}`)
  ) {
    throw new TaskCenterError(
      "TASK_RECORD_ID_MISMATCH",
      "任务行 ID 与 data-recordid 不一致，已停止读取。",
    );
  }
  if (identity.modelId && identity.modelId !== identity.recordId) {
    throw new TaskCenterError(
      "TASK_RECORD_ID_MISMATCH",
      "任务中心数据模型 ID 与页面记录 ID 不一致，已停止读取。",
    );
  }
  return {
    taskId: identity.recordId,
    taskIdEvidence: identity.modelId
      ? "dom_and_record_model"
      : "dom_consistent",
  };
}


export async function readCompletedImportTasks(
  frame,
  { limit = MAX_COMPLETED_IMPORT_SUMMARIES } = {},
) {
  const completedTab = await visibleExact(frame, "已完成");
  if (!completedTab) {
    throw new TaskCenterError(
      "COMPLETED_TAB_NOT_FOUND",
      "任务中心没有找到“已完成”页签。",
    );
  }
  await completedTab.click();
  await waitForTaskTable(frame);

  return await readVisibleImportTasks(frame, {
    limit,
    phase: "completed",
    status: "completed_import_tasks_ready",
  });
}


async function readVisibleImportTasks(
  frame,
  {
    limit = MAX_COMPLETED_IMPORT_SUMMARIES,
    phase,
    status,
  },
) {

  const tasks = [];
  const rows = frame.locator("tbody tr:visible");
  for (let index = 0; index < (await rows.count()); index += 1) {
    const cells = rows.nth(index).locator("td:visible");
    if ((await cells.count()) < 4) {
      continue;
    }
    // Deliberately read only the non-sensitive leading cells. The operator and
    // action columns are outside this allowlist and never enter the result.
    const taskName = normalizedText(
      await cells.nth(1).innerText().catch(() => ""),
    );
    if (taskName !== COMPLETED_IMPORT_TASK_NAME) {
      continue;
    }
    const recordIdentity = await readTaskRecordIdentity(rows.nth(index));
    tasks.push({
      taskId: recordIdentity.taskId,
      taskIdEvidence: recordIdentity.taskIdEvidence,
      sequence: normalizedText(
        await cells.nth(0).innerText().catch(() => ""),
      ),
      taskName,
      time: normalizedText(await cells.nth(2).innerText().catch(() => "")),
      status: normalizedText(
        await cells.nth(3).innerText().catch(() => ""),
      ),
      phase,
    });
  }

  const safeLimit = Math.min(
    Math.max(Number.parseInt(limit, 10) || MAX_COMPLETED_IMPORT_SUMMARIES, 1),
    MAX_COMPLETED_IMPORT_SUMMARIES,
  );
  const listedTasks = tasks.slice(0, safeLimit);
  const hasObservedIds = listedTasks.some((task) => task.taskId);
  const hasVerifiedIds =
    listedTasks.length > 0 &&
    listedTasks.every(
      (task) => task.taskId && task.taskIdEvidence === "dom_and_record_model",
    );
  return {
    status,
    taskName: COMPLETED_IMPORT_TASK_NAME,
    matchCount: tasks.length,
    tasks: listedTasks,
    taskIdStatus: hasVerifiedIds
      ? "task_record_ids_verified"
      : hasObservedIds
        ? "task_record_ids_observed"
        : "task_id_unavailable_in_list",
    safety:
      "Only task record ID, sequence, name, time, and status were read. No task row was opened and no operator or action content was read.",
  };
}


export async function readImportTaskSnapshot(frame) {
  const executingTab = await visibleExact(frame, "执行中");
  if (!executingTab) {
    throw new TaskCenterError(
      "EXECUTING_TAB_NOT_FOUND",
      "任务中心没有找到“执行中”页签。",
    );
  }
  await executingTab.click();
  await waitForTaskTable(frame);
  const executing = await readVisibleImportTasks(frame, {
    phase: "executing",
    status: "executing_import_tasks_ready",
  });
  const completed = await readCompletedImportTasks(frame);
  const byId = new Map();
  for (const task of [...executing.tasks, ...completed.tasks]) {
    if (task.taskId && !byId.has(task.taskId)) {
      byId.set(task.taskId, task);
    }
  }
  const tasks = [...byId.values()];
  const hasVerifiedIds =
    tasks.length > 0 &&
    tasks.every(
      (task) => task.taskIdEvidence === "dom_and_record_model",
    );
  return {
    status: "import_task_snapshot_ready",
    taskName: COMPLETED_IMPORT_TASK_NAME,
    matchCount: tasks.length,
    tasks,
    taskIdStatus: hasVerifiedIds
      ? "task_record_ids_verified"
      : tasks.length === 0
        ? "verified_empty_list"
        : "task_record_ids_observed",
    phases: {
      executing: executing.tasks.length,
      completed: completed.tasks.length,
    },
    safety:
      "Executing and completed task IDs were read without opening a task row.",
  };
}


export async function probeTaskCenter(page) {
  const navigation = await navigateToTaskCenter(page);
  return {
    ...(await readTaskCenterStructure(navigation.frame)),
    route: navigation.route,
    dismissedPopups: navigation.dismissedPopups,
    url: page.url(),
  };
}


export async function probeCompletedImportTasks(page) {
  const navigation = await navigateToTaskCenter(page);
  return {
    ...(await readCompletedImportTasks(navigation.frame)),
    route: navigation.route,
    dismissedPopups: navigation.dismissedPopups,
    url: page.url(),
  };
}


export async function probeImportTaskSnapshot(page) {
  const navigation = await navigateToTaskCenter(page);
  return {
    ...(await readImportTaskSnapshot(navigation.frame)),
    route: navigation.route,
    dismissedPopups: navigation.dismissedPopups,
    url: page.url(),
  };
}
