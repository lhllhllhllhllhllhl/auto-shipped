import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright-core";

import {
  BrowserBackend,
  BrowserBackendError,
  connectPlatformBrowser,
  resolveBrowserBackend,
} from "./browser-backend.mjs";
import {
  APP_URL,
  ARTIFACT_DIR,
  BROWSER_BACKEND,
  BROWSER_SESSION_LOCK,
  BROWSER_WINDOW_CLASS,
  CHROME_EXECUTABLE,
  DEFAULT_TIMEOUT_MS,
  HUMAN_VERIFICATION_TIMEOUT_MS,
  LOGIN_RESULT_TIMEOUT_MS,
  PLATFORM_CDP_ENDPOINT,
  PROFILE_DIR,
  LOGIN_URL,
  WINDOW_HEIGHT,
  WINDOW_WIDTH,
} from "./config.mjs";
import {
  ensurePageGeometry,
  registerPageGeometry,
} from "./geometry.mjs";
import {
  classifyRegisteredHumanVerificationUrl,
} from "./human-verification.mjs";


export function isLoginUrl(url) {
  return /(?:login\.|www\.)?guanyierp\.com\/login/i.test(url);
}


export class SessionError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "SessionError";
    this.code = code;
    this.details = details;
  }
}


function isProcessAlive(processId) {
  if (!Number.isInteger(processId) || processId < 1) return false;
  try {
    process.kill(processId, 0);
    return true;
  } catch (error) {
    return error?.code !== "ESRCH";
  }
}


export async function acquireBrowserSessionLease(
  lockPath = BROWSER_SESSION_LOCK,
  {
    processId = process.pid,
    processAlive = isProcessAlive,
    now = () => Date.now(),
  } = {},
) {
  await fs.mkdir(path.dirname(lockPath), { recursive: true, mode: 0o700 });
  const nonce = randomUUID();
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let handle;
    try {
      handle = await fs.open(lockPath, "wx", 0o600);
      await handle.writeFile(
        `${JSON.stringify({
          schemaVersion: "guanyi-browser-session-lock/1.0",
          processId,
          nonce,
          createdAt: new Date(now()).toISOString(),
        })}\n`,
        "utf8",
      );
      await handle.close();
      handle = null;
      let released = false;
      return {
        lockPath,
        async release() {
          if (released) return;
          const current = await fs
            .readFile(lockPath, "utf8")
            .then((value) => JSON.parse(value))
            .catch(() => null);
          if (current?.nonce === nonce) {
            await fs.unlink(lockPath).catch((error) => {
              if (error?.code !== "ENOENT") throw error;
            });
          }
          released = true;
        },
      };
    } catch (error) {
      await handle?.close().catch(() => null);
      if (error?.code !== "EEXIST") throw error;
      const [existing, stats] = await Promise.all([
        fs
          .readFile(lockPath, "utf8")
          .then((value) => JSON.parse(value))
          .catch(() => null),
        fs.stat(lockPath).catch(() => null),
      ]);
      const ownerAlive = processAlive(existing?.processId);
      const incompleteAndFresh =
        !existing && stats && now() - stats.mtimeMs < 5_000;
      if (ownerAlive || incompleteAndFresh) {
        throw new SessionError(
          "BROWSER_SESSION_BUSY",
          "管易专用浏览器正在被另一个操作使用；请继续当前操作，不要启动 probe、导航或第二个登录。",
          {
            ownerProcessId: Number.isInteger(existing?.processId)
              ? existing.processId
              : null,
            createdAt: existing?.createdAt || null,
          },
        );
      }
      await fs.unlink(lockPath).catch((unlinkError) => {
        if (unlinkError?.code !== "ENOENT") throw unlinkError;
      });
    }
  }
  throw new SessionError(
    "BROWSER_SESSION_BUSY",
    "无法取得管易专用浏览器的独占使用权。",
  );
}


export function isAccessBlockedText(text) {
  const value = String(text || "");
  return (
    value.includes("405") &&
    value.includes("安全威胁") &&
    value.includes("访问被阻断")
  );
}


