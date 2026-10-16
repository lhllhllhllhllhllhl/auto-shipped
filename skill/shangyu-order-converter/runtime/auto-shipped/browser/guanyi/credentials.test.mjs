import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  credentialProviderFor,
  readExternalCredentials,
  replacedMacCredentialAccount,
} from "./credentials.mjs";


test("selects a secure credential provider by operating system", () => {
  assert.equal(credentialProviderFor("darwin", ""), "macos_keychain");
  assert.equal(credentialProviderFor("win32", ""), "windows_dpapi");
  assert.equal(credentialProviderFor("linux", ""), "unsupported");
  assert.equal(
    credentialProviderFor("linux", "/opt/agent/bin/guanyi-secret"),
    "external_secret_helper",
  );
});


test("removes only a different macOS Keychain account during replacement", () => {
  assert.equal(replacedMacCredentialAccount("old-user", "new-user"), "old-user");
  assert.equal(replacedMacCredentialAccount("same-user", "same-user"), null);
  assert.equal(replacedMacCredentialAccount(null, "new-user"), null);
});


test("reads cloud credentials only through an executable helper pipe", {
  skip: process.platform === "win32",
}, async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-secret-"));
  const helper = path.join(directory, "credential-helper");
  try {
    await fs.writeFile(
      helper,
      "#!/bin/sh\n[ \"$1\" = \"get\" ] || exit 2\nprintf '%s\\n' '{\"username\":\"cloud-user\",\"password\":\"test-secret\"}'\n",
      { mode: 0o700 },
    );
    const value = await readExternalCredentials(helper);
    assert.deepEqual(value, {
      username: "cloud-user",
      password: "test-secret",
    });
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});
