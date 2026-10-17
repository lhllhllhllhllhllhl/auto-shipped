import { randomBytes, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";

import {
  LOGIN_OPERATION_DIR,
  LOGIN_TIMEOUT_MS,
} from "./config.mjs";


export const SMS_REQUEST_AUTHORIZATION = "CONFIRM_REQUEST_GUANYI_SMS";
export const TEMPORARY_CREDENTIALS_SUBMIT_AUTHORIZATION =
  "CONFIRM_TEMPORARY_CREDENTIALS_SUBMISSION";


export const LoginOperationState = Object.freeze({
  STARTING: "starting",
  AUTHENTICATING: "authenticating",
  WAITING_FOR_CREDENTIALS: "waiting_for_credentials",
  NEEDS_VERIFICATION: "needs_verification",
  SMS_REQUESTING: "sms_requesting",
  WAITING_FOR_USER_CODE: "waiting_for_user_code",
  CODE_SUBMITTED: "code_submitted",
  AUTHENTICATED: "authenticated",
  FAILED: "failed",
  EXPIRED: "expired",
});


const TERMINAL_STATES = new Set([
  LoginOperationState.AUTHENTICATED,
  LoginOperationState.FAILED,
  LoginOperationState.EXPIRED,
]);


const TRANSITIONS = Object.freeze({
  [LoginOperationState.STARTING]: new Set([
    LoginOperationState.AUTHENTICATING,
    LoginOperationState.FAILED,
    LoginOperationState.EXPIRED,
  ]),
  [LoginOperationState.AUTHENTICATING]: new Set([
    LoginOperationState.WAITING_FOR_CREDENTIALS,
    LoginOperationState.NEEDS_VERIFICATION,
    LoginOperationState.AUTHENTICATED,
    LoginOperationState.FAILED,
    LoginOperationState.EXPIRED,
  ]),
  [LoginOperationState.WAITING_FOR_CREDENTIALS]: new Set([
    LoginOperationState.AUTHENTICATING,
    LoginOperationState.FAILED,
    LoginOperationState.EXPIRED,
  ]),
  [LoginOperationState.NEEDS_VERIFICATION]: new Set([
    LoginOperationState.SMS_REQUESTING,
    LoginOperationState.FAILED,
    LoginOperationState.EXPIRED,
  ]),
  [LoginOperationState.SMS_REQUESTING]: new Set([
    LoginOperationState.WAITING_FOR_USER_CODE,
    LoginOperationState.FAILED,
    LoginOperationState.EXPIRED,
  ]),
  [LoginOperationState.WAITING_FOR_USER_CODE]: new Set([
    LoginOperationState.CODE_SUBMITTED,
    LoginOperationState.FAILED,
    LoginOperationState.EXPIRED,
  ]),
  [LoginOperationState.CODE_SUBMITTED]: new Set([
    LoginOperationState.AUTHENTICATED,
    LoginOperationState.FAILED,
    LoginOperationState.EXPIRED,
  ]),
  [LoginOperationState.AUTHENTICATED]: new Set(),
  [LoginOperationState.FAILED]: new Set(),
  [LoginOperationState.EXPIRED]: new Set(),
});


export class LoginOperationError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "LoginOperationError";
    this.code = code;
    this.details = details;
  }
}


function operationPath(operationId, directory = LOGIN_OPERATION_DIR) {
  if (!/^[0-9a-f-]{36}$/.test(operationId || "")) {
    throw new LoginOperationError(
      "LOGIN_OPERATION_ID_INVALID",
      "登录操作 ID 格式不正确。",
    );
  }
  return path.join(directory, `${operationId}.json`);
}


async function writeAtomic(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  await fs.rename(temporary, filePath);
}


function validateRecord(record, operationId) {
  if (
    record?.schemaVersion !== "guanyi-login-operation/1.0" ||
    record?.operationId !== operationId ||
    !TRANSITIONS[record?.state] ||
    typeof record?.control?.token !== "string"
  ) {
    throw new LoginOperationError(
      "LOGIN_OPERATION_INVALID",
      "登录操作记录结构不正确。",
    );
  }
  return record;
}