async function isAccessBlockedPage(page) {
  return await Promise.all([
    page.title().catch(() => ""),
    page.evaluate(() => document.body?.innerText || "").catch(() => ""),
  ]).then(([title, text]) => isAccessBlockedText(`${title} ${text}`));
}


export function isAuthenticatedAppUrl(url) {
  return /(?:www\.)?guanyierp\.com\/index/i.test(url);
}


export async function hasAuthenticatedAppEvidence(page) {
  const frames = typeof page.frames === "function" ? page.frames() : [page];
  for (const frame of frames) {
    const text = await frame
      .locator("body")
      .innerText()
      .catch(() => "");
    if (
      text.includes("菜单") &&
      ["首页", "我的工单", "售后服务"].some((marker) => text.includes(marker))
    ) {
      return true;
    }
  }
  return false;
}


export async function confirmStableAuthenticatedPage(page, stabilityMs = 5_000) {
  if (!isAuthenticatedAppUrl(page.url())) return false;

  const evidenceDeadline = Date.now() + 10_000;
  while (Date.now() < evidenceDeadline) {
    if (!isAuthenticatedAppUrl(page.url())) return false;
    if (await hasAuthenticatedAppEvidence(page)) break;
    await page.waitForTimeout(250);
  }
  if (!(await hasAuthenticatedAppEvidence(page))) return false;

  await page
    .waitForURL(
      (url) => !isAuthenticatedAppUrl(String(url)),
      { timeout: stabilityMs },
    )
    .catch(() => null);
  return isAuthenticatedAppUrl(page.url());
}


export async function retainSingleStartupPage(
  context,
  {
    closeOthers = true,
    createDedicatedPageWhenMissing = false,
  } = {},
) {
  const existingPages = context.pages();
  const page =
    existingPages.find((candidate) => isAuthenticatedAppUrl(candidate.url())) ||
    existingPages.find((candidate) => isLoginUrl(candidate.url())) ||
    (!createDedicatedPageWhenMissing ? existingPages[0] : null) ||
    (await context.newPage());

  if (closeOthers) {
    for (const candidate of existingPages) {
      if (candidate === page) continue;
      await candidate.close({ runBeforeUnload: false }).catch(() => null);
    }
  }
  return page;
}


export async function launchSession({
  chromiumInstance = chromium,
  configuredBackend = BROWSER_BACKEND,
  cdpEndpoint = PLATFORM_CDP_ENDPOINT,
  platform = process.platform,
  probe,
} = {}) {
  const lease = await acquireBrowserSessionLease();
  let context;
  let browserConnection;
  let backend;
  let closed = false;
  const close = async () => {
    if (closed) return;
    closed = true;
    try {
      if (backend === BrowserBackend.PLATFORM_CDP) {
        await browserConnection?.close?.();
      } else {
        await context?.close();
      }
    } finally {
      await lease.release();
    }
  };
  try {
    await fs.mkdir(ARTIFACT_DIR, { recursive: true, mode: 0o700 });
    backend = await resolveBrowserBackend({
      configuredBackend,
      platform,
      cdpEndpoint,
      ...(probe ? { probe } : {}),
    });
    if (backend === BrowserBackend.PLATFORM_CDP) {
      browserConnection = await connectPlatformBrowser(
        chromiumInstance,
        cdpEndpoint,
      );
      context = browserConnection.context;
      browserConnection.browser.once("disconnected", () => {
        void lease.release();
      });
    } else {
      await fs.access(CHROME_EXECUTABLE);
      await fs.mkdir(PROFILE_DIR, { recursive: true, mode: 0o700 });
      context = await chromiumInstance.launchPersistentContext(PROFILE_DIR, {
        executablePath: CHROME_EXECUTABLE,
        headless: false,
        viewport: null,
        acceptDownloads: false,
        locale: "zh-CN",
        args: [
          "--no-first-run",
          "--no-default-browser-check",
          `--window-size=${WINDOW_WIDTH},${WINDOW_HEIGHT}`,
          "--window-position=0,0",
          ...(platform === "linux"
            ? [`--class=${BROWSER_WINDOW_CLASS}`]
            : []),
        ],
      });
      context.once("close", () => {
        void lease.release();
      });
    }
    context.setDefaultTimeout(DEFAULT_TIMEOUT_MS);
    context.setDefaultNavigationTimeout(30_000);

    const platformBrowser = backend === BrowserBackend.PLATFORM_CDP;
    const page = await retainSingleStartupPage(context, {
      closeOthers: !platformBrowser,
      createDedicatedPageWhenMissing: platformBrowser,
    });
    for (const candidate of context.pages()) {
      registerPageGeometry(context, candidate);
    }
    context.on("page", (candidate) => {
      registerPageGeometry(context, candidate);
    });
    await page.bringToFront();
    await ensurePageGeometry(page, "browser_launch", { forceApply: true });
    return {
      backend,
      browser: browserConnection?.browser || null,
      browserControlTarget: platformBrowser
        ? "existing_platform_builtin_browser"
        : "existing_playwright_browser_via_platform_desktop",
      context,
      ownsContext: !platformBrowser,
      page,
      close,
      release: lease.release,
    };
  } catch (error) {
    await close().catch(() => null);
    if (error instanceof BrowserBackendError) {
      throw new SessionError(error.code, error.message, error.details);
    }
    throw error;
  }
}


