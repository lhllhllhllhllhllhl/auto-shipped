# 管易浏览器执行器

状态：受控的订单导入执行器。已实现只读探针、待审核表单准备、任务基线、文件选择、两段“确定”、字段对应核验和任务结果回读。2026-10-14 已完成所有者授权的真实全量成功闭环验收；生产入口只对用户在当前对话明确指定并授权的批次开放。审核、发货、作废和删除始终不在能力范围内。

## 边界

- 使用独立 Chrome 配置保存管易登录会话。
- 默认由用户首次手动登录；也可从 macOS 钥匙串读取凭证自动登录。
- Skill、源码、JSON、日志、普通配置和命令行均不保存或输出账号密码；macOS 使用 Keychain，Windows 使用当前用户 DPAPI 加密存储。云端优先使用外部 Secret/Vault 凭证助手；平台没有安全入口且用户明确接受对话留存风险时，也可把本次账号密码发给 Agent，由 Agent 通过一次性`127.0.0.1`页面或私有stdin交给当前登录worker。聊天平台仍可能保留原消息，项目无法代为删除。
- 浏览器接入分为两种：macOS/Windows继续由Playwright启动持久headed Chromium；Linux平台存在可信本机CDP端点时，Playwright连接平台自带的可见浏览器。两者复用同一套登录、导航、上传、任务核验和Geometry Guard。
- 使用页面文字、角色、URL 和状态验证，不使用固定坐标。
- headed 模式使用 `viewport: null`；原生窗口由 Geometry Guard 通过 CDP 统一校准，不再使用 `--start-maximized`。
- 自有Linux浏览器的原生垂直滚动条只在三次几何采样一致、无水平滚动、缩放正常且client/visual viewport证据一致时被识别。豆包`platform_cdp`浏览器由平台管理窗口，不要求外层窗口匹配`1440×850`，也不调用`Browser.setWindowBounds`；最大化窗口仍须三次采样稳定、缩放正常且有效client区域达到最小尺寸，宽高原生滚动条沟槽还须分别由document/visual一致证明且各不超过24px。两种模式都不使用全局20px宽容差。
- 主框架与订单子框架统一按页面状态识别；首页加载、菜单展开和弹窗出现均使用有界等待，不依赖固定延时或坐标。
- 截图只观察当前 viewport，截图前后验证页面尺寸和滚动位置不变；截图失败不覆盖原始业务结果。
- 只在标题已登记的弹窗内部点击“知道了”或语义明确的关闭控件；不点击页面品牌、背景、“不再提醒”或“去授权”。点击后必须确认弹窗消失且页面地址未变化，否则停止。
- 默认导航到“自定义导入”后停止；只有完成manifest、操作状态、任务基线和当次授权校验后，才允许单次选择文件。
- 可以验证上传预检 manifest 和 Excel 哈希，但该操作不打开文件选择器。

## 模块

- `session.mjs`：专用 Chrome 会话、单一profile进程锁与登录状态；验证码提交后只返回脱敏结果分类。
- `human-verification.mjs`：额外验证页白名单、脱敏URL分类和可替换的Linux窗口置前适配；不执行验证动作。
- `login-operation.mjs`：跨消息凭据/短信验证的短时登录操作、分阶段有效期、私有控制通道和状态回执；不保存账号密码或验证码。
- `geometry.mjs`：原生窗口策略、页面几何快照、稳定性检测与写后 fail-close 接口。
- `screenshot.mjs`：不改变 viewport、缩放和滚动的只读截图。
- `credentials.mjs`：macOS 钥匙串凭证提供器，可独立替换。
- `popup.mjs`：已知通知处理和未知弹窗阻断。
- `states.mjs`：页面状态识别。
- `navigation.mjs`：订单查询、自定义导入导航和“待审核”表单状态准备。
- `task-center.mjs`：只读任务中心导航、结构探针、已完成导入任务记录 ID 核验和结果窗口回读；结果仅返回汇总数量与脱敏失败原因。
- `task-reconciliation.mjs`：合并“执行中 + 已完成”任务建立上传前 ID 基线，并以集合差异识别唯一新增任务。
- `manifest.mjs`：验证预检 manifest，并把用户明确交付的当前 Excel 路径绑定到原始文件名、大小和 SHA-256；不扫描目录、不改写 manifest。
- `operation-state.mjs`：为通过预检的批次建立不可跳级的操作状态机和非敏感本地回执。
- `file-selection.mjs`：复核操作记录、任务基线与文件哈希，唯一定位“订单信息上传”输入框，并在当次授权门禁后选择一次文件。
- `submission.mjs`：在同一操作内选择文件，点击导入弹窗的第一次“确定”，完整滚动核对“自定义字段对应”的全部字段，再点击第二次“确定”；任一阶段出现未登记写请求、字段错配或证据不足即停止。
- `cli.mjs`：稳定命令入口。

