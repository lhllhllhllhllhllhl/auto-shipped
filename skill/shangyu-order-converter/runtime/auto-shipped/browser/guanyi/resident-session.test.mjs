import assert from "node:assert/strict";
import test from "node:test";

import {
  createResidentCommandQueue,
  ResidentCommand,
  ResidentSessionError,
  validateResidentCommand,
} from "./resident-session.mjs";
import { SUBMIT_AUTHORIZATION } from "./submission.mjs";


const FIRST_OPERATION = "11111111-1111-4111-8111-111111111111";
const SECOND_OPERATION = "22222222-2222-4222-8222-222222222222";


test("resident commands require exact upload authorization", () => {
  assert.throws(
    () => validateResidentCommand({
      command: ResidentCommand.EXECUTE_IMPORT,
      browserOperationId: FIRST_OPERATION,
      authorization: "yes",
    }),
    (error) =>
      error instanceof ResidentSessionError &&
      error.code === "RESIDENT_SUBMIT_AUTHORIZATION_REQUIRED",
  );
  assert.deepEqual(
    validateResidentCommand({
      command: ResidentCommand.EXECUTE_IMPORT,
      browserOperationId: FIRST_OPERATION,
      authorization: SUBMIT_AUTHORIZATION,
    }),
    {
      command: ResidentCommand.EXECUTE_IMPORT,
      browserOperationId: FIRST_OPERATION,
      authorization: SUBMIT_AUTHORIZATION,
    },
  );
});


test("resident command queue preserves FIFO order and serial ownership", async () => {
  const queue = createResidentCommandQueue();
  const first = queue.enqueue({
    command: ResidentCommand.EXECUTE_IMPORT,
    browserOperationId: FIRST_OPERATION,
    authorization: SUBMIT_AUTHORIZATION,
  });
  const second = queue.enqueue({
    command: ResidentCommand.RECONCILE_IMPORT,
    browserOperationId: SECOND_OPERATION,
  });

  const firstItem = await queue.take();
  assert.equal(firstItem.browserOperationId, FIRST_OPERATION);
  firstItem.resolve({ status: "verified" });
  assert.deepEqual(await first, { status: "verified" });

  const secondItem = await queue.take();
  assert.equal(secondItem.browserOperationId, SECOND_OPERATION);
  secondItem.resolve({ status: "reconciling" });
  assert.deepEqual(await second, { status: "reconciling" });
});