export async function openApp(page) {
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
  let accessBlocked = await isAccessBlockedPage(page);
  if (accessBlocked) {
    // A direct /index navigation can be rejected by the current application
    // firewall. Enter through the official login route instead; an existing
    // authenticated session may redirect back to the application normally.
    await page.goto(LOGIN_URL, { waitUntil: "domcontentloaded" });
    accessBlocked = await isAccessBlockedPage(page);
  }
  if (accessBlocked) {
    throw new SessionError(
      "APP_ACCESS_BLOCKED",
      "管易安全防火墙阻断了当前网络出口；请让 guanyierp.com 走直连后重试。",
      { url: page.url() },
    );
  }
  if (isAuthenticatedAppUrl(page.url())) {
    await confirmStableAuthenticatedPage(page);
  }
  if (!isLoginUrl(page.url())) {
    await page.waitForFunction(
      () => document.body?.innerText?.trim().length > 0,
      null,
      { timeout: 30_000 },
    );
  }
  await ensurePageGeometry(page, "open_app_after_navigation");
  return page.url();
}


export async function isVerificationRequired(page) {
  const codeInput = page.getByPlaceholder("请输入验证码", { exact: true });
  const requestCode = page.getByText("获取验证码", { exact: true });
  return (
    (await codeInput.isVisible().catch(() => false)) &&
    (await requestCode.isVisible().catch(() => false))
  );
}


export async function requestSmsCode(page) {
  if (!(await isVerificationRequired(page))) {
    throw new Error("当前页面没有进入短信验证状态。");
  }
  const requestCode = page.getByText("获取验证码", { exact: true });
  await requestCode.click();

  // 管易会在真正发送短信前后显示一层说明弹窗。必须先明确识别
  // 这条短信说明，再点击“知道了”；不能把任意同名按钮当作安全弹窗。
  const notice = page.getByText(/验证码将发送到手机号码为/, {
    exact: false,
  });
  await notice.waitFor({ state: "visible", timeout: 10_000 });
  const acknowledge = page.getByRole("button", {
    name: "知道了",
    exact: true,
  });
  await acknowledge.waitFor({ state: "visible", timeout: 5_000 });
  await acknowledge.click();
  await acknowledge.waitFor({ state: "hidden", timeout: 5_000 });
  await ensurePageGeometry(page, "sms_notice_closed");

  return { noticeDismissed: true };
}


export async function submitSmsCode(page, code) {
  if (!/^\d{4,8}$/.test(code)) {
    throw new Error("短信验证码格式不正确，应为 4 到 8 位数字。");
  }
  const panel = page.locator("#rc-tabs-0-panel-Account");
  await panel.getByPlaceholder("请输入验证码", { exact: true }).fill(code);
  await ensurePageGeometry(page, "before_sms_login_submit");
  await panel.getByRole("button", { name: "登 录", exact: true }).click();
}


