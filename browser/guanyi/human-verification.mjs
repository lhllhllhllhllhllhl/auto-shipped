import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { BROWSER_WINDOW_CLASS } from "./config.mjs";


const execFileAsync = promisify(execFile);

async function runBoundedCommand(command, args) {
  return await execFileAsync(command, args, {
    timeout: 5_000,
    windowsHide: true,
  });
}

const REGISTERED_CHALLENGES = Object.freeze([
  Object.freeze({
    hostname: "account.ose.aliyun.com",
    pathname: "/verify.html",
    type: "aliyun_account_verification",
  }),
]);


function safePageUrl(parsed) {
  return `${parsed.origin}${parsed.pathname}`;
}


export function classifyRegisteredHumanVerificationUrl(url) {
  let parsed;
  try {
    parsed = new URL(String(url || ""));
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  const matched = REGISTERED_CHALLENGES.find(
    (challenge) =>
      parsed.hostname.toLowerCase() === challenge.hostname &&
      parsed.pathname === challenge.pathname,
  );
  if (!matched) return null;
  return {
    type: matched.type,
    safeUrl: safePageUrl(parsed),
  };
}


function exactWindowClassPattern(windowClass) {
  return `^${windowClass.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`;
}


export async function focusHumanVerificationWindow(
  page,
  {
    browserControlTarget = "existing_playwright_browser_via_platform_desktop",
    platform = process.platform,
    runCommand = runBoundedCommand,
    windowClass = BROWSER_WINDOW_CLASS,
  } = {},
) {
  try {
    await page.bringToFront();
  } catch {
    return {
      focusReady: false,
      method: "playwright",
      code: "HUMAN_VERIFICATION_PAGE_UNAVAILABLE",
    };
  }
  if (browserControlTarget === "existing_platform_builtin_browser") {
    return {
      focusReady: true,
      method: "playwright_platform_browser",
      browserControlTarget,
    };
  }
  if (platform !== "linux") {
    return { focusReady: true, method: "playwright" };
  }

  try {
    const searched = await runCommand("xdotool", [
      "search",
      "--onlyvisible",
      "--class",
      exactWindowClassPattern(windowClass),
    ]);
    const windowIds = String(searched?.stdout || "")
      .split(/\s+/)
      .filter((value) => /^\d+$/.test(value));
    if (windowIds.length !== 1) {
      return {
        focusReady: false,
        method: "playwright",
        code:
          windowIds.length === 0
            ? "HUMAN_VERIFICATION_WINDOW_NOT_FOUND"
            : "HUMAN_VERIFICATION_WINDOW_AMBIGUOUS",
      };
    }
    const windowId = windowIds[0];
    await runCommand("xdotool", ["windowactivate", "--sync", windowId]);
    await runCommand("xdotool", ["windowraise", windowId]);
    await runCommand("xdotool", ["windowfocus", windowId]);
    const active = await runCommand("xdotool", ["getactivewindow"]);
    if (String(active?.stdout || "").trim() !== windowId) {
      return {
        focusReady: false,
        method: "playwright",
        code: "HUMAN_VERIFICATION_WINDOW_FOCUS_NOT_CONFIRMED",
      };
    }
    return { focusReady: true, method: "xdotool_exact_window_class" };
  } catch (error) {
    return {
      focusReady: false,
      method: "playwright",
      code:
        error?.code === "ENOENT"
          ? "HUMAN_VERIFICATION_WINDOW_TOOL_UNAVAILABLE"
          : error?.code === 1
            ? "HUMAN_VERIFICATION_WINDOW_NOT_FOUND"
          : "HUMAN_VERIFICATION_WINDOW_FOCUS_FAILED",
    };
  }
}
