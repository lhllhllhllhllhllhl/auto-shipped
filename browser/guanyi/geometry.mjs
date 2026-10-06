import {
  MINIMUM_INNER_HEIGHT,
  MINIMUM_INNER_WIDTH,
  WINDOW_HEIGHT,
  WINDOW_TOLERANCE,
  WINDOW_WIDTH,
} from "./config.mjs";


const PAGE_GUARDS = new WeakMap();
const NAVIGATION_ERROR_MARKERS = [
  "execution context was destroyed",
  "most likely because of a navigation",
  "cannot find context with specified id",
];


export class GeometryError extends Error {
  constructor(code, stage, details = {}) {
    const messages = {
      GEOMETRY_MISMATCH: "浏览器窗口与页面可用区域不一致。",
      GEOMETRY_CHANGED_AFTER_WRITE:
        "写入可能已经发生后浏览器布局发生变化，已停止自动恢复。",
      GEOMETRY_UNSTABLE: "浏览器页面布局持续变化，未达到安全操作状态。",
    };
    super(`${messages[code] || messages.GEOMETRY_UNSTABLE} 阶段：${stage}`);
    this.name = "GeometryError";
    this.code = code;
    this.stage = stage;
    this.details = details;
  }
}


function isNavigationContextError(error) {
  const message = String(error?.message || error || "").toLowerCase();
  return NAVIGATION_ERROR_MARKERS.some((marker) => message.includes(marker));
}


function signature(snapshot) {
  return [
    snapshot.innerWidth,
    snapshot.innerHeight,
    snapshot.outerWidth,
    snapshot.outerHeight,
    snapshot.documentClientWidth,
    snapshot.documentClientHeight,
    snapshot.visualViewportWidth,
    snapshot.visualViewportHeight,
    snapshot.visualViewportScale,
    snapshot.devicePixelRatio,
  ].join(":");
}


export function certifiedWindowFor(snapshot) {
  const availableWidth = snapshot.availableWidth || WINDOW_WIDTH;
  const availableHeight = snapshot.availableHeight || WINDOW_HEIGHT;
  return {
    width: Math.min(WINDOW_WIDTH, availableWidth),
    height: Math.min(WINDOW_HEIGHT, availableHeight),
  };
}


export function geometryMismatchReasons(snapshot) {
  const target = certifiedWindowFor(snapshot);
  const reasons = [];
  if (snapshot.playwrightViewport !== null) {
    reasons.push("headed_emulated_viewport_enabled");
  }
  if (snapshot.innerWidth < MINIMUM_INNER_WIDTH) {
    reasons.push("inner_width_too_small");
  }
  if (snapshot.innerHeight < MINIMUM_INNER_HEIGHT) {
    reasons.push("inner_height_too_small");
  }
  if (
    snapshot.outerWidth &&
    Math.abs(snapshot.outerWidth - target.width) > WINDOW_TOLERANCE
  ) {
    reasons.push("outer_width_mismatch");
  }
  if (
    snapshot.outerHeight &&
    Math.abs(snapshot.outerHeight - snapshot.innerHeight) > 3 &&
    Math.abs(snapshot.outerHeight - target.height) > WINDOW_TOLERANCE
  ) {
    reasons.push("outer_height_mismatch");
  }
  if (Math.abs(snapshot.documentClientWidth - snapshot.innerWidth) > 3) {
    reasons.push("document_client_width_mismatch");
  }
  if (Math.abs(snapshot.visualViewportWidth - snapshot.innerWidth) > 3) {
    reasons.push("visual_viewport_width_mismatch");
  }
  if (
    snapshot.visualViewportScale &&
    Math.abs(snapshot.visualViewportScale - 1) > 0.02
  ) {
    reasons.push("unexpected_page_scale");
  }
  return reasons;
}


