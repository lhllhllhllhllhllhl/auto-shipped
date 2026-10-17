import assert from "node:assert/strict";
import test from "node:test";

import {
  confirmStableAuthenticatedPage,
  fillPreparedCredentialLogin,
  isAccessBlockedText,
  isAuthenticatedAppUrl,
  isLoginUrl,
  retainSingleStartupPage,
  SessionError,
  submitPreparedCredentialLogin,
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