export function publicLoginOperation(record) {
  const payload = {
    status: record.state,
    loginOperationId: record.operationId,
    createdAt: record.createdAt,
    updatedAt: record.updatedAt,
    expiresAt: record.expiresAt,
  };
  if (record.error) {
    payload.code = record.error.code;
    payload.message = record.error.message;
    if (record.error.details) payload.details = record.error.details;
  }
  if (record.state === LoginOperationState.WAITING_FOR_CREDENTIALS) {
    payload.credentialMode = "temporary_agent_input";
    if (record.control?.host && record.control?.port && record.control?.token) {
      payload.credentialInputUrl =
        `http://${record.control.host}:${record.control.port}` +
        `/credentials/${record.control.token}`;
    }
    payload.instruction =
      "请先说明密码会保留在平台对话历史中；用户明确接受后，由 Agent 在一次性本机页面填入本次凭据，或通过标准输入交给当前登录进程。";
  } else if (record.state === LoginOperationState.NEEDS_VERIFICATION) {
    payload.verificationType = "sms";
    payload.instruction = "请询问用户现在是否方便接收短信验证码；尚未发送短信。";
  } else if (record.state === LoginOperationState.WAITING_FOR_USER_CODE) {
    payload.verificationType = "sms";
    if (record.control?.host && record.control?.port && record.control?.token) {
      payload.smsInputUrl =
        `http://${record.control.host}:${record.control.port}` +
        `/sms/${record.control.token}`;
    }
    payload.instruction =
      "短信请求已由说明弹窗确认；请用户把本次验证码提供给 Agent，再通过一次性本机页面或标准输入交给当前登录进程。";
  } else if (record.state === LoginOperationState.CODE_SUBMITTED) {
    payload.instruction = "验证码已提交，正在等待登录结果。";
  }
  return payload;
}


function controlHeaders(contentType) {
  return {
    "Cache-Control": "no-store, max-age=0",
    "Content-Type": contentType,
    "Content-Security-Policy":
      "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    Pragma: "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  };
}


function temporaryCredentialHtml(route) {
  return `<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>尚舆 · 本次管易登录</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#172033;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:520px;margin:8vh auto;padding:30px;background:#fff;border:1px solid #e4e8f0;border-radius:14px;box-shadow:0 12px 36px #1226501a}h1{margin:0 0 10px;font-size:24px}p{margin:8px 0 20px;color:#586174}.field{margin:16px 0}label{display:block;margin-bottom:6px;font-weight:650}input{width:100%;height:44px;padding:0 12px;border:1px solid #cfd6e4;border-radius:8px;font-size:16px}button{width:100%;height:46px;margin-top:12px;border:0;border-radius:8px;background:#1677ff;color:#fff;font-size:16px;font-weight:650}.safe{padding:10px 12px;border-radius:8px;background:#eef6ff;color:#24568f;font-size:13px}</style>
<main><h1>本次管易登录</h1><p>账号密码只发送给当前正在等待的管易登录进程，用后即丢弃，不写入文件、环境变量、命令参数或日志。</p><div class="safe">地址必须以 <strong>http://127.0.0.1:</strong> 开头；页面只能提交一次，并随登录操作到期。</div><form method="post" action="${route}" autocomplete="off"><div class="field"><label for="username">管易登录账号</label><input id="username" name="username" required autocomplete="off"></div><div class="field"><label for="password">管易登录密码</label><input id="password" name="password" type="password" required autocomplete="off"></div><button type="submit">交给当前登录进程</button></form></main></html>`;
}


