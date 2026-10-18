import assert from "node:assert/strict";
import test from "node:test";

import { browserCandidates, defaultBrowserBackend } from "./config.mjs";


test("discovers common Linux Chromium locations and PATH entries", () => {
  const candidates = browserCandidates("linux", {
    PATH: "/custom/bin:/another/bin",
  });
  assert.ok(candidates.includes("/usr/local/bin/chromium"));
  assert.ok(candidates.includes("/opt/browser/chrome"));
  assert.ok(candidates.includes("/custom/bin/chromium"));
  assert.ok(candidates.includes("/another/bin/google-chrome"));
});


test("requires platform CDP in a Doubao Work Linux environment", () => {
  assert.equal(
    defaultBrowserBackend(
      "linux",
      { DOUBAO_OFFICE_USER_ACCESS_TOKEN: "redacted" },
      () => false,
    ),
    "platform_cdp",
  );
  assert.equal(
    defaultBrowserBackend("linux", {}, (candidate) =>
      candidate === "/opt/browser/chrome"),
    "platform_cdp",
  );
  assert.equal(defaultBrowserBackend("darwin", {}, () => true), "auto");
});


test("explicit browser backend overrides environment detection", () => {
  assert.equal(
    defaultBrowserBackend(
      "linux",
      {
        DOUBAO_OFFICE_USER_ACCESS_TOKEN: "redacted",
        GUANYI_BROWSER_BACKEND: "persistent",
      },
      () => true,
    ),
    "persistent",
  );
});
