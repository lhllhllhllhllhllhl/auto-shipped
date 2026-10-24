import assert from "node:assert/strict";
import test from "node:test";

import {
  createResidentCommandQueue,
  createResidentSubmissionCadence,
  ResidentCommand,
  ResidentSessionError,
  validateResidentCommand,
} from "./resident-session.mjs";
import { SUBMIT_AUTHORIZATION } from "./submission.mjs";
import { NO_TASK_RESOLUTION_AUTHORIZATION } from "./operation-state.mjs";


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


test("resident no-task resolution requires its own exact authorization", () => {
  assert.throws(
    () => validateResidentCommand({
      command: ResidentCommand.RESOLVE_NO_TASK,
      browserOperationId: FIRST_OPERATION,
      authorization: SUBMIT_AUTHORIZATION,
    }),
    (error) =>
      error instanceof ResidentSessionError &&
      error.code === "RESIDENT_NO_TASK_RESOLUTION_AUTHORIZATION_REQUIRED",
  );
  assert.deepEqual(
    validateResidentCommand({
      command: ResidentCommand.RESOLVE_NO_TASK,
      browserOperationId: FIRST_OPERATION,
      authorization: NO_TASK_RESOLUTION_AUTHORIZATION,
    }),
    {
      command: ResidentCommand.RESOLVE_NO_TASK,
      browserOperationId: FIRST_OPERATION,
      authorization: NO_TASK_RESOLUTION_AUTHORIZATION,
    },
  );
});


test("resident submissions keep a minimum cadence without delaying the first batch", async () => {
  let current = 1_000;
  const waits = [];
  const cadence = createResidentSubmissionCadence({
    minimumIntervalMs: 60_000,
    now: () => current,
    wait: async (milliseconds) => {
      waits.push(milliseconds);
      current += milliseconds;
    },
  });

  assert.deepEqual(await cadence.waitForNextAttempt(), {
    waitedMs: 0,
    previousSubmissionAt: null,
  });
  cadence.markAttemptStarted();
  current += 45_000;
  cadence.markSubmissionObserved();
  current += 20_000;
  assert.equal((await cadence.waitForNextAttempt()).waitedMs, 40_000);
  assert.deepEqual(waits, [40_000]);
});
