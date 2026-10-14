import { ensurePageGeometry } from "./geometry.mjs";
import {
  prepareFileSelectionTarget,
  safeRequestMetadata,
  setFileInputOnce,
} from "./file-selection.mjs";
import {
  loadOperation,
  OperationState,
  transitionOperation,
} from "./operation-state.mjs";


export const SUBMIT_AUTHORIZATION = "CONFIRM_GUANYI_IMPORT_SUBMISSION";
export const CONTROLLED_VALIDATION_AUTHORIZATION =
  "CONFIRM_CONTROLLED_GUANYI_WRITE_VALIDATION";
const FIELD_MAPPING_TITLE = "自定义字段对应";
const EXPECTED_IMPORT_FIELD_COUNT = 48;
const REQUIRED_IMPORT_FIELDS = Object.freeze([
  "支付金额",
  "商品代码",
  "数量",
  "价格",
  "收货人",
  "收货地址",
]);


export class SubmissionError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "SubmissionError";
    this.code = code;
    this.details = details;
  }
}


export function requireSubmitAuthorization(authorization) {
  if (authorization !== SUBMIT_AUTHORIZATION) {
    throw new SubmissionError(
      "SUBMIT_AUTHORIZATION_REQUIRED",
      "点击管易确定需要用户对当前批次的明确授权。",
    );
  }
}


export function requireSubmitReadiness(environment = process.env) {
  if (
    environment.GUANYI_UPLOAD_RULES_READY !== "1" &&
    environment.GUANYI_CONTROLLED_WRITE_VALIDATION !==
      CONTROLLED_VALIDATION_AUTHORIZATION
  ) {
    throw new SubmissionError(
      "UPLOAD_RULE_GATE_BLOCKED",
      "真实写入验收完成并登记前，管易确定提交保持关闭。",
    );
  }
}


function normalized(value) {
  return String(value || "").replace(/\s+/g, "").trim();
}


export async function locateImportConfirmButton(page) {
  const matches = [];
  for (const frame of page.frames()) {
    const buttons = frame.getByRole("button");
    for (let index = 0; index < (await buttons.count()); index += 1) {
      const button = buttons.nth(index);
      if (
        !(await button.isVisible().catch(() => false)) ||
        normalized(await button.innerText().catch(() => "")) !== "确定"
      ) {
        continue;
      }
      const belongsToImportDialog = await button.evaluate((element) => {
        const surface = element.closest(
          '[role="dialog"], .ant-modal-content, .ant-modal',
        );
        const text = surface?.innerText || "";
        return text.includes("订单导入") && text.includes("自定义导入");
      });
      if (belongsToImportDialog) {
        matches.push(button);
      }
    }
  }
  if (matches.length !== 1) {
    throw new SubmissionError(
      "IMPORT_CONFIRM_BUTTON_NOT_UNIQUE",
      "无法在自定义订单导入弹窗中唯一定位确定按钮。",
      { matchCount: matches.length },
    );
  }
  return matches[0];
}


async function visibleFieldMappingDialogs(page) {
  const matches = [];
  for (const frame of page.frames()) {
    const dialogs = frame.locator('[role="dialog"]');
    for (let index = 0; index < (await dialogs.count()); index += 1) {
      const dialog = dialogs.nth(index);
      if (!(await dialog.isVisible().catch(() => false))) {
        continue;
      }
      const text = await dialog.innerText().catch(() => "");
      if (text.includes(FIELD_MAPPING_TITLE)) {
        matches.push(dialog);
      }
    }
  }
  return matches;
}


export async function waitForFieldMappingDialog(page, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const matches = await visibleFieldMappingDialogs(page);
    if (matches.length === 1) {
      return matches[0];
    }
    if (matches.length > 1) {
      throw new SubmissionError(
        "FIELD_MAPPING_DIALOG_AMBIGUOUS",
        "页面同时出现多个自定义字段对应窗口，已停止提交。",
        { matchCount: matches.length },
      );
    }
    await page.waitForTimeout(150);
  }
  throw new SubmissionError(
    "FIELD_MAPPING_DIALOG_NOT_FOUND",
    "第一次确定后没有出现自定义字段对应窗口。",
  );
}


