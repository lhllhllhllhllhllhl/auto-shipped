import { firstVisible, firstVisibleInFrames } from "./states.mjs";
import { ensurePageGeometry } from "./geometry.mjs";


const BLOCKING_SURFACES = [
  '[role="dialog"]:visible',
  ".ant-modal-wrap:visible .ant-modal:visible",
  ".el-dialog__wrapper:visible .el-dialog:visible",
  ".vxe-modal--wrapper:visible",
].join(", ");

const SAFE_ACKNOWLEDGEMENTS = ["知道了", "我知道了"];
const KNOWN_CLOSEABLE_TITLES = [
  "店铺授权到期提醒",
  "公告",
  "通知",
  "版本更新",
];
const KNOWN_NON_BLOCKING_SURFACES = ["全部功能菜单"];


async function clickVisibleButton(root, name) {
  const button = await firstVisible(
    root.getByRole("button", { name, exact: true }),
  );
  if (!button) {
    return false;
  }
  await button.click();
  return true;
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
  for (let pass = 0; pass < maxPasses; pass += 1) {
    let changed = false;

    for (const text of SAFE_ACKNOWLEDGEMENTS) {
      const button = await firstVisibleInFrames(
        page,
        (frame) => frame.getByRole("button", { name: text, exact: true }),
      );
      if (button) {
        await button.click();
        dismissed.push(text);
        changed = true;
        await page.waitForTimeout(250);
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
      const closeByLabel = await firstVisible(
        surface.locator('button[aria-label="Close"], button[aria-label="关闭"]'),
      );
      if (closeByLabel) {
        await closeByLabel.click();
        dismissed.push("known_modal_close");
        changed = true;
        await page.waitForTimeout(250);
        break;
      }
      if (await clickVisibleButton(surface, "关闭")) {
        dismissed.push("known_modal_close");
        changed = true;
        await page.waitForTimeout(250);
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
  if (dismissed.length > 0) {
    await ensurePageGeometry(page, "known_popups_closed");
  }
  return { dismissed, unknown };
}
