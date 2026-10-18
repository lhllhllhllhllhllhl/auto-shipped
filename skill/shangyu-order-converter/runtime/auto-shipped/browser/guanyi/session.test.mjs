import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  acquireBrowserSessionLease,
  classifyLoginFeedback,
  confirmStableAuthenticatedPage,
  fillPreparedCredentialLogin,
  inspectPostSmsLoginState,
  isAccessBlockedText,
  isAuthenticatedAppUrl,
  isLoginUrl,
  retainSingleStartupPage,
  SessionError,
  submitPreparedCredentialLogin,
  waitForAuthenticatedPage,
} from "./session.mjs";


test("recognizes login, app, and firewall-blocked states", () => {
  assert.equal(isLoginUrl("https://login.guanyierp.com/login"), true);
  assert.equal(isAuthenticatedAppUrl("https://www.guanyierp.com/index"), true);
  assert.equal(
    isAccessBlockedText("405 URL可能造成安全威胁，您的访问被阻断。"),
    true,
  );
  assert.equal(isAccessBlockedText("管易首页"), false);
});


test("classifies only safe login feedback categories", () => {
  assert.equal(classifyLoginFeedback("短信验证码错误，请重新输入"), "sms_code_rejected");
  assert.equal(classifyLoginFeedback("操作过于频繁，请稍后再试"), "login_risk_controlled");
  assert.equal(classifyLoginFeedback("账号或密码错误"), "credentials_rejected");
  assert.equal(classifyLoginFeedback("验证码将发送到手机号码"), null);
});


test("returns a redacted post-SMS observation", async () => {
  const page = {
    url() {
      return "https://login.guanyierp.com/login?account=private";
    },
    getByPlaceholder() {
      return { async isVisible() { return true; } };
    },
    getByText() {
      return { async isVisible() { return true; } };
    },
    locator() {
      return {
        async evaluateAll() {
          return {
            texts: ["短信验证码错误，账号 13800000000"],
            dialogVisible: false,
          };
        },
      };
    },
  };

  const result = await inspectPostSmsLoginState(page);
  assert.deepEqual(result, {
    pageState: "login_page",
    safeUrl: "https://login.guanyierp.com/login",
    verificationVisible: true,
    feedbackCategory: "sms_code_rejected",
    dialogVisible: false,
    humanVerificationType: null,
  });
  assert.equal(JSON.stringify(result).includes("13800000000"), false);
  assert.equal(JSON.stringify(result).includes("account=private"), false);
});


test("fails fast with a classified result after an SMS code is submitted", async () => {
  const page = {
    url() {
      return "https://login.guanyierp.com/login";
    },
    frames() {
      return [this];
    },
    async bringToFront() {},
    async waitForTimeout() {},
    getByPlaceholder() {
      return { async isVisible() { return true; } };
    },
    getByText() {
      return { async isVisible() { return true; } };
    },
    locator(selector) {
      if (selector === "body") {
        return { async innerText() { return ""; } };
      }
      return {
        async evaluateAll() {
          return {
            texts: ["验证码已过期"],
            dialogVisible: false,
          };
        },
      };
    },
  };
  const context = { pages() { return [page]; } };

  await assert.rejects(
    waitForAuthenticatedPage(context, page, 1_000, {
      postVerificationTimeoutMs: 100,
      async onVerificationRequired() {
        return { verificationSubmitted: true };
      },
    }),
    (error) =>
      error instanceof SessionError &&
      error.code === "SMS_CODE_REJECTED" &&
      error.details.feedbackCategory === "sms_code_rejected",
  );
});


test("fails fast when submitted credentials are rejected before SMS", async () => {
  const page = {
    url() {
      return "https://login.guanyierp.com/login";
    },
    frames() {
      return [this];
    },
    async waitForTimeout() {},
    getByPlaceholder() {
      return { async isVisible() { return false; } };
    },
    getByText() {
      return { async isVisible() { return false; } };
    },
    locator(selector) {
      if (selector === "body") {
        return { async innerText() { return ""; } };
      }
      return {
        async evaluateAll() {
          return { texts: ["账号或密码错误"], dialogVisible: false };
        },
      };
    },
  };
  const context = { pages() { return [page]; } };

  await assert.rejects(
    waitForAuthenticatedPage(context, page, 1_000, {
      credentialsSubmitted: true,
    }),
    (error) =>
      error instanceof SessionError &&
      error.code === "LOGIN_CREDENTIALS_REJECTED",
  );
});


