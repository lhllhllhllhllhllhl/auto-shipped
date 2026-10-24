import { SUBMIT_AUTHORIZATION } from "./submission.mjs";
import { NO_TASK_RESOLUTION_AUTHORIZATION } from "./operation-state.mjs";


export const ResidentCommand = Object.freeze({
  PROBE: "probe",
  EXECUTE_IMPORT: "execute_import",
  RECONCILE_IMPORT: "reconcile_import",
  RESOLVE_NO_TASK: "resolve_no_task",
});


export class ResidentSessionError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ResidentSessionError";
    this.code = code;
  }
}


function validateOperationId(value) {
  if (!/^[0-9a-f-]{36}$/.test(value || "")) {
    throw new ResidentSessionError(
      "RESIDENT_BROWSER_OPERATION_ID_INVALID",
      "常驻浏览器任务缺少有效的上传操作 ID。",
    );
  }
  return value;
}


export function validateResidentCommand(message) {
  const command = message?.command;
  if (!Object.values(ResidentCommand).includes(command)) {
    throw new ResidentSessionError(
      "RESIDENT_COMMAND_INVALID",
      "常驻浏览器收到未登记的任务。",
    );
  }
  if (command === ResidentCommand.EXECUTE_IMPORT) {
    validateOperationId(message.browserOperationId);
    if (message.authorization !== SUBMIT_AUTHORIZATION) {
      throw new ResidentSessionError(
        "RESIDENT_SUBMIT_AUTHORIZATION_REQUIRED",
        "常驻浏览器未收到本批订单的精确提交授权。",
      );
    }
  } else if (command === ResidentCommand.RECONCILE_IMPORT) {
    validateOperationId(message.browserOperationId);
  } else if (command === ResidentCommand.RESOLVE_NO_TASK) {
    validateOperationId(message.browserOperationId);
    if (message.authorization !== NO_TASK_RESOLUTION_AUTHORIZATION) {
      throw new ResidentSessionError(
        "RESIDENT_NO_TASK_RESOLUTION_AUTHORIZATION_REQUIRED",
        "常驻浏览器未收到关闭无新增任务操作的精确授权。",
      );
    }
  }
  return {
    command,
    browserOperationId: message.browserOperationId || null,
    authorization: message.authorization || null,
  };
}


export function createResidentCommandQueue() {
  const queued = [];
  const waiters = [];

  function deliver(item) {
    const waiter = waiters.shift();
    if (waiter) {
      waiter(item);
      return;
    }
    queued.push(item);
  }

  return {
    enqueue(message) {
      const command = validateResidentCommand(message);
      return new Promise((resolve, reject) => {
        deliver({ ...command, resolve, reject });
      });
    },

    async take() {
      if (queued.length) return queued.shift();
      return await new Promise((resolve) => waiters.push(resolve));
    },

    get length() {
      return queued.length;
    },
  };
}


export function createResidentSubmissionCadence({
  minimumIntervalMs = 60_000,
  now = () => Date.now(),
  wait = async (milliseconds) =>
    await new Promise((resolve) => setTimeout(resolve, milliseconds)),
} = {}) {
  if (!Number.isFinite(minimumIntervalMs) || minimumIntervalMs < 0) {
    throw new ResidentSessionError(
      "RESIDENT_SUBMISSION_INTERVAL_INVALID",
      "常驻浏览器的批次提交间隔配置无效。",
    );
  }
  let lastSubmissionAt = null;

  return {
    async waitForNextAttempt() {
      if (lastSubmissionAt === null) {
        return { waitedMs: 0, previousSubmissionAt: null };
      }
      const elapsedMs = Math.max(0, now() - lastSubmissionAt);
      const waitedMs = Math.max(0, minimumIntervalMs - elapsedMs);
      if (waitedMs > 0) {
        await wait(waitedMs);
      }
      return { waitedMs, previousSubmissionAt: lastSubmissionAt };
    },

    markAttemptStarted() {
      lastSubmissionAt = now();
      return lastSubmissionAt;
    },

    markSubmissionObserved() {
      lastSubmissionAt = now();
      return lastSubmissionAt;
    },

    get lastSubmissionAt() {
      return lastSubmissionAt;
    },
  };
}
