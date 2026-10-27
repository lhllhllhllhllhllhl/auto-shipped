#!/usr/bin/env node

import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

import {
  ARTIFACT_DIR,
  HUMAN_TAKEOVER_AVAILABLE,
  HUMAN_VERIFICATION_TIMEOUT_MS,
  LOGIN_RESULT_TIMEOUT_MS,
  LOGIN_TIMEOUT_MS,
  RESIDENT_SESSION_TTL_MS,
  RESIDENT_SUBMISSION_INTERVAL_MS,
} from "./config.mjs";
import {
  CredentialError,
  credentialProviderName,
  readStoredCredentials,
} from "./credentials.mjs";
import { ManifestError, verifyUploadManifest } from "./manifest.mjs";
import { GeometryError } from "./geometry.mjs";
import {
  createLoginControlServer,
  createLoginOperation,
  findActiveLoginOperation,
  isLoginWorkerAlive,
  loadLoginOperation,
  LoginOperationError,
  LoginOperationState,
  publicLoginOperation,
  requireSmsRequestAuthorization,
  requireTemporaryCredentialsSubmitAuthorization,
  setLoginWorkerProcess,
  sendLoginControl,
  SMS_REQUEST_AUTHORIZATION,
  TEMPORARY_CREDENTIALS_SUBMIT_AUTHORIZATION,
  transitionLoginOperation,
  waitForLoginOperation,
} from "./login-operation.mjs";
import {
  createResidentCommandQueue,
  createResidentSubmissionCadence,
  ResidentCommand,
  ResidentSessionError,
} from "./resident-session.mjs";
import {
  acquireBrowserOperationLease,
  withBrowserOperationLease,
} from "./browser-operation-broker.mjs";
import {
  createPreparedOperation,
  NO_TASK_RESOLUTION_AUTHORIZATION,
  OperationStateError,
  recordTaskBaseline,
  resolveNoTaskManualReview,
} from "./operation-state.mjs";
import {
  FILE_SELECTION_AUTHORIZATION,
  FileSelectionError,
  inspectFileSelectionReadiness,
  selectPreparedImportFile,
} from "./file-selection.mjs";
import {
  requireSubmitAuthorization,
  SUBMIT_AUTHORIZATION,
  SubmissionError,
  submitPreparedImport,
} from "./submission.mjs";
import {
  navigateToCustomImport,
  NavigationError,
  prepareCustomImportForm,
} from "./navigation.mjs";
import { dismissKnownPopups } from "./popup.mjs";
import {
  isLoginUrl,
  fillPreparedCredentialLogin,
  launchSession,
  loginWithCredentials,
  markDedicatedLoginWindow,
  openApp,
  requestSmsCode,
  SessionError,
  submitPreparedCredentialLogin,
  submitSmsCode,
  waitForAuthenticatedPage,
} from "./session.mjs";
import { focusHumanVerificationWindow } from "./human-verification.mjs";
import { detectState } from "./states.mjs";
import { captureReadOnlyScreenshot } from "./screenshot.mjs";
import {
  probeImportTaskSnapshot,
  probeCompletedImportTasks,
  probeTaskCenter,
  TaskCenterError,
} from "./task-center.mjs";
import {
  executeAuthorizedImportInSingleSession,
  reconcileImportInCurrentSession,
  workflowExitCode,
} from "./workflow.mjs";
import {
  executeAuthorizedOrderAudit,
  OrderAuditError,
  probeOrderAuditSurface,
  reconcileOrderAudit,
} from "./order-audit.mjs";
import {
  AuditOperationError,
  createPreparedAuditOperation,
} from "./order-audit-state.mjs";
import { AuditPolicyError } from "./audit-policy.mjs";


function printJson(payload) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
}


const LOGIN_SESSION_ERROR_CODES = new Set([
  "SMS_CODE_REJECTED",
  "LOGIN_RISK_CONTROLLED",
  "LOGIN_CREDENTIALS_REJECTED",
  "LOGIN_ADDITIONAL_STEP_REQUIRED",
  "LOGIN_HUMAN_VERIFICATION_INTERRUPTED",
  "LOGIN_HUMAN_VERIFICATION_TIMEOUT",
  "LOGIN_OUTCOME_UNCONFIRMED",
  "LOGIN_LOCAL_VERIFICATION_REQUIRED",
]);


function sessionErrorStatus(error) {
  if (error.code === "BROWSER_SESSION_BUSY") return "browser_session_busy";
  if (String(error.code || "").startsWith("PLATFORM_CDP")) {
    return "platform_browser_unavailable";
  }
  if (error.code === "PLATFORM_BROWSER_DISCONNECTED") {
    return "platform_browser_unavailable";
  }
  if (error.code === "LOGIN_LOCAL_VERIFICATION_REQUIRED") {
    return "local_verification_required";
  }
  if (error.code === "LOGIN_RISK_CONTROLLED") return "login_blocked";
  if (LOGIN_SESSION_ERROR_CODES.has(error.code)) return "login_failed";
  return "access_blocked";
}


