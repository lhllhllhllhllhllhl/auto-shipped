import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyRegisteredHumanVerificationUrl,
  focusHumanVerificationWindow,
} from "./human-verification.mjs";


test("registers only the exact Aliyun verification page and strips its query", () => {
  assert.deepEqual(
    classifyRegisteredHumanVerificationUrl(
      "http://account.ose.aliyun.com/verify.html?account=private",
    ),
    {
      type: "aliyun_account_verification",
      safeUrl: "http://account.ose.aliyun.com/verify.html",
    },
  );
  assert.equal(
    classifyRegisteredHumanVerificationUrl(
      "https://account.ose.aliyun.com/another.html",
    ),
    null,
  );
  assert.equal(
    classifyRegisteredHumanVerificationUrl(
      "https://account.ose.aliyun.com.example.com/verify.html",
    ),
    null,
  );
});


test("focuses exactly one registered Linux window without page interaction", async () => {
  const commands = [];
  let broughtToFront = 0;
  const result = await focusHumanVerificationWindow(
    {
      async bringToFront() {
        broughtToFront += 1;
      },
    },
    {
      platform: "linux",
      async runCommand(command, args) {
        commands.push([command, args]);
        if (args[0] === "search") return { stdout: "16777219\n" };
        if (args[0] === "getactivewindow") return { stdout: "16777219\n" };
        return { stdout: "" };
      },
    },
  );

  assert.deepEqual(result, {
    focusReady: true,
    method: "xdotool_exact_window_class",
  });
  assert.equal(broughtToFront, 1);
  assert.deepEqual(commands, [
    [
      "xdotool",
      [
        "search",
        "--onlyvisible",
        "--class",
        "^shangyu-guanyi-browser$",
      ],
    ],
    ["xdotool", ["windowactivate", "--sync", "16777219"]],
    ["xdotool", ["windowraise", "16777219"]],
    ["xdotool", ["windowfocus", "16777219"]],
    ["xdotool", ["getactivewindow"]],
  ]);
});


test("platform browser handoff uses the visible browser without VNC window search", async () => {
  let broughtToFront = 0;
  let commandCount = 0;
  const result = await focusHumanVerificationWindow(
    {
      async bringToFront() {
        broughtToFront += 1;
      },
    },
    {
      browserControlTarget: "existing_platform_builtin_browser",
      platform: "linux",
      async runCommand() {
        commandCount += 1;
      },
    },
  );

  assert.deepEqual(result, {
    focusReady: true,
    method: "playwright_platform_browser",
    browserControlTarget: "existing_platform_builtin_browser",
  });
  assert.equal(broughtToFront, 1);
  assert.equal(commandCount, 0);
});


test("does not guess when more than one matching window exists", async () => {
  const result = await focusHumanVerificationWindow(
    { async bringToFront() {} },
    {
      platform: "linux",
      async runCommand() {
        return { stdout: "10\n20\n" };
      },
    },
  );
  assert.deepEqual(result, {
    focusReady: false,
    method: "playwright",
    code: "HUMAN_VERIFICATION_WINDOW_AMBIGUOUS",
  });
});
