import { dismissKnownPopups } from "./popup.mjs";
import { ensurePageGeometry } from "./geometry.mjs";
import {
  BrowserState,
  detectState,
  firstVisible,
  firstVisibleInFrames,
} from "./states.mjs";


export class NavigationError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "NavigationError";
    this.code = code;
    this.details = details;
  }
}


export const IMPORT_OPTION_LABELS = Object.freeze([
  "相同平台单号，不同收货信息（收货人、收货地址）生成不同订单",
  "不校验已存在单据平台单号，允许平台单号重复",
]);
const IMPORT_BUTTON_CLICK_TIMEOUT_MS = 5_000;


async function clickFirstVisible(locator) {
  const target = await firstVisible(locator);
  if (!target) {
    return false;
  }
  await target.click();
  return true;
}


async function activateVisibleTab(page, locator, timeoutMs = 5_000) {
  const target = await firstVisible(locator);
  if (!target) {
    return false;
  }
  await target.click();
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const activated = await target
      .evaluate((element) => {
        const tab = element.closest('[role="tab"], .ant-tabs-tab') || element;
        return (
          tab.getAttribute("aria-selected") === "true" ||
          tab.classList.contains("ant-tabs-tab-active") ||
          Boolean(tab.closest(".ant-tabs-tab-active"))
        );
      })
      .catch(() => false);
    if (activated) {
      return true;
    }
    await page.waitForTimeout(100);
  }
  throw new NavigationError(
    "ORDER_QUERY_TAB_NOT_ACTIVATED",
    "已点击订单查询页签，但未能确认该页签已激活。",
  );
}


async function hasImportButton(page) {
  return Boolean(
    await firstVisibleInFrames(
      page,
      (frame) => frame.getByRole("button", {
        name: "订单导入",
        exact: true,
      }),
    ),
  );
}


async function findImportButton(page) {
  return await firstVisibleInFrames(
    page,
    (frame) => frame.getByRole("button", {
      name: "订单导入",
      exact: true,
    }),
  );
}


async function waitForImportButton(page, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await hasImportButton(page)) {
      return true;
    }
    await page.waitForTimeout(250);
  }
  return false;
}


async function waitForVisibleInFrames(
  page,
  locatorFactory,
  timeoutMs = 15_000,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const candidate = await firstVisibleInFrames(page, locatorFactory);
    if (candidate) {
      return candidate;
    }
    await page.waitForTimeout(250);
  }
  return null;
}


function mergePopupResults(...results) {
  return {
    dismissed: results.flatMap((result) => result.dismissed),
    unknown: results.flatMap((result) => result.unknown),
  };
}


async function finishOrderPage(page, route, previousPopups) {
  if (!(await waitForImportButton(page))) {
    return null;
  }
  const orderPagePopups = await dismissKnownPopups(page);
  const popups = mergePopupResults(previousPopups, orderPagePopups);
  if (popups.unknown.length) {
    throw new NavigationError(
      "POPUP_UNKNOWN",
      "订单查询页出现未登记的阻断弹窗，已停止导航。",
      { popupSummaries: popups.unknown },
    );
  }
  await ensurePageGeometry(page, "order_page_ready");
  return { route, popups };
}


export async function ensureOrderPage(page) {
  // The shop-authorization reminder is injected after the SPA first renders.
  // Give that known popup a bounded chance to appear before locating menus, so
  // it cannot cover a correct target halfway through navigation.
  await page
    .getByText("店铺授权到期提醒", { exact: false })
    .first()
    .waitFor({ state: "visible", timeout: 4_000 })
    .catch(() => null);
  const popups = await dismissKnownPopups(page);
  if (popups.unknown.length) {
    throw new NavigationError(
      "POPUP_UNKNOWN",
      "发现未登记的阻断弹窗，已停止导航。",
      { popupSummaries: popups.unknown },
    );
  }
  // Guanyi keeps inactive tab panels mounted. After reading the task-center
  // baseline, the order-query panel can still expose a visible import button
  // in the DOM while the active task-center panel covers it. Prefer activating
  // the existing order-query tab before treating that button as proof that the
  // page is ready.
  const importButtonPresent = await hasImportButton(page);

  let orderQueryClicked = await activateVisibleTab(
    page,
    page.getByRole("tab", { name: /订单查询/ }),
  );
  if (!orderQueryClicked) {
    orderQueryClicked = await clickFirstVisible(
      page.getByText("订单查询", { exact: true }),
    );
  }
  if (orderQueryClicked) {
    const existingTab = await finishOrderPage(
      page,
      "existing_order_query_tab",
      popups,
    );
    if (existingTab) {
      return existingTab;
    }
  }

  if (importButtonPresent) {
    return { route: "already_on_order_page", popups };
  }

  const menuOpened = await clickFirstVisible(
    page.getByText("菜单", { exact: true }),
  );
  if (menuOpened) {
    const orderQuery = await waitForVisibleInFrames(
      page,
      (frame) => frame.getByText("订单查询", { exact: true }),
      5_000,
    );
    if (orderQuery) {
      await orderQuery.click();
    }
  }
  const mainMenu = await finishOrderPage(
    page,
    "main_menu_order_query",
    popups,
  );
  if (mainMenu) {
    return mainMenu;
  }

  const observed = await detectState(page);
  throw new NavigationError(
    "ORDER_PAGE_NOT_REACHED",
    "无法验证已进入订单查询页面。",
    { observedState: observed.state, url: observed.url },
  );
}