function help() {
  printJson({
    usage:
      "node cli.mjs <login|login-auto|login-auto-start|login-agent-start|login-credentials-submit|login-status|login-sms-request|login-sms-submit|login-auto-sms|resident-start|resident-status|resident-probe|resident-execute-import|resident-reconcile|resident-resolve-no-task|probe|navigate-import|prepare-import-form|probe-task-center|probe-completed-imports|capture-task-baseline|inspect-file-selection|select-import-file|submit-import|execute-import|reconcile-operation|resolve-no-task|verify-manifest|prepare-operation|probe-order-audit|prepare-order-audit|execute-order-audit|reconcile-order-audit> [arguments]",
    commands: {
      login: "打开当前管易浏览器会话，由用户手动登录。",
      "login-auto": "从当前系统安全凭证提供器读取凭证并登录；不输出凭证。",
      "login-auto-start":
        "启动可跨消息恢复的自动登录；需要短信时先返回登录操作 ID，不发送短信。",
      "login-agent-start":
        "云端无安全凭证库时启动同一登录状态机，等待 Agent 通过标准输入提交本次临时凭据。",
      "login-credentials-submit":
        `从标准输入读取一次性账号密码 JSON 并交给当前登录进程；需要口令 ${TEMPORARY_CREDENTIALS_SUBMIT_AUTHORIZATION}。`,
      "login-status": "读取登录操作状态；不启动第二个浏览器。",
      "login-sms-request":
        `用户确认可以接收短信后，请求验证码；需要口令 ${SMS_REQUEST_AUTHORIZATION}。`,
      "login-sms-submit":
        "从标准输入接收一次性验证码并交给仍在运行的登录操作。",
      "login-auto-sms":
        "经用户当次确认后请求短信验证码，从标准输入接收验证码并完成登录。",
      "login-auto-sms-task-center":
        "短信登录成功后在同一浏览器进程内执行任务中心只读探针。",
      "resident-start":
        "启动或复用服务器唯一的常驻管易浏览器；登录后保持运行并串行处理任务。",
      "resident-status": "读取常驻浏览器状态，不启动第二个浏览器。",
      "resident-execute-import":
        `把已授权批次排入常驻浏览器；需要当次授权口令 ${SUBMIT_AUTHORIZATION}。`,
      "resident-reconcile": "在常驻浏览器中只读核验一个可能已提交的批次。",
      "resident-resolve-no-task":
        `任务中心确认没有新增任务且最终请求未发出时关闭本次尝试；需要口令 ${NO_TASK_RESOLUTION_AUTHORIZATION}。`,
      "resident-probe": "在常驻浏览器中只读检查登录与页面状态。",
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
      "submit-import":
        `在同一浏览器会话内选择文件并受控提交；需要当次授权口令 ${SUBMIT_AUTHORIZATION}。`,
      "execute-import":
        `用户确认当前批次后，在一个浏览器进程内完成表单、任务基线、受控提交和结果回读；需要当次授权口令 ${SUBMIT_AUTHORIZATION}。`,
      "reconcile-operation":
        "只读轮询任务中心，以唯一任务 ID、类型和状态核验提交结果。",
      "resolve-no-task":
        `任务中心确认没有新增任务后把旧操作标为可重试；需要口令 ${NO_TASK_RESOLUTION_AUTHORIZATION}。`,
      "verify-manifest":
        "校验预检 manifest 与当前 Excel 的文件名、大小和哈希；可显式传入迁移后的当前路径，不打开文件选择器。",
      "prepare-operation":
        "校验 manifest、绑定当前 Excel 路径并建立 prepared 操作记录；不选择文件或上传。",
      "probe-order-audit":
        "只读检查订单查询中的审核列、筛选框、全选框和审核按钮；不筛选、不勾选、不审核。",
      "prepare-order-audit":
        "在导入结果全部成功后建立待审核操作；默认范围 all_pending，current_batch 为特殊范围。",
      "execute-order-audit":
        "用户在上传成功后另行批准，才审核已准备范围；需要口令 CONFIRM_GUANYI_ORDER_AUDIT。",
      "reconcile-order-audit":
        "审核请求可能发出后，只读核验该范围是否仍有待审核订单，不重提审核。",
    },
  });
}


function commandMailbox(operationId) {
  const queued = new Map();
  const waiters = new Map();
  const pendingActions = new Set();

  function deliver(action, item) {
    const waiter = waiters.get(action);
    if (waiter) {
      waiters.delete(action);
      clearTimeout(waiter.timer);
      waiter.resolve(item);
      return;
    }
    const items = queued.get(action) || [];
    items.push(item);
    queued.set(action, items);
  }

  return {
    async handle({ action, code, credentials }) {
      const loaded = await loadLoginOperation(operationId);
      if (
        action === "submit_credentials" &&
        loaded.record.state !== LoginOperationState.WAITING_FOR_CREDENTIALS
      ) {
        throw new LoginOperationError(
          "TEMPORARY_CREDENTIALS_STATE_INVALID",
          "当前登录操作不在等待临时账号密码状态。",
        );
      }
      if (
        action === "request_sms" &&
        loaded.record.state !== LoginOperationState.NEEDS_VERIFICATION
      ) {
        throw new LoginOperationError(
          "SMS_REQUEST_STATE_INVALID",
          "当前登录操作不在等待短信确认状态。",
        );
      }
      if (
        action === "submit_sms" &&
        loaded.record.state !== LoginOperationState.WAITING_FOR_USER_CODE
      ) {
        throw new LoginOperationError(
          "SMS_SUBMIT_STATE_INVALID",
          "当前登录操作不在等待验证码状态。",
        );
      }
      if (action === "submit_sms" && !/^\d{4,8}$/.test(code || "")) {
        throw new LoginOperationError(
          "SMS_CODE_INVALID",
          "短信验证码格式不正确，应为 4 到 8 位数字。",
        );
      }
      if (
        action === "submit_credentials" &&
        (!credentials ||
          typeof credentials.username !== "string" ||
          credentials.username.length < 1 ||
          credentials.username.length > 256 ||
          typeof credentials.password !== "string" ||
          credentials.password.length < 1 ||
          credentials.password.length > 1024)
      ) {
        throw new LoginOperationError(
          "TEMPORARY_CREDENTIALS_INVALID",
          "临时账号或密码格式不正确。",
        );
      }
      if (
        ![
          "submit_credentials",
          "request_sms",
          "submit_sms",
        ].includes(action)
      ) {
        throw new LoginOperationError(
          "LOGIN_CONTROL_ACTION_INVALID",
          "登录控制动作不受支持。",
        );
      }
      if (pendingActions.has(action)) {
        throw new LoginOperationError(
          "LOGIN_CONTROL_ACTION_ALREADY_PENDING",
          "相同的登录控制动作已经在处理中。",
        );
      }
      pendingActions.add(action);
      return await new Promise((resolve, reject) => {
        deliver(action, {
          action,
          code,
          credentials,
          resolve(value) {
            pendingActions.delete(action);
            resolve(value);
          },
          reject(error) {
            pendingActions.delete(action);
            reject(error);
          },
        });
      });
    },

    async take(action, timeoutMs) {
      const items = queued.get(action) || [];
      if (items.length) {
        const item = items.shift();
        queued.set(action, items);
        return item;
      }
      return await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          waiters.delete(action);
          reject(
            new LoginOperationError(
              "LOGIN_OPERATION_EXPIRED",
              "等待用户处理短信验证超时，请重新开始登录。",
            ),
          );
        }, timeoutMs);
        waiters.set(action, { resolve, reject, timer });
      });
    },
  };
}


