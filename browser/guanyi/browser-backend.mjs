export const BrowserBackend = Object.freeze({
  AUTO: "auto",
  PERSISTENT: "persistent",
  PLATFORM_CDP: "platform_cdp",
});


export class BrowserBackendError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "BrowserBackendError";
    this.code = code;
    this.details = details;
  }
}


export function normalizeBrowserBackend(value) {
  const normalized = String(value || BrowserBackend.AUTO).trim().toLowerCase();
  if (!Object.values(BrowserBackend).includes(normalized)) {
    throw new BrowserBackendError(
      "BROWSER_BACKEND_INVALID",
      "浏览器接入方式不受支持。",
      { backend: normalized },
    );
  }
  return normalized;
}


export function normalizeLoopbackCdpEndpoint(value) {
  let parsed;
  try {
    parsed = new URL(String(value || ""));
  } catch {
    throw new BrowserBackendError(
      "CDP_ENDPOINT_INVALID",
      "平台浏览器 CDP 地址格式不正确。",
    );
  }
  const hostname = parsed.hostname.toLowerCase();
  if (
    parsed.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "[::1]"].includes(hostname) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    (parsed.pathname && parsed.pathname !== "/")
  ) {
    throw new BrowserBackendError(
      "CDP_ENDPOINT_NOT_LOOPBACK",
      "平台浏览器只允许通过无凭据的本机 HTTP CDP 地址接入。",
    );
  }
  return parsed.origin;
}


export async function probePlatformCdp(
  endpoint,
  {
    fetchImpl = globalThis.fetch,
    timeoutMs = 1_500,
  } = {},
) {
  const origin = normalizeLoopbackCdpEndpoint(endpoint);
  if (typeof fetchImpl !== "function") return false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${origin}/json/version`, {
      signal: controller.signal,
      redirect: "error",
    });
    if (!response.ok) return false;
    const payload = await response.json();
    return (
      /(?:chrome|chromium)/i.test(String(payload?.Browser || "")) &&
      /^ws:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?\//i.test(
        String(payload?.webSocketDebuggerUrl || ""),
      )
    );
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}


export async function resolveBrowserBackend({
  configuredBackend = BrowserBackend.AUTO,
  platform = process.platform,
  cdpEndpoint,
  probe = probePlatformCdp,
} = {}) {
  const backend = normalizeBrowserBackend(configuredBackend);
  if (backend !== BrowserBackend.AUTO) return backend;
  if (platform !== "linux") return BrowserBackend.PERSISTENT;
  return (await probe(cdpEndpoint))
    ? BrowserBackend.PLATFORM_CDP
    : BrowserBackend.PERSISTENT;
}


export async function connectPlatformBrowser(
  chromiumInstance,
  endpoint,
) {
  const normalizedEndpoint = normalizeLoopbackCdpEndpoint(endpoint);
  let browser;
  try {
    browser = await chromiumInstance.connectOverCDP(normalizedEndpoint, {
      timeout: 10_000,
    });
  } catch (error) {
    throw new BrowserBackendError(
      "PLATFORM_CDP_UNAVAILABLE",
      "无法连接平台自带浏览器；已停止，未另启第二个浏览器。",
      { cause: error?.message || String(error) },
    );
  }
  const contexts = browser.contexts();
  if (contexts.length !== 1) {
    await browser.close().catch(() => null);
    throw new BrowserBackendError(
      "PLATFORM_CDP_CONTEXT_AMBIGUOUS",
      "平台浏览器 context 数量不唯一，无法安全选择。",
      { contextCount: contexts.length },
    );
  }
  return {
    backend: BrowserBackend.PLATFORM_CDP,
    browser,
    context: contexts[0],
    ownsContext: false,
    // For a connectOverCDP browser this closes Playwright's transport; the
    // externally owned Chrome process and its default context remain alive.
    close: async () => await browser.close(),
  };
}
