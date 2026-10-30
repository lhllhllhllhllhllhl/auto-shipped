import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import {
  acquireBrowserOperationLease,
  BrowserOperationBrokerError,
  withBrowserOperationLease,
} from "./browser-operation-broker.mjs";


function fakeChild(line = null) {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stdin = new PassThrough();
  child.kill = () => child.emit("exit", 1);
  child.stdin.on("finish", () => child.emit("exit", 0));
  if (line) queueMicrotask(() => child.stdout.write(`${JSON.stringify(line)}\n`));
  return child;
}


test("disabled broker executes callback without spawning", async () => {
  let called = false;
  const result = await withBrowserOperationLease(
    "op-1",
    async () => {
      called = true;
      return 7;
    },
    { command: "" },
  );
  assert.equal(called, true);
  assert.equal(result, 7);
});


test("lease is released after callback", async () => {
  const child = fakeChild({ status: "lease_acquired", lease_id: "lease-1" });
  const lease = await acquireBrowserOperationLease("op-1", {
    command: "/fake/broker",
    spawnImpl: () => child,
  });
  assert.equal(lease.enabled, true);
  await lease.release();
  assert.equal(child.stdin.writableEnded, true);
});


test("invalid operation id fails before spawn", async () => {
  await assert.rejects(
    acquireBrowserOperationLease("bad id", { command: "/fake/broker" }),
    BrowserOperationBrokerError,
  );
});