function normalizedField(value) {
  return normalized(value).replace(/\*/g, "");
}


export async function auditFieldMappings(page, dialog) {
  const dialogText = await dialog.innerText().catch(() => "");
  if (!/匹配字段\s*\*?\s*全部/.test(dialogText.replace(/\s+/g, " "))) {
    throw new SubmissionError(
      "FIELD_MAPPING_FILTER_NOT_ALL",
      "自定义字段对应窗口当前不是“全部”视图。",
    );
  }
  const viewport = dialog.locator(".ag-body-viewport").first();
  if (!(await viewport.isVisible().catch(() => false))) {
    throw new SubmissionError(
      "FIELD_MAPPING_GRID_NOT_VISIBLE",
      "自定义字段对应表格当前不可见。",
    );
  }
  const originalTop = await viewport.evaluate((element) => element.scrollTop);
  const dimensions = await viewport.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  const maxTop = Math.max(0, dimensions.scrollHeight - dimensions.clientHeight);
  const step = Math.max(120, Math.floor(dimensions.clientHeight * 0.72));
  const positions = [];
  for (let top = 0; top < maxTop; top += step) {
    positions.push(top);
  }
  positions.push(maxTop);

  const rowMap = new Map();
  try {
    for (const top of positions) {
      await viewport.evaluate((element, nextTop) => {
        element.scrollTop = nextTop;
        element.dispatchEvent(new Event("scroll", { bubbles: true }));
      }, top);
      await page.waitForTimeout(120);
      const rows = await dialog
        .locator(".ag-center-cols-container .ag-row")
        .evaluateAll((elements) =>
          elements.map((row) => ({
            index: Number(row.getAttribute("row-index")),
            values: Array.from(row.querySelectorAll(".ag-cell"))
              .map((cell) => (cell.innerText || "").replace(/\s+/g, " ").trim())
              .filter(Boolean),
          })),
        );
      for (const row of rows) {
        if (Number.isInteger(row.index) && row.values.length >= 2) {
          rowMap.set(row.index, row.values.slice(-2));
        }
      }
    }
  } finally {
    await viewport.evaluate((element, top) => {
      element.scrollTop = top;
      element.dispatchEvent(new Event("scroll", { bubbles: true }));
    }, originalTop);
    await page.waitForTimeout(120);
  }

  const rows = [...rowMap.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([index, values]) => ({
      index,
      systemField: values[0],
      excelField: values[1],
    }));
  const contiguous = rows.every((row, index) => row.index === index);
  const mismatches = rows.filter(
    (row) =>
      !normalizedField(row.systemField) ||
      !normalizedField(row.excelField) ||
      normalizedField(row.systemField) !== normalizedField(row.excelField),
  );
  const excelFields = rows.map((row) => normalizedField(row.excelField));
  const duplicateExcelFields = [...new Set(
    excelFields.filter(
      (value, index, all) => value && all.indexOf(value) !== index,
    ),
  )];
  const mappedFields = new Set(
    rows
      .filter(
        (row) =>
          normalizedField(row.systemField) === normalizedField(row.excelField),
      )
      .map((row) => normalizedField(row.systemField)),
  );
  const missingRequiredFields = REQUIRED_IMPORT_FIELDS.filter(
    (field) => !mappedFields.has(field),
  );
  if (
    rows.length !== EXPECTED_IMPORT_FIELD_COUNT ||
    !contiguous ||
    mismatches.length > 0 ||
    duplicateExcelFields.length > 0 ||
    missingRequiredFields.length > 0
  ) {
    throw new SubmissionError(
      "FIELD_MAPPING_VERIFICATION_FAILED",
      "自定义字段对应未通过完整的一一映射校验。",
      {
        rowCount: rows.length,
        expectedRowCount: EXPECTED_IMPORT_FIELD_COUNT,
        contiguous,
        mismatchRows: mismatches.map((row) => row.index + 1),
        duplicateExcelFields,
        missingRequiredFields,
      },
    );
  }
  return {
    status: "field_mappings_verified",
    rowCount: rows.length,
    requiredFields: [...REQUIRED_IMPORT_FIELDS],
    filter: "全部",
  };
}