test("stops on an unregistered post-SMS dialog instead of retrying", async () => {
  const page = {
    url() {
      return "https://login.guanyierp.com/login";
    },
    frames() {
      return [this];
    },
    async bringToFront() {},
    async waitForTimeout() {},
    getByPlaceholder() {
      return { async isVisible() { return true; } };
    },
    getByText() {
      return { async isVisible() { return true; } };
    },
    locator(selector) {
      if (selector === "body") {
        return { async innerText() { return ""; } };
      }
      return {
        async evaluateAll() {
          return { texts: ["需要进行额外确认"], dialogVisible: true };
        },
      };
    },
  };
  const context = { pages() { return [page]; } };

  await assert.rejects(
    waitForAuthenticatedPage(context, page, 1_000, {
      postVerificationTimeoutMs: 100,
      additionalStepStabilityMs: 0,
      async onVerificationRequired() {
        return { verificationSubmitted: true };
      },
    }),
    (error) =>
      error instanceof SessionError &&
      error.code === "LOGIN_ADDITIONAL_STEP_REQUIRED" &&
      error.details.dialogVisible === true,
  );
});


test("allows a brief redirect page after SMS before authenticated app", async () => {
  let url = "https://login.guanyierp.com/login";
  const page = {
    url() {
      return url;
    },
    frames() {
      return [this];
    },
    async bringToFront() {},
    async waitForLoadState() {},
    async waitForTimeout() {},
    async waitForURL() {
      throw new Error("Timeout");
    },
    getByPlaceholder() {
      return { async isVisible() { return true; } };
    },
    getByText() {
      return { async isVisible() { return true; } };
    },
    locator(selector) {
      if (selector === "body") {
        return { async innerText() { return "菜单 首页 我的工单"; } };
      }
      return {
        async evaluateAll() {
          return { texts: [], dialogVisible: false };
        },
      };
    },
  };
  const context = { pages() { return [page]; } };

  const result = await waitForAuthenticatedPage(context, page, 1_000, {
    postVerificationTimeoutMs: 100,
    async onVerificationRequired() {
      url = "https://login.guanyierp.com/redirect";
      setTimeout(() => {
        url = "https://www.guanyierp.com/index";
      }, 0);
      return { verificationSubmitted: true };
    },
    pollIntervalMs: 1,
  });
  assert.equal(result, page);
});


test("pauses on the registered Aliyun page and resumes the same page after human verification", async () => {
  let url = "https://login.guanyierp.com/login";
  let humanObservation = null;
  const page = {
    url() {
      return url;
    },
    frames() {
      return [this];
    },
    async bringToFront() {},
    async waitForLoadState() {},
    async waitForTimeout() {},
    async waitForURL() {
      throw new Error("Timeout");
    },
    getByPlaceholder() {
      return { async isVisible() { return isLoginUrl(url); } };
    },
    getByText() {
      return { async isVisible() { return isLoginUrl(url); } };
    },
    locator(selector) {
      if (selector === "body") {
        return {
          async innerText() {
            return isAuthenticatedAppUrl(url) ? "菜单 首页 我的工单" : "";
          },
        };
      }
      return {
        async evaluateAll() {
          return { texts: [], dialogVisible: false };
        },
      };
    },
  };
  const context = { pages() { return [page]; } };

  const result = await waitForAuthenticatedPage(context, page, 1_000, {
    postVerificationTimeoutMs: 100,
    humanVerificationTimeoutMs: 1_000,
    pollIntervalMs: 1,
    async onVerificationRequired() {
      url = "http://account.ose.aliyun.com/verify.html?private=value";
      return { verificationSubmitted: true };
    },
    async onHumanVerificationRequired(candidate, observation) {
      assert.equal(candidate, page);
      humanObservation = observation;
      url = "https://www.guanyierp.com/index";
    },
  });

  assert.equal(result, page);
  assert.equal(humanObservation.humanVerificationType, "aliyun_account_verification");
  assert.equal(humanObservation.safeUrl, "http://account.ose.aliyun.com/verify.html");
  assert.equal(JSON.stringify(humanObservation).includes("private=value"), false);
});