export async function navigateToCustomImport(page) {
  const orderPage = await ensureOrderPage(page);
  await ensurePageGeometry(page, "before_open_import_dialog");
  let importButton = await findImportButton(page);
  if (!importButton) {
    throw new NavigationError(
      "IMPORT_BUTTON_NOT_FOUND",
      "订单查询页没有找到订单导入按钮。",
    );
  }
  let popups = orderPage.popups;
  try {
    await importButton.click({ timeout: IMPORT_BUTTON_CLICK_TIMEOUT_MS });
  } catch (error) {
    // Some Guanyi notices are injected only after the order page is already
    // actionable. A single retry is safe here because no file has been
    // selected and it is allowed only after a registered popup was closed.
    const latePopups = await dismissKnownPopups(page);
    popups = mergePopupResults(popups, latePopups);
    if (latePopups.unknown.length) {
      throw new NavigationError(
        "POPUP_UNKNOWN",
        "订单导入按钮被未登记的阻断弹窗覆盖，已停止导航。",
        { popupSummaries: latePopups.unknown },
      );
    }
    if (latePopups.dismissed.length === 0) {
      throw error;
    }
    await ensurePageGeometry(page, "late_known_popup_closed");
    importButton = await findImportButton(page);
    if (!importButton) {
      throw new NavigationError(
        "IMPORT_BUTTON_NOT_FOUND",
        "关闭延迟通知后没有找到订单导入按钮。",
      );
    }
    await importButton.click({ timeout: IMPORT_BUTTON_CLICK_TIMEOUT_MS });
  }

  const customImport = await waitForVisibleInFrames(
    page,
    (frame) => frame.getByRole("tab", {
      name: "自定义导入",
      exact: true,
    }),
  );
  if (customImport) {
    await customImport.click();
  } else {
    const customImportText = await waitForVisibleInFrames(
      page,
      (frame) => frame.getByText("自定义导入", { exact: true }),
    );
    if (!customImportText) {
      throw new NavigationError(
        "CUSTOM_IMPORT_TAB_NOT_FOUND",
        "订单导入弹窗没有找到自定义导入页签。",
      );
    }
    await customImportText.click();
  }

  await ensurePageGeometry(page, "custom_import_tab_selected");

  let observed = await detectState(page);
  const deadline = Date.now() + 15_000;
  while (
    observed.state !== BrowserState.IMPORT_DIALOG_READY &&
    Date.now() < deadline
  ) {
    await page.waitForTimeout(250);
    observed = await detectState(page);
  }
  if (observed.state !== BrowserState.IMPORT_DIALOG_READY) {
    throw new NavigationError(
      "IMPORT_DIALOG_NOT_READY",
      "已点击自定义导入，但页面状态校验未通过。",
      { observedState: observed.state, url: observed.url },
    );
  }

  return {
    status: "import_dialog_ready",
    state: observed.state,
    url: observed.url,
    route: orderPage.route,
    dismissedPopups: popups.dismissed,
    safety: "No file was selected or uploaded.",
  };
}


async function findOrderStatusControl(page) {
  for (const frame of page.frames()) {
    const input = frame.locator("#orderState");
    if (await input.first().isVisible().catch(() => false)) {
      return { frame, input: input.first() };
    }
  }
  return null;
}


async function findExactCheckbox(page, labelText) {
  const matches = [];
  for (const frame of page.frames()) {
    const accessible = frame.getByRole("checkbox", {
      name: labelText,
      exact: true,
    });
    let accessibleMatches = 0;
    for (let index = 0; index < (await accessible.count()); index += 1) {
      if (!(await accessible.nth(index).isVisible().catch(() => false))) {
        continue;
      }
      matches.push({
        checkbox: accessible.nth(index),
        trigger: frame.getByText(labelText, { exact: true }).first(),
      });
      accessibleMatches += 1;
    }
    if (accessibleMatches > 0) {
      continue;
    }
    const labels = frame.getByText(labelText, { exact: true });
    for (let index = 0; index < (await labels.count()); index += 1) {
      const text = labels.nth(index);
      if (!(await text.isVisible().catch(() => false))) {
        continue;
      }
      const wrapper = text.locator(
        "xpath=ancestor::*[self::label or contains(concat(' ',normalize-space(@class),' '),' ant-checkbox-wrapper ')][1]",
      );
      const inputs = wrapper.locator('input[type="checkbox"]');
      if ((await inputs.count()) === 1) {
        matches.push({ checkbox: inputs.first(), trigger: text });
      }
    }
  }
  if (matches.length !== 1) {
    throw new NavigationError(
      "IMPORT_OPTION_NOT_UNIQUE",
      "无法唯一定位自定义导入附加选项。",
      { label: labelText, matchCount: matches.length },
    );
  }
  return matches[0];
}