async function probeInResidentSession(page) {
  await openApp(page);
  const before = await detectState(page);
  if (["needs_login", "needs_verification"].includes(before.state)) {
    return before;
  }
  const popups = await dismissKnownPopups(page);
  const after = await detectState(page);
  return {
    status: popups.unknown.length ? "popup_unknown" : "ready",
    before: before.state,
    after: after.state,
    url: after.url,
    dismissedPopups: popups.dismissed,
    popupSummaries: popups.unknown,
  };
}


const RESIDENT_TASK_OBSERVER_NAME = "shangyu_guanyi_task_observer_v1";


async function createResidentTaskObserver(session, writerPage) {
  for (const candidate of session.context.pages()) {
    if (candidate === writerPage || candidate.isClosed()) continue;
    const name = await candidate
      .evaluate(() => globalThis.name || "")
      .catch(() => "");
    if (name === RESIDENT_TASK_OBSERVER_NAME) {
      await openApp(candidate);
      await writerPage.bringToFront();
      return candidate;
    }
  }
  const observerPage = await session.context.newPage();
  await observerPage.evaluate(
    (name) => {
      globalThis.name = name;
    },
    RESIDENT_TASK_OBSERVER_NAME,
  );
  await openApp(observerPage);
  const observed = await detectState(observerPage);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    await observerPage.close({ runBeforeUnload: false }).catch(() => null);
    throw new SessionError(
      "RESIDENT_TASK_OBSERVER_LOGIN_EXPIRED",
      "只读任务中心页面没有复用到当前登录会话；已停止，未执行上传。",
    );
  }
  await writerPage.bringToFront();
  return observerPage;
}


async function serveResidentCommands(
  operationId,
  session,
  authenticatedPage,
  residentQueue,
  startupLease = null,
) {
  const page = authenticatedPage;
  let taskObserverPage;
  const submissionCadence = createResidentSubmissionCadence({
    minimumIntervalMs: RESIDENT_SUBMISSION_INTERVAL_MS,
  });
  try {
    taskObserverPage = await createResidentTaskObserver(session, page);
    await transitionLoginOperation(
      operationId,
      LoginOperationState.RESIDENT_READY,
      {
        reason: "resident_browser_authenticated",
        expiresInMs: RESIDENT_SESSION_TTL_MS,
      },
    );
  } finally {
    await startupLease?.release();
  }
  while (true) {
    const item = await residentQueue.take();
    await transitionLoginOperation(
      operationId,
      LoginOperationState.RESIDENT_EXECUTING,
      {
        reason: `resident_command_${item.command}`,
        expiresInMs: RESIDENT_SESSION_TTL_MS,
      },
    );
    try {
      const brokerOperationId =
        item.browserOperationId || `${operationId}.${item.command}`;
      const result = await withBrowserOperationLease(
        brokerOperationId,
        async () => {
          let commandResult;
          if (item.command === ResidentCommand.PROBE) {
            commandResult = await probeInResidentSession(page);
            if (
              ["needs_login", "needs_verification"].includes(commandResult.state)
            ) {
              throw new SessionError(
                "RESIDENT_SESSION_LOGIN_EXPIRED",
                "常驻管易浏览器的登录状态已失效；请重新建立会话。",
              );
            }
          } else if (item.command === ResidentCommand.EXECUTE_IMPORT) {
            const observed = await probeInResidentSession(page);
            if (["needs_login", "needs_verification"].includes(observed.state)) {
              throw new SessionError(
                "RESIDENT_SESSION_LOGIN_EXPIRED",
                "常驻管易浏览器的登录状态已失效；本批尚未选择文件，请重新登录后再执行。",
              );
            }
            if (!taskObserverPage || taskObserverPage.isClosed()) {
              taskObserverPage = await createResidentTaskObserver(session, page);
            }
            const cadence = await submissionCadence.waitForNextAttempt();
            submissionCadence.markAttemptStarted();
            commandResult = await executeAuthorizedImportInSingleSession(
              page,
              item.browserOperationId,
              item.authorization,
              {
                taskCenterPage: taskObserverPage,
                onProgress: async (event) => {
                  if (
                    ["submit_maybe_sent", "file_selection_write_detected"].includes(
                      event?.status,
                    )
                  ) {
                    submissionCadence.markSubmissionObserved();
                  }
                },
              },
            );
            commandResult = {
              ...commandResult,
              queuePolicy: {
                writerPage: "dedicated_upload_page",
                taskObserverPage: "same_context_read_only_page",
                minimumSubmissionIntervalMs: RESIDENT_SUBMISSION_INTERVAL_MS,
                cadenceWaitedMs: cadence.waitedMs,
                crossServiceBroker: "server_capacity_1",
              },
            };
          } else if (item.command === ResidentCommand.RESOLVE_NO_TASK) {
            if (!taskObserverPage || taskObserverPage.isClosed()) {
              taskObserverPage = await createResidentTaskObserver(session, page);
            }
            commandResult = await resolveNoTaskInCurrentSession(
              taskObserverPage,
              item.browserOperationId,
              item.authorization,
            );
          } else {
            if (!taskObserverPage || taskObserverPage.isClosed()) {
              taskObserverPage = await createResidentTaskObserver(session, page);
            }
            commandResult = await reconcileImportInCurrentSession(
              taskObserverPage,
              item.browserOperationId,
            );
          }
          return commandResult;
        },
      );
      item.resolve(result);
    } catch (error) {
      item.reject(error);
      if (
        error?.code === "RESIDENT_SESSION_LOGIN_EXPIRED" ||
        page.isClosed() ||
        (session.browser && !session.browser.isConnected())
      ) {
        throw error;
      }
    } finally {
      const loaded = await loadLoginOperation(operationId).catch(() => null);
      if (loaded?.record.state === LoginOperationState.RESIDENT_EXECUTING) {
        await transitionLoginOperation(
          operationId,
          LoginOperationState.RESIDENT_READY,
          {
            reason: "resident_command_finished",
            expiresInMs: RESIDENT_SESSION_TTL_MS,
          },
        ).catch(() => null);
      }
    }
  }
}


