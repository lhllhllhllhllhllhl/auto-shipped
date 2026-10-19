import { firstVisible } from "./states.mjs";
import { ensurePageGeometry } from "./geometry.mjs";


const BLOCKING_SURFACES = [
  '[role="dialog"]:visible',
  ".ant-modal-wrap:visible .ant-modal:visible",
  ".ant-popconfirm:visible",
  ".el-dialog__wrapper:visible .el-dialog:visible",
  ".vxe-modal--wrapper:visible",
  ".x-window:visible",
].join(", ");

const SAFE_ACKNOWLEDGEMENTS = ["知道了", "我知道了"];
const SPEED_EDITION_NOTICE =
  "当前页面已经更新到极速版，可以通过当前按钮回到历史版本";
const EXACT_ACKNOWLEDGEMENT_NOTICES = [
  { text: SPEED_EDITION_NOTICE, acknowledgement: "知道了" },
];
const EXACT_NOTICE_SURFACE_SELECTORS = ".ant-popconfirm:visible";
const SAFE_CLOSE_SELECTORS = [
  'button[aria-label="Close"]',
  'button[aria-label="关闭"]',
  'button[title="关闭"]',
  ".ant-modal-close",
  ".el-dialog__headerbtn",
  ".vxe-modal--close-btn",
  ".x-tool-close",
  '[data-qtip="关闭"]',
].join(", ");
const KNOWN_CLOSEABLE_TITLES = [
  "店铺授权到期提醒",
  "公告",
  "通知",
  "版本更新",
];
const KNOWN_NON_BLOCKING_SURFACES = ["全部功能菜单"];
const CLOSE_CONFIRM_TIMEOUT_MS = 3_000;


async function visibleButton(root, name) {
  const button = await firstVisible(
    root.getByRole("button", { name, exact: true }),
  );
  return button || null;
}


async function visibleFullPageDimBackdrops(page) {
  const matches = [];
  const candidates = page.locator("body > div");
  for (let index = 0; index < (await candidates.count()); index += 1) {
    const candidate = candidates.nth(index);
    const isBackdrop = await candidate.evaluate((element) => {
      const minimumWidth = window.innerWidth * 0.9;
      const minimumHeight = window.innerHeight * 0.7;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const opacity = Number.parseFloat(style.opacity || "1");
      const background = style.backgroundColor.replaceAll(" ", "");
      return (
        style.position === "fixed" &&
        style.pointerEvents !== "none" &&
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        opacity > 0 &&
        opacity < 0.9 &&
        background === "rgb(0,0,0)" &&
        rect.width >= minimumWidth &&
        rect.height >= minimumHeight &&
        rect.top <= window.innerHeight * 0.25 &&
        !element.textContent.trim()
      );
    });
    if (isBackdrop) {
      matches.push(candidate);
    }
  }
  return matches;
}


async function clickAndConfirmClosed(
  page,
  surface,
  target,
  { confirmDimBackdrop = false } = {},
) {
  const initialBackdrops = confirmDimBackdrop
    ? await visibleFullPageDimBackdrops(page)
    : [];
  if (initialBackdrops.length > 1) {
    return false;
  }
  await target.click();
  const deadline = Date.now() + CLOSE_CONFIRM_TIMEOUT_MS;
  let surfaceClosed = false;
  while (Date.now() < deadline) {
    surfaceClosed = !(await surface.isVisible().catch(() => false));
    if (surfaceClosed) {
      break;
    }
    await page.waitForTimeout(50);
  }
  if (!surfaceClosed || !confirmDimBackdrop || initialBackdrops.length === 0) {
    return surfaceClosed;
  }

  let remainingBackdrops = await visibleFullPageDimBackdrops(page);
  if (remainingBackdrops.length === 0) {
    return true;
  }
  if (remainingBackdrops.length !== 1) {
    return false;
  }
  await remainingBackdrops[0].click();
  while (Date.now() < deadline) {
    remainingBackdrops = await visibleFullPageDimBackdrops(page);
    if (remainingBackdrops.length === 0) {
      return true;
    }
    await page.waitForTimeout(50);
  }
  return false;
}


async function visibleMatches(locator) {
  const matches = [];
  for (let index = 0; index < (await locator.count()); index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false)) {
      matches.push(candidate);
    }
  }
  return matches;
}


async function findExactSurfaceTargets(page, notice) {
  const targets = [];
  let visibleTextCount = 0;
  for (const frame of page.frames()) {
    const surfaces = await visibleMatches(
      frame.locator(EXACT_NOTICE_SURFACE_SELECTORS),
    );
    for (const surface of surfaces) {
      const texts = await visibleMatches(
        surface.getByText(notice.text, { exact: true }),
      );
      visibleTextCount += texts.length;
      if (texts.length !== 1) {
        continue;
      }
      const buttons = await visibleMatches(
        surface.getByRole("button", {
          name: notice.acknowledgement,
          exact: true,
        }),
      );
      if (buttons.length === 1) {
        targets.push({ notice: texts[0], surface, button: buttons[0] });
      }
    }
  }
  return { targets, visibleTextCount };
}