export async function locateFieldMappingConfirmButton(dialog) {
  const buttons = dialog.getByRole("button");
  const matches = [];
  for (let index = 0; index < (await buttons.count()); index += 1) {
    const button = buttons.nth(index);
    if (
      (await button.isVisible().catch(() => false)) &&
      normalized(await button.innerText().catch(() => "")) === "确定"
    ) {
      matches.push(button);
    }
  }
  if (matches.length !== 1) {
    throw new SubmissionError(
      "FIELD_MAPPING_CONFIRM_NOT_UNIQUE",
      "无法在自定义字段对应窗口中唯一定位确定按钮。",
      { matchCount: matches.length },
    );
  }
  return matches[0];
}


export function writeLikeRequests(requests) {
  return requests.filter(
    (request) => !["GET", "HEAD", "OPTIONS"].includes(request.method),
  );
}


const EXPECTED_FILE_STAGING_WRITES = Object.freeze([
  Object.freeze({
    method: "POST",
    origin: "https://www.guanyierp.com",
    path: "/upload/getOSSUploadUrlInfo",
  }),
  Object.freeze({
    method: "POST",
    origin: "https://zjk-prd-file-p.oss-cn-zhangjiakou.aliyuncs.com",
    path: "/",
  }),
]);

const REGISTERED_BACKGROUND_WRITES = Object.freeze([
  Object.freeze({
    method: "POST",
    origin: "https://mssdk.bytedance.com",
    path: "/web/common",
  }),
  Object.freeze({
    method: "POST",
    origin: "https://cloud-security.jinritemai.com",
    path: "/securityevent/web/batchreport",
  }),
  Object.freeze({
    method: "POST",
    origin: "https://mon.zijieapi.com",
    path: "/monitor_browser/collect/batch/",
  }),
  Object.freeze({
    method: "POST",
    origin: "https://cactus.jd.com",
    path: "/behavior_report",
  }),
  Object.freeze({
    method: "POST",
    origin: "https://mercury.jd.com",
    path: "/log.gif",
  }),
]);

const EXPECTED_TEMPLATE_LOAD_WRITE = Object.freeze({
  method: "POST",
  origin: "https://www.guanyierp.com",
  path: "/tc/trade/trade_order_header/getTrdeCustomTemplate",
});

const EXPECTED_FINAL_SUBMISSION_WRITE = Object.freeze({
  method: "POST",
  origin: "https://www.guanyierp.com",
  path: "/tc/trade/trade_order_header/importOrderCustom",
});

const FILE_SELECTION_INITIAL_OBSERVATION_MS = 1_500;
const FILE_STAGING_SETTLE_TIMEOUT_MS = 10_000;
const FILE_STAGING_SETTLE_POLL_MS = 100;


function matchesNetworkStep(item, step) {
  return (
    item?.method === step.method &&
    item?.origin === step.origin &&
    item?.path === step.path
  );
}


function partitionFileSelectionWrites(requests) {
  const writes = writeLikeRequests(requests);
  const ignoredBackgroundWrites = writes.filter((request) =>
    REGISTERED_BACKGROUND_WRITES.some((step) =>
      matchesNetworkStep(request, step),
    ),
  );
  return {
    ignoredBackgroundWrites,
    candidateWrites: writes.filter(
      (request) => !ignoredBackgroundWrites.includes(request),
    ),
  };
}


