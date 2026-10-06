import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import {
  FILE_SELECTION_AUTHORIZATION,
  locateOrderInfoFileInput,
  requireFileSelectionAuthorization,
  setFileInputOnce,
  validateFileSelectionOperation,
} from "./file-selection.mjs";
import { OperationState, OperationStateError } from "./operation-state.mjs";


test("locates only the file input owned by the order upload control", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <span class="ant-upload" role="button">
        <input type="file" accept=".xls,.xlsx">
        <button><span>订单信息上传</span></button>
      </span>
      <span class="ant-upload"><input type="file"><button>其他上传</button></span>
    `);
    const target = await locateOrderInfoFileInput(page);
    assert.equal(await target.input.getAttribute("accept"), ".xls,.xlsx");
  } finally {
    await browser.close();
  }
});


test("selects one authorized local file and verifies name and size", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-file-"));
  try {
    const filePath = path.join(directory, "orders.xlsx");
    await fs.writeFile(filePath, "simulated workbook");
    const stat = await fs.stat(filePath);
    const page = await browser.newPage();
    await page.setContent('<input type="file" accept=".xlsx">');
    requireFileSelectionAuthorization(FILE_SELECTION_AUTHORIZATION);
    const selected = await setFileInputOnce(page.locator("input"), {
      path: filePath,
      fileName: "orders.xlsx",
      sizeBytes: stat.size,
    });
    assert.deepEqual(selected, {
      count: 1,
      name: "orders.xlsx",
      size: stat.size,
    });
  } finally {
    await browser.close();
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("refuses file selection without the exact authorization token", () => {
  assert.throws(
    () => requireFileSelectionAuthorization("not-authorized"),
    (error) =>
      error.code === "FILE_SELECTION_AUTHORIZATION_REQUIRED",
  );
});


test("requires a verified task baseline before file selection", () => {
  const verified = {
    manifestPath: "/tmp/preflight.json",
    workbookPath: "/tmp/orders.xlsx",
    artifactSha256: "a".repeat(64),
    artifactSizeBytes: 123,
  };
  const record = {
    state: OperationState.PREPARED,
    manifest: { path: verified.manifestPath },
    artifact: {
      path: verified.workbookPath,
      sha256: verified.artifactSha256,
      sizeBytes: verified.artifactSizeBytes,
    },
    taskBaseline: null,
  };
  assert.throws(
    () => validateFileSelectionOperation(record, verified),
    (error) =>
      error instanceof OperationStateError &&
      error.code === "TASK_BASELINE_REQUIRED",
  );
  record.taskBaseline = { evidence: "verified_task_ids" };
  assert.equal(validateFileSelectionOperation(record, verified), true);
});