export async function captureGeometry(page) {
  let metrics;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      metrics = await page.evaluate(() => ({
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        outerWidth: window.outerWidth,
        outerHeight: window.outerHeight,
        screenWidth: window.screen.width,
        screenHeight: window.screen.height,
        availableWidth: window.screen.availWidth,
        availableHeight: window.screen.availHeight,
        devicePixelRatio: window.devicePixelRatio,
        visualViewportWidth:
          window.visualViewport?.width ?? window.innerWidth,
        visualViewportHeight:
          window.visualViewport?.height ?? window.innerHeight,
        visualViewportScale: window.visualViewport?.scale ?? 1,
        documentClientWidth:
          document.documentElement?.clientWidth ?? 0,
        documentClientHeight:
          document.documentElement?.clientHeight ?? 0,
        url: String(window.location.href || ""),
      }));
      break;
    } catch (error) {
      if (!isNavigationContextError(error) || attempt === 3) {
        throw error;
      }
      await page.waitForLoadState("domcontentloaded", { timeout: 5_000 })
        .catch(() => null);
      await page.waitForTimeout(150 * (attempt + 1));
    }
  }
  if (!metrics) {
    throw new GeometryError("GEOMETRY_UNSTABLE", "读取页面几何");
  }
  return {
    playwrightViewport: page.viewportSize(),
    ...metrics,
  };
}


export class GeometryGuard {
  constructor(context, page) {
    this.context = context;
    this.page = page;
    this.generation = 0;
    this.history = [];
  }

  async sampleStable() {
    const samples = [];
    for (let index = 0; index < 3; index += 1) {
      samples.push(await captureGeometry(this.page));
      if (index < 2) {
        await this.page.waitForTimeout(120);
      }
    }
    return {
      stable: new Set(samples.map(signature)).size === 1,
      samples,
    };
  }

  async applyNativeWindow(stage) {
    const before = await captureGeometry(this.page);
    const target = certifiedWindowFor(before);
    const session = await this.context.newCDPSession(this.page);
    try {
      const result = await session.send("Browser.getWindowForTarget");
      await session.send("Browser.setWindowBounds", {
        windowId: result.windowId,
        bounds: {
          left: 0,
          top: 0,
          width: target.width,
          height: target.height,
          windowState: "normal",
        },
      });
    } finally {
      await session.detach().catch(() => null);
    }
    await this.page.waitForTimeout(150);
    this.generation += 1;
    const after = await captureGeometry(this.page);
    const record = {
      stage,
      action: "cdp_native_window",
      generation: this.generation,
      target,
      before,
      after,
    };
    this.history.push(record);
    return record;
  }

  async ensureStable({
    stage,
    writeRequestSent = false,
    allowRepair = true,
    forceApply = false,
  }) {
    if (forceApply) {
      await this.applyNativeWindow(stage);
    }
    const before = await this.sampleStable();
    const reasons = geometryMismatchReasons(before.samples.at(-1));
    if (before.stable && reasons.length === 0) {
      const record = {
        stage,
        stable: true,
        repaired: false,
        generation: this.generation,
        snapshot: before.samples.at(-1),
      };
      this.history.push(record);
      return record;
    }
    const details = {
      stable: before.stable,
      reasons,
      samples: before.samples,
      generation: this.generation,
    };
    if (writeRequestSent) {
      throw new GeometryError(
        "GEOMETRY_CHANGED_AFTER_WRITE",
        stage,
        details,
      );
    }
    if (!allowRepair) {
      throw new GeometryError("GEOMETRY_UNSTABLE", stage, details);
    }
    await this.applyNativeWindow(stage);
    const after = await this.sampleStable();
    const afterReasons = geometryMismatchReasons(after.samples.at(-1));
    if (!after.stable || afterReasons.length > 0) {
      throw new GeometryError("GEOMETRY_MISMATCH", stage, {
        ...details,
        afterStable: after.stable,
        afterReasons,
        afterSamples: after.samples,
      });
    }
    const record = {
      stage,
      stable: true,
      repaired: true,
      generation: this.generation,
      snapshot: after.samples.at(-1),
    };
    this.history.push(record);
    return record;
  }
}


export function registerPageGeometry(context, page) {
  const guard = new GeometryGuard(context, page);
  PAGE_GUARDS.set(page, guard);
  return guard;
}


export function geometryGuardFor(page) {
  return PAGE_GUARDS.get(page) || null;
}


export async function ensurePageGeometry(page, stage, options = {}) {
  const guard = geometryGuardFor(page);
  if (!guard) {
    throw new GeometryError("GEOMETRY_UNSTABLE", stage, {
      reason: "guard_not_registered",
    });
  }
  return await guard.ensureStable({ stage, ...options });
}
