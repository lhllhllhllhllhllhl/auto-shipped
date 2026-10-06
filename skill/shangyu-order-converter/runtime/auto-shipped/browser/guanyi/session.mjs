import fs from "node:fs/promises";
import { chromium } from "playwright-core";

import {
  APP_URL,
  ARTIFACT_DIR,
  CHROME_EXECUTABLE,
  DEFAULT_TIMEOUT_MS,
  PROFILE_DIR,
  WINDOW_HEIGHT,
  WINDOW_WIDTH,
} from "./config.mjs";
import {
  ensurePageGeometry,
  registerPageGeometry,
} from "./geometry.mjs";


export function isLoginUrl(url) {
  return /(?:login\.|www\.)?guanyierp\.com\/login/i.test(url);
}


export function isAuthenticatedAppUrl(url) {
  return /(?:www\.)?guanyierp\.com\/index/i.test(url);
}


export async function launchSession() {
  await fs.access(CHROME_EXECUTABLE);
  await fs.mkdir(PROFILE_DIR, { recursive: true, mode: 0o700 });
  await fs.mkdir(ARTIFACT_DIR, { recursive: true, mode: 0o700 });

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    executablePath: CHROME_EXECUTABLE,
    headless: false,
    viewport: null,
    acceptDownloads: false,
    locale: "zh-CN",
    args: [
      "--no-first-run",
      "--no-default-browser-check",
      "--restore-last-session",
      `--window-size=${WINDOW_WIDTH},${WINDOW_HEIGHT}`,
      "--window-position=0,0",
    ],
  });
  context.setDefaultTimeout(DEFAULT_TIMEOUT_MS);
  context.setDefaultNavigationTimeout(30_000);

  const existingPages = context.pages();
  const page = existingPages[0] || (await context.newPage());
  for (const candidate of context.pages()) {
    registerPageGeometry(context, candidate);
  }
  context.on("page", (candidate) => {
    registerPageGeometry(context, candidate);
  });
  await ensurePageGeometry(page, "browser_launch", { forceApply: true });
  return { context, page };
}


export async function openApp(page) {
  await page.goto(APP_URL, { waitUntil: "domcontentloaded" });
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


export async function markDedicatedLoginWindow(page) {
  await page.evaluate(() => {
    document.title = "【尚舆自动化专用】管易登录";
    if (document.querySelector("#shangyu-guanyi-login-banner")) {
      return;
    }
    const banner = document.createElement("div");
    banner.id = "shangyu-guanyi-login-banner";
    banner.textContent = "尚舆自动化专用登录窗口（请在此窗口完成登录）";
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
    throw new Error("账户登录区域没有找到账号和密码输入框。");
  }
  await inputs.nth(0).fill(credentials.username);
  await inputs.nth(1).fill(credentials.password);

  const agreement = panel.getByRole("checkbox", {
    name: /我已阅读并同意/,
  });
  if (await agreement.isVisible().catch(() => false)) {
    await agreement.setChecked(true);
  }
  await panel.getByRole("button", { name: "登 录", exact: true }).click();
  return await waitForAuthenticatedPage(context, page, timeoutMs, options);
}


export async function waitForAuthenticatedPage(
  context,
  page,
  timeoutMs,
  { onVerificationRequired } = {},
) {
  const deadline = Date.now() + timeoutMs;
  let verificationReported = false;
  while (Date.now() < deadline) {
    for (const candidate of context.pages()) {
      if (isAuthenticatedAppUrl(candidate.url())) {
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
        await onVerificationRequired?.(candidate);
      }
    }
    if (isAuthenticatedAppUrl(page.url())) {
      return page;
    }
    await page.waitForTimeout(250);
  }
  throw new Error(`等待管易登录超时：${timeoutMs}ms`);
}
