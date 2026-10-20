import { createHash } from "node:crypto";

import {
  loadOrderAuditPolicy,
  requireAuditUiValidation,
} from "./audit-policy.mjs";
import { ensurePageGeometry } from "./geometry.mjs";
import { ensureOrderPage } from "./navigation.mjs";
import {
  authorizeAuditOperation,
  AuditOperationState,
  loadAuditOperation,
  recordAuditReconciliation,
  recordAuditWriteBoundary,
  recordNoPendingOrders,
} from "./order-audit-state.mjs";
import { firstVisible } from "./states.mjs";


export class OrderAuditError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "OrderAuditError";
    this.code = code;
    this.details = details;
  }
}


export function requireAuditReadiness(environment = process.env) {
  if (environment.GUANYI_AUDIT_RULES_READY !== "1") {
    throw new OrderAuditError(
      "ORDER_AUDIT_RULE_GATE_BLOCKED",
      "本地和豆包环境的只读页面探针完成并登记前，真实订单审核保持关闭。",
    );
  }
}


function normalizedText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}


async function frameSurfaceIsVisible(frame) {
  if (!frame.parentFrame()) return true;
  const element = await frame.frameElement().catch(() => null);
  if (!element) return false;
  return await element.evaluate((iframe) => {
    let current = iframe;
    while (current instanceof Element) {
      const style = current.ownerDocument.defaultView?.getComputedStyle(current);
      if (
        current.getAttribute("aria-hidden") === "true" ||
        style?.display === "none" ||
        style?.visibility === "hidden"
      ) {
        return false;
      }
      current = current.parentElement;
    }
    const rect = iframe.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  });
}


async function visibleExact(scope, text) {
  return await firstVisible(scope.getByText(text, { exact: true }));
}


async function findOrderAuditFrame(page, policy) {
  const matches = [];
  for (const frame of page.frames()) {
    if (!(await frameSurfaceIsVisible(frame))) continue;
    const auditHeader = await visibleExact(
      frame,
      policy.ui.audit_column_title,
    ).catch(() => null);
    const auditButton = await firstVisible(
      frame.getByRole("button", {
        name: policy.ui.audit_button_text,
        exact: true,
      }),
    ).catch(() => null);
    if (auditHeader && auditButton) {
      matches.push({ frame, auditHeader, auditButton });
    }
  }
  if (matches.length !== 1) {
    throw new OrderAuditError(
      "ORDER_AUDIT_SURFACE_NOT_UNIQUE",
      "无法在订单查询中唯一定位审核列和审核按钮。",
      { matchCount: matches.length },
    );
  }
  return matches[0];
}


async function visibleHeaders(frame) {
  const candidates = frame.locator(
    '[role="columnheader"]:visible, th:visible, .x-column-header:visible',
  );
  const headers = [];
  for (let index = 0; index < (await candidates.count()); index += 1) {
    const candidate = candidates.nth(index);
    const text = normalizedText(await candidate.innerText().catch(() => ""));
    if (!text) continue;
    const box = await candidate.boundingBox().catch(() => null);
    if (!box) continue;
    if (
      headers.some(
        (header) =>
          header.text === text &&
          Math.abs(header.box.x - box.x) < 2 &&
          Math.abs(header.box.y - box.y) < 2,
      )
    ) {
      continue;
    }
    headers.push({ locator: candidate, text, box });
  }
  return headers.sort((left, right) => left.box.x - right.box.x);
}


async function columnDescriptor(frame, title) {
  const headers = await visibleHeaders(frame);
  const matches = headers.filter((header) => header.text === title);
  if (matches.length !== 1) {
    throw new OrderAuditError(
      "ORDER_AUDIT_COLUMN_NOT_UNIQUE",
      `无法唯一定位“${title}”列。`,
      { title, matchCount: matches.length },
    );
  }
  return { ...matches[0], headers };
}


