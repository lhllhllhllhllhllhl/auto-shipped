import assert from "node:assert/strict";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import { registerPageGeometry } from "./geometry.mjs";
import {
  ensureImportOptionsSelected,
  IMPORT_OPTION_LABELS,
  NavigationError,
  navigateToCustomImport,
  selectCustomImportOrderStatus,
  selectOrderStatusControl,
} from "./navigation.mjs";


test("reactivates the order-query tab when task center covers a mounted import button", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const guard = registerPageGeometry(page.context(), page);
    guard.ensureStable = async () => ({ stable: true });
    await page.setContent(`
      <div role="tablist" style="position:relative;z-index:3">
        <button id="order-tab" role="tab" aria-selected="false">订单查询</button>
        <button id="task-tab" role="tab" aria-selected="true">任务中心</button>
      </div>
      <section id="order-panel" style="position:absolute;inset:50px 0 0;z-index:1">
        <button id="import-button">订单导入</button>
      </section>
      <section id="task-panel" role="tabpanel"
        style="position:absolute;inset:50px 0 0;z-index:2;background:white">
        <h2>任务中心</h2>
      </section>
      <section id="import-dialog"
        style="display:none;position:fixed;inset:100px;z-index:4;background:white">
        <h2>订单导入</h2>
        <button role="tab">自定义导入</button>
        <p>导入表格可增加、删除列</p>
      </section>
      <script>
        document.querySelector('#order-tab').addEventListener('click', () => {
          setTimeout(() => {
            document.querySelector('#order-tab').setAttribute('aria-selected', 'true');
            document.querySelector('#task-tab').setAttribute('aria-selected', 'false');
            document.querySelector('#task-panel').style.display = 'none';
          }, 100);
        });
        document.querySelector('#import-button').addEventListener('click', () => {
          document.querySelector('#import-dialog').style.display = 'block';
        });
      </script>
    `);

    assert.equal(await page.locator("#task-panel").isVisible(), true);
    assert.equal(await page.locator("#import-button").isVisible(), true);

    const result = await navigateToCustomImport(page);

    assert.equal(result.status, "import_dialog_ready");
    assert.equal(result.route, "existing_order_query_tab");
    assert.equal(
      await page.locator("#order-tab").getAttribute("aria-selected"),
      "true",
    );
    assert.equal(await page.locator("#task-panel").isVisible(), false);
  } finally {
    await browser.close();
  }
});


test("recovers once when the exact speed-edition notice appears during the import click", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const guard = registerPageGeometry(page.context(), page);
    guard.ensureStable = async () => ({ stable: true });
    await page.setContent(`
      <button id="import-button">订单导入</button>
      <section id="import-dialog" style="display:none">
        <h2>订单导入</h2>
        <button role="tab">自定义导入</button>
        <p>导入表格可增加、删除列</p>
      </section>
      <script>
        const importButton = document.querySelector('#import-button');
        let noticeShown = false;
        importButton.addEventListener('pointerover', () => {
          if (noticeShown) return;
          noticeShown = true;
          const notice = document.createElement('div');
          notice.style.cssText =
            'position:fixed;inset:0;z-index:9999;background:white';
          notice.innerHTML =
            '<p>当前页面已经更新到极速版，可以通过当前按钮回到历史版本</p>' +
            '<button onclick="this.parentElement.remove()">知道了</button>';
          document.body.appendChild(notice);
        });
        importButton.addEventListener('click', () => {
          document.querySelector('#import-dialog').style.display = 'block';
        });
      </script>
    `);
    const result = await navigateToCustomImport(page);
    assert.equal(result.status, "import_dialog_ready");
    assert.deepEqual(result.dismissedPopups, ["知道了"]);
    assert.equal(
      await page.getByText(
        "当前页面已经更新到极速版，可以通过当前按钮回到历史版本",
        { exact: true },
      ).count(),
      0,
    );
  } finally {
    await browser.close();
  }
});


test("closes a top-frame Ant speed notice before clicking import inside an iframe", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const guard = registerPageGeometry(page.context(), page);
    guard.ensureStable = async () => ({ stable: true });
    await page.setContent(`
      <div id="speed-backdrop" style="z-index:888;position:fixed;top:64px;left:0;width:100%;height:calc(100vh - 64px);opacity:.4;background:black" onclick="this.remove()"></div>
      <div class="ant-popover ant-popconfirm ant-popover-placement-left" style="position:absolute;z-index:1060;top:100px;left:100px">
        <div class="ant-popover-message-title">当前页面已经更新到极速版，可以通过当前按钮回到历史版本</div>
        <button class="ant-btn-primary ant-btn-sm" onclick="this.closest('.ant-popconfirm').remove()"><span>知道了</span></button>
      </div>
      <iframe id="trade-order-header"></iframe>
    `);
    await page.locator("#trade-order-header").evaluate((iframe) => {
      iframe.srcdoc = `
        <button id="import-button">订单导入</button>
        <section id="import-dialog" style="display:none">
          <h2>订单导入</h2>
          <button role="tab">自定义导入</button>
          <p>导入表格可增加、删除列</p>
        </section>
        <script>
          document.querySelector('#import-button').addEventListener('click', () => {
            document.querySelector('#import-dialog').style.display = 'block';
          });
        <\/script>
      `;
    });
    await page
      .frames()
      .find((frame) => frame !== page.mainFrame())
      .getByRole("button", { name: "订单导入", exact: true })
      .waitFor();

    const result = await navigateToCustomImport(page);
    assert.equal(result.status, "import_dialog_ready");
    assert.deepEqual(result.dismissedPopups, ["知道了"]);
    assert.equal(await page.locator("#speed-backdrop").count(), 0);
  } finally {
    await browser.close();
  }
});


