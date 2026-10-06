import assert from "node:assert/strict";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import {
  findTaskCenterFrame,
  readImportTaskSnapshot,
  readCompletedImportTasks,
  readTaskCenterStructure,
  TaskCenterError,
} from "./task-center.mjs";


test("reads task-center structure across an iframe without task contents", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <iframe srcdoc="
        <table>
          <thead><tr><th>任务名称</th><th>状态</th><th>当前进度</th></tr></thead>
          <tbody><tr><td>示例</td><td>完成</td><td>2026-01-01</td></tr></tbody>
        </table>
      "></iframe>
    `);
    const frame = await findTaskCenterFrame(page);
    assert.ok(frame);
    const structure = await readTaskCenterStructure(frame);
    assert.equal(structure.status, "task_center_structure_ready");
    assert.ok(structure.headers.includes("状态"));
    assert.equal(structure.visibleRowCount, 1);
    assert.equal(Object.hasOwn(structure, "rows"), false);
  } finally {
    await browser.close();
  }
});


test("reads only allowlisted fields from completed custom-import tasks", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <iframe srcdoc="
        <button>执行中</button><button>已完成</button>
        <table>
          <thead><tr><th>任务名称</th><th>时间</th><th>状态</th><th>操作人</th><th>操作</th></tr></thead>
          <tbody>
            <tr id='grid-record-12345' data-recordid='12345'><td>1</td><td>自定义订单导入</td><td>2026-09-28 10:00:00</td><td>执行完成</td><td>敏感操作人</td><td>查看</td></tr>
            <tr><td>2</td><td>其他任务</td><td>2026-09-28 09:00:00</td><td>执行完成</td><td>另一操作人</td><td>查看</td></tr>
          </tbody>
        </table>
      "></iframe>
    `);
    const frame = await findTaskCenterFrame(page);
    assert.ok(frame);
    const result = await readCompletedImportTasks(frame);
    assert.equal(result.status, "completed_import_tasks_ready");
    assert.equal(result.matchCount, 1);
    assert.deepEqual(result.tasks, [
      {
        taskId: "12345",
        taskIdEvidence: "dom_consistent",
        sequence: "1",
        taskName: "自定义订单导入",
        time: "2026-09-28 10:00:00",
        status: "执行完成",
        phase: "completed",
      },
    ]);
    assert.equal(result.taskIdStatus, "task_record_ids_observed");
    const serialized = JSON.stringify(result);
    assert.equal(serialized.includes("敏感操作人"), false);
    assert.equal(serialized.includes("另一操作人"), false);
    assert.equal(serialized.includes("查看"), false);
  } finally {
    await browser.close();
  }
});


test("captures both executing and completed import task ids", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent("<iframe></iframe>");
    const childFrame = page.frames().find((candidate) => candidate !== page.mainFrame());
    assert.ok(childFrame);
    await childFrame.setContent(`
        <button id='executing'>执行中</button><button id='completed'>已完成</button>
        <table><thead><tr><th>任务名称</th><th>时间</th><th>状态</th></tr></thead><tbody></tbody></table>
        <script>
          const body = document.querySelector('tbody');
          globalThis.Ext = { getCmp: () => ({ getRecord: (row) => ({ data: { id: row.dataset.recordid } }) }) };
          const render = (id, status) => body.innerHTML = '<tr role="row" id="grid-record-' + id + '" data-boundview="grid" data-recordid="' + id + '"><td>1</td><td>自定义订单导入</td><td>2026-09-28</td><td>' + status + '</td></tr>';
          document.querySelector('#executing').onclick = () => render('200', '执行中');
          document.querySelector('#completed').onclick = () => render('199', '执行完成');
          render('200', '执行中');
        </script>
    `);
    const frame = await findTaskCenterFrame(page);
    assert.ok(frame);
    const snapshot = await readImportTaskSnapshot(frame);
    assert.equal(snapshot.taskIdStatus, "task_record_ids_verified");
    assert.deepEqual(
      snapshot.tasks.map((task) => [task.taskId, task.phase]),
      [["200", "executing"], ["199", "completed"]],
    );
    assert.deepEqual(snapshot.phases, { executing: 1, completed: 1 });
  } finally {
    await browser.close();
  }
});


test("fails closed when the DOM task id disagrees with the record model", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <iframe srcdoc="
        <button>已完成</button>
        <table>
          <thead><tr><th>任务名称</th><th>时间</th><th>状态</th></tr></thead>
          <tbody><tr id='grid-record-12345' data-boundview='grid' data-recordid='12345'><td>1</td><td>自定义订单导入</td><td>2026-09-28</td><td>执行完成</td></tr></tbody>
        </table>
      "></iframe>
    `);
    const frame = await findTaskCenterFrame(page);
    assert.ok(frame);
    await frame.evaluate(() => {
      globalThis.Ext = {
        getCmp: () => ({
          getRecord: () => ({ data: { id: "99999" } }),
        }),
      };
    });
    await assert.rejects(
      () => readCompletedImportTasks(frame),
      (error) =>
        error instanceof TaskCenterError &&
        error.code === "TASK_RECORD_ID_MISMATCH",
    );
  } finally {
    await browser.close();
  }
});
