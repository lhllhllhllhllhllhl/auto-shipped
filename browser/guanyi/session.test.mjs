import assert from "node:assert/strict";
import test from "node:test";

import {
  isAccessBlockedText,
  isAuthenticatedAppUrl,
  isLoginUrl,
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


function fakeCredentialPage(values) {
  const actions = [];
  const inputs = {
    async count() {
      return 2;
    },
    nth(index) {
      return {
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