async function findExactAcknowledgementTargets(page, notice) {
  const surfaceMatches = await findExactSurfaceTargets(page, notice);
  if (surfaceMatches.visibleTextCount > 0) {
    return surfaceMatches;
  }

  const targets = [];
  let visibleTextCount = 0;
  for (const frame of page.frames()) {
    const texts = await visibleMatches(
      frame.getByText(notice.text, { exact: true }),
    );
    visibleTextCount += texts.length;
    for (const text of texts) {
      let ancestor = text;
      for (let depth = 0; depth < 12; depth += 1) {
        ancestor = ancestor.locator("..");
        const buttons = await visibleMatches(
          ancestor.getByRole("button", {
            name: notice.acknowledgement,
            exact: true,
          }),
        );
        if (buttons.length === 1) {
          targets.push({ notice: text, surface: ancestor, button: buttons[0] });
          break;
        }
        if (buttons.length > 1) {
          break;
        }
      }
    }
  }
  return { targets, visibleTextCount };
}


async function dismissExactAcknowledgementNotice(page, notice) {
  const { targets, visibleTextCount } =
    await findExactAcknowledgementTargets(page, notice);
  if (targets.length !== 1) {
    return {
      changed: false,
      ambiguous: visibleTextCount > 0,
      closeFailed: false,
    };
  }
  const closed = await clickAndConfirmClosed(
    page,
    targets[0].surface,
    targets[0].button,
    { confirmDimBackdrop: true },
  );
  return { changed: closed, ambiguous: false, closeFailed: !closed };
}


async function visibleBlockingSurfaces(page) {
  const visible = [];
  for (const frame of page.frames()) {
    const surfaces = frame.locator(BLOCKING_SURFACES);
    for (let index = 0; index < (await surfaces.count()); index += 1) {
      const surface = surfaces.nth(index);
      if (await surface.isVisible().catch(() => false)) {
        visible.push(surface);
      }
    }
  }
  return visible;
}


export async function dismissKnownPopups(page, maxPasses = 6) {
  const dismissed = [];
  const exactNoticeAmbiguities = [];
  const exactNoticeCloseFailures = [];
  const initialUrl = page.url();
  for (let pass = 0; pass < maxPasses; pass += 1) {
    let changed = false;

    for (const notice of EXACT_ACKNOWLEDGEMENT_NOTICES) {
      const result = await dismissExactAcknowledgementNotice(page, notice);
      if (result.ambiguous) {
        exactNoticeAmbiguities.push(notice.text);
        break;
      }
      if (result.closeFailed) {
        exactNoticeCloseFailures.push(notice.text);
        break;
      }
      if (result.changed) {
        dismissed.push(notice.acknowledgement);
        changed = true;
        break;
      }
    }
    if (changed) {
      continue;
    }

    const surfaces = await visibleBlockingSurfaces(page);
    for (const surface of surfaces) {
      const summary = (await surface.innerText().catch(() => "")).slice(0, 300);
      const known = KNOWN_CLOSEABLE_TITLES.some((title) => summary.includes(title));
      if (!known) {
        continue;
      }
      let attemptedClose = false;
      for (const text of SAFE_ACKNOWLEDGEMENTS) {
        const button = await visibleButton(surface, text);
        if (!button) {
          continue;
        }
        attemptedClose = true;
        if (await clickAndConfirmClosed(page, surface, button)) {
          dismissed.push(text);
          changed = true;
        }
        break;
      }
      if (changed) {
        break;
      }
      if (attemptedClose) {
        break;
      }
      const closeByLabel = await firstVisible(
        surface.locator(SAFE_CLOSE_SELECTORS),
      );
      if (closeByLabel) {
        attemptedClose = true;
        if (await clickAndConfirmClosed(page, surface, closeByLabel)) {
          dismissed.push("known_modal_close");
          changed = true;
        }
      }
      if (changed || attemptedClose) {
        break;
      }
      const closeButton = await visibleButton(surface, "关闭");
      if (closeButton) {
        if (await clickAndConfirmClosed(page, surface, closeButton)) {
          dismissed.push("known_modal_close");
          changed = true;
        }
        break;
      }
    }

    if (!changed) {
      break;
    }
  }

  const remaining = await visibleBlockingSurfaces(page);
  const unknown = [];
  for (const notice of EXACT_ACKNOWLEDGEMENT_NOTICES) {
    const { targets, visibleTextCount } =
      await findExactAcknowledgementTargets(page, notice);
    if (
      visibleTextCount > 0 ||
      targets.length > 0 ||
      exactNoticeAmbiguities.includes(notice.text)
    ) {
      unknown.push(`${notice.text} [未找到唯一安全确认按钮]`);
    }
  }
  for (const noticeText of exactNoticeCloseFailures) {
    unknown.push(`${noticeText} [安全确认后弹层或遮罩未完全消失]`);
  }
  for (const surface of remaining) {
    const text = (await surface.innerText().catch(() => ""))
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 160);
    const knownNonBlocking = KNOWN_NON_BLOCKING_SURFACES.some((title) =>
      text.includes(title),
    );
    if (text && !text.includes("订单导入") && !knownNonBlocking) {
      unknown.push(text);
    }
  }
  const pageUrlUnchanged = page.url() === initialUrl;
  if (!pageUrlUnchanged) {
    unknown.push("已知弹窗关闭动作改变了页面地址");
  }
  if (dismissed.length > 0) {
    await ensurePageGeometry(page, "known_popups_closed");
  }
  return { dismissed, unknown, pageUrlUnchanged };
}
