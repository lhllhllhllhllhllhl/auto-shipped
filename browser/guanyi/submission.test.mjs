import assert from "node:assert/strict";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import {
  auditFieldMappings,
  classifyFileSelectionNetwork,
  classifyFinalSubmissionNetwork,
  classifyTemplateLoadNetwork,
  CONTROLLED_VALIDATION_AUTHORIZATION,
  locateFieldMappingConfirmButton,
  locateImportConfirmButton,
  requireSubmitAuthorization,
  requireSubmitReadiness,
  SUBMIT_AUTHORIZATION,
  SubmissionError,
  waitForFileSelectionNetwork,
  writeLikeRequests,
} from "./submission.mjs";


test("requires the exact current-batch submission authorization", () => {
  assert.doesNotThrow(() => requireSubmitAuthorization(SUBMIT_AUTHORIZATION));
  assert.throws(
    () => requireSubmitAuthorization("not-authorized"),
    (error) =>
      error instanceof SubmissionError &&
      error.code === "SUBMIT_AUTHORIZATION_REQUIRED",
  );
});


test("keeps submit structurally closed until upload rules or controlled validation are ready", () => {
  assert.throws(
    () => requireSubmitReadiness({}),
    (error) =>
      error instanceof SubmissionError &&
      error.code === "UPLOAD_RULE_GATE_BLOCKED",
  );
  assert.doesNotThrow(() =>
    requireSubmitReadiness({ GUANYI_UPLOAD_RULES_READY: "1" }),
  );
  assert.doesNotThrow(() =>
    requireSubmitReadiness({
      GUANYI_CONTROLLED_WRITE_VALIDATION:
        CONTROLLED_VALIDATION_AUTHORIZATION,
    }),
  );
});


