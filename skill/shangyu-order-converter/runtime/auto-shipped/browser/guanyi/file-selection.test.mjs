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
      evidence: "input_files",
    });
  } finally {
    await browser.close();
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("confirms selection from the same dialog when Ant replaces its file input", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-ant-file-"));
  try {
    const filePath = path.join(directory, "orders.xlsx");
    await fs.writeFile(filePath, "simulated workbook");
    const stat = await fs.stat(filePath);
    const page = await browser.newPage();
    await page.setContent(`
      <div role="dialog">
        <span class="ant-upload" role="button">
          <input id="order-file" type="file" accept=".xls,.xlsx">
          <button><span>订单信息上传</span></button>
        </span>
        <div class="ant-upload-list ant-upload-list-text"></div>
      </div>
      <script>
        const input = document.querySelector("#order-file");
        input.addEventListener("change", () => {
          const fileName = input.files[0].name;
          document.querySelector(".ant-upload-list").innerHTML =
            '<div class="ant-upload-list-item"><a class="ant-upload-list-item-name">' +
            fileName + '</a></div>';
          input.value = "";
          input.replaceWith(input.cloneNode());
        });
      </script>
    `);
    const target = await locateOrderInfoFileInput(page);
    const selected = await setFileInputOnce(
      target.input,
      { path: filePath, fileName: "orders.xlsx", sizeBytes: stat.size },
      { confirmationRoot: target.confirmationRoot },
    );
    assert.deepEqual(selected, {
      count: 1,
      name: "orders.xlsx",
      size: stat.size,
      evidence: "upload_list_after_input_replaced",
    });
    assert.equal(
      await target.input.evaluate((element) => element.files.length),
      0,
    );
  } finally {
    await browser.close();
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("rejects a replaced file input without exact upload-list evidence", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-ant-miss-"));
  try {
    const filePath = path.join(directory, "orders.xlsx");
    await fs.writeFile(filePath, "simulated workbook");
    const stat = await fs.stat(filePath);
    const page = await browser.newPage();
    await page.setContent(`
      <div role="dialog">
        <span class="ant-upload" role="button">
          <input id="order-file" type="file" accept=".xlsx">
          <button><span>订单信息上传</span></button>
        </span>
        <div class="ant-upload-list ant-upload-list-text"></div>
      </div>
      <script>
        const input = document.querySelector("#order-file");
        input.addEventListener("change", () => {
          input.value = "";
          input.replaceWith(input.cloneNode());
        });
      </script>
    `);
    const target = await locateOrderInfoFileInput(page);
    await assert.rejects(
      setFileInputOnce(
        target.input,
        { path: filePath, fileName: "orders.xlsx", sizeBytes: stat.size },
        { confirmationRoot: target.confirmationRoot },
      ),
      (error) =>
        error instanceof Error &&
        error.code === "FILE_SELECTION_NOT_CONFIRMED",
    );
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
    manifestArtifactPath: "/original-machine/exports/orders.xlsx",
    workbookPath: "/tmp/orders.xlsx",
    artifactPathBinding: "explicit_current_path",
    artifactSha256: "a".repeat(64),
    artifactSizeBytes: 123,
  };
  const record = {
    state: OperationState.PREPARED,
    manifest: {
      path: verified.manifestPath,
      artifactPath: verified.manifestArtifactPath,
    },
    artifact: {
      path: verified.workbookPath,
      pathBinding: verified.artifactPathBinding,
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