test("still fails closed on an unregistered external page after SMS", async () => {
  let url = "https://login.guanyierp.com/login";
  const page = {
    url() {
      return url;
    },
    frames() {
      return [this];
    },
    async bringToFront() {},
    getByPlaceholder() {
      return { async isVisible() { return isLoginUrl(url); } };
    },
    getByText() {
      return { async isVisible() { return isLoginUrl(url); } };
    },
    locator(selector) {
      if (selector === "body") {
        return { async innerText() { return ""; } };
      }
      return {
        async evaluateAll() {
          return { texts: [], dialogVisible: false };
        },
      };
    },
  };
  const context = { pages() { return [page]; } };

  await assert.rejects(
    waitForAuthenticatedPage(context, page, 1_000, {
      additionalStepStabilityMs: 0,
      pollIntervalMs: 1,
      async onVerificationRequired() {
        url = "https://example.com/unknown-verification";
        return { verificationSubmitted: true };
      },
    }),
    (error) =>
      error instanceof SessionError &&
      error.code === "LOGIN_ADDITIONAL_STEP_REQUIRED",
  );
});


test("fails closed when the registered human-verification page disappears", async () => {
  let url = "https://login.guanyierp.com/login";
  let closed = false;
  const page = {
    url() { return url; },
    frames() { return [this]; },
    async bringToFront() {},
    getByPlaceholder() {
      return { async isVisible() { return isLoginUrl(url); } };
    },
    getByText() {
      return { async isVisible() { return isLoginUrl(url); } };
    },
    locator(selector) {
      if (selector === "body") return { async innerText() { return ""; } };
      return {
        async evaluateAll() {
          return { texts: [], dialogVisible: false };
        },
      };
    },
  };
  const context = { pages() { return closed ? [] : [page]; } };

  await assert.rejects(
    waitForAuthenticatedPage(context, page, 1_000, {
      humanVerificationTimeoutMs: 1_000,
      pollIntervalMs: 1,
      async onVerificationRequired() {
        url = "https://account.ose.aliyun.com/verify.html";
        return { verificationSubmitted: true };
      },
      async onHumanVerificationRequired() {
        closed = true;
      },
    }),
    (error) =>
      error instanceof SessionError &&
      error.code === "LOGIN_HUMAN_VERIFICATION_INTERRUPTED",
  );
});


test("allows only one Playwright owner for a browser profile", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-lock-"));
  const lockPath = path.join(directory, "browser.lock");
  let first;
  let second;
  try {
    first = await acquireBrowserSessionLease(lockPath, {
      processId: 111,
      processAlive: (processId) => processId === 111,
    });
    await assert.rejects(
      acquireBrowserSessionLease(lockPath, {
        processId: 222,
        processAlive: (processId) => processId === 111,
      }),
      (error) =>
        error instanceof SessionError &&
        error.code === "BROWSER_SESSION_BUSY",
    );
    await first.release();
    first = null;
    second = await acquireBrowserSessionLease(lockPath, {
      processId: 222,
      processAlive: () => false,
    });
  } finally {
    await first?.release();
    await second?.release();
    await fs.rm(directory, { recursive: true, force: true });
  }
});


test("accepts an authenticated URL only after it remains stable", async () => {
  const frame = {
    locator() {
      return {
        async innerText() {
          return "菜单 首页 我的工单";
        },
      };
    },
  };
  const page = {
    url() {
      return "https://www.guanyierp.com/index";
    },
    frames() {
      return [frame];
    },
    async waitForTimeout() {},
    async waitForURL() {
      throw new Error("Timeout");
    },
  };

  assert.equal(await confirmStableAuthenticatedPage(page, 1), true);
});


test("rejects a transient authenticated URL that redirects to login", async () => {
  let url = "https://www.guanyierp.com/index";
  const frame = {
    locator() {
      return {
        async innerText() {
          return "菜单 首页 我的工单";
        },
      };
    },
  };
  const page = {
    url() {
      return url;
    },
    frames() {
      return [frame];
    },
    async waitForTimeout() {},
    async waitForURL(predicate) {
      url = "https://login.guanyierp.com/login";
      assert.equal(predicate(new URL(url)), true);
    },
  };

  assert.equal(await confirmStableAuthenticatedPage(page, 1), false);
});


function fakeStartupPage(url, closed) {
  return {
    url() {
      return url;
    },
    async close(options) {
      closed.push([url, options]);
    },
  };
}


