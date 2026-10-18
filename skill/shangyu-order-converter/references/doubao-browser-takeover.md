# 豆包同浏览器人工接管

仅当管易登录 operation 返回 `waiting_for_human_verification`、`requiresUserAction=true` 且 `userAction.kind=browser_control` 时使用本适配。它只负责把豆包云电脑的 VNC 桌面交给用户；通用 Playwright 登录、导航和上传状态机不包含豆包专用逻辑。

## 必须区分的界面

- 豆包自带浏览器：用于显示 AIO Sandbox 控制面板，或打开带随机令牌的一次性凭据/验证码页面。
- 项目 Playwright Chromium：运行管易和阿里云验证页，由原登录 worker 持有。
- AIO Sandbox 的 VNC 标签：把 Playwright Chromium 所在的同一云桌面实时显示给用户。

人工接管不是把阿里云 URL 重新打开到豆包自带浏览器，也不是另启第二个 Chromium。`humanVerification.safeUrl` 只用于证明命中了登记页面，不是导航目标。

## 豆包交接顺序

1. 保持原登录 operation 和 worker 运行；不得运行 `probe`、第二个登录、导航或上传命令。
2. 若豆包的 `browserControl` 要求先存在可控制的页面，只能打开已验证的 AIO Sandbox 入口 `http://127.0.0.1:8080/`。必须回读页面确有 `AIO Sandbox` 和 `VNC` 标签；不能用 `127.0.0.1:1`、任意占位端口、阿里云验证 URL 或其他猜测地址来“激活浏览器”。
3. 切到 AIO Sandbox 的 `VNC` 标签，确认桌面已经加载。项目窗口应是同一云桌面中的 Playwright Chromium；窗口未置前时，可让用户在 VNC 中切换窗口，但不得另开浏览器。
4. 立即调用豆包 `interaction.request_action` 的 `browserControl`，让界面真正出现“去操作/请求用户协助”。只发送文字或停在“调用交互请求工具”不算完成交接。
5. 告诉用户只完成当前验证页上的点击、输入或拖动，然后使用平台的“确认完成”返回对话。Agent不得代做验证。
6. 用户返回后，只查询原 operation 的 `login-status`。worker会在同一个 context 中回读管易首页并继续；不得根据“确认完成”按钮本身宣称登录成功。

若 `127.0.0.1:8080` 无法打开、VNC 标签不存在、`browserControl` 调用失败或用户看到的不是项目 Playwright 窗口，明确报告人工接管不可用并保持原 operation 等待。只要 worker 仍存活且未超时，可以对同一 VNC 入口重新发起一次交接；不得重发短信、重启登录、删除 profile 或编造其他本地网址。
