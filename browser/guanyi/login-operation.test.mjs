import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createLoginControlServer,
  createLoginOperation,
  LoginOperationError,
  LoginOperationState,
  publicLoginOperation,
  requireSmsRequestAuthorization,
  requireTemporaryCredentialsReadyAuthorization,
  sendLoginControl,
  setLoginControlEndpoint,
  SMS_REQUEST_AUTHORIZATION,
  TEMPORARY_CREDENTIALS_READY_AUTHORIZATION,
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
      requireTemporaryCredentialsReadyAuthorization(
        TEMPORARY_CREDENTIALS_READY_AUTHORIZATION,
      ),
    );
    assert.throws(
      () => requireTemporaryCredentialsReadyAuthorization("yes"),
      (error) =>
        error instanceof LoginOperationError &&
        error.code ===
          "TEMPORARY_CREDENTIALS_READY_AUTHORIZATION_REQUIRED",
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