test("keeps one authenticated startup page and closes restored extras", async () => {
  const closed = [];
  const blank = fakeStartupPage("about:blank", closed);
  const login = fakeStartupPage("https://login.guanyierp.com/login", closed);
  const app = fakeStartupPage("https://www.guanyierp.com/index", closed);
  const context = {
    pages() {
      return [blank, login, app];
    },
    async newPage() {
      throw new Error("should not create a page when one already exists");
    },
  };

  assert.equal(await retainSingleStartupPage(context), app);
  assert.deepEqual(closed, [
    ["about:blank", { runBeforeUnload: false }],
    ["https://login.guanyierp.com/login", { runBeforeUnload: false }],
  ]);
});


test("creates one startup page when Chrome starts without tabs", async () => {
  const created = fakeStartupPage("about:blank", []);
  const context = {
    pages() {
      return [];
    },
    async newPage() {
      return created;
    },
  };

  assert.equal(await retainSingleStartupPage(context), created);
});


test("platform browser keeps unrelated tabs and creates a dedicated page", async () => {
  const closed = [];
  const unrelated = fakeStartupPage("https://example.com/work", closed);
  const created = fakeStartupPage("about:blank", closed);
  const context = {
    pages() {
      return [unrelated];
    },
    async newPage() {
      return created;
    },
  };

  assert.equal(
    await retainSingleStartupPage(context, {
      closeOthers: false,
      createDedicatedPageWhenMissing: true,
    }),
    created,
  );
  assert.deepEqual(closed, []);
});


function fakeCredentialPage(values) {
  const actions = [];
  const inputs = {
    async count() {
      return 2;
    },
    nth(index) {
      return {
        async fill(value) {
          values[index] = value;
          actions.push([index === 0 ? "username" : "password", "filled"]);
        },
        async evaluate(callback) {
          return callback({ value: values[index] || "" });
        },
      };
    },
  };
  const panel = {
    locator(selector) {
      assert.equal(selector, "input");
      return inputs;
    },
    getByRole(role) {
      if (role === "checkbox") {
        return {
          async isVisible() {
            return true;
          },
          async setChecked(value) {
            actions.push(["agreement", value]);
          },
        };
      }
      return {
        async click() {
          actions.push(["login", "clicked"]);
        },
      };
    },
  };
  return {
    actions,
    page: {
      getByRole() {
        return {
          async isVisible() {
            return false;
          },
        };
      },
      locator(selector) {
        assert.equal(selector, "#rc-tabs-0-panel-Account");
        return panel;
      },
    },
  };
}


test("submits Agent-filled credentials without returning their values", async () => {
  const fake = fakeCredentialPage(["cloud-user", "test-secret"]);
  const result = await submitPreparedCredentialLogin(fake.page);
  assert.deepEqual(result, { submitted: true });
  assert.deepEqual(fake.actions, [
    ["agreement", true],
    ["login", "clicked"],
  ]);
  assert.equal(JSON.stringify(result).includes("cloud-user"), false);
  assert.equal(JSON.stringify(result).includes("test-secret"), false);
});


test("fills temporary credentials without returning or logging their values", async () => {
  const values = ["", ""];
  const fake = fakeCredentialPage(values);
  const prepared = await fillPreparedCredentialLogin(fake.page, {
    username: "cloud-user",
    password: "one-time-secret",
  });
  assert.equal(prepared.inputs !== undefined, true);
  assert.deepEqual(values, ["cloud-user", "one-time-secret"]);
  assert.deepEqual(fake.actions, [
    ["username", "filled"],
    ["password", "filled"],
  ]);
  assert.equal(JSON.stringify(prepared).includes("cloud-user"), false);
  assert.equal(JSON.stringify(prepared).includes("one-time-secret"), false);
});


test("rejects empty temporary credentials before filling the page", async () => {
  const fake = fakeCredentialPage(["", ""]);
  await assert.rejects(
    fillPreparedCredentialLogin(fake.page, {
      username: "cloud-user",
      password: "",
    }),
    (error) =>
      error instanceof SessionError &&
      error.code === "TEMPORARY_CREDENTIALS_INVALID",
  );
  assert.deepEqual(fake.actions, []);
});


test("refuses to continue when Agent has not filled both credentials", async () => {
  const fake = fakeCredentialPage(["cloud-user", ""]);
  await assert.rejects(
    submitPreparedCredentialLogin(fake.page),
    (error) =>
      error instanceof SessionError &&
      error.code === "TEMPORARY_CREDENTIALS_NOT_FILLED",
  );
  assert.deepEqual(fake.actions, []);
});
