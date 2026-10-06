import { isLoginUrl, isVerificationRequired } from "./session.mjs";


export const BrowserState = Object.freeze({
  NEEDS_LOGIN: "needs_login",
  NEEDS_VERIFICATION: "needs_verification",
  HOME_READY: "home_ready",
  ORDER_PAGE_READY: "order_page_ready",
  IMPORT_DIALOG_READY: "import_dialog_ready",
  AUTHENTICATED_UNKNOWN: "authenticated_unknown",
});


async function firstVisible(locator) {
  const count = await locator.count();
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false)) {
      return candidate;
    }
  }
  return null;
}


export async function firstVisibleInFrames(page, locatorFactory) {
  for (const frame of page.frames()) {
    const candidate = await firstVisible(locatorFactory(frame)).catch(() => null);
    if (candidate) {
      return candidate;
    }
  }
  return null;
}


export async function detectState(page) {
  const url = page.url();
  if (isLoginUrl(url)) {
    if (await isVerificationRequired(page)) {
      return { state: BrowserState.NEEDS_VERIFICATION, url };
    }
    return { state: BrowserState.NEEDS_LOGIN, url };
  }

  const importDialog = await firstVisibleInFrames(
    page,
    (frame) => frame.getByText("订单导入", { exact: true }),
  );
  const customImport = await firstVisibleInFrames(
    page,
    (frame) => frame.getByText("自定义导入", { exact: true }),
  );
  const customImportInstructions = await firstVisibleInFrames(
    page,
    (frame) => frame.getByText(/导入表格可增加、删除列/),
  );
  if (importDialog && customImport && customImportInstructions) {
    return { state: BrowserState.IMPORT_DIALOG_READY, url };
  }

  const importButton = await firstVisibleInFrames(
    page,
    (frame) => frame.getByRole("button", { name: "订单导入", exact: true }),
  );
  if (importButton) {
    return { state: BrowserState.ORDER_PAGE_READY, url };
  }

  const home = await firstVisibleInFrames(
    page,
    (frame) => frame.getByText("首页", { exact: true }),
  );
  const menu = await firstVisibleInFrames(
    page,
    (frame) => frame.getByText("菜单", { exact: true }),
  );
  if (home || menu) {
    return { state: BrowserState.HOME_READY, url };
  }

  return { state: BrowserState.AUTHENTICATED_UNKNOWN, url };
}


export { firstVisible };
