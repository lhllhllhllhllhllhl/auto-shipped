import assert from "node:assert/strict";
import test from "node:test";

import { credentialProviderFor } from "./credentials.mjs";


test("selects a secure credential provider by operating system", () => {
  assert.equal(credentialProviderFor("darwin"), "macos_keychain");
  assert.equal(credentialProviderFor("win32"), "windows_dpapi");
  assert.equal(credentialProviderFor("linux"), "unsupported");
});
