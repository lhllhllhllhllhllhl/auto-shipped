import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createLoginControlServer,
  createLoginOperation,
  isLoginWorkerAlive,
  LoginOperationError,
  LoginOperationState,
  publicLoginOperation,
  requireSmsRequestAuthorization,
  requireTemporaryCredentialsSubmitAuthorization,
  sendLoginControl,
  setLoginControlEndpoint,
  setLoginWorkerProcess,
  SMS_REQUEST_AUTHORIZATION,
  TEMPORARY_CREDENTIALS_SUBMIT_AUTHORIZATION,
  transitionLoginOperation,
  waitForLoginOperation,
} from "./login-operation.mjs";


test("login handoff requires explicit SMS authorization and redacts control data", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-login-"));
  try {
    const created = await createLoginOperation(directory, 60_000);
    await setLoginControlEndpoint(created.record.operationId, 32123, directory);
    const loaded = await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.AUTHENTICATING,
      { reason: "worker_started" },
      directory,
    );
    const waiting = await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.NEEDS_VERIFICATION,
      { reason: "sms_required" },
      directory,
    );
    const publicStatus = publicLoginOperation(waiting.record);
    assert.equal(publicStatus.status, "needs_verification");
    assert.equal(JSON.stringify(publicStatus).includes("token"), false);
    assert.equal(JSON.stringify(publicStatus).includes("32123"), false);
    assert.ok(loaded.record.control.token);
    assert.doesNotThrow(() =>
      requireSmsRequestAuthorization(SMS_REQUEST_AUTHORIZATION),
    );
    assert.throws(
      () => requireSmsRequestAuthorization("yes"),
      (error) =>
        error instanceof LoginOperationError &&
        error.code === "SMS_REQUEST_AUTHORIZATION_REQUIRED",
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("temporary Agent credentials use the existing login state without persistence", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-login-"));
  try {
    const created = await createLoginOperation(directory, 60_000);
    await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.AUTHENTICATING,
      { reason: "worker_started" },
      directory,
    );
    const waiting = await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.WAITING_FOR_CREDENTIALS,
      { reason: "temporary_agent_credentials_required" },
      directory,
    );
    const publicStatus = publicLoginOperation(waiting.record);
    assert.equal(publicStatus.status, "waiting_for_credentials");
    assert.equal(publicStatus.credentialMode, "temporary_agent_input");
    const observed = await waitForLoginOperation(created.record.operationId, {
      directory,
      timeoutMs: 25,
    });
    assert.equal(observed.record.state, "waiting_for_credentials");
    assert.doesNotThrow(() =>
      requireTemporaryCredentialsSubmitAuthorization(
        TEMPORARY_CREDENTIALS_SUBMIT_AUTHORIZATION,
      ),
    );
    assert.throws(
      () => requireTemporaryCredentialsSubmitAuthorization("yes"),
      (error) =>
        error instanceof LoginOperationError &&
        error.code ===
          "TEMPORARY_CREDENTIALS_SUBMISSION_AUTHORIZATION_REQUIRED",
    );
    const receipt = await fs.readFile(
      path.join(directory, `${created.record.operationId}.json`),
      "utf8",
    );
    assert.equal(receipt.includes("username"), false);
    assert.equal(receipt.includes("password"), false);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("user handoff transitions refresh expiry and expose only safe failure details", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-login-"));
  try {
    const created = await createLoginOperation(directory, 1_000);
    await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.AUTHENTICATING,
      { reason: "worker_started" },
      directory,
    );
    const waiting = await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.WAITING_FOR_CREDENTIALS,
      { reason: "waiting", expiresInMs: 60_000 },
      directory,
    );
    assert.ok(
      Date.parse(waiting.record.expiresAt) > Date.parse(created.record.expiresAt),
    );
    await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.AUTHENTICATING,
      { reason: "credentials_received" },
      directory,
    );
    const failed = await transitionLoginOperation(
      created.record.operationId,
      LoginOperationState.FAILED,
      {
        reason: "page_observed",
        code: "SMS_CODE_REJECTED",
        message: "验证码被拒绝。",
        details: {
          pageState: "login_page",
          feedbackCategory: "sms_code_rejected",
        },
      },
      directory,
    );
    assert.deepEqual(publicLoginOperation(failed.record).details, {
      pageState: "login_page",
      feedbackCategory: "sms_code_rejected",
    });
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("registered browser verification exposes an explicit same-context user action", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-login-"));
  try {
    const created = await createLoginOperation(directory, 1_000);
    const operationId = created.record.operationId;
    await setLoginWorkerProcess(operationId, 43210, directory);
    let current = await transitionLoginOperation(
      operationId,
      LoginOperationState.AUTHENTICATING,
      { reason: "worker_started" },
      directory,
    );
    current = await transitionLoginOperation(
      operationId,
      LoginOperationState.NEEDS_VERIFICATION,
      { reason: "sms_required" },
      directory,
    );
    current = await transitionLoginOperation(
      operationId,
      LoginOperationState.SMS_REQUESTING,
      { reason: "sms_authorized" },
      directory,
    );
    current = await transitionLoginOperation(
      operationId,
      LoginOperationState.WAITING_FOR_USER_CODE,
      { reason: "sms_sent" },
      directory,
    );
    current = await transitionLoginOperation(
      operationId,
      LoginOperationState.CODE_SUBMITTED,
      { reason: "code_submitted" },
      directory,
    );
    current = await transitionLoginOperation(
      operationId,
      LoginOperationState.WAITING_FOR_HUMAN_VERIFICATION,
      {
        reason: "registered_challenge",
        expiresInMs: 15 * 60_000,
        humanVerification: {
          type: "aliyun_account_verification",
          safeUrl: "http://account.ose.aliyun.com/verify.html",
          focusReady: true,
          focusMethod: "xdotool_exact_window_class",
        },
      },
      directory,
    );

    const status = publicLoginOperation(current.record);
    assert.equal(status.status, "waiting_for_human_verification");
    assert.equal(status.requiresUserAction, true);
    assert.equal(status.userAction.kind, "browser_control");
    assert.equal(status.userAction.sameBrowserContext, true);
    assert.equal(status.userAction.agentMustInvokeInteractiveControl, true);
    assert.equal(status.humanVerification.type, "aliyun_account_verification");
    assert.equal(status.humanVerification.focusReady, true);
    assert.equal(JSON.stringify(status).includes("token"), false);
    assert.equal(JSON.stringify(status).includes("43210"), false);
    assert.equal(isLoginWorkerAlive(current.record, (pid) => pid === 43210), true);
    assert.equal(isLoginWorkerAlive(current.record, () => false), false);

    const authenticated = await transitionLoginOperation(
      operationId,
      LoginOperationState.AUTHENTICATED,
      { reason: "authenticated_after_human_verification" },
      directory,
    );
    assert.equal(authenticated.record.humanVerification, null);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("login control uses a private token and never persists the SMS code", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-login-"));
  let server;
  try {
    const created = await createLoginOperation(directory, 60_000);
    const operationId = created.record.operationId;
    server = await createLoginControlServer(
      operationId,
      async ({ action, code }) => ({ action, accepted: /^\d{4,8}$/.test(code) }),
      directory,
    );
    const result = await sendLoginControl(operationId, "submit_sms", {
      code: "123456",
      directory,
    });
    assert.deepEqual(result, { action: "submit_sms", accepted: true });
    const receipt = await fs.readFile(
      path.join(directory, `${operationId}.json`),
      "utf8",
    );
    assert.equal(receipt.includes("123456"), false);
  } finally {
    await new Promise((resolve) => server?.close(resolve) || resolve());
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("login control passes temporary credentials only through memory", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-login-"));
  let server;
  try {
    const created = await createLoginOperation(directory, 60_000);
    const operationId = created.record.operationId;
    server = await createLoginControlServer(
      operationId,
      async ({ action, credentials }) => ({
        action,
        accepted: Boolean(credentials?.username && credentials?.password),
      }),
      directory,
    );
    const result = await sendLoginControl(operationId, "submit_credentials", {
      credentials: { username: "cloud-user", password: "one-time-secret" },
      directory,
    });
    assert.deepEqual(result, {
      action: "submit_credentials",
      accepted: true,
    });
    const receipt = await fs.readFile(
      path.join(directory, `${operationId}.json`),
      "utf8",
    );
    assert.equal(receipt.includes("cloud-user"), false);
    assert.equal(receipt.includes("one-time-secret"), false);
  } finally {
    await new Promise((resolve) => server?.close(resolve) || resolve());
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("one-time localhost page hands credentials to the same login control", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-login-"));
  let server;
  try {
    const created = await createLoginOperation(directory, 60_000);
    const operationId = created.record.operationId;
    let observed = null;
    server = await createLoginControlServer(
      operationId,
      async ({ action, credentials }) => {
        observed = {
          action,
          username: credentials?.username,
          password: credentials?.password,
        };
        return { accepted: true };
      },
      directory,
    );
    await transitionLoginOperation(
      operationId,
      LoginOperationState.AUTHENTICATING,
      { reason: "worker_started" },
      directory,
    );
    const waiting = await transitionLoginOperation(
      operationId,
      LoginOperationState.WAITING_FOR_CREDENTIALS,
      { reason: "temporary_agent_credentials_required" },
      directory,
    );
    const status = publicLoginOperation(waiting.record);
    assert.match(
      status.credentialInputUrl,
      /^http:\/\/127\.0\.0\.1:\d+\/credentials\/[0-9a-f]+$/,
    );

    const page = await fetch(status.credentialInputUrl);
    assert.equal(page.status, 200);
    assert.match(await page.text(), /本次管易登录/);

    const form = new URLSearchParams({
      username: "cloud-user",
      password: "one-time-secret",
    });
    const submitted = await fetch(status.credentialInputUrl, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form,
    });
    assert.equal(submitted.status, 200);
    assert.match(await submitted.text(), /凭据已交接/);
    assert.deepEqual(observed, {
      action: "submit_credentials",
      username: "cloud-user",
      password: "one-time-secret",
    });

    const reused = await fetch(status.credentialInputUrl);
    assert.equal(reused.status, 404);
    const receipt = await fs.readFile(
      path.join(directory, `${operationId}.json`),
      "utf8",
    );
    assert.equal(receipt.includes("cloud-user"), false);
    assert.equal(receipt.includes("one-time-secret"), false);
  } finally {
    await new Promise((resolve) => server?.close(resolve) || resolve());
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("one-time localhost page hands the SMS code to the same worker", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-login-"));
  let server;
  try {
    const created = await createLoginOperation(directory, 60_000);
    const operationId = created.record.operationId;
    let observed = null;
    server = await createLoginControlServer(
      operationId,
      async ({ action, code }) => {
        observed = { action, code };
        return { accepted: true };
      },
      directory,
    );
    await transitionLoginOperation(
      operationId,
      LoginOperationState.AUTHENTICATING,
      { reason: "worker_started" },
      directory,
    );
    await transitionLoginOperation(
      operationId,
      LoginOperationState.NEEDS_VERIFICATION,
      { reason: "sms_required" },
      directory,
    );
    await transitionLoginOperation(
      operationId,
      LoginOperationState.SMS_REQUESTING,
      { reason: "user_confirmed" },
      directory,
    );
    const waiting = await transitionLoginOperation(
      operationId,
      LoginOperationState.WAITING_FOR_USER_CODE,
      { reason: "notice_confirmed" },
      directory,
    );
    const status = publicLoginOperation(waiting.record);
    assert.match(
      status.smsInputUrl,
      /^http:\/\/127\.0\.0\.1:\d+\/sms\/[0-9a-f]+$/,
    );

    const page = await fetch(status.smsInputUrl);
    assert.equal(page.status, 200);
    assert.match(await page.text(), /本次短信验证码/);
    const submitted = await fetch(status.smsInputUrl, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ code: "123456" }),
    });
    assert.equal(submitted.status, 200);
    assert.match(await submitted.text(), /验证码已交接/);
    assert.deepEqual(observed, { action: "submit_sms", code: "123456" });
    assert.equal((await fetch(status.smsInputUrl)).status, 404);

    const receipt = await fs.readFile(
      path.join(directory, `${operationId}.json`),
      "utf8",
    );
    assert.equal(receipt.includes("123456"), false);
  } finally {
    await new Promise((resolve) => server?.close(resolve) || resolve());
    await fs.rm(directory, { recursive: true, force: true });
  }
});