async function runLoginAutoWorker(
  operationId,
  credentialMode = "stored",
  workerMode = "one_shot",
) {
  if (!["stored", "agent_input", "auto"].includes(credentialMode)) {
    throw new LoginOperationError(
      "LOGIN_CREDENTIAL_MODE_INVALID",
      "登录凭据模式不受支持。",
    );
  }
  const mailbox = commandMailbox(operationId);
  const residentQueue = createResidentCommandQueue();
  const resident = workerMode === "resident";
  let server;
  let session;
  let pendingCommand = null;
  let startupLease = null;
  try {
    server = await createLoginControlServer(
      operationId,
      async (message) =>
        message.action === "resident_command"
          ? await residentQueue.enqueue(message)
          : await mailbox.handle(message),
    );
    await setLoginWorkerProcess(operationId);
    await transitionLoginOperation(
      operationId,
      LoginOperationState.AUTHENTICATING,
      { reason: "login_worker_started" },
    );
    startupLease = await acquireBrowserOperationLease(`${operationId}.login`);
    session = await launchSession();
    await openApp(session.page);
    if (!isLoginUrl(session.page.url())) {
      if (resident) {
        return await serveResidentCommands(
          operationId,
          session,
          session.page,
          residentQueue,
          startupLease,
        );
      }
      await transitionLoginOperation(operationId, LoginOperationState.AUTHENTICATED, {
        reason: "browser_session_authenticated",
      });
      return 0;
    }
    await session.page.bringToFront();
    await markDedicatedLoginWindow(session.page);
    const loginOptions = {
      onHumanVerificationRequired: async (candidate, observation) => {
        if (
          session.backend === "platform_cdp" ||
          !HUMAN_TAKEOVER_AVAILABLE
        ) {
          throw new SessionError(
            "LOGIN_LOCAL_VERIFICATION_REQUIRED",
            "远程登录遇到阿里云额外验证；请用户或管理员先在自己的可信电脑完整登录管易并通过验证。",
            {
              humanVerificationType: observation.humanVerificationType,
              safeUrl: observation.safeUrl,
              requiredAction: "complete_login_in_trusted_local_browser",
              resumePolicy: "confirm_then_probe_and_single_retry",
            },
          );
        }
        const focus = await focusHumanVerificationWindow(candidate);
        await transitionLoginOperation(
          operationId,
          LoginOperationState.WAITING_FOR_HUMAN_VERIFICATION,
          {
            reason: "registered_human_verification_required",
            expiresInMs: HUMAN_VERIFICATION_TIMEOUT_MS,
            humanVerification: {
              type: observation.humanVerificationType,
              safeUrl: observation.safeUrl,
              focusReady: focus.focusReady,
              focusMethod: focus.method,
              focusCode: focus.code || null,
              browserBackend: session.backend,
            },
          },
        );
      },
      onVerificationRequired: async (candidate) => {
        const verification = await transitionLoginOperation(
          operationId,
          LoginOperationState.NEEDS_VERIFICATION,
          {
            reason: "sms_verification_required",
            expiresInMs: LOGIN_TIMEOUT_MS,
          },
        );
        pendingCommand = await mailbox.take(
          "request_sms",
          Math.max(Date.parse(verification.record.expiresAt) - Date.now(), 1),
        );
        try {
          await transitionLoginOperation(
            operationId,
            LoginOperationState.SMS_REQUESTING,
            { reason: "current_user_confirmed_sms_request" },
          );
          await requestSmsCode(candidate);
          const waiting = await transitionLoginOperation(
            operationId,
            LoginOperationState.WAITING_FOR_USER_CODE,
            {
              reason: "sms_requested_and_notice_dismissed",
              expiresInMs: LOGIN_TIMEOUT_MS,
            },
          );
          pendingCommand.resolve(publicLoginOperation(waiting.record));
          pendingCommand = await mailbox.take(
            "submit_sms",
            Math.max(Date.parse(waiting.record.expiresAt) - Date.now(), 1),
          );
          await submitSmsCode(candidate, pendingCommand.code);
          const submitted = await transitionLoginOperation(
            operationId,
            LoginOperationState.CODE_SUBMITTED,
            {
              reason: "one_time_sms_code_submitted",
              expiresInMs: LOGIN_RESULT_TIMEOUT_MS + 10_000,
            },
          );
          pendingCommand.resolve(publicLoginOperation(submitted.record));
          pendingCommand = null;
          return { verificationSubmitted: true };
        } catch (error) {
          pendingCommand?.reject(error);
          pendingCommand = null;
          throw error;
        }
      },
    };
    let authenticatedPage;
    let selectedCredentialMode = credentialMode;
    let storedCredentials = null;
    if (credentialMode === "auto") {
      try {
        storedCredentials = await readStoredCredentials();
        selectedCredentialMode = "stored";
      } catch (error) {
        if (!(error instanceof CredentialError)) throw error;
        selectedCredentialMode = "agent_input";
      }
    }
    if (selectedCredentialMode === "agent_input") {
      const waiting = await transitionLoginOperation(
        operationId,
        LoginOperationState.WAITING_FOR_CREDENTIALS,
        {
          reason: "temporary_agent_credentials_required",
          expiresInMs: LOGIN_TIMEOUT_MS,
        },
      );
      pendingCommand = await mailbox.take(
        "submit_credentials",
        Math.max(Date.parse(waiting.record.expiresAt) - Date.now(), 1),
      );
      try {
        const authenticating = await transitionLoginOperation(
          operationId,
          LoginOperationState.AUTHENTICATING,
          {
            reason: "temporary_agent_credentials_filled",
            expiresInMs: LOGIN_TIMEOUT_MS,
          },
        );
        if (pendingCommand.action === "submit_credentials") {
          const temporaryCredentials = pendingCommand.credentials;
          const prepared = await fillPreparedCredentialLogin(
            session.page,
            temporaryCredentials,
          );
          pendingCommand.credentials = null;
          temporaryCredentials.username = "";
          temporaryCredentials.password = "";
          await submitPreparedCredentialLogin(session.page, prepared);
        } else {
          throw new LoginOperationError(
            "TEMPORARY_CREDENTIALS_INPUT_REQUIRED",
            "当前云端登录操作必须通过标准输入提交临时凭据。",
          );
        }
        pendingCommand.resolve(publicLoginOperation(authenticating.record));
        pendingCommand = null;
        authenticatedPage = await waitForAuthenticatedPage(
          session.context,
          session.page,
          LOGIN_TIMEOUT_MS,
          { ...loginOptions, credentialsSubmitted: true },
        );
      } catch (error) {
        pendingCommand?.reject(error);
        pendingCommand = null;
        throw error;
      }
    } else {
      const credentials = storedCredentials || (await readStoredCredentials());
      try {
        authenticatedPage = await loginWithCredentials(
          session.context,
          session.page,
          credentials,
          LOGIN_TIMEOUT_MS,
          loginOptions,
        );
      } finally {
        credentials.username = "";
        credentials.password = "";
      }
    }
    if (resident) {
      return await serveResidentCommands(
        operationId,
        session,
        authenticatedPage,
        residentQueue,
        startupLease,
      );
    }
    await transitionLoginOperation(
      operationId,
      LoginOperationState.AUTHENTICATED,
      { reason: "authenticated_page_verified" },
    );
    return 0;
  } catch (caughtError) {
    const error =
      session?.backend === "platform_cdp" &&
      session.browser &&
      !session.browser.isConnected()
        ? new SessionError(
            "PLATFORM_BROWSER_DISCONNECTED",
            "豆包平台浏览器连接已中断；已停止，未自动重试登录或上传。",
          )
        : caughtError;
    pendingCommand?.reject(error);
    const loaded = await loadLoginOperation(operationId).catch(() => null);
    if (
      loaded &&
      ![
        LoginOperationState.AUTHENTICATED,
        LoginOperationState.FAILED,
        LoginOperationState.EXPIRED,
      ].includes(loaded.record.state)
    ) {
      await transitionLoginOperation(
        operationId,
        error?.code === "LOGIN_OPERATION_EXPIRED"
          ? LoginOperationState.EXPIRED
          : LoginOperationState.FAILED,
        {
          reason: "login_worker_failed",
          code: error?.code || error?.name || "LOGIN_FAILED",
          message: error?.message || "管易登录失败。",
          details: error?.details || null,
        },
      ).catch(() => null);
    }
    return 1;
  } finally {
    await startupLease?.release();
    await new Promise((resolve) => server?.close(resolve) || resolve());
    await session?.close?.().catch(() => null);
  }
}


