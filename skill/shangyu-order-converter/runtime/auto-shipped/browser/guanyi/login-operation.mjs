import { randomBytes, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import net from "node:net";
import path from "node:path";

import { LOGIN_OPERATION_DIR, LOGIN_TIMEOUT_MS } from "./config.mjs";


export const SMS_REQUEST_AUTHORIZATION = "CONFIRM_REQUEST_GUANYI_SMS";
export const TEMPORARY_CREDENTIALS_READY_AUTHORIZATION =
  "CONFIRM_TEMPORARY_CREDENTIALS_FILLED";


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
  }
  if (record.state === LoginOperationState.WAITING_FOR_CREDENTIALS) {
    payload.credentialMode = "temporary_agent_input";
    payload.instruction =
      "请先说明密码会保留在平台对话历史中；用户明确接受后，由 Agent 通过私有 GUI 填入当前管易登录页，再确认凭据已填好。";
  } else if (record.state === LoginOperationState.NEEDS_VERIFICATION) {
    payload.verificationType = "sms";
    payload.instruction = "请询问用户现在是否方便接收短信验证码；尚未发送短信。";
  } else if (record.state === LoginOperationState.WAITING_FOR_USER_CODE) {
    payload.verificationType = "sms";
    payload.instruction = "短信已请求，请用户把本次验证码提供给 Agent。";
  } else if (record.state === LoginOperationState.CODE_SUBMITTED) {
    payload.instruction = "验证码已提交，正在等待登录结果。";
  }
  return payload;
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
  { reason = null, code = null, message = null } = {},
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
    control: terminal
      ? { host: null, port: null, token: "" }
      : loaded.record.control,
    error:
      nextState === LoginOperationState.FAILED
        ? {
            code: code || "LOGIN_FAILED",
            message: message || "管易登录失败。",
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


export function requireTemporaryCredentialsReadyAuthorization(authorization) {
  if (authorization !== TEMPORARY_CREDENTIALS_READY_AUTHORIZATION) {
    throw new LoginOperationError(
      "TEMPORARY_CREDENTIALS_READY_AUTHORIZATION_REQUIRED",
      "只有用户已在当前对话提供本次登录凭据且 Agent 已通过私有 GUI 填好后，才能继续登录。",
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
  const server = net.createServer((socket) => {
    socket.setEncoding("utf8");
    let input = "";
    socket.on("data", (chunk) => {
      input += chunk;
      if (input.length > 2048) socket.destroy();
      if (!input.includes("\n")) return;
      const line = input.slice(0, input.indexOf("\n"));
      Promise.resolve()
        .then(async () => {
          const message = JSON.parse(line);
          if (
            message.operationId !== operationId ||
            message.token !== expectedToken
          ) {
            throw new LoginOperationError(
              "LOGIN_CONTROL_UNAUTHORIZED",
              "登录控制请求未通过校验。",
            );
          }
          return await handler({
            action: message.action,
            code: message.code,
          });
        })
        .then((result) => {
          socket.end(`${JSON.stringify({ ok: true, result })}\n`);
        })
        .catch((error) => {
          socket.end(
            `${JSON.stringify({
              ok: false,
              code: error?.code || "LOGIN_CONTROL_FAILED",
              message: error?.message || "登录控制请求失败。",
            })}\n`,
          );
        });
    });
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
  { code = null, directory = LOGIN_OPERATION_DIR, timeoutMs = 20_000 } = {},
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
    const socket = net.createConnection({ host, port });
    const timer = setTimeout(() => {
      socket.destroy();
      reject(
        new LoginOperationError(
          "LOGIN_CONTROL_TIMEOUT",
          "等待登录控制操作完成超时。",
        ),
      );
    }, timeoutMs);
    let output = "";
    socket.setEncoding("utf8");
    socket.once("connect", () => {
      socket.write(
        `${JSON.stringify({ operationId, token, action, code })}\n`,
      );
    });
    socket.on("data", (chunk) => {
      output += chunk;
    });
    socket.once("error", (error) => {
      clearTimeout(timer);
      reject(
        new LoginOperationError(
          "LOGIN_CONTROL_UNAVAILABLE",
          "无法连接仍在等待验证码的登录进程。",
          { cause: error?.code || null },
        ),
      );
    });
    socket.once("end", () => {
      clearTimeout(timer);
      try {
        const response = JSON.parse(output.trim());
        if (!response.ok) {
          reject(
            new LoginOperationError(
              response.code || "LOGIN_CONTROL_FAILED",
              response.message || "登录控制操作失败。",
            ),
          );
          return;
        }
        resolve(response.result);
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
}

