import assert from "node:assert/strict";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import {
  NavigationError,
  selectCustomImportOrderStatus,
  selectOrderStatusControl,
} from "./navigation.mjs";


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