async function runResidentStart() {
  const active = await findActiveLoginOperation();
  if (active) {
    if (isLoginWorkerAlive(active)) {
      printJson(publicLoginOperation(active));
      return active.state === LoginOperationState.FAILED ? 2 : 0;
    }
    await transitionLoginOperation(
      active.operationId,
      LoginOperationState.FAILED,
      {
        reason: "resident_worker_not_alive",
        code: "RESIDENT_WORKER_EXITED",
        message: "常驻管易浏览器进程已退出；可以安全启动新的常驻会话。",
      },
    ).catch(() => null);
  }
  const credentialMode = "auto";
  const created = await createLoginOperation();
  const child = spawn(
    process.execPath,
    [
      fileURLToPath(import.meta.url),
      "login-auto-worker",
      created.record.operationId,
      credentialMode,
      "resident",
    ],
    {
      detached: true,
      env: process.env,
      stdio: "ignore",
      windowsHide: true,
    },
  );
  child.once("error", async (error) => {
    await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.FAILED,
      {
        reason: "resident_worker_spawn_failed",
        code: error?.code || "RESIDENT_WORKER_SPAWN_FAILED",
        message: "无法启动常驻管易浏览器进程。",
      },
    ).catch(() => null);
  });
  child.once("exit", async (code) => {
    if (code === 0) return;
    const loaded = await loadLoginOperation(created.record.operationId).catch(
      () => null,
    );
    if (
      loaded &&
      ![
        LoginOperationState.FAILED,
        LoginOperationState.EXPIRED,
      ].includes(loaded.record.state)
    ) {
      await transitionLoginOperation(
        created.record.operationId,
        LoginOperationState.FAILED,
        {
          reason: "resident_worker_exited",
          code: "RESIDENT_WORKER_EXITED",
          message: "常驻管易浏览器进程提前结束。",
        },
      ).catch(() => null);
    }
  });
  child.unref();
  const loaded = await waitForLoginOperation(created.record.operationId, {
    timeoutMs: 30_000,
  });
  printJson(publicLoginOperation(loaded.record));
  return loaded.record.state === LoginOperationState.FAILED ? 2 : 0;
}


async function currentResidentOperation() {
  const record = await findActiveLoginOperation();
  if (!record || !isLoginWorkerAlive(record)) {
    return null;
  }
  return record;
}


async function runResidentStatus() {
  const record = await currentResidentOperation();
  if (!record) {
    printJson({
      status: "needs_login",
      browserMode: "resident_single_owner",
      instruction: "常驻管易浏览器尚未运行，请先启动登录。",
    });
    return 2;
  }
  printJson(publicLoginOperation(record));
  return 0;
}


async function sendResidentCommand(command, browserOperationId, authorization) {
  const record = await currentResidentOperation();
  if (!record) {
    printJson({
      status: "needs_login",
      browserMode: "resident_single_owner",
      safety: "No browser write was attempted.",
    });
    return 2;
  }
  if (
    ![
      LoginOperationState.RESIDENT_READY,
      LoginOperationState.RESIDENT_EXECUTING,
    ].includes(record.state)
  ) {
    printJson(publicLoginOperation(record));
    return 2;
  }
  const result = await sendLoginControl(
    record.operationId,
    "resident_command",
    {
      command,
      browserOperationId,
      authorization,
      timeoutMs: 15 * 60_000,
    },
  );
  printJson(result);
  return command === ResidentCommand.EXECUTE_IMPORT
    ? workflowExitCode(result)
    : 0;
}


