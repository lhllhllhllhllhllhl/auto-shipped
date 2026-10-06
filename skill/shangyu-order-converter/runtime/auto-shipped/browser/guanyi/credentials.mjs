import { execFile, spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

import {
  KEYCHAIN_SERVICE,
  WINDOWS_CREDENTIAL_FILE,
} from "./config.mjs";


const execFileAsync = promisify(execFile);


export class CredentialError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "CredentialError";
    this.code = code;
  }
}


export function credentialProviderFor(platform) {
  if (platform === "darwin") {
    return "macos_keychain";
  }
  if (platform === "win32") {
    return "windows_dpapi";
  }
  return "unsupported";
}


export function credentialProviderName() {
  return credentialProviderFor(process.platform);
}


function requireSupportedPlatform() {
  const provider = credentialProviderName();
  if (provider === "unsupported") {
    throw new CredentialError(
      "CREDENTIAL_PROVIDER_UNSUPPORTED",
      `当前系统 ${process.platform} 尚未配置安全凭证提供器。`,
    );
  }
  return provider;
}


function parseAccountName(output) {
  const match = output.match(/^\s*"acct"<blob>="((?:[^"\\]|\\.)*)"/m);
  if (!match) {
    throw new CredentialError(
      "CREDENTIAL_ACCOUNT_MISSING",
      "安全凭证存在，但无法读取登录账号。",
    );
  }
  return match[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\");
}


async function findMacMetadata() {
  try {
    return await execFileAsync(
      "security",
      ["find-generic-password", "-s", KEYCHAIN_SERVICE],
      { encoding: "utf8", maxBuffer: 1024 * 1024 },
    );
  } catch {
    throw new CredentialError(
      "CREDENTIAL_MISSING",
      "尚未配置管易 macOS 钥匙串凭证。",
    );
  }
}


function powershellExecutable() {
  return process.env.GUANYI_POWERSHELL_EXECUTABLE || "powershell.exe";
}


async function runPowerShell(script, input = null) {
  return await new Promise((resolve, reject) => {
    const child = spawn(
      powershellExecutable(),
      [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
      ],
      {
        env: {
          ...process.env,
          SHANGYU_GUANYI_CREDENTIAL_FILE: WINDOWS_CREDENTIAL_FILE,
        },
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) {
        resolve(stdout);
      } else {
        reject(new Error(stderr.trim() || `powershell_exit_${code}`));
      }
    });
    child.stdin.end(input === null ? "" : input);
  });
}


async function readMacCredentials() {
  const metadata = await findMacMetadata();
  const username = parseAccountName(metadata.stdout);
  try {
    const result = await execFileAsync(
      "security",
      ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
      { encoding: "utf8", maxBuffer: 1024 * 1024 },
    );
    const password = result.stdout.replace(/[\r\n]+$/, "");
    if (!username || !password) {
      throw new Error("incomplete");
    }
    return { username, password };
  } catch {
    throw new CredentialError(
      "CREDENTIAL_PASSWORD_UNAVAILABLE",
      "macOS 钥匙串凭证存在，但密码当前不可读取。",
    );
  }
}


async function readWindowsCredentials() {
  const script = `
$ErrorActionPreference = 'Stop'
$data = Get-Content -LiteralPath $env:SHANGYU_GUANYI_CREDENTIAL_FILE -Raw | ConvertFrom-Json
$secure = ConvertTo-SecureString $data.password
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
  @{ username = $data.username; password = $plain } | ConvertTo-Json -Compress
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}`;
  try {
    const output = await runPowerShell(script);
    const value = JSON.parse(output.trim());
    if (!value.username || !value.password) {
      throw new Error("incomplete");
    }
    return { username: value.username, password: value.password };
  } catch {
    throw new CredentialError(
      "CREDENTIAL_MISSING",
      "尚未配置或无法解密当前 Windows 用户的管易凭证。",
    );
  }
}


async function storeMacCredentials(username, password) {
  return await new Promise((resolve, reject) => {
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
      { stdio: ["pipe", "ignore", "pipe"] },
    );
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(stderr.trim() || "keychain_write_failed"));
      }
    });
    child.stdin.end(`${password}\n${password}\n`);
  });
}


async function storeWindowsCredentials(username, password) {
  const script = `
$ErrorActionPreference = 'Stop'
$value = [Console]::In.ReadToEnd() | ConvertFrom-Json
$parent = Split-Path -Parent $env:SHANGYU_GUANYI_CREDENTIAL_FILE
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$secure = ConvertTo-SecureString $value.password -AsPlainText -Force
$encrypted = ConvertFrom-SecureString $secure
@{ username = $value.username; password = $encrypted } |
  ConvertTo-Json -Compress |
  Set-Content -LiteralPath $env:SHANGYU_GUANYI_CREDENTIAL_FILE -Encoding UTF8
`;
  await runPowerShell(script, JSON.stringify({ username, password }));
}


export async function storedCredentialStatus() {
  const provider = requireSupportedPlatform();
  try {
    if (provider === "macos_keychain") {
      await findMacMetadata();
    } else {
      await fs.access(WINDOWS_CREDENTIAL_FILE);
      await readWindowsCredentials();
    }
    return { configured: true, provider };
  } catch (error) {
    if (error instanceof CredentialError || error?.code === "ENOENT") {
      return {
        configured: false,
        provider,
        code: error.code || "CREDENTIAL_MISSING",
      };
    }
    throw error;
  }
}


export async function readStoredCredentials() {
  const provider = requireSupportedPlatform();
  return provider === "macos_keychain"
    ? await readMacCredentials()
    : await readWindowsCredentials();
}


export async function storeCredentials(username, password) {
  const provider = requireSupportedPlatform();
  if (!username || !password) {
    throw new CredentialError(
      "CREDENTIAL_INVALID",
      "账号和密码不能为空。",
    );
  }
  if (provider === "macos_keychain") {
    await storeMacCredentials(username, password);
  } else {
    await fs.mkdir(path.dirname(WINDOWS_CREDENTIAL_FILE), {
      recursive: true,
      mode: 0o700,
    });
    await storeWindowsCredentials(username, password);
  }
  return { provider };
}