async function visibleRows(frame) {
  const primary = frame.locator("tbody tr:visible");
  if ((await primary.count()) > 0) return primary;
  return frame.locator('.x-grid-item:visible, [role="row"]:visible');
}


async function rowCellTexts(frame, descriptor) {
  const rows = await visibleRows(frame);
  const values = [];
  for (let index = 0; index < (await rows.count()); index += 1) {
    const row = rows.nth(index);
    let cells = row.locator('td:visible, [role="gridcell"]:visible');
    if ((await cells.count()) === 0) {
      cells = row.locator(".x-grid-cell:visible");
    }
    let matchedCell = null;
    let nearestDistance = Number.POSITIVE_INFINITY;
    const headerCenter = descriptor.box.x + descriptor.box.width / 2;
    for (let cellIndex = 0; cellIndex < (await cells.count()); cellIndex += 1) {
      const cell = cells.nth(cellIndex);
      const box = await cell.boundingBox().catch(() => null);
      if (!box) continue;
      const center = box.x + box.width / 2;
      const distance = Math.abs(center - headerCenter);
      if (
        center >= descriptor.box.x - 4 &&
        center <= descriptor.box.x + descriptor.box.width + 4 &&
        distance < nearestDistance
      ) {
        matchedCell = cell;
        nearestDistance = distance;
      }
    }
    if (!matchedCell) continue;
    const text = normalizedText(
      await matchedCell.innerText().catch(() => ""),
    );
    if (text) values.push(text);
  }
  return values;
}


async function findColumnFilterInput(frame, descriptor) {
  const candidates = frame.locator(
    'input[placeholder*="筛选"]:visible, input[placeholder*="回车"]:visible',
  );
  const matches = [];
  for (let index = 0; index < (await candidates.count()); index += 1) {
    const candidate = candidates.nth(index);
    const box = await candidate.boundingBox().catch(() => null);
    if (!box) continue;
    const center = box.x + box.width / 2;
    if (
      center >= descriptor.box.x - 4 &&
      center <= descriptor.box.x + descriptor.box.width + 4 &&
      box.y >= descriptor.box.y
    ) {
      matches.push(candidate);
    }
  }
  if (matches.length !== 1) {
    throw new OrderAuditError(
      "ORDER_AUDIT_FILTER_NOT_UNIQUE",
      `无法唯一定位“${descriptor.text}”列的筛选输入框。`,
      { title: descriptor.text, matchCount: matches.length },
    );
  }
  return matches[0];
}


async function clickQuery(frame, policy) {
  const button = await firstVisible(
    frame.getByRole("button", {
      name: policy.ui.query_button_text,
      exact: true,
    }),
  );
  if (!button) {
    throw new OrderAuditError(
      "ORDER_AUDIT_QUERY_BUTTON_NOT_FOUND",
      "订单查询页没有找到唯一的查询按钮。",
    );
  }
  await button.click();
}


async function waitForGridStable(frame, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  let previous = null;
  let stableReads = 0;
  while (Date.now() < deadline) {
    const rows = await visibleRows(frame);
    const snapshot = `${await rows.count()}:${normalizedText(
      await frame.locator("body").innerText().catch(() => ""),
    ).slice(0, 500)}`;
    if (snapshot === previous) {
      stableReads += 1;
      if (stableReads >= 2) return;
    } else {
      stableReads = 0;
      previous = snapshot;
    }
    await frame.waitForTimeout(200);
  }
  throw new OrderAuditError(
    "ORDER_AUDIT_GRID_TIMEOUT",
    "订单查询结果没有在限定时间内稳定。",
  );
}


async function applyTextFilter(frame, descriptor, values) {
  const input = await findColumnFilterInput(frame, descriptor);
  await input.fill("");
  for (const value of values) {
    await input.fill(value);
    await input.press("Enter");
  }
}


