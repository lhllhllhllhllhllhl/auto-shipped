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
