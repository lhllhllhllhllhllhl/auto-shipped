import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { ManifestError, verifyUploadManifest } from "./manifest.mjs";


const execFileAsync = promisify(execFile);
const CLI_PATH = fileURLToPath(new URL("./cli.mjs", import.meta.url));


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
    assert.equal(result.manifestArtifactPath, item.workbookPath);
    assert.equal(result.workbookPath, item.workbookPath);
    assert.equal(result.artifactPathBinding, "manifest_path");
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
  }
});


test("keeps the manifest binding when the same path is supplied explicitly", async () => {
  const item = await fixture();
  try {
    const result = await verifyUploadManifest(
      item.manifestPath,
      item.workbookPath,
    );
    assert.equal(result.artifactPathBinding, "manifest_path");
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
  }
});


test("binds an explicitly provided moved artifact without rewriting provenance", async () => {
  const item = await fixture();
  const movedRoot = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-moved-"));
  const movedPath = path.join(movedRoot, "orders.xlsx");
  try {
    await fs.copyFile(item.workbookPath, movedPath);
    await fs.rm(item.workbookPath);
    const result = await verifyUploadManifest(item.manifestPath, movedPath);
    assert.equal(result.status, "manifest_verified");
    assert.equal(result.manifestArtifactPath, item.workbookPath);
    assert.equal(result.workbookPath, movedPath);
    assert.equal(result.artifactPathBinding, "explicit_current_path");
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
    await fs.rm(movedRoot, { recursive: true, force: true });
  }
});


test("CLI forwards the explicit current artifact path", async () => {
  const item = await fixture();
  const movedRoot = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-moved-"));
  const movedPath = path.join(movedRoot, "orders.xlsx");
  try {
    await fs.copyFile(item.workbookPath, movedPath);
    await fs.rm(item.workbookPath);
    const { stdout } = await execFileAsync(process.execPath, [
      CLI_PATH,
      "verify-manifest",
      item.manifestPath,
      movedPath,
    ]);
    const result = JSON.parse(stdout);
    assert.equal(result.status, "manifest_verified");
    assert.equal(result.workbookPath, movedPath);
    assert.equal(result.artifactPathBinding, "explicit_current_path");
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
    await fs.rm(movedRoot, { recursive: true, force: true });
  }
});


test("does not guess a moved artifact when no current path is supplied", async () => {
  const item = await fixture();
  try {
    await fs.rm(item.workbookPath);
    await assert.rejects(
      verifyUploadManifest(item.manifestPath),
      (error) =>
        error instanceof ManifestError && error.code === "ARTIFACT_NOT_FOUND",
    );
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
  }
});


test("rejects a renamed explicit artifact even when its bytes match", async () => {
  const item = await fixture();
  const renamedPath = path.join(item.root, "different-name.xlsx");
  try {
    await fs.copyFile(item.workbookPath, renamedPath);
    await assert.rejects(
      verifyUploadManifest(item.manifestPath, renamedPath),
      (error) =>
        error instanceof ManifestError &&
        error.code === "ARTIFACT_FILENAME_MISMATCH",
    );
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
  }
});


test("rejects changed bytes at an explicitly bound path", async () => {
  const item = await fixture();
  const movedRoot = await fs.mkdtemp(path.join(os.tmpdir(), "guanyi-moved-"));
  const movedPath = path.join(movedRoot, "orders.xlsx");
  try {
    await fs.writeFile(movedPath, "changed workbook bytes");
    await assert.rejects(
      verifyUploadManifest(item.manifestPath, movedPath),
      (error) =>
        error instanceof ManifestError &&
        ["ARTIFACT_SIZE_MISMATCH", "ARTIFACT_HASH_MISMATCH"].includes(
          error.code,
        ),
    );
  } finally {
    await fs.rm(item.root, { recursive: true, force: true });
    await fs.rm(movedRoot, { recursive: true, force: true });
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
