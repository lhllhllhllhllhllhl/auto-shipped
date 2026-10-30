import { spawn } from "node:child_process";
import { createInterface } from "node:readline";


export class BrowserOperationBrokerError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BrowserOperationBrokerError";
    this.code = code;
  }
}


export async function acquireBrowserOperationLease(
  operationId,
  {
    command = process.env.GUANYI_BROWSER_BROKER_COMMAND || "",
    timeoutMs = Number(process.env.GUANYI_BROWSER_BROKER_TIMEOUT_MS || 30 * 60_000),
    spawnImpl = spawn,
  } = {},
) {
  if (!command) {
    return {
      enabled: false,
      metadata: null,
      release: async () => {},
    };
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(operationId || "")) {
    throw new BrowserOperationBrokerError(
      "BROKER_OPERATION_ID_INVALID",
      "管易浏览器操作缺少有效的共享租约任务 ID。",
    );
  }
  const child = spawnImpl(
    command,
    ["acquire", "guanyi", operationId, "--timeout", String(Math.ceil(timeoutMs / 1000))],
    { stdio: ["pipe", "pipe", "ignore"], windowsHide: true },
  );
  const line = await new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill("SIGTERM");
      reject(
        new BrowserOperationBrokerError(
          "BROKER_ACQUIRE_TIMEOUT",
          "等待服务器浏览器操作租约超时。",
        ),
      );
    }, timeoutMs + 5_000);
    const reader = createInterface({ input: child.stdout });
    reader.once("line", (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reader.close();
      resolve(value);
    });
    child.once("error", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reader.close();
      reject(
        new BrowserOperationBrokerError(
          "BROKER_UNAVAILABLE",
          "服务器浏览器操作 broker 不可用。",
        ),
      );
    });
    child.once("exit", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reader.close();
      reject(
        new BrowserOperationBrokerError(
          "BROKER_ACQUIRE_FAILED",
          "服务器浏览器操作 broker 未授予租约。",
        ),
      );
    });
  });
  let metadata;
  try {
    metadata = JSON.parse(line);
  } catch {
    child.kill("SIGTERM");
    throw new BrowserOperationBrokerError(
      "BROKER_RESPONSE_INVALID",
      "服务器浏览器操作 broker 返回无效。",
    );
  }
  if (metadata?.status !== "lease_acquired") {
    child.kill("SIGTERM");
    throw new BrowserOperationBrokerError(
      metadata?.code || "BROKER_ACQUIRE_FAILED",
      metadata?.message || "服务器浏览器操作 broker 未授予租约。",
    );
  }
  let released = false;
  return {
    enabled: true,
    metadata,
    async release() {
      if (released) return;
      released = true;
      child.stdin.end();
      await new Promise((resolve) => {
        const timer = setTimeout(() => {
          child.kill("SIGTERM");
          resolve();
        }, 10_000);
        child.once("exit", () => {
          clearTimeout(timer);
          resolve();
        });
      });
    },
  };
}


export async function withBrowserOperationLease(operationId, callback, options) {
  const lease = await acquireBrowserOperationLease(operationId, options);
  try {
    return await callback(lease.metadata);
  } finally {
    await lease.release();
  }
}