async function findHeaderCheckbox(frame, firstHeader) {
  const candidates = frame.locator(
    '[role="columnheader"] input[type="checkbox"]:visible, th input[type="checkbox"]:visible, .x-column-header-checkbox input[type="checkbox"]:visible, [role="columnheader"] [role="checkbox"]:visible',
  );
  const visible = [];
  for (let index = 0; index < (await candidates.count()); index += 1) {
    const candidate = candidates.nth(index);
    const box = await candidate.boundingBox().catch(() => null);
    if (!box) continue;
    if (box.x <= firstHeader.box.x + firstHeader.box.width) {
      visible.push(candidate);
    }
  }
  if (visible.length !== 1) {
    throw new OrderAuditError(
      "ORDER_AUDIT_SELECT_ALL_NOT_UNIQUE",
      "无法唯一定位订单列表的全选框。",
      { matchCount: visible.length },
    );
  }
  return visible[0];
}


async function selectAllVisiblePending(frame, descriptor, expectedCount) {
  if (expectedCount === 0) return 0;
  const checkbox = await findHeaderCheckbox(frame, descriptor.headers[0]);
  if (await checkbox.isChecked().catch(() => false)) {
    return expectedCount;
  }
  await checkbox.click();
  const checked = await checkbox.isChecked().catch(() => false);
  const ariaChecked = await checkbox.getAttribute("aria-checked").catch(() => null);
  if (!checked && ariaChecked !== "true" && ariaChecked !== "mixed") {
    throw new OrderAuditError(
      "ORDER_AUDIT_SELECT_ALL_NOT_CONFIRMED",
      "点击全选后未能回读勾选状态。",
    );
  }
  return expectedCount;
}


async function clickAuditAction(frame, auditButton) {
  await auditButton.click();
  const dialogs = frame.locator(
    '[role="dialog"]:visible, .x-window:visible, .ant-modal:visible',
  );
  const deadline = Date.now() + 2_000;
  while (Date.now() < deadline) {
    for (let index = 0; index < (await dialogs.count()); index += 1) {
      const dialog = dialogs.nth(index);
      const text = normalizedText(await dialog.innerText().catch(() => ""));
      if (!text.includes("审核")) continue;
      const confirm = await firstVisible(
        dialog.getByRole("button", { name: "确定", exact: true }),
      ).catch(() => null);
      if (!confirm) {
        throw new OrderAuditError(
          "ORDER_AUDIT_CONFIRMATION_UNKNOWN",
          "审核弹窗没有找到已登记的确定按钮。",
        );
      }
      await confirm.click();
      return { confirmation: "dialog_confirmed" };
    }
    await frame.waitForTimeout(100);
  }
  return { confirmation: "no_dialog_observed" };
}


export async function probeOrderAuditSurface(
  page,
  { policy: suppliedPolicy = null, executionEnvironment = null } = {},
) {
  const policy = suppliedPolicy || (await loadOrderAuditPolicy());
  const navigation = await ensureOrderPage(page);
  await ensurePageGeometry(page, "order_audit_probe");
  const surface = await findOrderAuditFrame(page, policy);
  const auditColumn = await columnDescriptor(
    surface.frame,
    policy.ui.audit_column_title,
  );
  const platformOrderColumn = await columnDescriptor(
    surface.frame,
    policy.ui.platform_order_column_title,
  );
  const auditValues = [...new Set(await rowCellTexts(surface.frame, auditColumn))];
  const auditFilter = await findColumnFilterInput(surface.frame, auditColumn);
  const orderFilter = await findColumnFilterInput(
    surface.frame,
    platformOrderColumn,
  );
  const selectAll = await findHeaderCheckbox(
    surface.frame,
    auditColumn.headers[0],
  );
  return {
    status: "order_audit_surface_probe_ready",
    executionEnvironment,
    route: navigation.route,
    auditButtonFound: true,
    auditColumnFound: true,
    platformOrderColumnFound: true,
    auditFilterFound: Boolean(auditFilter),
    platformOrderFilterFound: Boolean(orderFilter),
    selectAllFound: Boolean(selectAll),
    visibleAuditStatuses: auditValues.slice(0, 10),
    observedPendingStatusCandidates: auditValues.filter((value) =>
      policy.ui.pending_status_candidates.includes(value),
    ),
    pendingStatusCandidates: [...policy.ui.pending_status_candidates],
    confirmedPendingStatus: policy.ui.confirmed_pending_status,
    policyValidationStatus: policy.validation.status,
    writePerformed: false,
    safety:
      "Only the order-query audit controls and non-sensitive audit-status labels were read. No filter, checkbox, or audit action was changed.",
  };
}


