import { ensurePageGeometry } from "./geometry.mjs";
import {
  OperationState,
  OperationStateError,
  loadOperation,
  transitionOperation,
} from "./operation-state.mjs";
import { verifyUploadManifest } from "./manifest.mjs";
import { prepareCustomImportForm } from "./navigation.mjs";


export class FileSelectionError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "FileSelectionError";
    this.code = code;
    this.details = details;
  }
}


export const FILE_SELECTION_AUTHORIZATION =
  "CONFIRM_FILE_SELECTION_MAY_UPLOAD";


export function requireFileSelectionAuthorization(authorization) {
  if (authorization !== FILE_SELECTION_AUTHORIZATION) {
    throw new FileSelectionError(
      "FILE_SELECTION_AUTHORIZATION_REQUIRED",
      "真实文件选择需要用户当次明确授权。",
    );
  }
}


export async function locateOrderInfoFileInput(page) {
  const matches = [];
  for (const frame of page.frames()) {
    const labels = frame.getByText("订单信息上传", { exact: true });
    for (let index = 0; index < (await labels.count()); index += 1) {
      const label = labels.nth(index);
      if (!(await label.isVisible().catch(() => false))) {
        continue;
      }
      const upload = label.locator(
        "xpath=ancestor::span[contains(concat(' ',normalize-space(@class),' '),' ant-upload ')][1]",
      );
      const inputs = upload.locator('input[type="file"]');
      if ((await inputs.count()) === 1) {
        const confirmationRoots = label.locator(
          "xpath=ancestor::*[@role='dialog' or " +
            "contains(concat(' ',normalize-space(@class),' '),' ant-modal-content ')][1]",
        );
        matches.push({
          frame,
          input: inputs.first(),
          trigger: label,
          confirmationRoot:
            (await confirmationRoots.count()) === 1
              ? confirmationRoots.first()
              : null,
        });
      }
    }
  }
  if (matches.length !== 1) {
    throw new FileSelectionError(
      "ORDER_FILE_INPUT_NOT_UNIQUE",
      "无法唯一定位“订单信息上传”的文件输入框。",
      { matchCount: matches.length },
    );
  }
  return matches[0];
}


export function validateFileSelectionOperation(record, verifiedManifest) {
  if (record?.state !== OperationState.PREPARED) {
    throw new OperationStateError(
      "FILE_SELECTION_STATE_INVALID",
      "文件选择只允许从 prepared 状态开始。",
    );
  }
  if (
    !record.taskBaseline ||
    !["verified_task_ids", "verified_empty_list"].includes(
      record.taskBaseline.evidence,
    )
  ) {
    throw new OperationStateError(
      "TASK_BASELINE_REQUIRED",
      "文件选择前必须保存经过核验的任务中心基线。",
    );
  }
  if (
    record.manifest?.path !== verifiedManifest.manifestPath ||
    record.artifact?.path !== verifiedManifest.workbookPath ||
    record.artifact?.sha256 !== verifiedManifest.artifactSha256 ||
    record.artifact?.sizeBytes !== verifiedManifest.artifactSizeBytes
  ) {
    throw new OperationStateError(
      "OPERATION_ARTIFACT_MISMATCH",
      "操作记录与重新校验后的上传文件不一致。",
    );
  }
  return true;
}


async function readSelectedFile(input) {
  return await input.evaluate((element) => {
    const file = element.files?.[0];
    return file
      ? { count: element.files.length, name: file.name, size: file.size }
      : { count: 0, name: null, size: null };
  });
}


async function readUploadListEvidence(confirmationRoot, expectedFileName) {
  if (!confirmationRoot) {
    return { matchCount: 0 };
  }
  return await confirmationRoot.evaluate((root, expected) => {
    const selectors = [
      ".ant-upload-list-item-name",
      ".ant-upload-list-item a",
      ".ant-upload-list-item [title]",
      '[class*="upload-list"] a',
      '[class*="upload-list"] [title]',
    ];
    const matches = new Set();
    for (const element of root.querySelectorAll(selectors.join(","))) {
      const text = element.textContent?.trim() || "";
      const title = element.getAttribute("title")?.trim() || "";
      const style = element.ownerDocument.defaultView?.getComputedStyle(element);
      const visible =
        style?.display !== "none" &&
        style?.visibility !== "hidden" &&
        element.getClientRects().length > 0;
      if (visible && (text === expected || title === expected)) {
        matches.add(element);
      }
    }
    return { matchCount: matches.size };
  }, expectedFileName);
}


