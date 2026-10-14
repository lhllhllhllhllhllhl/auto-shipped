import { firstVisible } from "./states.mjs";
import { ensurePageGeometry } from "./geometry.mjs";


const BLOCKING_SURFACES = [
  '[role="dialog"]:visible',
  ".ant-modal-wrap:visible .ant-modal:visible",
  ".el-dialog__wrapper:visible .el-dialog:visible",
  ".vxe-modal--wrapper:visible",
  ".x-window:visible",
].join(", ");

const SAFE_ACKNOWLEDGEMENTS = ["知道了", "我知道了"];
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


async function clickAndConfirmClosed(page, surface, target) {
  await target.click();
  const deadline = Date.now() + CLOSE_CONFIRM_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!(await surface.isVisible().catch(() => false))) {
      return true;
    }
    await page.waitForTimeout(50);
  }
  return false;
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
  const initialUrl = page.url();
  for (let pass = 0; pass < maxPasses; pass += 1) {
    let changed = false;

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