test("reuses an already-open custom-import dialog without clicking import again", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const guard = registerPageGeometry(page.context(), page);
    guard.ensureStable = async () => ({ stable: true });
    await page.setContent(`
      <div role="tablist" style="position:relative;z-index:3">
        <button id="order-tab" role="tab" aria-selected="false">订单查询</button>
        <button id="task-tab" role="tab" aria-selected="true">任务中心</button>
      </div>
      <button id="import-button">订单导入</button>
      <div class="ant-modal-wrap order-import-modal"
        style="position:fixed;inset:60px;z-index:4;background:white">
        <h2>订单导入</h2>
        <button role="tab">自定义导入</button>
        <p>导入表格可增加、删除列</p>
      </div>
      <section id="task-panel" role="tabpanel"
        style="position:fixed;inset:50px 0 0;z-index:5;background:white">
        <iframe id="paneltaskCenter"></iframe>
      </section>
      <script>
        window.importClickCount = 0;
        document.querySelector('#order-tab').addEventListener('click', () => {
          document.querySelector('#order-tab').setAttribute('aria-selected', 'true');
          document.querySelector('#task-tab').setAttribute('aria-selected', 'false');
          document.querySelector('#task-panel').style.display = 'none';
        });
        document.querySelector('#import-button').addEventListener('click', () => {
          window.importClickCount += 1;
        });
      </script>
    `);

    const result = await navigateToCustomImport(page);

    assert.equal(result.status, "import_dialog_ready");
    assert.equal(result.route, "existing_import_dialog");
    assert.equal(
      await page.locator("#order-tab").getAttribute("aria-selected"),
      "true",
    );
    assert.equal(await page.locator("#task-panel").isVisible(), false);
    assert.equal(await page.evaluate(() => window.importClickCount), 0);
  } finally {
    await browser.close();
  }
});


test("selects pending-review and confirms the rendered value without a file", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <div class="ant-select">
        <div class="ant-select-selector">选择订单状态</div>
        <input id="orderState" role="combobox" aria-expanded="false">
      </div>
      <input type="file">
      <script>
        const root = document.querySelector('.ant-select');
        root.querySelector('.ant-select-selector').addEventListener('click', () => {
          document.querySelector('#orderState').setAttribute('aria-expanded', 'true');
          const dropdown = document.createElement('div');
          dropdown.className = 'ant-select-dropdown';
          for (const value of ['待审核', '已发货']) {
            const option = document.createElement('div');
            option.className = 'ant-select-item-option';
            option.textContent = value;
            option.addEventListener('click', () => {
              root.querySelector('.ant-select-selector').innerHTML =
                '<span class="ant-select-selection-item" title="' + value + '">' + value + '</span>';
              document.querySelector('#orderState').setAttribute('aria-expanded', 'false');
              dropdown.remove();
            });
            dropdown.appendChild(option);
          }
          document.body.appendChild(dropdown);
        });
      </script>
    `);
    const result = await selectOrderStatusControl(
      page.mainFrame(),
      page.locator("#orderState"),
      "待审核",
    );
    assert.equal(result.status, "import_order_status_ready");
    assert.equal(result.orderStatus, "待审核");
    assert.equal(
      await page.locator('.ant-select-selection-item').innerText(),
      "待审核",
    );
    assert.equal(
      await page.locator('input[type="file"]').evaluate((element) =>
        element.files?.length || 0),
      0,
    );
  } finally {
    await browser.close();
  }
});


test("rejects importing directly as shipped", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await assert.rejects(
      () => selectCustomImportOrderStatus(page, "已发货"),
      (error) =>
        error instanceof NavigationError &&
        error.code === "ORDER_STATUS_NOT_ALLOWED",
    );
  } finally {
    await browser.close();
  }
});


test("selects and confirms both required custom-import options", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <label><input type="checkbox">${IMPORT_OPTION_LABELS[0]}</label>
      <label><input type="checkbox">${IMPORT_OPTION_LABELS[1]}</label>
    `);
    const result = await ensureImportOptionsSelected(page);
    assert.equal(result.importOptionsConfirmed, true);
    assert.deepEqual(result.importOptions, [...IMPORT_OPTION_LABELS]);
    assert.deepEqual(
      await page.locator('input[type="checkbox"]').evaluateAll((items) =>
        items.map((item) => item.checked)),
      [true, true],
    );
  } finally {
    await browser.close();
  }
});
