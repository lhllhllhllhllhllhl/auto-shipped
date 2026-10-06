#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline/promises";

import { ARTIFACT_DIR, LOGIN_TIMEOUT_MS } from "./config.mjs";
import {
  CredentialError,
  credentialProviderName,
  readStoredCredentials,
} from "./credentials.mjs";
import { ManifestError, verifyUploadManifest } from "./manifest.mjs";
import { GeometryError } from "./geometry.mjs";
import {
  createPreparedOperation,
  OperationStateError,
  recordTaskBaseline,
} from "./operation-state.mjs";
import {
  FILE_SELECTION_AUTHORIZATION,
  FileSelectionError,
  inspectFileSelectionReadiness,
  selectPreparedImportFile,
} from "./file-selection.mjs";
import {
  navigateToCustomImport,
  NavigationError,
  prepareCustomImportForm,
} from "./navigation.mjs";
import { dismissKnownPopups } from "./popup.mjs";
import {
  isLoginUrl,
  launchSession,
  loginWithCredentials,
  markDedicatedLoginWindow,
  openApp,
  requestSmsCode,
  submitSmsCode,
  waitForAuthenticatedPage,
} from "./session.mjs";
import { detectState } from "./states.mjs";
import { captureReadOnlyScreenshot } from "./screenshot.mjs";
import {
  probeImportTaskSnapshot,
  probeCompletedImportTasks,
  probeTaskCenter,
  TaskCenterError,
} from "./task-center.mjs";


function printJson(payload) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
}


function help() {
  printJson({
    usage:
      "node cli.mjs <login|login-auto|login-auto-sms|login-auto-sms-task-center|probe|navigate-import|prepare-import-form|probe-task-center|probe-completed-imports|capture-task-baseline|inspect-file-selection|select-import-file|verify-manifest|prepare-operation> [path]",
    commands: {
      login: "打开专用Chrome配置，由用户手动登录并保存会话。",
      "login-auto": "从当前系统安全凭证提供器读取凭证并登录；不输出凭证。",
      "login-auto-sms":
        "经用户当次确认后请求短信验证码，从标准输入接收验证码并完成登录。",
      "login-auto-sms-task-center":
        "短信登录成功后在同一浏览器进程内执行任务中心只读探针。",
      probe: "只读检查登录和页面状态；只关闭已登记的安全通知。",
      "navigate-import": "只读导航到自定义订单导入页面，不选择文件。",
      "prepare-import-form":
        "导航到自定义订单导入并把订单状态设为待审核；不选择文件、不确认。",
      "probe-task-center":
        "只读导航到任务中心并返回页面结构，不打开任务行。",
      "probe-completed-imports":
        "只读返回已完成的自定义订单导入任务名称、时间和状态。",
      "capture-task-baseline":
        "把当前经过核验的任务 ID 保存到 prepared 操作记录。",
      "inspect-file-selection":
        "复核 manifest、任务基线和订单上传输入框；不选择文件。",
      "select-import-file":
        `真实选择文件但不点确定；需要当次授权口令 ${FILE_SELECTION_AUTHORIZATION}。`,
      "verify-manifest": "校验预检 manifest 与 Excel 哈希，不打开文件选择器。",
      "prepare-operation":
        "校验 manifest 并建立 prepared 操作记录；不选择文件或上传。",
    },
  });
}


async function saveFailureScreenshot(page, prefix) {
  await fs.mkdir(ARTIFACT_DIR, { recursive: true, mode: 0o700 });
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const output = path.join(ARTIFACT_DIR, `${prefix}-${timestamp}.png`);
  const result = await captureReadOnlyScreenshot(page, output).catch(() => ({
    saved: false,
  }));
  return result.saved ? output : null;
}


async function runLogin(context, page) {
  await openApp(page);
  if (!isLoginUrl(page.url())) {
    const observed = await detectState(page);
    printJson({ status: "authenticated", ...observed });
    return 0;
  }
  await page.bringToFront();
  const accountLogin = page.getByRole("tab", {
    name: "账户登录",
    exact: true,
  });
  if (await accountLogin.isVisible().catch(() => false)) {
    await accountLogin.click();
  }
  await markDedicatedLoginWindow(page);
  printJson({
    status: "needs_login",
    instruction: "请在打开的专用Chrome窗口中手动登录管易。",
    timeoutMs: LOGIN_TIMEOUT_MS,
  });
  const authenticatedPage = await waitForAuthenticatedPage(
    context,
    page,
    LOGIN_TIMEOUT_MS,
  );
  const observed = await detectState(authenticatedPage);
  printJson({ status: "authenticated", ...observed });
  return 0;
}