async function runLoginAutoStart(credentialMode = "stored") {
  const created = await createLoginOperation();
  const child = spawn(
    process.execPath,
    [
      fileURLToPath(import.meta.url),
      "login-auto-worker",
      created.record.operationId,
      credentialMode,
    ],
    {
      detached: true,
      env: process.env,
      stdio: "ignore",
      windowsHide: true,
    },
  );
  child.once("error", async (error) => {
    await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.FAILED,
      {
        reason: "login_worker_spawn_failed",
        code: error?.code || "LOGIN_WORKER_SPAWN_FAILED",
        message: "无法启动管易登录进程。",
      },
    ).catch(() => null);
  });
  child.once("exit", async (code) => {
    if (code === 0) return;
    const loaded = await loadLoginOperation(created.record.operationId).catch(
      () => null,
    );
    if (
      loaded &&
      ![
        LoginOperationState.AUTHENTICATED,
        LoginOperationState.FAILED,
        LoginOperationState.EXPIRED,
      ].includes(loaded.record.state)
    ) {
      await transitionLoginOperation(
        created.record.operationId,
        LoginOperationState.FAILED,
        {
          reason: "login_worker_exited",
          code: "LOGIN_WORKER_EXITED",
          message: "管易登录进程提前结束。",
        },
      ).catch(() => null);
    }
  });
  child.unref();
  const loaded = await waitForLoginOperation(created.record.operationId, {
    timeoutMs: 30_000,
  });
  printJson(publicLoginOperation(loaded.record));
  return loaded.record.state === LoginOperationState.FAILED ? 2 : 0;
}


async function readTemporaryCredentials() {
  const prompt = createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false,
  });
  try {
    const line = (await prompt.question("")).trim();
    let parsed;
    try {
      parsed = JSON.parse(line);
    } catch {
      throw new LoginOperationError(
        "TEMPORARY_CREDENTIALS_INPUT_INVALID",
        "标准输入必须是一行包含 username 和 password 的 JSON。",
      );
    }
    if (
      typeof parsed?.username !== "string" ||
      !parsed.username ||
      typeof parsed?.password !== "string" ||
      !parsed.password
    ) {
      throw new LoginOperationError(
        "TEMPORARY_CREDENTIALS_INPUT_INVALID",
        "标准输入中的账号或密码为空。",
      );
    }
    return { username: parsed.username, password: parsed.password };
  } finally {
    prompt.close();
  }
}


async function runLoginCredentialsSubmit(operationId, authorization) {
  requireTemporaryCredentialsSubmitAuthorization(authorization);
  const credentials = await readTemporaryCredentials();
  try {
    const result = await sendLoginControl(operationId, "submit_credentials", {
      credentials,
    });
    printJson(result);
    return 0;
  } finally {
    credentials.username = "";
    credentials.password = "";
  }
}


async function runLoginStatus(operationId) {
  let loaded = await loadLoginOperation(operationId);
  const workerAlive = isLoginWorkerAlive(loaded.record);
  if (
    workerAlive === false &&
    ![
      LoginOperationState.AUTHENTICATED,
      LoginOperationState.FAILED,
      LoginOperationState.EXPIRED,
    ].includes(loaded.record.state)
  ) {
    const interruptedHumanVerification =
      loaded.record.state ===
      LoginOperationState.WAITING_FOR_HUMAN_VERIFICATION;
    loaded = await transitionLoginOperation(
      operationId,
      LoginOperationState.FAILED,
      {
        reason: "login_worker_not_alive",
        code: interruptedHumanVerification
          ? "LOGIN_HUMAN_VERIFICATION_INTERRUPTED"
          : "LOGIN_WORKER_EXITED",
        message: interruptedHumanVerification
          ? "人工验证期间原登录worker已中断；禁止盲目重试。请先只读检查持久会话是否已经登录。"
          : "管易登录worker已中断。",
      },
    );
  }
  if (
    Date.parse(loaded.record.expiresAt) <= Date.now() &&
    ![
      LoginOperationState.AUTHENTICATED,
      LoginOperationState.RESIDENT_READY,
      LoginOperationState.RESIDENT_EXECUTING,
      LoginOperationState.FAILED,
      LoginOperationState.EXPIRED,
    ].includes(loaded.record.state)
  ) {
    loaded = await transitionLoginOperation(
      operationId,
      LoginOperationState.EXPIRED,
      { reason: "login_operation_deadline_reached" },
    );
  }
  printJson(publicLoginOperation(loaded.record));
  return [
    LoginOperationState.FAILED,
    LoginOperationState.EXPIRED,
  ].includes(loaded.record.state)
    ? 2
    : 0;
}


async function runLoginSmsRequest(operationId, authorization) {
  requireSmsRequestAuthorization(authorization);
  const result = await sendLoginControl(operationId, "request_sms");
  printJson(result);
  return 0;
}