export async function preparePendingAuditSelection(
  page,
  auditRecord,
  pendingStatus,
  { policy: suppliedPolicy = null, selectRows = true } = {},
) {
  const policy = suppliedPolicy || (await loadOrderAuditPolicy());
  await ensureOrderPage(page);
  await ensurePageGeometry(page, "before_order_audit_filter");
  const surface = await findOrderAuditFrame(page, policy);
  const auditColumn = await columnDescriptor(
    surface.frame,
    policy.ui.audit_column_title,
  );
  await applyTextFilter(surface.frame, auditColumn, [pendingStatus]);
  const orderColumn = await columnDescriptor(
    surface.frame,
    policy.ui.platform_order_column_title,
  );
  if (auditRecord.scope.mode === "current_batch") {
    await applyTextFilter(
      surface.frame,
      orderColumn,
      auditRecord.scope.platformOrderNumbers,
    );
  }
  await clickQuery(surface.frame, policy);
  await waitForGridStable(surface.frame);
  const auditValues = await rowCellTexts(surface.frame, auditColumn);
  if (auditValues.some((value) => value !== pendingStatus)) {
    throw new OrderAuditError(
      "ORDER_AUDIT_FILTER_RESULT_UNSAFE",
      "审核筛选结果中仍包含非待审核订单，已停止。",
      { observedAuditStatuses: [...new Set(auditValues)] },
    );
  }
  const visibleOrderNumbers = await rowCellTexts(surface.frame, orderColumn);
  if (auditValues.length > 0 && visibleOrderNumbers.length !== auditValues.length) {
    throw new OrderAuditError(
      "ORDER_AUDIT_PLATFORM_IDS_INCOMPLETE",
      "待审核结果中的平台单号读取不完整，无法安全识别分页是否推进。",
      {
        pendingCount: auditValues.length,
        platformOrderNumberCount: visibleOrderNumbers.length,
      },
    );
  }
  if (auditRecord.scope.mode === "current_batch" && auditValues.length > 0) {
    const expected = new Set(auditRecord.scope.platformOrderNumbers);
    if (visibleOrderNumbers.some((value) => !expected.has(value))) {
      throw new OrderAuditError(
        "CURRENT_BATCH_AUDIT_SCOPE_MISMATCH",
        "只审核本批的筛选结果包含其他平台单号，已停止。",
      );
    }
  }
  const selectionSignature = createHash("sha256")
    .update(visibleOrderNumbers.join("\n"), "utf8")
    .digest("hex");
  const selectedCount = selectRows
    ? await selectAllVisiblePending(
        surface.frame,
        auditColumn,
        auditValues.length,
      )
    : 0;
  return {
    pendingCount: auditValues.length,
    selectedCount,
    selectionSignature,
    frame: surface.frame,
    auditButton: surface.auditButton,
  };
}


const DEFAULT_DEPENDENCIES = Object.freeze({
  authorizeAuditOperation,
  clickAuditAction,
  loadAuditOperation,
  loadOrderAuditPolicy,
  preparePendingAuditSelection,
  recordAuditReconciliation,
  recordAuditWriteBoundary,
  recordNoPendingOrders,
  requireAuditReadiness,
  requireAuditUiValidation,
});