async function runAutoLogin(context, page) {
  await openApp(page);
  if (!isLoginUrl(page.url())) {
    const observed = await detectState(page);
    printJson({ status: "authenticated", ...observed });
    return 0;
  }
  await page.bringToFront();
  await markDedicatedLoginWindow(page);
  const credentials = await readStoredCredentials();
  printJson({ status: "authenticating", credentialSource: credentialProviderName() });
  const authenticatedPage = await loginWithCredentials(
    context,
    page,
    credentials,
    LOGIN_TIMEOUT_MS,
    {
      onVerificationRequired: async () => {
        printJson({
          status: "needs_verification",
          verificationType: "sms",
          instruction:
            "请在尚舆自动化专用登录窗口中点击获取验证码、输入短信码并登录；窗口会继续等待。",
          safety: "验证码不会保存到 Skill、源码、配置或日志。",
        });
      },
    },
  );
  const observed = await detectState(authenticatedPage);
  printJson({ status: "authenticated", ...observed });
  return 0;
}


async function readSmsCode() {
  if (process.stdin.isTTY && typeof process.stdin.setRawMode === "function") {
    const previousRawMode = process.stdin.isRaw;
    process.stdin.setRawMode(true);
    process.stdin.resume();
    try {
      return await new Promise((resolve, reject) => {
        let code = "";
        const onData = (chunk) => {
          for (const character of chunk.toString("utf8")) {
            if (character === "\u0003") {
              process.stdin.off("data", onData);
              reject(new Error("验证码输入已取消。"));
              return;
            }
            if (character === "\r" || character === "\n") {
              process.stdin.off("data", onData);
              resolve(code.trim());
              return;
            }
            if (character === "\u007f" || character === "\b") {
              code = code.slice(0, -1);
              continue;
            }
            code += character;
          }
        };
        process.stdin.on("data", onData);
      });
    } finally {
      process.stdin.setRawMode(Boolean(previousRawMode));
      process.stdin.pause();
    }
  }

  const prompt = createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false,
  });
  try {
    return (await prompt.question("")).trim();
  } finally {
    prompt.close();
  }
}


async function runAutoLoginWithSms(context, page, afterAuthenticated = null) {
  await openApp(page);
  if (!isLoginUrl(page.url())) {
    const observed = await detectState(page);
    printJson({ status: "authenticated", ...observed });
    if (afterAuthenticated) {
      printJson(await afterAuthenticated(page));
    }
    return 0;
  }
  await page.bringToFront();
  await markDedicatedLoginWindow(page);
  const credentials = await readStoredCredentials();
  printJson({ status: "authenticating", credentialSource: credentialProviderName() });
  const authenticatedPage = await loginWithCredentials(
    context,
    page,
    credentials,
    LOGIN_TIMEOUT_MS,
    {
      onVerificationRequired: async (candidate) => {
        const requestResult = await requestSmsCode(candidate);
        printJson({
          status: "sms_code_requested",
          noticeDismissed: requestResult.noticeDismissed,
          instruction: "请把本次短信验证码提供给 Agent。",
          timeoutMs: LOGIN_TIMEOUT_MS,
        });
        const code = await readSmsCode();
        await submitSmsCode(candidate, code);
        printJson({ status: "sms_code_submitted" });
      },
    },
  );
  const observed = await detectState(authenticatedPage);
  printJson({ status: "authenticated", ...observed });
  if (afterAuthenticated) {
    printJson(await afterAuthenticated(authenticatedPage));
  }
  return 0;
}


async function runProbe(page) {
  await openApp(page);
  const before = await detectState(page);
  if (["needs_login", "needs_verification"].includes(before.state)) {
    printJson(before);
    return 2;
  }
  const popups = await dismissKnownPopups(page);
  const after = await detectState(page);
  printJson({
    status: popups.unknown.length ? "popup_unknown" : "ready",
    before: before.state,
    after: after.state,
    url: after.url,
    dismissedPopups: popups.dismissed,
    popupSummaries: popups.unknown,
  });
  return popups.unknown.length ? 3 : 0;
}


async function runNavigateImport(page) {
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  const result = await navigateToCustomImport(page);
  printJson(result);
  return 0;
}


async function runPrepareImportForm(page) {
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  printJson(await prepareCustomImportForm(page));
  return 0;
}


async function runProbeTaskCenter(page) {
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  printJson(await probeTaskCenter(page));
  return 0;
}


async function runProbeCompletedImports(page) {
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  printJson(await probeCompletedImportTasks(page));
  return 0;
}


async function runCaptureTaskBaseline(page, operationId) {
  if (!operationId) {
    throw new OperationStateError(
      "OPERATION_ID_REQUIRED",
      "记录任务基线需要操作 ID。",
    );
  }
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  const tasks = await probeImportTaskSnapshot(page);
  const saved = await recordTaskBaseline(operationId, tasks);
  printJson({
    status: "task_baseline_captured",
    operationId,
    taskCount: saved.record.taskBaseline.taskIds.length,
    capturedAt: saved.record.taskBaseline.capturedAt,
    safety: "No file was selected and no write request was sent.",
  });
  return 0;
}


async function runInspectFileSelection(page, operationId) {
  if (!operationId) {
    throw new OperationStateError(
      "OPERATION_ID_REQUIRED",
      "检查文件选择条件需要操作 ID。",
    );
  }
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  printJson(await inspectFileSelectionReadiness(page, operationId));
  return 0;
}