async function runLoginSmsSubmit(operationId) {
  const code = await readSmsCode();
  await sendLoginControl(operationId, "submit_sms", { code });
  const loaded = await waitForLoginOperation(operationId, {
    timeoutMs: LOGIN_RESULT_TIMEOUT_MS + 5_000,
    states: new Set([
      LoginOperationState.AUTHENTICATED,
      LoginOperationState.RESIDENT_READY,
      LoginOperationState.WAITING_FOR_HUMAN_VERIFICATION,
      LoginOperationState.FAILED,
      LoginOperationState.EXPIRED,
    ]),
  });
  printJson(publicLoginOperation(loaded.record));
  if (loaded.record.state === LoginOperationState.AUTHENTICATED) return 0;
  if (loaded.record.state === LoginOperationState.RESIDENT_READY) return 0;
  if (
    loaded.record.state ===
    LoginOperationState.WAITING_FOR_HUMAN_VERIFICATION
  ) return 0;
  if (loaded.record.state === LoginOperationState.CODE_SUBMITTED) return 0;
  return 2;
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
    instruction: "请在当前已打开的管易浏览器页面中手动登录。",
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
        return { verificationSubmitted: true };
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


async function runSubmitImport(page, operationId, authorization) {
  if (!operationId) {
    throw new OperationStateError(
      "OPERATION_ID_REQUIRED",
      "提交导入需要操作 ID。",
    );
  }
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  const result = await submitPreparedImport(page, operationId, authorization);
  printJson(result);
  return result.status === "submit_maybe_sent" ? 0 : 2;
}


async function authenticateForAuthorizedImport(context, page) {
  await openApp(page);
  const observed = await detectState(page);
  if (observed.state === "needs_verification") {
    return { result: observed };
  }
  if (observed.state !== "needs_login") {
    return { page };
  }

  await page.bringToFront();
  await markDedicatedLoginWindow(page);
  const credentials = await readStoredCredentials();
  printJson({
    status: "authenticating",
    credentialSource: credentialProviderName(),
    browserSession: "single_process",
  });
  try {
    const authenticatedPage = await loginWithCredentials(
      context,
      page,
      credentials,
      LOGIN_TIMEOUT_MS,
      {
        onVerificationRequired: async () => {
          throw new SessionError(
            "SMS_VERIFICATION_AUTHORIZATION_REQUIRED",
            "管易要求短信验证；尚未取得本次短信发送确认。",
          );
        },
      },
    );
    return { page: authenticatedPage };
  } catch (error) {
    if (error?.code !== "SMS_VERIFICATION_AUTHORIZATION_REQUIRED") {
      throw error;
    }
    return {
      result: {
        status: "needs_verification",
        state: "needs_verification",
        verificationType: "sms",
        instruction:
          "请先通过 login-auto-start 完成当次短信验证，再重新执行当前已授权批次；未发送短信，也未选择文件。",
        safety: "验证码不会写入凭证库、Skill、命令参数或日志。",
      },
    };
  }
}


async function runExecuteImport(context, page, operationId, authorization) {
  if (!operationId) {
    throw new OperationStateError(
      "OPERATION_ID_REQUIRED",
      "执行单批导入闭环需要操作 ID。",
    );
  }
  requireSubmitAuthorization(authorization);
  const session = await authenticateForAuthorizedImport(context, page);
  if (session.result) {
    printJson(session.result);
    return workflowExitCode(session.result);
  }
  const result = await executeAuthorizedImportInSingleSession(
    session.page,
    operationId,
    authorization,
    { onProgress: async (event) => printJson(event) },
  );
  printJson(result);
  return workflowExitCode(result);
}


async function runReconcileOperation(page, operationId) {
  if (!operationId) {
    throw new OperationStateError(
      "OPERATION_ID_REQUIRED",
      "核验导入结果需要操作 ID。",
    );
  }
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  const result = await reconcileImportInCurrentSession(page, operationId);
  printJson(result);
  return workflowExitCode(result);
}


async function runResolveNoTask(page, operationId, authorization) {
  if (!operationId) {
    throw new OperationStateError(
      "OPERATION_ID_REQUIRED",
      "关闭人工复核操作需要操作 ID。",
    );
  }
  const result = await resolveNoTaskInCurrentSession(
    page,
    operationId,
    authorization,
  );
  printJson(result);
  return ["needs_login", "needs_verification"].includes(result.state) ? 2 : 0;
}


async function resolveNoTaskInCurrentSession(page, operationId, authorization) {
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    return observed;
  }
  const tasks = await probeImportTaskSnapshot(page);
  const closed = await resolveNoTaskManualReview(
    operationId,
    tasks,
    authorization,
  );
  return {
    status: "operation_retryable_no_task",
    operationId,
    state: closed.record.state,
    attemptNumber: closed.record.attemptNumber,
    failureCode: closed.record.failure.code,
    safety: "The verified task snapshot had no additions; no browser write was performed.",
  };
}


async function runProbeOrderAudit(page, browserBackend) {
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  printJson(
    await probeOrderAuditSurface(page, {
      executionEnvironment:
        browserBackend === "platform_cdp" ? "doubao" : "local",
    }),
  );
  return 0;
}


async function runExecuteOrderAudit(
  context,
  page,
  auditOperationId,
  authorization,
) {
  if (!auditOperationId) {
    throw new AuditOperationError(
      "AUDIT_OPERATION_ID_REQUIRED",
      "执行订单审核需要审核操作 ID。",
    );
  }
  const session = await authenticateForAuthorizedImport(context, page);
  if (session.result) {
    printJson(session.result);
    return workflowExitCode(session.result);
  }
  const result = await executeAuthorizedOrderAudit(
    session.page,
    auditOperationId,
    authorization,
  );
  printJson(result);
  return ["audit_verified", "nothing_to_audit"].includes(result.status)
    ? 0
    : 2;
}