export async function ensureImportOptionsSelected(page) {
  const selected = [];
  for (const label of IMPORT_OPTION_LABELS) {
    const target = await findExactCheckbox(page, label);
    if (!(await target.checkbox.isChecked())) {
      if (await target.checkbox.isVisible().catch(() => false)) {
        await target.checkbox.setChecked(true);
      } else {
        await target.trigger.click();
      }
    }
    if (!(await target.checkbox.isChecked())) {
      throw new NavigationError(
        "IMPORT_OPTION_NOT_CONFIRMED",
        "自定义导入附加选项勾选后未能回读确认。",
        { label },
      );
    }
    selected.push(label);
  }
  return {
    importOptions: selected,
    importOptionsConfirmed: true,
  };
}


async function exactVisibleOption(frame, text, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const options = frame.locator(
      ".ant-select-dropdown:visible .ant-select-item-option:visible",
    );
    for (let index = 0; index < (await options.count()); index += 1) {
      const option = options.nth(index);
      if ((await option.innerText().catch(() => "")).trim() === text) {
        return option;
      }
    }
    await frame.waitForTimeout(100);
  }
  return null;
}


async function clickExactVisibleOption(frame, text) {
  const option = await exactVisibleOption(frame, text);
  if (!option) return false;
  try {
    // Ant Design can replace or animate the dropdown between Playwright's
    // stability checks. The candidate is already constrained to an exact
    // label inside the currently visible dropdown, so dispatch its native
    // click immediately and verify the rendered selection afterwards.
    await option.evaluate((element) => element.click());
    return true;
  } catch {
    return false;
  }
}


export async function selectCustomImportOrderStatus(
  page,
  orderStatus = "待审核",
) {
  if (orderStatus !== "待审核") {
    throw new NavigationError(
      "ORDER_STATUS_NOT_ALLOWED",
      "当前自动化只允许把导入订单设置为待审核。",
      { requestedStatus: orderStatus },
    );
  }
  const control = await findOrderStatusControl(page);
  if (!control) {
    throw new NavigationError(
      "ORDER_STATUS_CONTROL_NOT_FOUND",
      "自定义订单导入表单没有找到订单状态控件。",
    );
  }
  await ensurePageGeometry(page, "before_select_import_order_status");
  const result = await selectOrderStatusControl(
    control.frame,
    control.input,
    orderStatus,
  );

  const fileInputs = control.frame.locator('input[type="file"]');
  for (let index = 0; index < (await fileInputs.count()); index += 1) {
    const fileCount = await fileInputs
      .nth(index)
      .evaluate((element) => element.files?.length || 0);
    if (fileCount > 0) {
      throw new NavigationError(
        "UNEXPECTED_FILE_SELECTION",
        "准备导入表单时检测到文件已被选择，已停止。",
      );
    }
  }
  await ensurePageGeometry(page, "import_order_status_selected");
  return result;
}


export async function selectOrderStatusControl(frame, input, orderStatus) {
  const select = input.locator(
    "xpath=ancestor::div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')][1]",
  );
  const selector = select.locator(".ant-select-selector");
  if (!(await selector.isVisible().catch(() => false))) {
    throw new NavigationError(
      "ORDER_STATUS_SELECTOR_NOT_VISIBLE",
      "订单状态下拉框当前不可见。",
    );
  }
  await selector.click();
  if (!(await clickExactVisibleOption(frame, orderStatus))) {
    throw new NavigationError(
      "ORDER_STATUS_OPTION_NOT_FOUND",
      "订单状态下拉框没有找到待审核选项。",
    );
  }

  const selected = select.locator(".ant-select-selection-item");
  await selected.waitFor({ state: "visible", timeout: 5_000 }).catch(() => null);
  const selectedText = (await selected.innerText().catch(() => "")).trim();
  const selectedTitle = await selected.getAttribute("title").catch(() => null);
  if (selectedText !== orderStatus || selectedTitle !== orderStatus) {
    throw new NavigationError(
      "ORDER_STATUS_NOT_CONFIRMED",
      "订单状态选择后未能回读确认待审核。",
      { selectedText, selectedTitle },
    );
  }

  return {
    status: "import_order_status_ready",
    orderStatus,
    safety: "No file was selected and no confirmation button was clicked.",
  };
}


export async function prepareCustomImportForm(page) {
  const navigation = await navigateToCustomImport(page);
  const orderStatus = await selectCustomImportOrderStatus(page, "待审核");
  await ensurePageGeometry(page, "before_select_import_options");
  const importOptions = await ensureImportOptionsSelected(page);
  await ensurePageGeometry(page, "import_options_selected");
  return {
    ...navigation,
    ...orderStatus,
    ...importOptions,
  };
}
