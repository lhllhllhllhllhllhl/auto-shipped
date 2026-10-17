import assert from "node:assert/strict";
import test from "node:test";

import { browserCandidates } from "./config.mjs";


test("discovers common Linux Chromium locations and PATH entries", () => {
  const candidates = browserCandidates("linux", {
    PATH: "/custom/bin:/another/bin",
  });
  assert.ok(candidates.includes("/usr/local/bin/chromium"));
  assert.ok(candidates.includes("/opt/browser/chrome"));
  assert.ok(candidates.includes("/custom/bin/chromium"));
  assert.ok(candidates.includes("/another/bin/google-chrome"));
});