async function runReconcileOrderAudit(page, auditOperationId) {
  if (!auditOperationId) {
    throw new AuditOperationError(
      "AUDIT_OPERATION_ID_REQUIRED",
      "核验订单审核需要审核操作 ID。",
    );
  }
  await openApp(page);
  const observed = await detectState(page);
  if (["needs_login", "needs_verification"].includes(observed.state)) {
    printJson(observed);
    return 2;
  }
  const result = await reconcileOrderAudit(page, auditOperationId);
  printJson(result);
  return result.status === "audit_verified" ? 0 : 2;
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
      "login-auto-start",
      "login-agent-start",
      "login-auto-worker",
      "login-credentials-submit",
      "login-status",
      "login-sms-request",
      "login-sms-submit",
      "login-auto-sms",
      "login-auto-sms-task-center",
      "resident-start",
      "resident-status",
      "resident-execute-import",
      "resident-reconcile",
      "resident-resolve-no-task",
      "resident-probe",
      "probe",
      "navigate-import",
      "prepare-import-form",
      "probe-task-center",
      "probe-completed-imports",
      "capture-task-baseline",
      "inspect-file-selection",
      "select-import-file",
      "submit-import",
      "execute-import",
      "reconcile-operation",
      "resolve-no-task",
      "verify-manifest",
      "prepare-operation",
      "probe-order-audit",
      "prepare-order-audit",
      "execute-order-audit",
      "reconcile-order-audit",
    ].includes(command)
  ) {
    help();
    return 1;
  }

  if (["verify-manifest", "prepare-operation"].includes(command)) {
    const manifestPath = process.argv[3];
    const currentArtifactPath = process.argv[4] || null;
    if (!manifestPath) {
      printJson({ status: "failed", code: "MANIFEST_PATH_REQUIRED" });
      return 1;
    }
    try {
      const verified = await verifyUploadManifest(
        manifestPath,
        currentArtifactPath,
      );
      if (command === "verify-manifest") {
        printJson(verified);
        return 0;
      }
      const prepared = await createPreparedOperation(verified);
      printJson({
        status: "operation_prepared",
        operationId: prepared.record.operationId,
        retryOf: prepared.record.retryOf,
        attemptNumber: prepared.record.attemptNumber,
        state: prepared.record.state,
        receiptPath: prepared.receiptPath,
        orders: prepared.record.counts.orders,
        itemRows: prepared.record.counts.itemRows,
        artifactPathBinding: prepared.record.artifact.pathBinding,
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

  if (command === "prepare-order-audit") {
    const sourceImportOperationId = process.argv[3];
    const scope = process.argv[4] || null;
    if (!sourceImportOperationId) {
      printJson({ status: "failed", code: "IMPORT_OPERATION_ID_REQUIRED" });
      return 1;
    }
    try {
      const prepared = await createPreparedAuditOperation(
        sourceImportOperationId,
        scope,
      );
      printJson({
        status: "order_audit_prepared",
        auditOperationId: prepared.record.auditOperationId,
        sourceImportOperationId,
        state: prepared.record.state,
        scope: prepared.record.scope.mode,
        separateAuthorizationRequired:
          prepared.record.authorization.separateRequired,
        safety: "No browser was opened and no order was audited.",
      });
      return 0;
    } catch (error) {
      if (
        error instanceof AuditOperationError ||
        error instanceof AuditPolicyError
      ) {
        printJson({
          status: "order_audit_rejected",
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
    if (command === "login-auto-worker") {
      return await runLoginAutoWorker(
        process.argv[3],
        process.argv[4],
        process.argv[5],
      );
    }
    if (command === "resident-start") {
      return await runResidentStart();
    }
    if (command === "resident-status") {
      return await runResidentStatus();
    }
    if (command === "resident-execute-import") {
      return await sendResidentCommand(
        ResidentCommand.EXECUTE_IMPORT,
        process.argv[3],
        process.argv[4],
      );
    }
    if (command === "resident-reconcile") {
      return await sendResidentCommand(
        ResidentCommand.RECONCILE_IMPORT,
        process.argv[3],
        null,
      );
    }
    if (command === "resident-resolve-no-task") {
      return await sendResidentCommand(
        ResidentCommand.RESOLVE_NO_TASK,
        process.argv[3],
        process.argv[4],
      );
    }
    if (command === "resident-probe") {
      return await sendResidentCommand(ResidentCommand.PROBE, null, null);
    }
    if (command === "login-auto-start") {
      return await runLoginAutoStart();
    }
    if (command === "login-agent-start") {
      return await runLoginAutoStart("agent_input");
    }
    if (command === "login-credentials-submit") {
      return await runLoginCredentialsSubmit(process.argv[3], process.argv[4]);
    }
    if (command === "login-status") {
      return await runLoginStatus(process.argv[3]);
    }
    if (command === "login-sms-request") {
      return await runLoginSmsRequest(process.argv[3], process.argv[4]);
    }
    if (command === "login-sms-submit") {
      return await runLoginSmsSubmit(process.argv[3]);
    }
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
    if (command === "submit-import") {
      return await runSubmitImport(
        session.page,
        process.argv[3],
        process.argv[4],
      );
    }
    if (command === "execute-import") {
      return await runExecuteImport(
        session.context,
        session.page,
        process.argv[3],
        process.argv[4],
      );
    }
    if (command === "reconcile-operation") {
      return await runReconcileOperation(session.page, process.argv[3]);
    }
    if (command === "probe-order-audit") {
      return await runProbeOrderAudit(session.page, session.backend);
    }
    if (command === "execute-order-audit") {
      return await runExecuteOrderAudit(
        session.context,
        session.page,
        process.argv[3],
        process.argv[4],
      );
    }
    if (command === "reconcile-order-audit") {
      return await runReconcileOrderAudit(session.page, process.argv[3]);
    }
    if (command === "resolve-no-task") {
      return await runResolveNoTask(
        session.page,
        process.argv[3],
        process.argv[4],
      );
    }
    return await runNavigateImport(session.page);
  } catch (caughtError) {
    const error =
      session?.backend === "platform_cdp" &&
      session.browser &&
      !session.browser.isConnected()
        ? new SessionError(
            "PLATFORM_BROWSER_DISCONNECTED",
            "平台浏览器连接已中断；已停止当前操作，未自动重试。",
          )
        : caughtError;
    const screenshot =
      session?.page &&
      !(error instanceof SessionError &&
        (LOGIN_SESSION_ERROR_CODES.has(error.code) ||
          error.code === "BROWSER_SESSION_BUSY" ||
          String(error.code || "").startsWith("PLATFORM_")))
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
    if (error instanceof LoginOperationError) {
      printJson({
        status: "login_operation_blocked",
        code: error.code,
        message: error.message,
        details: error.details,
        screenshot,
      });
      return 2;
    }
    if (error instanceof ResidentSessionError) {
      printJson({
        status: "resident_browser_blocked",
        code: error.code,
        message: error.message,
        screenshot,
      });
      return 2;
    }
    if (error instanceof SessionError) {
      printJson({
        status: sessionErrorStatus(error),
        code: error.code,
        message: error.message,
        details: error.details,
        screenshot,
      });
      return 4;
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
    if (error instanceof SubmissionError) {
      printJson({
        status: "submission_blocked",
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
    if (
      error instanceof AuditOperationError ||
      error instanceof AuditPolicyError ||
      error instanceof OrderAuditError
    ) {
      printJson({
        status: "order_audit_blocked",
        code: error.code,
        message: error.message,
        details: error.details,
        screenshot,
      });
      return 3;
    }
    printJson({
      status: "failed",
      code: error?.name || "Error",
      message: error?.message || String(error),
      screenshot,
    });
    return 1;
  } finally {
    await session?.close?.().catch(() => null);
  }
}


process.exitCode = await main();
