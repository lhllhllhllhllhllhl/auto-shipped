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
export const ARTIFACT_DIR = path.resolve(
  process.env.GUANYI_ARTIFACT_DIR || path.join(RUNTIME_ROOT, "guanyi-browser-artifacts"),
);
export const OPERATION_DIR = path.resolve(
  process.env.GUANYI_OPERATION_DIR || path.join(RUNTIME_ROOT, "guanyi-operations"),
);
function browserCandidates() {
  const configured = process.env.GUANYI_CHROME_EXECUTABLE;
  if (process.platform === "darwin") {
    return [
      configured,
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ];
  }
  if (process.platform === "win32") {
    return [
      configured,
      process.env.PROGRAMFILES
        ? path.join(process.env.PROGRAMFILES, "Google/Chrome/Application/chrome.exe")
        : null,
      process.env["PROGRAMFILES(X86)"]
        ? path.join(process.env["PROGRAMFILES(X86)"], "Google/Chrome/Application/chrome.exe")
        : null,
      process.env.LOCALAPPDATA
        ? path.join(process.env.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe")
        : null,
    ];
  }
  return [
    configured,
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
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
  process.env.GUANYI_LOGIN_TIMEOUT_MS || 5 * 60_000,
);
export const KEYCHAIN_SERVICE =
  process.env.GUANYI_KEYCHAIN_SERVICE || "com.shangyu.auto-shipped.guanyi";
export const WINDOWS_CREDENTIAL_FILE = path.resolve(
  process.env.GUANYI_WINDOWS_CREDENTIAL_FILE ||
    path.join(os.homedir(), ".codex", "credentials", "shangyu-guanyi.json"),
);
