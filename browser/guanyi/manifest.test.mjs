import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { ManifestError, verifyUploadManifest } from "./manifest.mjs";


function digest(data) {
  return createHash("sha256").update(data).digest("hex");
}


async function fixture() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-manifest-"));
  const workbookPath = path.join(root, "orders.xlsx");
  const contents = Buffer.from("test workbook bytes");
  await fs.writeFile(workbookPath, contents);
  const manifestPath = path.join(root, "manifest.json");
  const manifest = {
    schema_version: "upload-batch/1.0",
    status: "ready",
    platform: "guanyi",
    operation: "custom_order_import",
    profile: { profile_id: "guanyi_order_import_v1", version: 1 },
    artifact: {
      path: workbookPath,
      file_name: "orders.xlsx",
      sha256: digest(contents),
      size_bytes: contents.length,
    },
    counts: { orders: 1, item_rows: 1 },
    checks: {},
    issues: [],
    safety: {
      manifest_contains_recipient_pii: false,
      workbook_contains_recipient_pii: true,
      upload_authorized: false,
    },
  };
  await fs.writeFile(manifestPath, JSON.stringify(manifest));
  return { root, workbookPath, manifestPath };
}


test("verifies unchanged preflight artifact", async () => {
  const item = await fixture();
  try {
    const result = await verifyUploadManifest(item.manifestPath);
    assert.equal(result.status, "manifest_verified");
    assert.equal(result.orders, 1);
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
  }
});


test("rejects artifact changed after preflight", async () => {
  const item = await fixture();
  try {
    await fs.writeFile(item.workbookPath, "changed workbook bytes");
    await assert.rejects(
      verifyUploadManifest(item.manifestPath),
      (error) =>
        error instanceof ManifestError &&
        ["ARTIFACT_SIZE_MISMATCH", "ARTIFACT_HASH_MISMATCH"].includes(error.code),
    );
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
  }
});