export async function setFileInputOnce(
  input,
  artifact,
  { confirmationRoot = null } = {},
) {
  const inputHandle = await input.elementHandle();
  if (!inputHandle) {
    throw new FileSelectionError(
      "FILE_INPUT_NOT_ATTACHED",
      "订单文件输入框当前不可用。",
    );
  }
  const beforeCount = await inputHandle.evaluate(
    (element) => element.files?.length || 0,
  );
  if (beforeCount !== 0) {
    throw new FileSelectionError(
      "FILE_INPUT_ALREADY_POPULATED",
      "订单文件输入框已经包含文件，禁止覆盖或重复选择。",
    );
  }
  await inputHandle.setInputFiles(artifact.path);
  const selected = await readSelectedFile(inputHandle).catch(() => ({
    count: 0,
    name: null,
    size: null,
  }));
  const inputReadbackMatches =
    selected.count !== 1 ||
    selected.name !== artifact.fileName ||
    selected.size !== artifact.sizeBytes
      ? false
      : true;
  if (inputReadbackMatches) {
    return { ...selected, evidence: "input_files" };
  }

  let uploadList = { matchCount: 0 };
  for (let attempt = 0; attempt < 15; attempt += 1) {
    uploadList = await readUploadListEvidence(
      confirmationRoot,
      artifact.fileName,
    ).catch(() => ({ matchCount: 0 }));
    if (uploadList.matchCount !== 0) {
      break;
    }
    await input.page().waitForTimeout(100);
  }
  if (uploadList.matchCount !== 1) {
    throw new FileSelectionError(
      "FILE_SELECTION_NOT_CONFIRMED",
      "文件选择后既无法从原始文件框回读，也无法在当前导入弹窗中唯一确认文件名。",
      { selected, uploadListMatchCount: uploadList.matchCount },
    );
  }
  return {
    count: 1,
    name: artifact.fileName,
    size: artifact.sizeBytes,
    evidence: "upload_list_after_input_replaced",
  };
}


export function safeRequestMetadata(request) {
  try {
    const url = new URL(request.url());
    return {
      method: request.method(),
      origin: url.origin,
      path: url.pathname,
      resourceType: request.resourceType(),
      hasPostData: Boolean(request.postData()),
    };
  } catch {
    return {
      method: request.method(),
      origin: null,
      path: null,
      resourceType: request.resourceType(),
      hasPostData: Boolean(request.postData()),
    };
  }
}


export async function prepareFileSelectionTarget(
  page,
  operationId,
  directory,
) {
  const loaded = await loadOperation(operationId, directory);
  const verified = await verifyUploadManifest(loaded.record.manifest.path);
  validateFileSelectionOperation(loaded.record, verified);
  const form = await prepareCustomImportForm(page);
  const target = await locateOrderInfoFileInput(page);
  const accept = await target.input.getAttribute("accept");
  const fileCount = await target.input.evaluate(
    (element) => element.files?.length || 0,
  );
  if (!accept?.includes(".xlsx") || fileCount !== 0) {
    throw new FileSelectionError(
      "ORDER_FILE_INPUT_NOT_READY",
      "订单文件输入框类型不正确或已存在文件。",
      { accept, fileCount },
    );
  }
  return { loaded, verified, form, target, accept };
}


export async function inspectFileSelectionReadiness(
  page,
  operationId,
  directory,
) {
  const { loaded, form } = await prepareFileSelectionTarget(
    page,
    operationId,
    directory,
  );
  return {
    status: "file_selection_ready",
    operationId,
    artifactFileName: loaded.record.artifact.fileName,
    artifactSha256: loaded.record.artifact.sha256,
    orderStatus: form.orderStatus,
    taskBaselineCount: loaded.record.taskBaseline.taskIds.length,
    safety: "The correct file input was located, but no file was selected.",
  };
}


export async function selectPreparedImportFile(
  page,
  operationId,
  authorization,
  directory,
) {
  requireFileSelectionAuthorization(authorization);
  const { loaded, form, target } = await prepareFileSelectionTarget(
    page,
    operationId,
    directory,
  );
  await ensurePageGeometry(page, "before_authorized_file_selection");

  await transitionOperation(
    operationId,
    OperationState.FILE_SELECTION_STARTED,
    { reason: "current_turn_authorized_file_selection" },
    directory,
  );

  const requests = [];
  const onRequest = (request) => requests.push(safeRequestMetadata(request));
  page.on("request", onRequest);
  let selection;
  try {
    let selectionError = null;
    try {
      selection = await setFileInputOnce(
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
      { reason: "file_input_change_attempted" },
      directory,
    );
    await page.waitForTimeout(1_000);
    if (selectionError) {
      throw selectionError;
    }
  } catch (error) {
    await transitionOperation(
      operationId,
      OperationState.NEEDS_MANUAL_REVIEW,
      {
        reason: "file_selection_result_uncertain",
        failureCode: error?.code || error?.name || "FILE_SELECTION_FAILED",
      },
      directory,
    ).catch(() => null);
    throw error;
  } finally {
    page.off("request", onRequest);
  }

  return {
    status: "file_selected_write_status_unknown",
    operationId,
    orderStatus: form.orderStatus,
    selectedFile: selection,
    observedRequests: requests,
    state: OperationState.WRITE_REQUEST_MAYBE_SENT,
    safety:
      "The file input changed. Do not retry, reload, or click confirm until task-center reconciliation is complete.",
  };
}
