import assert from "node:assert/strict";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import {
  findTaskCenterFrame,
  parseImportResultSummary,
  readCompletedImportTaskResultFromFrame,
  readImportTaskSnapshot,
  readCompletedImportTasks,
  readTaskCenterStructure,
  sanitizeImportFailure,
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
    await page.setContent("<div>已登录应用</div><iframe></iframe>");
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


test("does not turn visible tasks without model ids into a verified empty snapshot", async () => {
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
      <button id="executing">执行中</button><button id="completed">已完成</button>
      <table><thead><tr><th>任务名称</th><th>时间</th><th>状态</th></tr></thead><tbody></tbody></table>
      <script>
        const body = document.querySelector('tbody');
        const render = (id, status) => body.innerHTML = '<tr id="grid-record-' + id + '" data-recordid="' + id + '"><td>1</td><td>自定义订单导入</td><td>2026-09-28</td><td>' + status + '</td></tr>';
        document.querySelector('#executing').onclick = () => render('200', '执行中');
        document.querySelector('#completed').onclick = () => render('199', '执行完成');
        render('200', '执行中');
      </script>
    `);
    const frame = await findTaskCenterFrame(page);
    assert.ok(frame);
    const snapshot = await readImportTaskSnapshot(frame);
    assert.equal(snapshot.taskIdStatus, "task_record_ids_observed");
    assert.equal(snapshot.tasks.length, 2);
    assert.ok(snapshot.tasks.every((task) => task.taskId));
    assert.ok(
      snapshot.tasks.every((task) => task.taskIdEvidence === "dom_consistent"),
    );
  } finally {
    await browser.close();
  }
});


test("ignores a task-center iframe inside an inactive application tab", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <div aria-hidden="true" style="display:none">
        <iframe srcdoc="<div>任务名称</div><div>任务状态</div><div>完成时间</div>"></iframe>
      </div>
      <iframe srcdoc="<div>普通页面</div>"></iframe>
    `);
    assert.equal(await findTaskCenterFrame(page), null);
  } finally {
    await browser.close();
  }
});


test("parses aggregate import counts and redacts raw failure details", () => {
  assert.deepEqual(
    parseImportResultSummary(
      "导入总数： 2条 ，其中： 成功导入 1条 格式错误 1条 非法数据 0条",
    ),
    {
      total: 2,
      successful: 1,
      formatErrors: 1,
      illegalData: 0,
      failed: 1,
    },
  );
  const sanitized = sanitizeImportFailure(
    "订单中，第1行，平台单号（13800000000_某人_某详细地址）订单匹配异常！（测试店铺[无法匹配到系统中店铺信息]）",
  );
  assert.deepEqual(sanitized, {
    sourceRow: 1,
    code: "STORE_NOT_FOUND",
    message: "无法匹配到管易店铺信息",
  });
  assert.equal(JSON.stringify(sanitized).includes("13800000000"), false);
  assert.equal(JSON.stringify(sanitized).includes("某详细地址"), false);
});


test("opens one verified task result and returns only redacted summaries", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent("<div>已登录应用</div><iframe></iframe>");
    const frame = page.frames().find((candidate) => candidate !== page.mainFrame());
    assert.ok(frame);
    await frame.setContent(`
      <button id="completed">已完成</button>
      <table>
        <thead><tr><th>序号</th><th>任务名称</th><th>时间</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr id="grid-record-12345" data-boundview="grid" data-recordid="12345">
            <td>1</td><td>自定义订单导入</td><td>2026-10-14 19:24:13</td><td>执行完成</td><td><button id="result">查看结果</button></td>
          </tr>
        </tbody>
      </table>
      <div class="x-window" id="result-window" style="display:none">
        <div>Excel导入结果信息</div>
        <button class="x-tool-close" aria-label="关闭">关闭</button>
        <div>导入总数： 2条 ，其中： 成功导入 1条 格式错误 1条 非法数据 0条</div>
        <table><tbody><tr><td>订单中，第1行，平台单号（13800000000_某人_某详细地址）订单匹配异常！（测试店铺[无法匹配到系统中店铺信息]）</td></tr></tbody></table>
      </div>
      <script>
        globalThis.Ext = { getCmp: () => ({ getRecord: row => ({ data: { id: row.dataset.recordid } }) }) };
        document.querySelector('#result').onclick = () => document.querySelector('#result-window').style.display = 'block';
        document.querySelector('.x-tool-close').onclick = () => document.querySelector('#result-window').style.display = 'none';
      </script>
    `);
    const result = await readCompletedImportTaskResultFromFrame(
      page,
      frame,
      "12345",
    );
    assert.equal(result.status, "import_task_result_ready");
    assert.deepEqual(result.summary, {
      total: 2,
      successful: 1,
      formatErrors: 1,
      illegalData: 0,
      failed: 1,
    });
    assert.deepEqual(result.failures, [
      {
        sourceRow: 1,
        code: "STORE_NOT_FOUND",
        message: "无法匹配到管易店铺信息",
      },
    ]);
    const serialized = JSON.stringify(result);
    assert.equal(serialized.includes("13800000000"), false);
    assert.equal(serialized.includes("某详细地址"), false);
    assert.equal(
      await frame.locator("#result-window").isVisible().catch(() => false),
      false,
    );
  } finally {
    await browser.close();
  }
});
