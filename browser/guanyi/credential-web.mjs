#!/usr/bin/env node

import http from "node:http";
import { randomBytes } from "node:crypto";

import {
  credentialProviderName,
  storeCredentials,
} from "./credentials.mjs";


const token = randomBytes(24).toString("hex");
const route = `/setup/${token}`;


function printJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}


function securityHeaders(contentType) {
  return {
    "Cache-Control": "no-store, max-age=0",
    "Content-Type": contentType,
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    Pragma: "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  };
}


function pageHtml(message = "") {
  const providerLabel =
    credentialProviderName() === "macos_keychain"
      ? "macOS 钥匙串"
      : "Windows 当前用户 DPAPI 加密存储";
  const notice = message
    ? `<p class="notice">${message}</p>`
    : "";
  return `<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>尚舆 · 管易凭证安全配置</title>
<style>
  *{box-sizing:border-box} body{margin:0;background:#f5f7fb;color:#172033;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
  main{max-width:520px;margin:8vh auto;padding:30px;background:#fff;border:1px solid #e4e8f0;border-radius:14px;box-shadow:0 12px 36px rgba(18,38,80,.10)}
  h1{margin:0 0 10px;font-size:24px} p{margin:8px 0 20px;color:#586174}.field{margin:16px 0} label{display:block;margin-bottom:6px;font-weight:650;color:#27324a}
  input{width:100%;height:44px;padding:0 12px;border:1px solid #cfd6e4;border-radius:8px;font-size:16px} input:focus{outline:2px solid #1677ff33;border-color:#1677ff}
  button{width:100%;height:46px;margin-top:12px;border:0;border-radius:8px;background:#1677ff;color:#fff;font-size:16px;font-weight:650;cursor:pointer}
  .safe{padding:10px 12px;border-radius:8px;background:#eef6ff;color:#24568f;font-size:13px}.notice{background:#fff2f0;color:#a8071a;padding:10px 12px;border-radius:8px}
</style>
<main>
  <h1>管易凭证安全配置</h1>
  <p>请在此页面输入。数据只发送到本机，并写入${providerLabel}；不会保存到项目、Skill 或日志。</p>
  <div class="safe">地址必须以 <strong>http://127.0.0.1:</strong> 开头。配置成功后本地服务会自动关闭。</div>
  ${notice}
  <form method="post" action="${route}" autocomplete="off">
    <div class="field"><label for="username">管易登录账号</label><input id="username" name="username" required autocomplete="username"></div>
    <div class="field"><label for="password">管易登录密码</label><input id="password" name="password" type="password" required autocomplete="current-password"></div>
    <button type="submit">安全保存到本机凭证存储</button>
  </form>
</main>
</html>`;
}


function successHtml() {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>配置完成</title>
  <style>body{font:16px/1.6 -apple-system,sans-serif;background:#f5f7fb;color:#172033}main{max-width:520px;margin:12vh auto;padding:32px;background:#fff;border-radius:14px;box-shadow:0 12px 36px #12305018}h1{color:#15803d}</style>
  <main><h1>配置完成</h1><p>凭证已保存到当前系统用户的安全凭证存储。可以关闭此页面并回到 Codex。</p></main></html>`;
}


async function readBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 8192) {
      throw new Error("request_too_large");
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}


if (credentialProviderName() === "unsupported") {
  printJson({ status: "unsupported", platform: process.platform });
  process.exitCode = 1;
} else {
  let configured = false;
  const server = http.createServer(async (request, response) => {
    if (configured || request.url !== route) {
      response.writeHead(404, securityHeaders("text/plain; charset=utf-8"));
      response.end("Not found");
      return;
    }

    if (request.method === "GET") {
      response.writeHead(200, securityHeaders("text/html; charset=utf-8"));
      response.end(pageHtml());
      return;
    }

    if (request.method !== "POST") {
      response.writeHead(405, securityHeaders("text/plain; charset=utf-8"));
      response.end("Method not allowed");
      return;
    }

    try {
      const body = await readBody(request);
      const form = new URLSearchParams(body);
      let username = (form.get("username") || "").trim();
      let password = form.get("password") || "";
      if (!username || !password) {
        response.writeHead(400, securityHeaders("text/html; charset=utf-8"));
        response.end(pageHtml("账号和密码不能为空。"));
        return;
      }
      const stored = await storeCredentials(username, password);
      username = "";
      password = "";
      configured = true;
      response.writeHead(200, securityHeaders("text/html; charset=utf-8"));
      response.end(successHtml());
      printJson({ status: "configured", provider: stored.provider });
      setTimeout(() => server.close(), 250);
    } catch {
      response.writeHead(500, securityHeaders("text/html; charset=utf-8"));
      response.end(pageHtml("写入钥匙串失败，请重试或改用终端配置。"));
    }
  });

  server.listen(0, "127.0.0.1", () => {
    const address = server.address();
    const url = `http://127.0.0.1:${address.port}${route}`;
    printJson({ status: "ready", url });
  });
}
