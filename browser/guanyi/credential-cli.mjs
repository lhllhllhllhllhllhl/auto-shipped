#!/usr/bin/env node

import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline/promises";

import { KEYCHAIN_SERVICE } from "./config.mjs";
import {
  credentialProviderName,
  storedCredentialStatus,
} from "./credentials.mjs";


function printJson(payload) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
}


async function setCredential() {
  const provider = credentialProviderName();
  if (provider === "unsupported") {
    printJson({ status: "unsupported", platform: process.platform, provider });
    return 1;
  }
  if (provider === "windows_dpapi") {
    printJson({
      status: "local_web_setup_required",
      provider,
      instruction: "请执行 npm run credential:web，在本机一次性页面中安全配置。",
    });
    return 2;
  }
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    printJson({
      status: "interactive_terminal_required",
      instruction: "请在本机终端执行 npm run credential:set。",
    });
    return 2;
  }

  const prompt = createInterface({ input: process.stdin, output: process.stdout });
  const username = (await prompt.question("管易登录账号： ")).trim();
  prompt.close();
  if (!username) {
    printJson({ status: "cancelled", reason: "empty_username" });
    return 2;
  }

  process.stdout.write("接下来由 macOS 钥匙串安全提示输入密码；输入内容不会显示。\n");
  const child = spawn(
    "security",
    [
      "add-generic-password",
      "-U",
      "-a",
      username,
      "-s",
      KEYCHAIN_SERVICE,
      "-w",
    ],
    { stdio: "inherit" },
  );
  const [exitCode] = await once(child, "exit");
  if (exitCode !== 0) {
    printJson({ status: "failed", code: "KEYCHAIN_WRITE_FAILED" });
    return 1;
  }
  printJson({ status: "configured", provider, service: KEYCHAIN_SERVICE });
  return 0;
}


async function main() {
  const command = process.argv[2] || "status";
  if (command === "status") {
    const status = await storedCredentialStatus();
    printJson({ status: status.configured ? "configured" : "missing", ...status });
    return status.configured ? 0 : 2;
  }
  if (command === "set") {
    return await setCredential();
  }
  printJson({ usage: "node credential-cli.mjs <status|set>" });
  return 1;
}


process.exitCode = await main();
