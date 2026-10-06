import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";


export class ManifestError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ManifestError";
    this.code = code;
  }
}


async function sha256(filePath) {
  const digest = createHash("sha256");
  const data = await fs.readFile(filePath);
  digest.update(data);
  return digest.digest("hex");
}


function requireValue(condition, code, message) {
  if (!condition) {
    throw new ManifestError(code, message);
  }
}


export async function verifyUploadManifest(manifestPath) {
  const manifestFile = path.resolve(manifestPath);
  let manifest;
  try {
    manifest = JSON.parse(await fs.readFile(manifestFile, "utf8"));
  } catch {
    throw new ManifestError(
      "MANIFEST_UNREADABLE",
      "无法读取上传预检 manifest。",
    );
  }

  requireValue(
    manifest.schema_version === "upload-batch/1.0",
    "MANIFEST_SCHEMA_UNSUPPORTED",
    "上传预检 manifest 版本不受支持。",
  );
  requireValue(
    manifest.status === "ready",
    "PREFLIGHT_NOT_READY",
    "上传预检未通过。",
  );
  requireValue(
    manifest.platform === "guanyi" &&
      manifest.operation === "custom_order_import",
    "MANIFEST_TARGET_MISMATCH",
    "manifest 不是管易自定义订单导入批次。",
  );
  requireValue(
    manifest.safety?.upload_authorized === false,
    "MANIFEST_AUTHORIZATION_INVALID",
    "manifest 只能描述预检结果，不能携带上传授权。",
  );

  const configuredPath = manifest.artifact?.path;
  requireValue(
    typeof configuredPath === "string" && configuredPath.length > 0,
    "ARTIFACT_PATH_MISSING",
    "manifest 缺少待上传文件路径。",
  );
  const workbookPath = path.isAbsolute(configuredPath)
    ? configuredPath
    : path.resolve(path.dirname(manifestFile), configuredPath);
  requireValue(
    path.extname(workbookPath).toLowerCase() === ".xlsx",
    "ARTIFACT_TYPE_UNSUPPORTED",
    "待上传文件不是 .xlsx。",
  );

  let stat;
  try {
    stat = await fs.stat(workbookPath);
  } catch {
    throw new ManifestError("ARTIFACT_NOT_FOUND", "待上传 Excel 不存在。");
  }
  requireValue(
    stat.isFile(),
    "ARTIFACT_NOT_FILE",
    "manifest 指向的待上传对象不是文件。",
  );
  requireValue(
    path.basename(workbookPath) === manifest.artifact.file_name,
    "ARTIFACT_FILENAME_MISMATCH",
    "Excel 文件名与 manifest 不一致。",
  );
  requireValue(
    stat.size === manifest.artifact.size_bytes,
    "ARTIFACT_SIZE_MISMATCH",
    "Excel 文件大小与预检结果不一致。",
  );
  const actualHash = await sha256(workbookPath);
  requireValue(
    actualHash === manifest.artifact.sha256,
    "ARTIFACT_HASH_MISMATCH",
    "Excel 文件内容在预检后发生变化。",
  );

  return {
    status: "manifest_verified",
    manifestPath: manifestFile,
    workbookPath,
    artifactSha256: actualHash,
    artifactSizeBytes: stat.size,
    artifactFileName: path.basename(workbookPath),
    profileId: manifest.profile?.profile_id,
    orders: manifest.counts?.orders,
    itemRows: manifest.counts?.item_rows,
    safety: "No file chooser was opened and no upload was performed.",
  };
}