export function classifyFileSelectionNetwork(requests, responses) {
  const { candidateWrites, ignoredBackgroundWrites } =
    partitionFileSelectionWrites(requests);
  if (candidateWrites.length === 0) {
    return {
      safeToContinue: false,
      status: "file_selection_network_uncertain",
      expectedWriteCount: 0,
      unexpectedWrites: [],
      ignoredBackgroundWrites,
    };
  }
  const unexpectedWrites = candidateWrites.filter(
    (request) =>
      !EXPECTED_FILE_STAGING_WRITES.some((step) =>
        matchesNetworkStep(request, step),
      ),
  );
  const exactRequestSequence =
    candidateWrites.length === EXPECTED_FILE_STAGING_WRITES.length &&
    EXPECTED_FILE_STAGING_WRITES.every((step, index) =>
      matchesNetworkStep(candidateWrites[index], step),
    );
  const successfulResponses = EXPECTED_FILE_STAGING_WRITES.every(
    (step) =>
      responses.filter(
        (response) =>
          matchesNetworkStep(response, step) &&
          Number.isInteger(response.status) &&
          response.status >= 200 &&
          response.status < 300,
      ).length === 1,
  );
  const safeToContinue =
    unexpectedWrites.length === 0 &&
    exactRequestSequence &&
    successfulResponses;
  return {
    safeToContinue,
    status: safeToContinue
      ? "expected_file_staging_verified"
      : "file_selection_network_uncertain",
    expectedWriteCount: candidateWrites.length - unexpectedWrites.length,
    unexpectedWrites,
    ignoredBackgroundWrites,
  };
}


function fileSelectionNetworkCanStillSettle(requests, responses) {
  const { candidateWrites } = partitionFileSelectionWrites(requests);
  if (
    candidateWrites.length === 0 ||
    candidateWrites.length > EXPECTED_FILE_STAGING_WRITES.length ||
    !candidateWrites.every((request, index) =>
      matchesNetworkStep(request, EXPECTED_FILE_STAGING_WRITES[index]),
    )
  ) {
    return false;
  }
  for (const step of EXPECTED_FILE_STAGING_WRITES) {
    const matches = responses.filter((response) =>
      matchesNetworkStep(response, step),
    );
    if (
      matches.length > 1 ||
      matches.some(
        (response) =>
          !Number.isInteger(response.status) ||
          response.status < 200 ||
          response.status >= 300,
      )
    ) {
      return false;
    }
  }
  return true;
}


export async function waitForFileSelectionNetwork(
  page,
  requests,
  responses,
  {
    initialObservationMs = FILE_SELECTION_INITIAL_OBSERVATION_MS,
    settleTimeoutMs = FILE_STAGING_SETTLE_TIMEOUT_MS,
    pollMs = FILE_STAGING_SETTLE_POLL_MS,
  } = {},
) {
  await page.waitForTimeout(initialObservationMs);
  let result = classifyFileSelectionNetwork(requests, responses);
  if (result.safeToContinue || !fileSelectionNetworkCanStillSettle(requests, responses)) {
    return result;
  }

  const deadline = Date.now() + settleTimeoutMs;
  while (Date.now() < deadline) {
    await page.waitForTimeout(Math.min(pollMs, deadline - Date.now()));
    result = classifyFileSelectionNetwork(requests, responses);
    if (
      result.safeToContinue ||
      !fileSelectionNetworkCanStillSettle(requests, responses)
    ) {
      return result;
    }
  }
  return result;
}


