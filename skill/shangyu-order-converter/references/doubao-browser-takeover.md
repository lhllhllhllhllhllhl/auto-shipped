# 豆包平台浏览器接入

本参考只在豆包工作云电脑执行管易登录、导航、上传或人工验证时使用。业务状态机仍是 `browser/guanyi` 中唯一的一套；本适配只决定 Playwright 控制哪个浏览器。

## 核心关系

- 豆包工作提供一个用户可以直接看到和操作的 Browser/云浏览器。
- 该浏览器在云电脑本机提供 Chrome DevTools Protocol（CDP）端点 `http://127.0.0.1:9222`。
- 项目仍使用 Node `playwright-core`，但不再 `launchPersistentContext` 启动第二个 Chromium；它改用 `connectOverCDP` 接管平台浏览器的唯一 context。
- 用户在 Browser 面板中的操作与 worker 的 DOM 回读发生在同一页面。正常路径不需要 AIO Sandbox、VNC、窗口切换或第二个浏览器。

CDP 是浏览器暴露的控制协议，Playwright 是调用该协议并提供定位、等待、文件选择和状态回读能力的控制库。模型不必亲自拼 CDP 指令；它调用项目入口，由 Playwright 统一执行。

## 开始前

1. 先使用豆包工作的正式浏览器控制能力让 Browser/云浏览器处于可用状态。不要用任意网址或本地占位页“激活”浏览器。
2. 运行 `scripts/doctor.py`。豆包正常路径必须满足：
   - `playwright_core.ready=true`；
   - `browser_backend.ready=true`；
   - `browser_backend.selected=platform_cdp`；
   - `browser_backend.platform_cdp.endpoint=http://127.0.0.1:9222`。
3. 如果 doctor 选择 `persistent`，先停止，不要继续登录或上传，也不要退回 VNC。重新打开平台 Browser 后再检查；仍无法识别时报告 `platform_cdp` 不可用。

Skill 的 `guanyi_browser.py` 会在 Linux 上只读探测该本机端点，确认它确实是 Chrome/Chromium 后为本次进程选择 `platform_cdp`。端点只允许无凭据的 loopback HTTP 地址；外部主机、带账号密码的URL、路径、查询串都被拒绝。

## 正常流程

1. 执行器取得单一浏览器会话锁，通过CDP连接平台浏览器。
2. 已有管易首页或登录页时复用该标签；没有时新建专用标签。不得关闭或改写其他无关平台标签。
3. 账号密码、短信验证码仍通过现有登录operation和内存通道交给同一worker；不得写入脚本、文件、环境变量、命令参数或日志。
4. 登录、弹窗处理、导航、待审核状态、任务基线、文件选择、两段确认、任务中心核验继续调用原模块，不建立豆包专用副本。
5. 命令完成时只断开Playwright的CDP连接并释放会话锁；不得关闭平台默认context或退出豆包浏览器，因此登录Cookie与当前页面由平台浏览器自行保留。

## 阿里云额外验证

只有状态同时满足以下条件才交给用户：

- `status=waiting_for_human_verification`；
- `requiresUserAction=true`；
- `userAction.kind=browser_control`；
- `userAction.target=existing_platform_builtin_browser`；
- `userAction.presentation=platform_browser_panel`。

Agent收到后必须立即调用豆包工作的正式 `browserControl` / 请求用户协助入口，让用户直接看到当前平台Browser页面。不能只发送文字，也不能打开VNC、AIO Sandbox、验证URL、`127.0.0.1`占位页或第二个浏览器。

用户只完成页面要求的点击、输入或拖动，然后确认完成。原worker持续等待同一页面回到稳定的管易首页；无需再让用户复制页面结果。

## 停止条件

出现以下任一情况立即停止并报告，不自动重新登录、重发短信或继续上传：

- CDP端点不可达或不再是本机Chrome/Chromium；
- 平台浏览器context不是唯一一个；
- worker或浏览器连接中断；
- Browser控制入口没有真正弹给用户；
- 当前页不是登记的验证页，或验证后没有回到稳定管易首页；
- 云工作区重置导致进程、operation或状态证据丢失。

恢复时先运行doctor并只读检查当前平台浏览器是否仍登录。不得删除本地持久profile、平台浏览器profile或operation证据，也不得用启动第二个Chromium来掩盖CDP故障。