export async function executeAuthorizedOrderAudit(
  page,
  auditOperationId,
  authorization,
  {
    directory,
    maxPasses = 20,
    environment = process.env,
    dependencies: overrides = null,
  } = {},
) {
  const service = { ...DEFAULT_DEPENDENCIES, ...(overrides || {}) };
  service.requireAuditReadiness(environment);
  const policy = await service.loadOrderAuditPolicy();
  const pendingStatus = service.requireAuditUiValidation(policy);
  let authorized = await service.authorizeAuditOperation(
    auditOperationId,
    authorization,
    directory,
  );
  let previousSelectionSignature = null;
  let previousSelectedCount = null;
  for (let pass = 1; pass <= maxPasses; pass += 1) {
    const selection = await service.preparePendingAuditSelection(
      page,
      authorized.record,
      pendingStatus,
      { policy },
    );
    if (selection.pendingCount === 0) {
      if (pass === 1) {
        const empty = await service.recordNoPendingOrders(
          auditOperationId,
          directory,
        );
        return {
          status: "nothing_to_audit",
          auditOperationId,
          state: empty.record.state,
          scope: empty.record.scope.mode,
        };
      }
      const verified = await service.recordAuditReconciliation(
        auditOperationId,
        0,
        directory,
      );
      return {
        status: "audit_verified",
        auditOperationId,
        state: verified.record.state,
        scope: verified.record.scope.mode,
        auditedPasses: pass - 1,
      };
    }
    if (pass === 1) {
      await service.recordAuditWriteBoundary(
        auditOperationId,
        selection.selectedCount,
        directory,
      );
    } else if (
      previousSelectionSignature !== null &&
      selection.selectionSignature === previousSelectionSignature
    ) {
      const reviewed = await service.recordAuditReconciliation(
        auditOperationId,
        selection.pendingCount,
        directory,
      );
      return {
        status: "reconciling",
        reason: "pending_page_repeated",
        auditOperationId,
        state: reviewed.record.state,
        remainingPendingCount: selection.pendingCount,
      };
    }
    await service.clickAuditAction(selection.frame, selection.auditButton);
    previousSelectionSignature = selection.selectionSignature;
    previousSelectedCount = selection.selectedCount;
    authorized = await service.loadAuditOperation(
      auditOperationId,
      directory,
    );
  }
  const reviewed = await service.recordAuditReconciliation(
    auditOperationId,
    previousSelectedCount || 1,
    directory,
  );
  return {
    status: "reconciling",
    reason: "audit_pass_limit_reached",
    auditOperationId,
    state: reviewed.record.state,
  };
}


export async function reconcileOrderAudit(
  page,
  auditOperationId,
  { directory, policy: suppliedPolicy = null } = {},
) {
  const policy = suppliedPolicy || (await loadOrderAuditPolicy());
  const pendingStatus = requireAuditUiValidation(policy);
  const loaded = await loadAuditOperation(auditOperationId, directory);
  if (
    ![
      AuditOperationState.WRITE_REQUEST_MAYBE_SENT,
      AuditOperationState.RECONCILING,
    ].includes(loaded.record.state)
  ) {
    throw new OrderAuditError(
      "ORDER_AUDIT_RECONCILIATION_STATE_INVALID",
      "当前审核操作不允许只读核验。",
    );
  }
  const selection = await preparePendingAuditSelection(
    page,
    loaded.record,
    pendingStatus,
    { policy, selectRows: false },
  );
  const reconciled = await recordAuditReconciliation(
    auditOperationId,
    selection.pendingCount,
    directory,
  );
  return {
    status:
      reconciled.record.state === AuditOperationState.VERIFIED
        ? "audit_verified"
        : "reconciling",
    auditOperationId,
    state: reconciled.record.state,
    remainingPendingCount: selection.pendingCount,
    safety:
      "No row was selected and no audit button was clicked during reconciliation.",
  };
}
