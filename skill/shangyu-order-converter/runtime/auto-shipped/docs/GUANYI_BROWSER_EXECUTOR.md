# 管易浏览器执行器

状态：受控的上传前执行器。已实现只读探针、待审核表单准备、任务基线、文件输入框检查和带当次授权的单次文件选择；当前不包含“确定”提交、审核或发货能力。

## 边界

- 使用独立 Chrome 配置保存管易登录会话。
- 默认由用户首次手动登录；也可从 macOS 钥匙串读取凭证自动登录。
- Skill、源码、JSON、日志和命令行均不保存或输出账号密码；macOS 使用 Keychain，Windows 使用当前用户 DPAPI 加密存储，验证码仍由用户处理。
- 使用页面文字、角色、URL 和状态验证，不使用固定坐标。
- headed 模式使用 `viewport: null`；原生窗口由 Geometry Guard 通过 CDP 统一校准，不再使用 `--start-maximized`。
- 主框架与订单子框架统一按页面状态识别；首页加载、菜单展开和弹窗出现均使用有界等待，不依赖固定延时或坐标。
- 截图只观察当前 viewport，截图前后验证页面尺寸和滚动位置不变；截图失败不覆盖原始业务结果。
- 只关闭已登记的安全通知；遇到未知弹窗停止。
- 默认导航到“自定义导入”后停止；只有完成manifest、操作状态、任务基线和当次授权校验后，才允许单次选择文件。
- 可以验证上传预检 manifest 和 Excel 哈希，但该操作不打开文件选择器。

## 模块

- `session.mjs`：专用 Chrome 会话与登录状态。
- `geometry.mjs`：原生窗口策略、页面几何快照、稳定性检测与写后 fail-close 接口。
- `screenshot.mjs`：不改变 viewport、缩放和滚动的只读截图。
- `credentials.mjs`：macOS 钥匙串凭证提供器，可独立替换。
- `popup.mjs`：已知通知处理和未知弹窗阻断。
- `states.mjs`：页面状态识别。
- `navigation.mjs`：订单查询、自定义导入导航和“待审核”表单状态准备。
- `task-center.mjs`：只读任务中心导航、结构探针和已完成导入任务记录 ID 核验，不打开任务行。
- `task-reconciliation.mjs`：合并“执行中 + 已完成”任务建立上传前 ID 基线，并以集合差异识别唯一新增任务。
- `manifest.mjs`：验证预检 manifest、Excel 路径、大小和 SHA-256。
- `operation-state.mjs`：为通过预检的批次建立不可跳级的操作状态机和非敏感本地回执。
- `file-selection.mjs`：复核操作记录、任务基线与文件哈希，唯一定位“订单信息上传”输入框，并在当次授权门禁后选择一次文件；不点击“确定”。
- `cli.mjs`：稳定命令入口。

## 命令

在 `browser/guanyi` 目录执行：

```bash
npm run login
npm run credential:set
npm run credential:web
npm run credential:status
npm run login-auto
npm run login-auto-sms
npm run probe
npm run navigate-import
npm run prepare-import-form
npm run probe-task-center
npm run probe-completed-imports
npm run capture-task-baseline -- "<operation-id>"
npm run inspect-file-selection -- "<operation-id>"
npm run select-import-file -- "<operation-id>" "CONFIRM_FILE_SELECTION_MAY_UPLOAD"
npm run verify-manifest -- "/path/to/上传预检.json"
npm run prepare-operation -- "/path/to/上传预检.json"
```

`credential:set` 由员工在本机终端交互执行。看不到终端时可运行 `credential:web`，它会提供一次性 `127.0.0.1` 页面，配置成功后自动关闭本地服务。两种方式都只写入 macOS 钥匙串。不要把密码写进聊天、Skill、`.env`、源码或配置文件。通常优先复用专用 Chrome 的持久登录会话；会话失效时才调用 `login-auto`。

不勾选管易“记住账户”，也不启用 Chrome 保存密码：持久会话负责减少重复登录，钥匙串负责会话失效后的账号密码填充。默认 `login-auto` 在短信验证页等待人工处理。员工明确表示当前可以接收短信后，Agent 才可运行 `login-auto-sms`：执行器点击“获取验证码”，等待并核验“验证码将发送到手机号码为…”提示弹窗，只点击该弹窗的“知道了”，然后用户把本次验证码提供给 Agent，Agent 通过进程标准输入提交。验证码只存在于当前登录进程，不写入钥匙串、Skill、配置或处理报告，也不在命令输出中显示。

运行时会话和失败截图位于 `runtime/`，已排除出版本控制。截图可能包含后台信息，只用于本地故障诊断。

## 第一阶段验收

1. 登录失效时返回 `needs_login`。
2. 用户登录后返回可识别的已认证状态。
3. 出现短信验证时返回 `needs_verification`，并保持窗口等待用户完成。
4. 已知公告可以安全关闭，未知弹窗返回 `popup_unknown`。
5. 侧栏展开或折叠时都能进入订单查询。
6. 能打开订单导入并切换到自定义导入。
7. 默认流程不选择文件；受控文件选择必须经过精确授权口令，且始终不点击“确定”、不审核、不发货。
8. manifest 与 Excel 不一致时在文件选择之前阻断。
9. 浏览器启动、路由跳转、弹窗关闭和关键动作前的几何保持稳定。
10. 截图前后 viewport 与滚动位置保持不变。
11. `prepare-operation` 只建立 `prepared` 操作 ID；不能跳过文件选择边界直接进入已提交状态。
12. 未登录时任务中心探针返回 `needs_login`，不自动触发短信；登录后只读取标题、表头和可见行数。
13. `probe-completed-imports` 只读取“自定义订单导入”的任务记录 ID、序号、任务名称、时间和状态；任务 ID 必须同时通过行属性与 ExtJS 数据记录模型一致性校验。不读取操作人、操作列，不打开任务行；ID 证据不足时不得把列表结果当作写后成功凭证。
14. `prepare-import-form` 必须导航到“订单导入 → 自定义导入”，只允许把订单状态设为“待审核”，并回读选中值；文件输入框必须保持为空，禁止点击“确定”。
15. `capture-task-baseline` 只能在 `prepared` 状态执行，保存“执行中 + 已完成”两个页签经过双重 ID 校验的当前任务集合；文件选择开始后禁止覆盖基线。
16. `inspect-file-selection` 必须重新校验 manifest、文件 SHA-256、操作状态和任务基线，并唯一定位“订单信息上传”输入框；只报告就绪，不调用文件选择。
17. `select-import-file` 只有在用户当次明确授权后才可运行；必须使用精确授权口令，选择文件前写入 `file_selection_started`，选择尝试后立即写入 `write_request_maybe_sent`。它记录脱敏网络请求元数据，但不点击“确定”；无论页面是否显示上传结果，都不得自动重试。

## 实机验收记录

2026-09-28 已在专用 Chrome 配置完成以下只读验收：钥匙串凭证登录、短信验证码请求、短信说明弹窗“知道了”、验证码提交、浏览器重启后的持久会话复用、Geometry Guard、订单查询子框架识别、已知通知关闭、进入“订单导入 → 自定义导入”、把订单状态设为“待审核”并回读确认，以及“常用页面 → 任务中心”真实页面结构和任务记录 ID 探针。真实页面的 `data-recordid` 与 ExtJS 记录模型 `id` 一致。执行器未选择文件、未上传、未点击导入确认、未打开任务行，也未点击结果下载链接。
