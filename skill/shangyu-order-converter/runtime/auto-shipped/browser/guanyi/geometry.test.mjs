import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright-core";

import { CHROME_EXECUTABLE } from "./config.mjs";
import { geometryMismatchReasons } from "./geometry.mjs";
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
