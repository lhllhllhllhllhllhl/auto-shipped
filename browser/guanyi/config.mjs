import { fileURLToPath } from "node:url";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";


const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));

export const PROJECT_ROOT = path.resolve(MODULE_DIR, "../..");
export const RUNTIME_ROOT = path.resolve(
  process.env.GUANYI_RUNTIME_ROOT || path.join(PROJECT_ROOT, "runtime"),
);
export const PROFILE_DIR = path.resolve(
  process.env.GUANYI_PROFILE_DIR || path.join(RUNTIME_ROOT, "guanyi-browser-profile"),
);
export const BROWSER_SESSION_LOCK = path.resolve(
  process.env.GUANYI_BROWSER_SESSION_LOCK ||
    `${PROFILE_DIR}.lock`,
);
export const ARTIFACT_DIR = path.resolve(
  process.env.GUANYI_ARTIFACT_DIR || path.join(RUNTIME_ROOT, "guanyi-browser-artifacts"),
);
export const OPERATION_DIR = path.resolve(
  process.env.GUANYI_OPERATION_DIR || path.join(RUNTIME_ROOT, "guanyi-operations"),
);
export const LOGIN_OPERATION_DIR = path.resolve(
  process.env.GUANYI_LOGIN_OPERATION_DIR ||
    path.join(RUNTIME_ROOT, "guanyi-login-operations"),
);
export const CREDENTIAL_HELPER = process.env.GUANYI_CREDENTIAL_HELPER || "";
export function defaultBrowserBackend(
  platform = process.platform,
  environment = process.env,
  pathExists = fs.existsSync,
) {
  if (environment.GUANYI_BROWSER_BACKEND) {
    return environment.GUANYI_BROWSER_BACKEND;
  }
  if (
    platform === "linux" &&
    ("DOUBAO_OFFICE_USER_ACCESS_TOKEN" in environment ||
      pathExists("/opt/browser/chrome"))
  ) {
    return "platform_cdp";
  }
  return "auto";
}


export const BROWSER_BACKEND = defaultBrowserBackend();
export const PLATFORM_CDP_ENDPOINT =
  process.env.GUANYI_CDP_ENDPOINT || "http://127.0.0.1:9222";
function pathCandidates(names, environment = process.env) {
  const directories = String(environment.PATH || "")
    .split(path.delimiter)
    .filter(Boolean);
  return directories.flatMap((directory) =>
    names.map((name) => path.join(directory, name)),
  );
}


export function browserCandidates(
  platform = process.platform,
  environment = process.env,
) {
  const configured = environment.GUANYI_CHROME_EXECUTABLE;
  if (platform === "darwin") {
    return [
      configured,
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ];
  }
  if (platform === "win32") {
    return [
      configured,
      environment.PROGRAMFILES
        ? path.join(environment.PROGRAMFILES, "Google/Chrome/Application/chrome.exe")
        : null,
      environment["PROGRAMFILES(X86)"]
        ? path.join(environment["PROGRAMFILES(X86)"], "Google/Chrome/Application/chrome.exe")
        : null,
      environment.LOCALAPPDATA
        ? path.join(environment.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe")
        : null,
    ];
  }
  return [
    configured,
    "/usr/local/bin/chromium",
    "/usr/local/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/opt/browser/chrome",
    ...pathCandidates(
      ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"],
      environment,
    ),
  ];
}


const CHROME_CANDIDATES = browserCandidates().filter(Boolean);
export const CHROME_EXECUTABLE =
  CHROME_CANDIDATES.find((candidate) => fs.existsSync(candidate)) ||
  CHROME_CANDIDATES[0] ||
  "";

export const WINDOW_WIDTH = Number(
  process.env.GUANYI_WINDOW_WIDTH || 1440,
);
export const WINDOW_HEIGHT = Number(
  process.env.GUANYI_WINDOW_HEIGHT || 850,
);
export const MINIMUM_INNER_WIDTH = Number(
  process.env.GUANYI_MINIMUM_INNER_WIDTH || 1200,
);
export const MINIMUM_INNER_HEIGHT = Number(
  process.env.GUANYI_MINIMUM_INNER_HEIGHT || 620,
);
export const WINDOW_TOLERANCE = Number(
  process.env.GUANYI_WINDOW_TOLERANCE || 40,
);

export const LOGIN_URL = "https://login.guanyierp.com/login";
export const APP_URL = "https://www.guanyierp.com/index";
export const DEFAULT_TIMEOUT_MS = Number(
  process.env.GUANYI_BROWSER_TIMEOUT_MS || 15_000,
);
export const LOGIN_TIMEOUT_MS = Number(
  process.env.GUANYI_LOGIN_TIMEOUT_MS || 10 * 60_000,
);
export const LOGIN_RESULT_TIMEOUT_MS = Number(
  process.env.GUANYI_LOGIN_RESULT_TIMEOUT_MS || 45_000,
);
export const HUMAN_VERIFICATION_TIMEOUT_MS = Number(
  process.env.GUANYI_HUMAN_VERIFICATION_TIMEOUT_MS || 15 * 60_000,
);
export const BROWSER_WINDOW_CLASS = "shangyu-guanyi-browser";
export const KEYCHAIN_SERVICE =
  process.env.GUANYI_KEYCHAIN_SERVICE || "com.shangyu.auto-shipped.guanyi";
export const WINDOWS_CREDENTIAL_FILE = path.resolve(
  process.env.GUANYI_WINDOWS_CREDENTIAL_FILE ||
    path.join(os.homedir(), ".codex", "credentials", "shangyu-guanyi.json"),
);
