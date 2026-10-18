import assert from "node:assert/strict";
import test from "node:test";

import {
  BrowserBackend,
  BrowserBackendError,
  connectPlatformBrowser,
  normalizeLoopbackCdpEndpoint,
  resolveBrowserBackend,
} from "./browser-backend.mjs";


test("macOS and Windows auto mode retain the persistent browser", async () => {
  for (const platform of ["darwin", "win32"]) {
    assert.equal(
      await resolveBrowserBackend({
        configuredBackend: "auto",
        platform,
        cdpEndpoint: "http://127.0.0.1:9222",
        probe: async () => true,
      }),
      BrowserBackend.PERSISTENT,
    );
  }
});


test("Linux auto mode uses a reachable platform CDP browser", async () => {
  assert.equal(
    await resolveBrowserBackend({
      configuredBackend: "auto",
      platform: "linux",
      cdpEndpoint: "http://127.0.0.1:9222",
      probe: async () => true,
    }),
    BrowserBackend.PLATFORM_CDP,
  );
  assert.equal(
    await resolveBrowserBackend({
      configuredBackend: "auto",
      platform: "linux",
      cdpEndpoint: "http://127.0.0.1:9222",
      probe: async () => false,
    }),
    BrowserBackend.PERSISTENT,
  );
});


test("CDP endpoint is restricted to a plain loopback origin", () => {
  assert.equal(
    normalizeLoopbackCdpEndpoint("http://127.0.0.1:9222/"),
    "http://127.0.0.1:9222",
  );
  for (const value of [
    "https://127.0.0.1:9222",
    "http://example.com:9222",
    "http://user:secret@127.0.0.1:9222",
    "http://127.0.0.1:9222/json/version",
  ]) {
    assert.throws(
      () => normalizeLoopbackCdpEndpoint(value),
      (error) => error instanceof BrowserBackendError,
    );
  }
});


test("CDP attachment reuses exactly one context and only disconnects", async () => {
  let closeCount = 0;
  const context = { pages() { return []; } };
  const browser = {
    contexts() { return [context]; },
    async close() { closeCount += 1; },
  };
  const connected = await connectPlatformBrowser(
    { async connectOverCDP() { return browser; } },
    "http://127.0.0.1:9222",
  );
  assert.equal(connected.context, context);
  assert.equal(connected.ownsContext, false);
  await connected.close();
  assert.equal(closeCount, 1);
});


test("CDP attachment fails closed when the context is ambiguous", async () => {
  let closeCount = 0;
  const browser = {
    contexts() { return [{}, {}]; },
    async close() { closeCount += 1; },
  };
  await assert.rejects(
    connectPlatformBrowser(
      { async connectOverCDP() { return browser; } },
      "http://127.0.0.1:9222",
    ),
    (error) =>
      error instanceof BrowserBackendError &&
      error.code === "PLATFORM_CDP_CONTEXT_AMBIGUOUS",
  );
  assert.equal(closeCount, 1);
});
