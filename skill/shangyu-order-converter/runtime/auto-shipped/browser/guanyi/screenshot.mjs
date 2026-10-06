import { captureGeometry } from "./geometry.mjs";


function invariantSignature(snapshot, scroll) {
  return JSON.stringify({
    viewport: snapshot.playwrightViewport,
    innerWidth: snapshot.innerWidth,
    innerHeight: snapshot.innerHeight,
    visualViewportWidth: snapshot.visualViewportWidth,
    visualViewportHeight: snapshot.visualViewportHeight,
    visualViewportScale: snapshot.visualViewportScale,
    scrollX: scroll.x,
    scrollY: scroll.y,
  });
}


async function readScroll(page) {
  return await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
}


export async function captureReadOnlyScreenshot(page, output, options = {}) {
  const beforeGeometry = await captureGeometry(page);
  const beforeScroll = await readScroll(page);
  try {
    await page.screenshot({ path: output, fullPage: false, ...options });
  } catch (error) {
    return {
      saved: false,
      output: null,
      code: "SCREENSHOT_FAILED",
      message: error?.message || String(error),
    };
  }
  const afterGeometry = await captureGeometry(page);
  const afterScroll = await readScroll(page);
  const unchanged =
    invariantSignature(beforeGeometry, beforeScroll) ===
    invariantSignature(afterGeometry, afterScroll);
  return {
    saved: unchanged,
    output: unchanged ? output : null,
    code: unchanged ? null : "SCREENSHOT_CHANGED_PAGE",
    before: { geometry: beforeGeometry, scroll: beforeScroll },
    after: { geometry: afterGeometry, scroll: afterScroll },
  };
}