## 命令

在 `browser/guanyi` 目录执行：

```bash
npm run login
npm run credential:set
npm run credential:web
npm run credential:status
npm run login-auto
npm run login-auto-start
npm run login-agent-start
npm run login-credentials-submit -- "<login-operation-id>" "CONFIRM_TEMPORARY_CREDENTIALS_SUBMISSION"
npm run login-status -- "<login-operation-id>"
npm run login-sms-request -- "<login-operation-id>" "CONFIRM_REQUEST_GUANYI_SMS"
npm run login-sms-submit -- "<login-operation-id>"
npm run login-auto-sms
npm run probe
npm run navigate-import
npm run prepare-import-form
npm run probe-task-center
npm run probe-completed-imports
npm run capture-task-baseline -- "<operation-id>"
npm run inspect-file-selection -- "<operation-id>"
npm run select-import-file -- "<operation-id>" "CONFIRM_FILE_SELECTION_MAY_UPLOAD"
npm run submit-import -- "<operation-id>" "CONFIRM_GUANYI_IMPORT_SUBMISSION"
npm run execute-import -- "<operation-id>" "CONFIRM_GUANYI_IMPORT_SUBMISSION"
npm run reconcile-operation -- "<operation-id>"
npm run verify-manifest -- "/path/to/上传预检.json"
npm run prepare-operation -- "/path/to/上传预检.json"

# 跨电脑或云工作区时显式提供当前 Excel 路径
npm run verify-manifest -- "/current/path/上传预检.json" "/current/path/管易自定义订单导入.xlsx"
npm run prepare-operation -- "/current/path/上传预检.json" "/current/path/管易自定义订单导入.xlsx"
```

manifest 中的 `artifact.path` 是转换环境留下的来源位置证据，不因文件传输而改写。当前环境无法访问该位置时，必须把用户明确交付的 Excel 路径作为第二个参数传入；文件名、大小或 SHA-256 任一不一致即停止。不得复制到伪造的原路径、扫描下载目录、按修改时间猜文件或只因路径变化重建 manifest。

`credential:set` 由员工在本机终端交互执行。看不到终端时可运行 `credential:web`，它会提供一次性 `127.0.0.1` 页面，配置成功后自动关闭本地服务。两种方式都只写入当前系统安全凭证区。通常优先复用专用 Chrome 的持久登录会话；会话失效时才调用登录流程。

Linux/云端存在 Secret/Vault 时，由平台管理员配置绝对路径 `GUANYI_CREDENTIAL_HELPER`；执行器只以参数 `get` 调用它，并从私有进程管道接收唯一一条 `{"username":"...","password":"..."}` JSON。助手不得把秘密输出到日志，执行器也不会写入或修改 Vault。

平台没有安全入口时，先运行 `login-agent-start`。只有持久会话确实失效，它才返回 `waiting_for_credentials`、登录operation ID和单次`credentialInputUrl`。Agent必须先说明“密码会留在平台对话历史中”；用户明确接受并发送本次账号密码后，平台具备私有标准输入时优先运行`login-credentials-submit`并通过stdin提交单行JSON。没有私有stdin时才用平台私有浏览器打开该`127.0.0.1`页面；页面POST后直接交给同一Playwright worker并立即失效。不得把凭据转存到临时脚本、文件、环境变量、剪贴板脚本或命令参数。两种入口共用同一个随机令牌控制通道和登录状态机，不建立第二套登录实现。

