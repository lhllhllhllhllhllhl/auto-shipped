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

## 生产登录流程

1. 运行只读 `probe`。返回 `home_ready` 就直接复用，不得重复登录。
2. 只有 `needs_login` 才启动一个 `login-agent-start` operation。已有未结束operation时继续它，不新建第二个。
3. 返回 `waiting_for_credentials` 后，先说明对话历史留存风险；用户接受并提供本次凭据后，优先用 `login-credentials-submit` 经私有stdin交给原worker。平台没有私有stdin时才使用operation返回的一次性本机页面。凭据不得落盘、进入命令参数或跨任务复用。
4. 返回 `needs_verification` 时先询问用户是否方便接收；得到当次确认后才运行一次 `login-sms-request`。收到验证码后用 `login-sms-submit`交给同一operation，不得自动重发短信。
5. `code_submitted`只表示worker仍在判定，继续轮询同一`login-status`；不得启动`probe`或新登录与它争抢浏览器锁。
6. 返回 `LOGIN_LOCAL_VERIFICATION_REQUIRED` 时按下一节让用户在自己的电脑完成验证。其他未知页面或错误立即停止。
7. 只有operation返回`authenticated`，随后只读`probe`又返回`home_ready`，才向用户确认登录成功。登录本身不构成订单上传授权。

登录、弹窗处理、导航、上传和任务核验继续调用`browser/guanyi`原模块，不建立豆包专用副本。命令结束只断开Playwright的CDP连接并释放会话锁，不关闭平台浏览器、默认context或其他标签，因此平台可以自行保留当前页面与Cookie。

## 任务结束后的会话复用

- 同一豆包工作对话完成一次任务后，不主动退出管易、关闭管易标签、关闭平台浏览器或默认context，也不清理平台profile。成功、失败关闭和等待人工处理都遵守这一约束；执行器只释放自己的会话锁和CDP连接。
- 同一对话的下一次任务继续使用平台现有云浏览器：页面为空或不在管易时先打开管易首页，再运行只读`probe`。`home_ready`直接复用，只有`needs_login`才进入登录流程；不得因为开始了新一批订单就预先索要账号密码。
- 这种复用只对仍存活的当前浏览器进程和平台会话有效。平台主动回收、浏览器完整重启或云工作区重置后Cookie可能丢失，必须重新以`probe`结果为准，不得宣称跨重启持久化。
- 不通过定时刷新、空转轮询、后台保活脚本或复制Cookie来阻止平台回收。任务结束后保持自然空闲即可；后续请求到来时再检查状态。

## 阿里云额外验证

精确命中登记的阿里云验证页时，豆包登录operation必须以以下结构化结果终止并释放浏览器锁：

- `status=failed`；
- `code=LOGIN_LOCAL_VERIFICATION_REQUIRED`；
- `requiresUserAction=true`；
- `userAction.kind=complete_login_in_trusted_local_browser`；
- `userAction.target=user_or_admin_local_computer`；
- `userAction.resumePolicy=confirm_then_probe_and_single_retry`。

Agent必须提示用户或管理员在自己的可信macOS/Windows电脑打开管易，使用同一账号完整登录并通过短信及阿里云验证，直到亲眼看到稳定的管易首页。豆包不得尝试在云浏览器完成该验证，不调用`browserControl`，也不打开VNC、AIO Sandbox、验证URL、占位页或第二个浏览器。

用户明确回复“本机已经登录成功”后，豆包先只读`probe`当前平台浏览器；仍为`needs_login`时最多新建一个登录operation重试一次。这个步骤只用于解除账号当次风控，本机与云端不会共享会话；不得导出、复制或上传Cookie、Chrome profile、验证码或其他会话材料。2026-10-19实测确认该流程曾使豆包随后正常进入管易，但不能承诺永久免验证。若本机无法完成，或一次云端重试再次要求阿里云验证，停止并提示联系管易管理员或官方支持。

## 停止条件

出现以下任一情况立即停止并报告，不自动重新登录、重发短信或继续上传：

- CDP端点不可达或不再是本机Chrome/Chromium；
- 平台浏览器context不是唯一一个；
- worker或浏览器连接中断；
- Browser控制入口没有真正弹给用户；
- 当前页不是登记的验证页，或验证后没有回到稳定管易首页；
- 云工作区重置导致进程、operation或状态证据丢失。

恢复时先运行doctor并只读检查当前平台浏览器是否仍登录。不得删除本地持久profile、平台浏览器profile或operation证据，也不得用启动第二个Chromium来掩盖CDP故障。