function temporaryCredentialSuccessHtml() {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>凭据已交接</title><style>body{font:16px/1.6 -apple-system,sans-serif;background:#f5f7fb;color:#172033}main{max-width:520px;margin:12vh auto;padding:32px;background:#fff;border-radius:14px;box-shadow:0 12px 36px #12305018}h1{color:#15803d}</style><main><h1>凭据已交接</h1><p>当前管易登录进程已经接收。可以关闭此页面并等待登录状态；本页面没有保存账号密码。</p></main></html>`;
}


function temporarySmsHtml(route) {
  return `<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>尚舆 · 本次短信验证码</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#172033;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:520px;margin:10vh auto;padding:30px;background:#fff;border:1px solid #e4e8f0;border-radius:14px;box-shadow:0 12px 36px #1226501a}h1{margin:0 0 10px;font-size:24px}p{color:#586174}label{display:block;margin:18px 0 6px;font-weight:650}input{width:100%;height:44px;padding:0 12px;border:1px solid #cfd6e4;border-radius:8px;font-size:18px}button{width:100%;height:46px;margin-top:18px;border:0;border-radius:8px;background:#1677ff;color:#fff;font-size:16px;font-weight:650}</style>
<main><h1>本次短信验证码</h1><p>验证码只交给当前管易登录进程，提交后页面立即失效。</p><form method="post" action="${route}" autocomplete="off"><label for="code">短信验证码</label><input id="code" name="code" inputmode="numeric" pattern="[0-9]{4,8}" minlength="4" maxlength="8" required autocomplete="off"><button type="submit">提交验证码</button></form></main></html>`;
}


function temporarySmsSuccessHtml() {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>验证码已交接</title><style>body{font:16px/1.6 -apple-system,sans-serif;background:#f5f7fb;color:#172033}main{max-width:520px;margin:12vh auto;padding:32px;background:#fff;border-radius:14px;box-shadow:0 12px 36px #12305018}h1{color:#15803d}</style><main><h1>验证码已交接</h1><p>当前管易登录进程正在验证，可以关闭此页面。</p></main></html>`;
}


async function readRequestBody(request, limit = 8192) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) {
      throw new LoginOperationError(
        "LOGIN_CONTROL_REQUEST_TOO_LARGE",
        "登录控制请求过大。",
      );
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}


export async function loadLoginOperation(
  operationId,
  directory = LOGIN_OPERATION_DIR,
) {
  const filePath = operationPath(operationId, directory);
  let record;
  try {
    record = JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch {
    throw new LoginOperationError(
      "LOGIN_OPERATION_NOT_FOUND",
      "找不到对应的登录操作。",
    );
  }
  return { record: validateRecord(record, operationId), receiptPath: filePath };
}


async function findActiveOperation(directory) {
  let entries = [];
  try {
    entries = await fs.readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
  const now = Date.now();
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    try {
      const record = JSON.parse(
        await fs.readFile(path.join(directory, entry.name), "utf8"),
      );
      if (
        record?.schemaVersion === "guanyi-login-operation/1.0" &&
        !TERMINAL_STATES.has(record.state) &&
        Date.parse(record.expiresAt) > now
      ) {
        return record;
      }
    } catch {
      // Invalid and unrelated files are ignored here and rejected when loaded directly.
    }
  }
  return null;
}


export async function createLoginOperation(
  directory = LOGIN_OPERATION_DIR,
  timeoutMs = LOGIN_TIMEOUT_MS,
) {
  const existing = await findActiveOperation(directory);
  if (existing) {
    throw new LoginOperationError(
      "LOGIN_OPERATION_ALREADY_ACTIVE",
      `已有登录操作 ${existing.operationId} 正在等待处理，请继续该操作。`,
    );
  }
  const operationId = randomUUID();
  const now = new Date();
  const record = {
    schemaVersion: "guanyi-login-operation/1.0",
    operationId,
    state: LoginOperationState.STARTING,
    createdAt: now.toISOString(),
    updatedAt: now.toISOString(),
    expiresAt: new Date(now.getTime() + timeoutMs).toISOString(),
    control: {
      host: "127.0.0.1",
      port: null,
      token: randomBytes(32).toString("hex"),
    },
    error: null,
    history: [
      {
        at: now.toISOString(),
        from: null,
        to: LoginOperationState.STARTING,
        reason: "login_operation_created",
      },
    ],
  };
  const receiptPath = operationPath(operationId, directory);
  await writeAtomic(receiptPath, record);
  return { record, receiptPath };
}


export async function setLoginControlEndpoint(
  operationId,
  port,
  directory = LOGIN_OPERATION_DIR,
) {
  const loaded = await loadLoginOperation(operationId, directory);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new LoginOperationError(
      "LOGIN_CONTROL_PORT_INVALID",
      "登录控制端口无效。",
    );
  }
  const updated = {
    ...loaded.record,
    updatedAt: new Date().toISOString(),
    control: { ...loaded.record.control, port },
  };
  await writeAtomic(loaded.receiptPath, updated);
  return { record: updated, receiptPath: loaded.receiptPath };
}


export async function transitionLoginOperation(
  operationId,
  nextState,
  {
    reason = null,
    code = null,
    message = null,
    details = null,
    expiresInMs = null,
  } = {},
  directory = LOGIN_OPERATION_DIR,
) {
  const loaded = await loadLoginOperation(operationId, directory);
  const current = loaded.record.state;
  if (!TRANSITIONS[current]?.has(nextState)) {
    throw new LoginOperationError(
      "LOGIN_OPERATION_TRANSITION_INVALID",
      `不允许从 ${current} 转换到 ${nextState}。`,
    );
  }
  const now = new Date().toISOString();
  const terminal = TERMINAL_STATES.has(nextState);
  const updated = {
    ...loaded.record,
    state: nextState,
    updatedAt: now,
    expiresAt:
      Number.isFinite(expiresInMs) && expiresInMs > 0
        ? new Date(Date.now() + expiresInMs).toISOString()
        : loaded.record.expiresAt,
    control: terminal
      ? { host: null, port: null, token: "" }
      : loaded.record.control,
    error:
      nextState === LoginOperationState.FAILED
        ? {
            code: code || "LOGIN_FAILED",
            message: message || "管易登录失败。",
            details,
          }
        : loaded.record.error,
    history: [
      ...loaded.record.history,
      { at: now, from: current, to: nextState, reason },
    ],
  };
  await writeAtomic(loaded.receiptPath, updated);
  return { record: updated, receiptPath: loaded.receiptPath };
}


export function requireSmsRequestAuthorization(authorization) {
  if (authorization !== SMS_REQUEST_AUTHORIZATION) {
    throw new LoginOperationError(
      "SMS_REQUEST_AUTHORIZATION_REQUIRED",
      "只有用户当次确认可以接收短信后，才能请求验证码。",
    );
  }
}


export function requireTemporaryCredentialsSubmitAuthorization(authorization) {
  if (authorization !== TEMPORARY_CREDENTIALS_SUBMIT_AUTHORIZATION) {
    throw new LoginOperationError(
      "TEMPORARY_CREDENTIALS_SUBMISSION_AUTHORIZATION_REQUIRED",
      "只有用户已在当前对话提供本次登录凭据并接受对话历史风险后，才能提交临时凭据。",
    );
  }
}


export async function waitForLoginOperation(
  operationId,
  {
    directory = LOGIN_OPERATION_DIR,
    timeoutMs = 30_000,
    states = new Set([
      LoginOperationState.WAITING_FOR_CREDENTIALS,
      LoginOperationState.NEEDS_VERIFICATION,
      LoginOperationState.WAITING_FOR_USER_CODE,
      LoginOperationState.CODE_SUBMITTED,
      LoginOperationState.AUTHENTICATED,
      LoginOperationState.FAILED,
      LoginOperationState.EXPIRED,
    ]),
  } = {},
) {
  const deadline = Date.now() + timeoutMs;
  let loaded = await loadLoginOperation(operationId, directory);
  while (!states.has(loaded.record.state) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    loaded = await loadLoginOperation(operationId, directory);
  }
  return loaded;
}


export async function createLoginControlServer(
  operationId,
  handler,
  directory = LOGIN_OPERATION_DIR,
) {
  const loaded = await loadLoginOperation(operationId, directory);
  const expectedToken = loaded.record.control.token;
  const credentialRoute = `/credentials/${expectedToken}`;
  const smsRoute = `/sms/${expectedToken}`;
  const controlRoute = `/control/${expectedToken}`;
  let credentialsAccepted = false;
  let smsAccepted = false;
  const server = http.createServer(async (request, response) => {
    const expectedOrigin = `http://${request.headers.host}`;
    const localHost = String(request.headers.host || "").split(":")[0];
    if (!new Set(["127.0.0.1", "localhost"]).has(localHost)) {
      response.writeHead(403, controlHeaders("text/plain; charset=utf-8"));
      response.end("Forbidden");
      return;
    }
    try {
      if (
        request.method === "GET" &&
        request.url === credentialRoute &&
        !credentialsAccepted
      ) {
        response.writeHead(200, controlHeaders("text/html; charset=utf-8"));
        response.end(temporaryCredentialHtml(credentialRoute));
        return;
      }
      if (
        request.method === "POST" &&
        request.url === credentialRoute &&
        !credentialsAccepted
      ) {
        if (
          request.headers.origin &&
          request.headers.origin !== expectedOrigin
        ) {
          throw new LoginOperationError(
            "LOGIN_CONTROL_ORIGIN_INVALID",
            "临时凭据页面来源无效。",
          );
        }
        const form = new URLSearchParams(await readRequestBody(request));
        const credentials = {
          username: (form.get("username") || "").trim(),
          password: form.get("password") || "",
        };
        try {
          await handler({
            action: "submit_credentials",
            code: null,
            credentials,
          });
        } finally {
          credentials.username = "";
          credentials.password = "";
        }
        credentialsAccepted = true;
        response.writeHead(200, controlHeaders("text/html; charset=utf-8"));
        response.end(temporaryCredentialSuccessHtml());
        return;
      }
      if (
        request.method === "GET" &&
        request.url === smsRoute &&
        !smsAccepted
      ) {
        response.writeHead(200, controlHeaders("text/html; charset=utf-8"));
        response.end(temporarySmsHtml(smsRoute));
        return;
      }
      if (
        request.method === "POST" &&
        request.url === smsRoute &&
        !smsAccepted
      ) {
        if (
          request.headers.origin &&
          request.headers.origin !== expectedOrigin
        ) {
          throw new LoginOperationError(
            "LOGIN_CONTROL_ORIGIN_INVALID",
            "短信验证码页面来源无效。",
          );
        }
        const form = new URLSearchParams(await readRequestBody(request));
        let code = (form.get("code") || "").trim();
        try {
          await handler({
            action: "submit_sms",
            code,
            credentials: null,
          });
        } finally {
          code = "";
        }
        smsAccepted = true;
        response.writeHead(200, controlHeaders("text/html; charset=utf-8"));
        response.end(temporarySmsSuccessHtml());
        return;
      }
      if (request.method === "POST" && request.url === controlRoute) {
        const message = JSON.parse(await readRequestBody(request));
        if (message.operationId !== operationId) {
          throw new LoginOperationError(
            "LOGIN_CONTROL_UNAUTHORIZED",
            "登录控制请求未通过校验。",
          );
        }
        let result;
        try {
          result = await handler({
            action: message.action,
            code: message.code,
            credentials: message.credentials,
          });
        } finally {
          if (message.credentials) {
            message.credentials.username = "";
            message.credentials.password = "";
          }
          message.code = null;
        }
        if (message.action === "submit_credentials") credentialsAccepted = true;
        if (message.action === "submit_sms") smsAccepted = true;
        response.writeHead(200, controlHeaders("application/json; charset=utf-8"));
        response.end(JSON.stringify({ ok: true, result }));
        return;
      }
      response.writeHead(404, controlHeaders("text/plain; charset=utf-8"));
      response.end("Not found");
    } catch (error) {
      const payload = JSON.stringify({
        ok: false,
        code: error?.code || "LOGIN_CONTROL_FAILED",
        message: error?.message || "登录控制请求失败。",
      });
      response.writeHead(400, controlHeaders("application/json; charset=utf-8"));
      response.end(payload);
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  await setLoginControlEndpoint(operationId, address.port, directory);
  return server;
}


export async function sendLoginControl(
  operationId,
  action,
  {
    code = null,
    credentials = null,
    directory = LOGIN_OPERATION_DIR,
    timeoutMs = 20_000,
  } = {},
) {
  const loaded = await loadLoginOperation(operationId, directory);
  const { host, port, token } = loaded.record.control;
  if (!host || !port || !token) {
    throw new LoginOperationError(
      "LOGIN_CONTROL_UNAVAILABLE",
      "登录操作当前没有可用的控制通道。",
    );
  }
  return await new Promise((resolve, reject) => {
    const body = JSON.stringify({
      operationId,
      action,
      code,
      credentials,
    });
    const request = http.request({
      hostname: host,
      port,
      path: `/control/${token}`,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    });
    const timer = setTimeout(() => {
      request.destroy();
      reject(
        new LoginOperationError(
          "LOGIN_CONTROL_TIMEOUT",
          "等待登录控制操作完成超时。",
        ),
      );
    }, timeoutMs);
    let output = "";
    request.once("response", (response) => {
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        output += chunk;
      });
      response.once("end", () => {
        clearTimeout(timer);
        try {
          const payload = JSON.parse(output.trim());
          if (!payload.ok) {
            reject(
              new LoginOperationError(
                payload.code || "LOGIN_CONTROL_FAILED",
                payload.message || "登录控制操作失败。",
              ),
            );
            return;
          }
          resolve(payload.result);
        } catch (error) {
          reject(
            error instanceof LoginOperationError
              ? error
              : new LoginOperationError(
                  "LOGIN_CONTROL_RESPONSE_INVALID",
                  "登录控制进程返回了无效响应。",
                ),
          );
        }
      });
    });
    request.once("error", (error) => {
      clearTimeout(timer);
      reject(
        new LoginOperationError(
          "LOGIN_CONTROL_UNAVAILABLE",
          "无法连接仍在等待验证码的登录进程。",
          { cause: error?.code || null },
        ),
      );
    });
    request.end(body);
  });
}