const LOGIN_FEEDBACK_SELECTORS = [
  '[role="alert"]',
  '[role="dialog"]',
  ".ant-message-notice-content",
  ".ant-notification-notice",
  ".ant-modal-content",
  ".ant-form-item-explain-error",
  ".ant-form-item-explain",
].join(",");


export function classifyLoginFeedback(text) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (
    /验证码.{0,12}(错误|不正确|无效|过期|失效)|(?:错误|不正确|无效|过期|失效).{0,12}验证码/.test(
      value,
    )
  ) {
    return "sms_code_rejected";
  }
  if (
    /操作过于频繁|操作频繁|登录频繁|请求频繁|稍后再试|安全风险|风险验证|账号异常|访问受限|登录受限/.test(
      value,
    )
  ) {
    return "login_risk_controlled";
  }
  if (
    /账号或密码|密码.{0,8}(错误|不正确|过期)|账号.{0,8}(不存在|锁定|禁用)/.test(
      value,
    )
  ) {
    return "credentials_rejected";
  }
  return null;
}


function feedbackError(category, details) {
  if (category === "sms_code_rejected") {
    return new SessionError(
      "SMS_CODE_REJECTED",
      "管易登录页拒绝了本次短信验证码；不要自动重新发送短信。",
      details,
    );
  }
  if (category === "login_risk_controlled") {
    return new SessionError(
      "LOGIN_RISK_CONTROLLED",
      "管易登录页显示频率或安全风控提示；请停止重试并人工确认。",
      details,
    );
  }
  if (category === "credentials_rejected") {
    return new SessionError(
      "LOGIN_CREDENTIALS_REJECTED",
      "管易登录页拒绝了当前账号或密码。",
      details,
    );
  }
  return null;
}


export async function inspectPostSmsLoginState(page) {
  const url = page.url();
  const humanVerification = classifyRegisteredHumanVerificationUrl(url);
  const safeUrl = (() => {
    try {
      const parsed = new URL(url);
      return `${parsed.origin}${parsed.pathname}`;
    } catch {
      return "unknown";
    }
  })();
  const verificationVisible = await isVerificationRequired(page);
  const visibleFeedback = await page
    .locator(LOGIN_FEEDBACK_SELECTORS)
    .evaluateAll((elements) =>
      elements.reduce(
        (result, element) => {
          const style = window.getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          const visible =
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            rect.width > 0 &&
            rect.height > 0;
          if (!visible) return result;
          const text = element.innerText || element.textContent || "";
          if (text) result.texts.push(text);
          if (
            element.matches('[role="dialog"], .ant-modal-content') ||
            element.closest('[role="dialog"], .ant-modal-content')
          ) {
            result.dialogVisible = true;
          }
          return result;
        },
        { texts: [], dialogVisible: false },
      ),
    )
    .catch(() => ({ texts: [], dialogVisible: false }));
  const feedbackCategory = visibleFeedback.texts
    .map((value) => classifyLoginFeedback(value))
    .find(Boolean) || null;
  return {
    pageState: isAuthenticatedAppUrl(url)
      ? "authenticated_app"
      : isLoginUrl(url)
        ? "login_page"
        : "other_page",
    safeUrl,
    verificationVisible,
    feedbackCategory,
    dialogVisible: visibleFeedback.dialogVisible,
    humanVerificationType: humanVerification?.type || null,
  };
}


export async function markDedicatedLoginWindow(page) {
  await page.evaluate(() => {
    document.title = "【尚舆自动化专用】管易登录";
    if (document.querySelector("#shangyu-guanyi-login-banner")) {
      return;
    }
    const banner = document.createElement("div");
    banner.id = "shangyu-guanyi-login-banner";
    banner.textContent = "尚舆自动化专用登录窗口";
    Object.assign(banner.style, {
      position: "fixed",
      top: "8px",
      right: "8px",
      zIndex: "2147483647",
      padding: "8px 12px",
      borderRadius: "6px",
      background: "#1677ff",
      color: "#fff",
      fontSize: "14px",
      fontWeight: "600",
      boxShadow: "0 2px 8px rgba(0,0,0,.2)",
      pointerEvents: "none",
    });
    document.body.appendChild(banner);
  });
}