function classifyExactNetworkPhase(requests, responses, expected) {
  const { candidateWrites, ignoredBackgroundWrites } =
    partitionFileSelectionWrites(requests);
  const expectedRequests = candidateWrites.filter((request) =>
    matchesNetworkStep(request, expected),
  );
  const unexpectedWrites = candidateWrites.filter(
    (request) => !matchesNetworkStep(request, expected),
  );
  const matchingResponses = responses.filter((response) =>
    matchesNetworkStep(response, expected),
  );
  const safeToContinue =
    expectedRequests.length === 1 &&
    unexpectedWrites.length === 0 &&
    matchingResponses.length === 1 &&
    matchingResponses[0].status >= 200 &&
    matchingResponses[0].status < 300;
  return {
    safeToContinue,
    expectedRequestCount: expectedRequests.length,
    responseStatuses: matchingResponses.map((response) => response.status),
    unexpectedWrites,
    ignoredBackgroundWrites,
  };
}


export function classifyTemplateLoadNetwork(requests, responses) {
  return classifyExactNetworkPhase(
    requests,
    responses,
    EXPECTED_TEMPLATE_LOAD_WRITE,
  );
}


export function classifyFinalSubmissionNetwork(requests, responses) {
  return classifyExactNetworkPhase(
    requests,
    responses,
    EXPECTED_FINAL_SUBMISSION_WRITE,
  );
}


async function waitForExactNetworkPhase(
  page,
  requests,
  responses,
  classifier,
  {
    timeoutMs = 10_000,
    requestStart = 0,
    responseStart = 0,
  } = {},
) {
  const deadline = Date.now() + timeoutMs;
  let result = classifier(
    requests.slice(requestStart),
    responses.slice(responseStart),
  );
  while (Date.now() < deadline && !result.safeToContinue) {
    if (result.unexpectedWrites.length > 0) {
      return result;
    }
    await page.waitForTimeout(Math.min(100, deadline - Date.now()));
    result = classifier(
      requests.slice(requestStart),
      responses.slice(responseStart),
    );
  }
  return result;
}


function safeResponseMetadata(response) {
  try {
    const request = response.request();
    const url = new URL(response.url());
    return {
      method: request.method(),
      origin: url.origin,
      path: url.pathname,
      status: response.status(),
    };
  } catch {
    return {
      method: null,
      origin: null,
      path: null,
      status: null,
    };
  }
}


async function moveToManualReview(operationId, reason, directory) {
  const loaded = await loadOperation(operationId, directory);
  if (
    [
      OperationState.POST_WRITE_VERIFIED,
      OperationState.NEEDS_MANUAL_REVIEW,
      OperationState.FAILED_CLOSED,
    ].includes(loaded.record.state)
  ) {
    return loaded;
  }
  return await transitionOperation(
    operationId,
    OperationState.NEEDS_MANUAL_REVIEW,
    { reason },
    directory,
  );
}