不勾选管易“记住账户”，也不启用 Chrome 保存密码：持久会话负责减少重复登录，安全凭证提供器负责会话失效后的账号密码填充。跨消息场景优先运行 `login-auto-start`。它检测到短信验证后只返回 `needs_verification` 和登录操作 ID，不会立即发短信。用户当次确认可以接收后，Agent 才运行 `login-sms-request`；用户随后把一次性验证码发给 Agent，平台支持私有标准输入时优先通过 `login-sms-submit` 交给仍在运行的原登录进程，否则才使用返回的单次`smsInputUrl`页面。验证码只存在于进程内存，不进入操作回执、命令参数、日志、Skill 或凭证库。旧的 `login-auto-sms` 仅保留给同一终端可持续读取标准输入的本地环境。

豆包工作云电脑中，平台自带浏览器就是项目Playwright通过`connectOverCDP`控制的同一个页面会话。Skill只允许`http://127.0.0.1:9222`等无凭据loopback端点，并要求唯一context；已有管易标签时复用，没有时新建标签，但不关闭其他平台标签。验证码提交后，worker会区分已登录、验证码拒绝、凭据拒绝、风控、未知弹窗/额外步骤和超时未确认，并只返回页面类型、安全URL与分类等脱敏证据。返回`code_submitted`表示结果仍在判定中，Agent只能查询原operation，不能重发短信或重启登录。

精确命中`account.ose.aliyun.com/verify.html`时，本机持久浏览器仍可进入`waiting_for_human_verification`并把同一窗口交给当前电脑的用户处理；豆包`platform_cdp`模式不得走这条路径。豆包operation直接以`LOGIN_LOCAL_VERIFICATION_REQUIRED`终止并释放浏览器锁，输出`userAction.kind=complete_login_in_trusted_local_browser`。Agent提示用户/管理员在自己的可信电脑完整登录同一账号并通过阿里云验证，直到进入管易首页；用户确认后豆包先只读`probe`，仍未登录时最多重新登录一次。

豆包不得在云浏览器代做或交接阿里云验证，也不得打开VNC、AIO Sandbox、验证URL、本地占位页或第二个浏览器。2026-10-19实测本机验证后的一次云端重试曾正常登录；这不是永久豁免，不涉及VPN切换，也不允许在设备间复制Cookie、profile或验证码。

独立headed Linux浏览器使用专用window class和可选`xdotool`精确置前；平台CDP浏览器只调用Playwright `bringToFront`，不做VNC窗口搜索。未知验证URL、未知弹窗、人工等待超时、worker或浏览器连接中断都失败关闭。中断后不得自动重发短信、重启登录或清理profile；先只读检查当前浏览器是否已登录。Skill更新/bootstrap不删除`guanyi-browser-profile`，CDP清理也不得关闭平台默认context或平台profile。

同一浏览器会话同时只允许一个执行器所有者。登录worker占用期间另启`probe`、导航、上传或第二个登录会返回`browser_session_busy`。命令结束时，本地模式关闭自有context；CDP模式只断开Playwright控制连接，保留平台浏览器、标签、Cookie和profile。等待凭据、短信授权和验证码的阶段默认分别为10分钟；验证码提交后的普通结果窗口默认45秒，登记的人工验证阶段默认15分钟。

同一对话中可以存在多个已经转换成功的 Excel。Agent 必须先把“这些都上传”解析并回显为明确的 manifest/operation 清单；一次自然语言授权可以覆盖这份明确清单，但执行器仍按顺序逐批运行。每批独立校验哈希、建立任务基线、提交并核验唯一任务 ID；前一批没有到达 `post_write_verified` 时，不开始下一批。不得扫描目录猜测“最新文件”。

正式 Skill 包装入口只接受清单一致的内置/状态运行时，或相关浏览器与Skill入口文件没有未提交修改的Git开发工作区，并只在 `GY-UPLOAD-SUBMISSION` 达到 `implemented + confirmed` 时为本次进程开启 Node 写入层。显式指定普通复制目录不能绕过`runtime-manifest.json`；规则回退、实现引用失效、临时修改或打包不一致都会结构性拒绝浏览器入口。开发维护者未来需要重新验收时可临时设置 `GUANYI_CONTROLLED_WRITE_VALIDATION=CONFIRM_CONTROLLED_GUANYI_WRITE_VALIDATION`；该口令不参与普通生产流程，且不能代替当前批次的用户提交授权。

运行时会话和失败截图位于 `runtime/`，已排除出版本控制。截图可能包含后台信息，只用于本地故障诊断。

## 第一阶段验收

