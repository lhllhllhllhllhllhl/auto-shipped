import assert from "node:assert/strict";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import { registerPageGeometry } from "./geometry.mjs";
import { dismissKnownPopups } from "./popup.mjs";


test("dismisses an acknowledgement only inside a known popup", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const guard = registerPageGeometry(page.context(), page);
    guard.ensureStable = async () => ({ stable: true });
    await page.setContent(`
      <div role="dialog"><h2>店铺授权到期提醒</h2><button onclick="this.parentElement.remove()">知道了</button></div>
    `);
    const result = await dismissKnownPopups(page);
    assert.deepEqual(result.dismissed, ["知道了"]);
    assert.deepEqual(result.unknown, []);
    assert.equal(result.pageUrlUnchanged, true);
  } finally {
    await browser.close();
  }
});


test("dismisses the exact speed-edition notice without relying on modal classes", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const guard = registerPageGeometry(page.context(), page);
    guard.ensureStable = async () => ({ stable: true });
    await page.setContent(`
      <div class="late-speed-notice">
        <p>当前页面已经更新到极速版，可以通过当前按钮回到历史版本</p>
        <button onclick="this.parentElement.remove()">知道了</button>
      </div>
    `);
    const result = await dismissKnownPopups(page);
    assert.deepEqual(result.dismissed, ["知道了"]);
    assert.deepEqual(result.unknown, []);
    assert.equal(result.pageUrlUnchanged, true);
  } finally {
    await browser.close();
  }
});


test("does not click an ambiguous acknowledgement for the exact speed notice", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <div>
        <p>当前页面已经更新到极速版，可以通过当前按钮回到历史版本</p>
        <button onclick="window.clicks = (window.clicks || 0) + 1">知道了</button>
        <button onclick="window.clicks = (window.clicks || 0) + 1">知道了</button>
      </div>
    `);
    const result = await dismissKnownPopups(page);
    assert.deepEqual(result.dismissed, []);
    assert.equal(result.unknown.length, 1);
    assert.match(result.unknown[0], /未找到唯一安全确认按钮/);
    assert.equal(await page.evaluate(() => window.clicks || 0), 0);
  } finally {
    await browser.close();
  }
});


test("does not click an acknowledgement in an unknown popup", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <div role="dialog"><h2>未知业务确认</h2><button>知道了</button></div>
    `);
    const result = await dismissKnownPopups(page);
    assert.deepEqual(result.dismissed, []);
    assert.deepEqual(result.unknown, ["未知业务确认 知道了"]);
    assert.equal(result.pageUrlUnchanged, true);
  } finally {
    await browser.close();
  }
});


test("does not click a close tool inside an unknown popup", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <div class="x-window">
        <h2>未知业务确认</h2>
        <div class="x-tool-close" role="button" aria-label="关闭"
             onclick="window.closeAttempts = (window.closeAttempts || 0) + 1">×</div>
      </div>
    `);
    const result = await dismissKnownPopups(page);
    assert.deepEqual(result.dismissed, []);
    assert.deepEqual(result.unknown, ["未知业务确认 ×"]);
    assert.equal(await page.evaluate(() => window.closeAttempts || 0), 0);
  } finally {
    await browser.close();
  }
});


test("dismisses an ExtJS close tool only inside a known popup", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const guard = registerPageGeometry(page.context(), page);
    guard.ensureStable = async () => ({ stable: true });
    await page.setContent(`
      <div class="x-window">
        <h2>店铺授权到期提醒</h2>
        <div class="x-tool-close" role="button" aria-label="关闭"
             onclick="this.closest('.x-window').remove()">×</div>
        <a>不再提醒</a><button>去授权</button>
      </div>
    `);
    const result = await dismissKnownPopups(page);
    assert.deepEqual(result.dismissed, ["known_modal_close"]);
    assert.deepEqual(result.unknown, []);
    assert.equal(result.pageUrlUnchanged, true);
  } finally {
    await browser.close();
  }
});


test("does not report success unless the known popup actually closes", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <div class="x-window">
        <h2>店铺授权到期提醒</h2>
        <div class="x-tool-close" role="button" aria-label="关闭"
             onclick="window.closeAttempts = (window.closeAttempts || 0) + 1">×</div>
      </div>
    `);
    const result = await dismissKnownPopups(page);
    assert.deepEqual(result.dismissed, []);
    assert.deepEqual(result.unknown, ["店铺授权到期提醒 ×"]);
    assert.equal(await page.evaluate(() => window.closeAttempts), 1);
  } finally {
    await browser.close();
  }
});


test("reports a page-address change after closing a known popup", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const guard = registerPageGeometry(page.context(), page);
    guard.ensureStable = async () => ({ stable: true });
    await page.setContent(`
      <div class="x-window">
        <h2>店铺授权到期提醒</h2>
        <div class="x-tool-close" role="button" aria-label="关闭"
             onclick="this.closest('.x-window').remove(); location.hash = 'unexpected'">×</div>
      </div>
    `);
    const result = await dismissKnownPopups(page);
    assert.deepEqual(result.dismissed, ["known_modal_close"]);
    assert.deepEqual(result.unknown, ["已知弹窗关闭动作改变了页面地址"]);
    assert.equal(result.pageUrlUnchanged, false);
  } finally {
    await browser.close();
  }
});