export async function submitPreparedImport(
  page,
  operationId,
  authorization,
  directory,
) {
  requireSubmitReadiness();
  requireSubmitAuthorization(authorization);
  const { loaded, form, target } = await prepareFileSelectionTarget(
    page,
    operationId,
    directory,
  );
  await ensurePageGeometry(page, "before_authorized_file_selection");

  await transitionOperation(
    operationId,
    OperationState.FILE_SELECTION_STARTED,
    { reason: "current_turn_authorized_batch_submission" },
    directory,
  );

  const requests = [];
  const responses = [];
  const onRequest = (request) => requests.push(safeRequestMetadata(request));
  const onResponse = (response) =>
    responses.push(safeResponseMetadata(response));
  page.on("request", onRequest);
  page.on("response", onResponse);
  let selectedFile = null;
  try {
    let selectionError = null;
    try {
      selectedFile = await setFileInputOnce(
        target.input,
        loaded.record.artifact,
        { confirmationRoot: target.confirmationRoot },
      );
    } catch (error) {
      selectionError = error;
    }
    await transitionOperation(
      operationId,
      OperationState.WRITE_REQUEST_MAYBE_SENT,
      { reason: "file_input_change_attempted_during_submission" },
      directory,
    );
    if (selectionError) {
      throw selectionError;
    }

    const fileSelectionNetwork = await waitForFileSelectionNetwork(
      page,
      requests,
      responses,
    );
    if (!fileSelectionNetwork.safeToContinue) {
      return {
        status: "file_selection_write_detected",
        operationId,
        state: OperationState.WRITE_REQUEST_MAYBE_SENT,
        selectedFile,
        observedRequests: requests,
        observedResponses: responses,
        fileSelectionNetwork,
        confirmClicked: false,
        safety:
          "File selection emitted a write-like request. The confirm button was not clicked; reconcile the task center before any further action.",
      };
    }

    await transitionOperation(
      operationId,
      OperationState.SUBMIT_AUTHORIZED,
      { reason: "current_turn_authorized_confirm_after_quiet_file_selection" },
      directory,
    );
    const confirmButton = await locateImportConfirmButton(page);
    await ensurePageGeometry(page, "before_authorized_import_confirm", {
      writeRequestSent: true,
      allowRepair: false,
    });
    if (!(await confirmButton.isEnabled().catch(() => false))) {
      throw new SubmissionError(
        "IMPORT_CONFIRM_BUTTON_DISABLED",
        "自定义订单导入确定按钮当前不可用。",
      );
    }
    const templateRequestStart = requests.length;
    const templateResponseStart = responses.length;
    await confirmButton.click();
    const mappingDialog = await waitForFieldMappingDialog(page);
    const templateNetwork = await waitForExactNetworkPhase(
      page,
      requests,
      responses,
      classifyTemplateLoadNetwork,
      {
        requestStart: templateRequestStart,
        responseStart: templateResponseStart,
      },
    );
    if (!templateNetwork.safeToContinue) {
      throw new SubmissionError(
        "FIELD_MAPPING_LOAD_NETWORK_UNCERTAIN",
        "第一次确定后的字段模板请求不符合已登记证据，已停止最终提交。",
        { templateNetwork },
      );
    }
    const fieldMappings = await auditFieldMappings(page, mappingDialog);
    const mappingConfirm = await locateFieldMappingConfirmButton(mappingDialog);
    if (!(await mappingConfirm.isEnabled().catch(() => false))) {
      throw new SubmissionError(
        "FIELD_MAPPING_CONFIRM_DISABLED",
        "自定义字段对应确定按钮当前不可用。",
      );
    }
    await ensurePageGeometry(page, "before_authorized_field_mapping_confirm", {
      writeRequestSent: true,
      allowRepair: false,
    });
    const finalRequestStart = requests.length;
    const finalResponseStart = responses.length;
    await mappingConfirm.click();
    await transitionOperation(
      operationId,
      OperationState.WRITE_REQUEST_SENT,
      { reason: "field_mappings_verified_and_final_import_confirm_clicked_once" },
      directory,
    );
    const finalSubmissionNetwork = await waitForExactNetworkPhase(
      page,
      requests,
      responses,
      classifyFinalSubmissionNetwork,
      {
        requestStart: finalRequestStart,
        responseStart: finalResponseStart,
      },
    );
    return {
      status: "submit_maybe_sent",
      operationId,
      state: OperationState.WRITE_REQUEST_SENT,
      orderStatus: form.orderStatus,
      importOptions: form.importOptions,
      selectedFile,
      observedRequests: requests,
      observedResponses: responses,
      fileSelectionNetwork,
      templateNetwork,
      fieldMappings,
      finalSubmissionNetwork,
      initialConfirmClicked: true,
      finalConfirmClicked: true,
      safety:
        "The field mappings were verified and the final confirm was clicked once. Do not retry or reload; reconcile the task center and read the import result.",
    };
  } catch (error) {
    await moveToManualReview(
      operationId,
      "submission_result_uncertain",
      directory,
    ).catch(() => null);
    throw error;
  } finally {
    page.off("request", onRequest);
    page.off("response", onResponse);
  }
}