test("locates confirm only inside the custom-import dialog", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <button>确定</button>
      <div role="dialog">
        <h2>订单导入</h2><div>自定义导入</div>
        <button id="confirm">确 定</button><button>取消</button>
      </div>
    `);
    const button = await locateImportConfirmButton(page);
    assert.equal(await button.getAttribute("id"), "confirm");
  } finally {
    await browser.close();
  }
});


test("treats every non-read request after file selection as a possible write", () => {
  const requests = [
    { method: "GET", path: "/poll" },
    { method: "POST", path: "/upload" },
  ];
  assert.deepEqual(writeLikeRequests(requests), [requests[1]]);
});


test("continues only for the exact successful Guanyi OSS staging sequence", () => {
  const requests = [
    {
      method: "POST",
      origin: "https://www.guanyierp.com",
      path: "/upload/getOSSUploadUrlInfo",
    },
    {
      method: "POST",
      origin: "https://zjk-prd-file-p.oss-cn-zhangjiakou.aliyuncs.com",
      path: "/",
    },
  ];
  const responses = [
    { ...requests[0], status: 200 },
    { ...requests[1], status: 204 },
  ];
  assert.deepEqual(
    classifyFileSelectionNetwork(requests, responses),
    {
      safeToContinue: true,
      status: "expected_file_staging_verified",
      expectedWriteCount: 2,
      unexpectedWrites: [],
      ignoredBackgroundWrites: [],
    },
  );
});


test("stops when file selection produces no verified staging requests", () => {
  const result = classifyFileSelectionNetwork([], []);
  assert.equal(result.safeToContinue, false);
  assert.equal(result.status, "file_selection_network_uncertain");
  assert.equal(result.expectedWriteCount, 0);
});


test("stops for incomplete staging or any additional write request", () => {
  const stagingRequest = {
    method: "POST",
    origin: "https://www.guanyierp.com",
    path: "/upload/getOSSUploadUrlInfo",
  };
  const incomplete = classifyFileSelectionNetwork(
    [stagingRequest],
    [{ ...stagingRequest, status: 200 }],
  );
  assert.equal(incomplete.safeToContinue, false);
  assert.equal(incomplete.status, "file_selection_network_uncertain");

  const unexpected = {
    method: "POST",
    origin: "https://www.guanyierp.com",
    path: "/unexpected-write",
  };
  const result = classifyFileSelectionNetwork(
    [
      stagingRequest,
      {
        method: "POST",
        origin: "https://zjk-prd-file-p.oss-cn-zhangjiakou.aliyuncs.com",
        path: "/",
      },
      unexpected,
    ],
    [],
  );
  assert.equal(result.safeToContinue, false);
  assert.deepEqual(result.unexpectedWrites, [unexpected]);
});


test("waits for a delayed OSS response before deciding whether to confirm", async () => {
  const requests = [
    {
      method: "POST",
      origin: "https://www.guanyierp.com",
      path: "/upload/getOSSUploadUrlInfo",
    },
    {
      method: "POST",
      origin: "https://zjk-prd-file-p.oss-cn-zhangjiakou.aliyuncs.com",
      path: "/",
    },
  ];
  const responses = [{ ...requests[0], status: 200 }];
  let waits = 0;
  const page = {
    async waitForTimeout() {
      waits += 1;
      if (waits === 2) {
        responses.push({ ...requests[1], status: 204 });
      }
    },
  };

  const result = await waitForFileSelectionNetwork(
    page,
    requests,
    responses,
    { initialObservationMs: 1, settleTimeoutMs: 1_000, pollMs: 1 },
  );

  assert.equal(waits, 2);
  assert.equal(result.safeToContinue, true);
  assert.equal(result.status, "expected_file_staging_verified");
});


test("does not wait through an unexpected write", async () => {
  const requests = [
    {
      method: "POST",
      origin: "https://www.guanyierp.com",
      path: "/unexpected-write",
    },
  ];
  let waits = 0;
  const page = {
    async waitForTimeout() {
      waits += 1;
    },
  };

  const result = await waitForFileSelectionNetwork(page, requests, [], {
    initialObservationMs: 1,
    settleTimeoutMs: 1_000,
    pollMs: 1,
  });

  assert.equal(waits, 1);
  assert.equal(result.safeToContinue, false);
  assert.equal(result.unexpectedWrites.length, 1);
});


test("ignores only exact registered background telemetry during staging", () => {
  const requests = [
    {
      method: "POST",
      origin: "https://www.guanyierp.com",
      path: "/upload/getOSSUploadUrlInfo",
    },
    {
      method: "POST",
      origin: "https://zjk-prd-file-p.oss-cn-zhangjiakou.aliyuncs.com",
      path: "/",
    },
    {
      method: "POST",
      origin: "https://mssdk.bytedance.com",
      path: "/web/common",
    },
    {
      method: "POST",
      origin: "https://mercury.jd.com",
      path: "/log.gif",
    },
  ];
  const responses = [
    { ...requests[0], status: 200 },
    { ...requests[1], status: 204 },
  ];

  const result = classifyFileSelectionNetwork(requests, responses);

  assert.equal(result.safeToContinue, true);
  assert.equal(result.status, "expected_file_staging_verified");
  assert.deepEqual(result.unexpectedWrites, []);
  assert.deepEqual(result.ignoredBackgroundWrites, [requests[2], requests[3]]);
});


test("does not ignore an unregistered path on a telemetry origin", () => {
  const request = {
    method: "POST",
    origin: "https://mssdk.bytedance.com",
    path: "/unknown-write",
  };

  const result = classifyFileSelectionNetwork([request], []);

  assert.equal(result.safeToContinue, false);
  assert.deepEqual(result.unexpectedWrites, [request]);
  assert.deepEqual(result.ignoredBackgroundWrites, []);
});


test("verifies all 48 virtualized field mappings before locating the final confirm", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const fields = [
      "支付金额",
      "商品代码",
      "数量",
      "价格",
      "收货人",
      "收货地址",
      ...Array.from({ length: 42 }, (_, index) => `扩展字段${index + 1}`),
    ];
    await page.setContent(`
      <div role="dialog" id="mapping">
        <h2>自定义字段对应</h2>
        <div>匹配字段* 全部</div>
        <div class="ag-body-viewport" style="height:180px;overflow:auto;position:relative">
          <div class="ag-center-cols-container" style="height:1440px;position:relative"></div>
        </div>
        <button id="mapping-confirm">确 定</button><button>取消</button>
      </div>
      <script>
        const fields = ${JSON.stringify(fields)};
        const viewport = document.querySelector(".ag-body-viewport");
        const container = document.querySelector(".ag-center-cols-container");
        function renderRows() {
          const first = Math.max(0, Math.floor(viewport.scrollTop / 30) - 1);
          const last = Math.min(fields.length, first + 8);
          container.replaceChildren(...fields.slice(first, last).map((field, offset) => {
            const index = first + offset;
            const row = document.createElement("div");
            row.className = "ag-row";
            row.setAttribute("row-index", String(index));
            row.style.cssText = "position:absolute;top:" + (index * 30) + "px;height:30px";
            row.innerHTML = '<div class="ag-cell">' + field + (index < 2 ? "*" : "") + '</div><div class="ag-cell">' + field + '</div>';
            return row;
          }));
        }
        viewport.addEventListener("scroll", renderRows);
        renderRows();
      </script>
    `);
    const dialog = page.locator("#mapping");
    const result = await auditFieldMappings(page, dialog);
    assert.equal(result.status, "field_mappings_verified");
    assert.equal(result.rowCount, 48);
    const confirm = await locateFieldMappingConfirmButton(dialog);
    assert.equal(await confirm.getAttribute("id"), "mapping-confirm");
  } finally {
    await browser.close();
  }
});


test("rejects a mismatched field mapping", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <div role="dialog" id="mapping">
        <div>自定义字段对应</div><div>匹配字段* 全部</div>
        <div class="ag-body-viewport" style="height:200px;overflow:auto">
          <div class="ag-center-cols-container">
            <div class="ag-row" row-index="0"><div class="ag-cell">支付金额*</div><div class="ag-cell">价格</div></div>
          </div>
        </div>
      </div>
    `);
    await assert.rejects(
      auditFieldMappings(page, page.locator("#mapping")),
      (error) =>
        error instanceof SubmissionError &&
        error.code === "FIELD_MAPPING_VERIFICATION_FAILED",
    );
  } finally {
    await browser.close();
  }
});


test("requires exact successful template-load and final-submit requests", () => {
  const template = {
    method: "POST",
    origin: "https://www.guanyierp.com",
    path: "/tc/trade/trade_order_header/getTrdeCustomTemplate",
  };
  const finalSubmit = {
    method: "POST",
    origin: "https://www.guanyierp.com",
    path: "/tc/trade/trade_order_header/importOrderCustom",
  };
  assert.equal(
    classifyTemplateLoadNetwork(
      [template],
      [{ ...template, status: 200 }],
    ).safeToContinue,
    true,
  );
  assert.equal(
    classifyFinalSubmissionNetwork(
      [finalSubmit],
      [{ ...finalSubmit, status: 200 }],
    ).safeToContinue,
    true,
  );
  assert.equal(
    classifyFinalSubmissionNetwork(
      [finalSubmit, { ...finalSubmit, path: "/unexpected" }],
      [{ ...finalSubmit, status: 200 }],
    ).safeToContinue,
    false,
  );
});
