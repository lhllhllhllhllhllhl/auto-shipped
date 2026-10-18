import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import {
  GeometryGuard,
  geometryMismatchReasons,
  nativePlatformScrollbarGutters,
  nativeVerticalScrollbarGutter,
} from "./geometry.mjs";
import { captureReadOnlyScreenshot } from "./screenshot.mjs";


function stableHeadedSnapshot() {
  return {
    playwrightViewport: null,
    innerWidth: 1440,
    innerHeight: 707,
    outerWidth: 1440,
    outerHeight: 850,
    screenWidth: 1470,
    screenHeight: 956,
    availableWidth: 1470,
    availableHeight: 858,
    devicePixelRatio: 2,
    visualViewportWidth: 1440,
    visualViewportHeight: 707,
    visualViewportScale: 1,
    documentClientWidth: 1440,
    documentClientHeight: 707,
    url: "https://www.guanyierp.com/index",
  };
}


test("accepts certified headed geometry and rejects emulated viewport", () => {
  const snapshot = stableHeadedSnapshot();
  assert.deepEqual(geometryMismatchReasons(snapshot), []);

  const emulated = {
    ...snapshot,
    playwrightViewport: { width: 1280, height: 720 },
  };
  assert.ok(
    geometryMismatchReasons(emulated).includes(
      "headed_emulated_viewport_enabled",
    ),
  );
});


test("accepts macOS restored window when outer height mirrors content height", () => {
  const snapshot = stableHeadedSnapshot();
  snapshot.outerHeight = snapshot.innerHeight;
  assert.deepEqual(geometryMismatchReasons(snapshot), []);
});


test("accepts stable platform-managed native scrollbar gutters", () => {
  const snapshot = {
    ...stableHeadedSnapshot(),
    innerWidth: 1280,
    innerHeight: 960,
    outerWidth: 1440,
    outerHeight: 850,
    availableWidth: 1920,
    availableHeight: 1080,
    documentClientWidth: 1265,
    documentClientHeight: 945,
    visualViewportWidth: 1265,
    visualViewportHeight: 945,
    devicePixelRatio: 1,
  };

  assert.equal(nativeVerticalScrollbarGutter(snapshot), null);
  assert.deepEqual(nativePlatformScrollbarGutters(snapshot), {
    vertical: 15,
    horizontal: 15,
  });
  assert.ok(
    geometryMismatchReasons(snapshot).includes(
      "document_client_width_mismatch",
    ),
  );
  assert.deepEqual(
    geometryMismatchReasons(snapshot, { externallyManagedWindow: true }),
    [],
  );
});


test("accepts a stable maximized platform-managed viewport", () => {
  const snapshot = {
    ...stableHeadedSnapshot(),
    innerWidth: 1920,
    innerHeight: 993,
    outerWidth: 1920,
    outerHeight: 1080,
    screenWidth: 1920,
    screenHeight: 1080,
    availableWidth: 1920,
    availableHeight: 1080,
    documentClientWidth: 1920,
    documentClientHeight: 993,
    visualViewportWidth: 1920,
    visualViewportHeight: 993,
    devicePixelRatio: 1,
    url: "about:blank",
  };

  const locallyOwnedReasons = geometryMismatchReasons(snapshot);
  assert.ok(locallyOwnedReasons.includes("outer_width_mismatch"));
  assert.ok(locallyOwnedReasons.includes("outer_height_mismatch"));
  assert.deepEqual(
    geometryMismatchReasons(snapshot, { externallyManagedWindow: true }),
    [],
  );
});


test("platform-managed viewport still rejects unsafe page geometry", () => {
  const snapshot = {
    ...stableHeadedSnapshot(),
    innerWidth: 1280,
    innerHeight: 960,
    outerWidth: 1440,
    outerHeight: 850,
    availableWidth: 1920,
    availableHeight: 1080,
    documentClientWidth: 1265,
    documentClientHeight: 930,
    visualViewportWidth: 1265,
    visualViewportHeight: 930,
    devicePixelRatio: 1,
  };

  assert.equal(nativePlatformScrollbarGutters(snapshot), null);
  const reasons = geometryMismatchReasons(snapshot, {
    externallyManagedWindow: true,
  });
  assert.ok(reasons.includes("document_client_width_mismatch"));
  assert.ok(reasons.includes("visual_viewport_width_mismatch"));
});