export async function loginWithCredentials(
  context,
  page,
  credentials,
  timeoutMs,
  options = {},
) {
  const prepared = await fillPreparedCredentialLogin(page, credentials);
  await submitPreparedCredentialLogin(page, prepared);
  return await waitForAuthenticatedPage(context, page, timeoutMs, {
    ...options,
    credentialsSubmitted: true,
  });
}


export async function fillPreparedCredentialLogin(
  page,
  credentials,
  prepared = null,
) {
  if (
    typeof credentials?.username !== "string" ||
    !credentials.username ||
    typeof credentials?.password !== "string" ||
    !credentials.password
  ) {
    throw new SessionError(
      "TEMPORARY_CREDENTIALS_INVALID",
      "临时账号或密码为空。",
    );
  }
  const result = prepared || (await prepareCredentialLogin(page));
  await result.inputs.nth(0).fill(credentials.username);
  await result.inputs.nth(1).fill(credentials.password);
  return result;
}


export async function prepareCredentialLogin(page) {
  const accountLogin = page.getByRole("tab", {
    name: "账户登录",
    exact: true,
  });
  if (await accountLogin.isVisible().catch(() => false)) {
    await accountLogin.click();
  }

  const panel = page.locator("#rc-tabs-0-panel-Account");
  const inputs = panel.locator("input");
  if ((await inputs.count()) < 2) {
    throw new SessionError(
      "LOGIN_CREDENTIAL_INPUTS_MISSING",
      "账户登录区域没有找到账号和密码输入框。",
    );
  }
  return { panel, inputs };
}


export async function submitPreparedCredentialLogin(
  page,
  prepared = null,
) {
  const { panel, inputs } = prepared || (await prepareCredentialLogin(page));
  const filled = await Promise.all([
    inputs.nth(0).evaluate((input) => Boolean(input.value)),
    inputs.nth(1).evaluate((input) => Boolean(input.value)),
  ]);
  if (!filled.every(Boolean)) {
    throw new SessionError(
      "TEMPORARY_CREDENTIALS_NOT_FILLED",
      "Agent 尚未在管易登录页完整填写账号和密码。",
    );
  }

  const agreement = panel.getByRole("checkbox", {
    name: /我已阅读并同意/,
  });
  if (await agreement.isVisible().catch(() => false)) {
    await agreement.setChecked(true);
  }
  await panel.getByRole("button", { name: "登 录", exact: true }).click();
  return { submitted: true };
}