async function runSelectImportFile(page, operationId, authorization) {
  if (!operationId) {
    throw new OperationStateError(
      "OPERATION_ID_REQUIRED",
      "选择文件需要操作 ID。",
    );
  }
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  printJson(
    await selectPreparedImportFile(
      page,
      operationId,
      authorization,
    ),
  );
  return 0;
}


async function main() {
  const command = process.argv[2];
  if (!command || ["help", "--help", "-h"].includes(command)) {
    help();
    return 0;
  }
  if (
    ![
      "login",
      "login-auto",
      "login-auto-sms",
      "login-auto-sms-task-center",
      "probe",
      "navigate-import",
      "prepare-import-form",
      "probe-task-center",
      "probe-completed-imports",
      "capture-task-baseline",
      "inspect-file-selection",
      "select-import-file",
      "verify-manifest",
      "prepare-operation",
    ].includes(command)
  ) {
    help();
    return 1;
  }

  if (["verify-manifest", "prepare-operation"].includes(command)) {
    const manifestPath = process.argv[3];
    if (!manifestPath) {
      printJson({ status: "failed", code: "MANIFEST_PATH_REQUIRED" });
      return 1;
    }
    try {
      const verified = await verifyUploadManifest(manifestPath);
      if (command === "verify-manifest") {
        printJson(verified);
        return 0;
      }
      const prepared = await createPreparedOperation(verified);
      printJson({
        status: "operation_prepared",
        operationId: prepared.record.operationId,
        state: prepared.record.state,
        receiptPath: prepared.receiptPath,
        orders: prepared.record.counts.orders,
        itemRows: prepared.record.counts.itemRows,
        safety: "No file chooser was opened and no upload was performed.",
      });
      return 0;
    } catch (error) {
      if (error instanceof ManifestError) {
        printJson({
          status: "manifest_rejected",
          code: error.code,
          message: error.message,
        });
        return 2;
      }
      if (error instanceof OperationStateError) {
        printJson({
          status: "operation_rejected",
          code: error.code,
          message: error.message,
        });
        return 2;
      }
      throw error;
    }
  }

  let session;
  try {
    session = await launchSession();
    if (command === "login") {
      return await runLogin(session.context, session.page);
    }
    if (command === "login-auto") {
      return await runAutoLogin(session.context, session.page);
    }
    if (command === "login-auto-sms") {
      return await runAutoLoginWithSms(session.context, session.page);
    }
    if (command === "login-auto-sms-task-center") {
      return await runAutoLoginWithSms(
        session.context,
        session.page,
        async (authenticatedPage) => await probeTaskCenter(authenticatedPage),
      );
    }
    if (command === "probe") {
      return await runProbe(session.page);
    }
    if (command === "probe-task-center") {
      return await runProbeTaskCenter(session.page);
    }
    if (command === "prepare-import-form") {
      return await runPrepareImportForm(session.page);
    }
    if (command === "probe-completed-imports") {
      return await runProbeCompletedImports(session.page);
    }
    if (command === "capture-task-baseline") {
      return await runCaptureTaskBaseline(session.page, process.argv[3]);
    }
    if (command === "inspect-file-selection") {
      return await runInspectFileSelection(session.page, process.argv[3]);
    }
    if (command === "select-import-file") {
      return await runSelectImportFile(
        session.page,
        process.argv[3],
        process.argv[4],
      );
    }
    return await runNavigateImport(session.page);
  } catch (error) {
    const screenshot = session?.page
      ? await saveFailureScreenshot(session.page, "guanyi-probe-failure")
      : null;
    if (error instanceof CredentialError) {
      printJson({
        status: "credential_unavailable",
        code: error.code,
        message: error.message,
        screenshot,
      });
      return 2;
    }
    if (error instanceof GeometryError) {
      printJson({
        status: "geometry_blocked",
        code: error.code,
        message: error.message,
        stage: error.stage,
        details: error.details,
        screenshot,
      });
      return 3;
    }
    if (error instanceof NavigationError) {
      printJson({
        status: "navigation_failed",
        code: error.code,
        message: error.message,
        details: error.details,
        screenshot,
      });
      return 3;
    }
    if (error instanceof TaskCenterError) {
      printJson({
        status: "task_center_failed",
        code: error.code,
        message: error.message,
        details: error.details,
        screenshot,
      });
      return 3;
    }
    if (error instanceof FileSelectionError) {
      printJson({
        status: "file_selection_not_ready",
        code: error.code,
        message: error.message,
        details: error.details,
        screenshot,
      });
      return 3;
    }
    if (error instanceof OperationStateError) {
      printJson({
        status: "operation_rejected",
        code: error.code,
        message: error.message,
        screenshot,
      });
      return 2;
    }
    printJson({
      status: "failed",
      code: error?.name || "Error",
      message: error?.message || String(error),
      screenshot,
    });
    return 1;
  } finally {
    await session?.context.close().catch(() => null);
  }
}


process.exitCode = await main();