test("platform-managed viewport still enforces minimum usable area", () => {
  const snapshot = {
    ...stableHeadedSnapshot(),
    innerWidth: 1100,
    innerHeight: 600,
    outerWidth: 1920,
    outerHeight: 1080,
    availableWidth: 1920,
    availableHeight: 1080,
    documentClientWidth: 1100,
    documentClientHeight: 600,
    visualViewportWidth: 1100,
    visualViewportHeight: 600,
    devicePixelRatio: 1,
  };

  const reasons = geometryMismatchReasons(snapshot, {
    externallyManagedWindow: true,
  });
  assert.ok(reasons.includes("inner_width_too_small"));
  assert.ok(reasons.includes("inner_height_too_small"));
});


test("platform-managed guard validates without mutating the external window", async () => {
  const snapshot = {
    ...stableHeadedSnapshot(),
    innerWidth: 1920,
    innerHeight: 993,
    outerWidth: 1920,
    outerHeight: 1080,
    screenWidth: 1920,
    screenHeight: 1080,
    availableWidth: 1920,
    availableHeight: 1080,
    documentClientWidth: 1920,
    documentClientHeight: 993,
    visualViewportWidth: 1920,
    visualViewportHeight: 993,
    devicePixelRatio: 1,
    url: "about:blank",
  };
  let cdpSessionCount = 0;
  const context = {
    async newCDPSession() {
      cdpSessionCount += 1;
      throw new Error("must not mutate a platform-managed window");
    },
  };
  const page = {
    async evaluate() {
      return snapshot;
    },
    viewportSize() {
      return null;
    },
    async waitForTimeout() {},
  };

  const guard = new GeometryGuard(context, page, {
    externallyManagedWindow: true,
  });
  const result = await guard.ensureStable({
    stage: "browser_launch",
    forceApply: true,
  });

  assert.equal(result.stable, true);
  assert.equal(result.repaired, false);
  assert.equal(cdpSessionCount, 0);
});


test("accepts a stable native Linux vertical scrollbar gutter", () => {
  const snapshot = {
    ...stableHeadedSnapshot(),
    innerWidth: 1432,
    innerHeight: 759,
    outerWidth: 1440,
    documentClientWidth: 1417,
    documentClientHeight: 759,
    visualViewportWidth: 1417,
    visualViewportHeight: 759,
    devicePixelRatio: 1,
  };
  assert.equal(nativeVerticalScrollbarGutter(snapshot), 15);
  assert.deepEqual(geometryMismatchReasons(snapshot), []);
});


test("rejects width mismatches that are not a native vertical scrollbar", () => {
  const inconsistentWidths = {
    ...stableHeadedSnapshot(),
    innerWidth: 1432,
    documentClientWidth: 1417,
    visualViewportWidth: 1390,
  };
  assert.equal(nativeVerticalScrollbarGutter(inconsistentWidths), null);
  assert.ok(
    geometryMismatchReasons(inconsistentWidths).includes(
      "document_client_width_mismatch",
    ),
  );

  const horizontalScrollbar = {
    ...stableHeadedSnapshot(),
    innerWidth: 1432,
    innerHeight: 759,
    documentClientWidth: 1417,
    documentClientHeight: 744,
    visualViewportWidth: 1417,
    visualViewportHeight: 744,
  };
  assert.equal(nativeVerticalScrollbarGutter(horizontalScrollbar), null);
  assert.ok(
    geometryMismatchReasons(horizontalScrollbar).includes(
      "visual_viewport_width_mismatch",
    ),
  );
});


test("read-only screenshot preserves viewport and scroll", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-screenshot-"));
  const output = path.join(root, "screen.png");
  const browser = await chromium.launch({
    executablePath: CHROME_EXECUTABLE,
    headless: true,
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1200, height: 700 },
    });
    await page.setContent('<main style="height:2400px">test</main>');
    await page.evaluate(() => window.scrollTo(0, 420));
    const before = await page.evaluate(() => ({
      width: window.innerWidth,
      height: window.innerHeight,
      scrollY: window.scrollY,
    }));
    const result = await captureReadOnlyScreenshot(page, output);
    const after = await page.evaluate(() => ({
      width: window.innerWidth,
      height: window.innerHeight,
      scrollY: window.scrollY,
    }));

    assert.equal(result.saved, true);
    assert.deepEqual(after, before);
    assert.ok((await fs.stat(output)).size > 0);
  } finally {
    await browser.close();
    await fs.rm(root, { recursive: true, force: true });
  }
});