1. 登录失效时返回 `needs_login`。
2. 用户登录后返回可识别的已认证状态。
3. 出现短信验证时返回 `needs_verification`，并保持窗口等待用户完成。云端提交验证码后必须在同一operation返回`authenticated`、`LOGIN_LOCAL_VERIFICATION_REQUIRED`或明确的脱敏失败分类；等待中的`code_submitted`不得触发重发短信。豆包遇到阿里云验证必须终止worker并要求用户在自己的电脑完整登录，确认后只读探测并最多重试一次。登录worker运行期间启动第二个浏览器命令必须返回`browser_session_busy`，不能抢占或关闭profile。
4. 已知公告可以安全关闭；关闭未生效、页面地址变化或出现未知弹窗时返回 `popup_unknown`。
5. 侧栏展开或折叠时都能进入订单查询。
6. 能打开订单导入并切换到自定义导入；任务中心读取基线后，必须重新激活并回读确认“订单查询”页签，不能仅因后台 DOM 中仍存在“订单导入”按钮就判定已经切回。
7. 默认流程不选择文件；真实提交必须经过当前对话对明确批次的授权和精确口令，只能导入为“待审核”，不审核、不发货。
8. manifest 与 Excel 不一致时在文件选择之前阻断。
9. 浏览器启动、路由跳转、弹窗关闭和关键动作前的几何保持稳定。
10. 截图前后 viewport 与滚动位置保持不变。
11. `prepare-operation` 只建立 `prepared` 操作 ID，并同时记录 manifest 的原始文件位置和当前环境中经过哈希核验的绑定路径；不能跳过文件选择边界直接进入已提交状态。
12. 未登录时任务中心探针返回 `needs_login`，不自动触发短信；登录后只读取标题、表头和可见行数。
13. `probe-completed-imports` 只读取“自定义订单导入”的任务记录 ID、序号、任务名称、时间和状态；任务 ID 必须同时通过行属性与 ExtJS 数据记录模型一致性校验。不读取操作人、操作列，不打开任务行；ID 证据不足时不得把列表结果当作写后成功凭证。
14. `prepare-import-form` 必须导航到“订单导入 → 自定义导入”，只允许把订单状态设为“待审核”，并回读选中值；文件输入框必须保持为空，禁止点击“确定”。
15. `capture-task-baseline` 只能在 `prepared` 状态执行，保存“执行中 + 已完成”两个页签经过双重 ID 校验的当前任务集合；文件选择开始后禁止覆盖基线。
16. `inspect-file-selection` 必须重新校验 manifest、文件 SHA-256、操作状态和任务基线，并唯一定位“订单信息上传”输入框；只报告就绪，不调用文件选择。
17. `select-import-file` 只有在用户当次明确授权后才可运行；必须使用精确授权口令，选择文件前写入 `file_selection_started`，选择尝试后立即写入 `write_request_maybe_sent`。它记录脱敏网络请求元数据，但不点击“确定”；无论页面是否显示上传结果，都不得自动重试。
18. `submit-import` 只有在用户明确授权当前批次后才可运行；文件选择只允许出现已登记的管易取 OSS 地址与指定 OSS 暂存请求，且两条响应必须成功。第一次“确定”后必须唯一出现“自定义字段对应”，完整遍历虚拟滚动表格的全部行，确认系统字段与 Excel 字段逐行同名、一一对应且必填字段齐全；随后才允许点击第二次“确定”。模板读取和最终导入请求必须分别精确匹配已登记接口，缺失、重复、非成功响应或任何额外写请求都停止并进入查重，禁止刷新、重选或重提。
19. `reconcile-operation` 先进入持久的 `reconciling` 状态。已经点击第二次确定但短时间没有新任务时保持该状态并允许稍后只读复查；只有文件暂存、没有授权或没有进入最终提交，且任务中心确认无新增任务时进入 `retryable_failed`。唯一新增任务 ID、任务名称和“执行完成”状态只证明任务已经结束，执行器还必须打开该任务的“查看结果”，回读导入总数、成功数、格式错误数和非法数据数。只有 `成功数=总数` 且失败数为零才进入 `post_write_verified`；部分成功进入 `needs_manual_review`，全部失败进入 `retryable_failed`，多条新增、ID 证据不足、统计矛盾或未知状态进入人工确认。
20. 相同 Excel 哈希只有在 `post_write_verified` 或仍有未闭环操作时阻止新操作。`retryable_failed` 或明确失败关闭后允许创建新操作，新记录必须关联 `retryOf`、递增 `attemptNumber`、重建任务基线并重新取得用户授权。
21. `execute-import` 是正常生产路径：只接受已 `prepared` 且得到当前批次授权的操作。浏览器会话不可用时，它先在当前进程读取安全凭证并自动登录，再依次完成表单、基线、提交和结果回读；不得在这些阶段之间关闭浏览器或重新登录。若管易要求短信验证，命令只返回 `needs_verification`，不发送短信、不选择文件；Agent 先走标准短信交接，再重新执行当前已授权批次。两分钟内仍无唯一结果时返回 `reconciling`，后续仅允许只读续查。