export async function waitForAuthenticatedPage(
  context,
  page,
  timeoutMs,
  {
    onVerificationRequired,
    onHumanVerificationRequired,
    postVerificationTimeoutMs = LOGIN_RESULT_TIMEOUT_MS,
    humanVerificationTimeoutMs = HUMAN_VERIFICATION_TIMEOUT_MS,
    additionalStepStabilityMs = 1_500,
    pollIntervalMs = 250,
    credentialsSubmitted = false,
  } = {},
) {
  let deadline = Date.now() + timeoutMs;
  let verificationReported = false;
  let verificationSubmitted = false;
  let lastObservation = null;
  let unregisteredPageSince = null;
  let unregisteredSafeUrl = null;
  let humanVerificationReported = false;
  let humanVerificationActive = false;
  let humanVerificationPage = null;
  while (Date.now() < deadline) {
    const pages = context.pages();
    if (humanVerificationActive && pages.length === 0) {
      throw new SessionError(
        "LOGIN_HUMAN_VERIFICATION_INTERRUPTED",
        "人工验证期间原浏览器页面已关闭；禁止自动重启登录或重新发送短信。",
        lastObservation || {},
      );
    }
    for (const candidate of pages) {
      if (
        isAuthenticatedAppUrl(candidate.url()) &&
        (await confirmStableAuthenticatedPage(candidate))
      ) {
        await candidate.bringToFront();
        await candidate.waitForLoadState("domcontentloaded").catch(() => null);
        return candidate;
      }
      if (
        !verificationReported &&
        (await isVerificationRequired(candidate))
      ) {
        verificationReported = true;
        await candidate.bringToFront();
        const result = await onVerificationRequired?.(candidate);
        if (result?.verificationSubmitted) {
          verificationSubmitted = true;
          deadline = Date.now() + postVerificationTimeoutMs;
        }
      }
      if (verificationSubmitted) {
        lastObservation = await inspectPostSmsLoginState(candidate);
        if (lastObservation.humanVerificationType) {
          unregisteredPageSince = null;
          unregisteredSafeUrl = null;
          if (!humanVerificationReported) {
            humanVerificationReported = true;
            humanVerificationActive = true;
            humanVerificationPage = candidate;
            deadline = Date.now() + humanVerificationTimeoutMs;
            await onHumanVerificationRequired?.(candidate, lastObservation);
          }
          continue;
        }
        const feedbackFailure = feedbackError(
          lastObservation.feedbackCategory,
          lastObservation,
        );
        if (feedbackFailure) throw feedbackFailure;
        if (lastObservation.dialogVisible) {
          throw new SessionError(
            "LOGIN_ADDITIONAL_STEP_REQUIRED",
            "验证码提交后出现了未登记的登录步骤或弹窗；已停止自动处理。",
            lastObservation,
          );
        }
        if (lastObservation.pageState === "other_page") {
          if (unregisteredSafeUrl !== lastObservation.safeUrl) {
            unregisteredSafeUrl = lastObservation.safeUrl;
            unregisteredPageSince = Date.now();
          } else if (
            Date.now() - unregisteredPageSince >= additionalStepStabilityMs
          ) {
            throw new SessionError(
              "LOGIN_ADDITIONAL_STEP_REQUIRED",
              "验证码提交后停留在未登记的登录步骤；已停止自动处理。",
              lastObservation,
            );
          }
        } else {
          unregisteredPageSince = null;
          unregisteredSafeUrl = null;
        }
      } else if (credentialsSubmitted && candidate === page) {
        const observation = await inspectPostSmsLoginState(candidate);
        const feedbackFailure = feedbackError(
          observation.feedbackCategory,
          observation,
        );
        if (feedbackFailure) throw feedbackFailure;
      }
    }
    if (
      humanVerificationActive &&
      humanVerificationPage &&
      !pages.includes(humanVerificationPage)
    ) {
      throw new SessionError(
        "LOGIN_HUMAN_VERIFICATION_INTERRUPTED",
        "人工验证期间原浏览器页面已关闭；禁止自动重启登录或重新发送短信。",
        lastObservation || {},
      );
    }
    if (
      isAuthenticatedAppUrl(page.url()) &&
      (await confirmStableAuthenticatedPage(page))
    ) {
      return page;
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  if (humanVerificationActive) {
    throw new SessionError(
      "LOGIN_HUMAN_VERIFICATION_TIMEOUT",
      "人工验证等待超时；禁止自动重启登录或重新发送短信。",
      lastObservation || {
        pageState: "unknown",
        safeUrl: "unknown",
        verificationVisible: null,
        feedbackCategory: null,
        dialogVisible: null,
        humanVerificationType: null,
      },
    );
  }
  if (verificationSubmitted) {
    throw new SessionError(
      "LOGIN_OUTCOME_UNCONFIRMED",
      "验证码已提交，但管易没有在限定时间内给出可确认的登录结果；禁止自动重试。",
      lastObservation || {
        pageState: "unknown",
        safeUrl: "unknown",
        verificationVisible: null,
        feedbackCategory: null,
        dialogVisible: null,
      },
    );
  }
  throw new Error(`等待管易登录超时：${timeoutMs}ms`);
}