## 实机验收记录

2026-09-28 已在专用 Chrome 配置完成以下只读验收：钥匙串凭证登录、短信验证码请求、短信说明弹窗“知道了”、验证码提交、浏览器重启后的持久会话复用、Geometry Guard、订单查询子框架识别、已知通知关闭、进入“订单导入 → 自定义导入”、把订单状态设为“待审核”并回读确认，以及“常用页面 → 任务中心”真实页面结构和任务记录 ID 探针。真实页面的 `data-recordid` 与 ExtJS 记录模型 `id` 一致。

2026-10-14 在所有者逐步授权下完成第一次真实写入链路观测：文件暂存后第一次“确定”只读取自定义模板并打开“自定义字段对应”；虚拟滚动表格共核对 48 行，系统字段与 Excel 字段全部同名对应；第二次“确定”才调用最终导入接口。任务中心回读到唯一任务 ID `705662035`，结果为总数 2、成功 1、格式错误 1、非法数据 0。失败原因属于店铺无法匹配；原始结果行含收件信息，因此执行器只允许返回来源行号、错误代码和脱敏说明，不保存或输出原文。该批次证明了两段确认、最终请求、任务 ID 和结果窗口的真实结构，同时暴露了店铺名称配置问题。

2026-10-18 豆包云工作区实测确认：平台Chrome以`--remote-debugging-port=9222`运行，项目Node `playwright-core`可通过`connectOverCDP`读取用户正在看的同一测试页，正式`browserControl`交接后用户点击产生`SAME_BROWSER_CONFIRMED`。另一轮VNC测试也确认键盘和拖动能力，但VNC现仅保留为历史证据，不再是豆包生产路径。长时间等待期间平台可能重置云会话，因此生产worker必须事件驱动回读并在CDP断开时停止。

2026-10-19 豆包工作按正式`platform_cdp`流程完成真实登录验收：doctor确认后先只读探测为`needs_login`，单一`login-agent-start` operation经私有stdin接收临时凭据，未重复发送短信或进入额外验证，最终同时得到`authenticated`与`home_ready`。此前账号在阿里云二次验证后无法建立会话时，由用户先在可信本机浏览器完整验证并进入管易首页，随后云端一次重试成功；该恢复步骤不代表永久免验证。

同日只读导航验收确认：“当前页面已经更新到极速版，可以通过当前按钮回到历史版本”提示可能在订单查询页初次检查后延迟出现并拦截“订单导入”。豆包真实页面取证进一步确认：提示是顶层文档的 Ant Popconfirm，“订单导入”位于同源 `trade_order_header` iframe；Popconfirm 配套的匿名全屏 dim 遮罩从顶层拦截 iframe 内按钮，且稳定存在而非动画。执行器仅在完整正文与同一 Ant Popconfirm 内唯一“知道了”按钮同时匹配时关闭该提示，并同时确认提示和全屏遮罩消失、页面地址未变化，才允许在尚未选择文件的导航阶段继续或安全重试一次“订单导入”。未知、重复、关闭未生效或遮罩残留时仍然失败关闭。

同日修正店铺名称后，用新 operation、重新建立任务基线并重新取得用户授权完成第二次真实验收。执行器核对同一套 48 行字段对应，任务中心回读唯一任务 ID `705668612`，结果为总数 2、成功 2、格式错误 0、非法数据 0，状态进入 `post_write_verified`。这条证据解锁 `GY-UPLOAD-SUBMISSION` 的生产规则门禁，但不会取消“转换后先交付确认”“当前对话明确指定批次”“仅待审核”“写后禁止盲目重试”等运行时边界。
