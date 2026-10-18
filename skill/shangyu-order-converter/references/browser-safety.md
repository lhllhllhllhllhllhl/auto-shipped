# 管易浏览器稳定与写入边界

## 几何与定位

- headed 浏览器只使用原生窗口和 `viewport: null`；Geometry Guard 是窗口几何的权威来源。
- Guard 在浏览器启动、路由跳转、弹窗关闭和关键动作前读取 viewport、inner/outer、client、visual viewport、screen 和 DPR。
- 几何变化后重新获取 Locator。当前执行器禁止固定坐标；若未来确需坐标，必须即时测量并回读业务结果。
- 截图只观察当前页面，不改变 viewport、缩放或滚动。截图失败不能覆盖原始业务结果。

## 当前只读能力

- `probe`：检查登录和页面状态。
- `navigate-import`：到达“自定义导入”并停止。
- `prepare-import-form`：到达“自定义导入”，只把订单状态设为“待审核”并回读；所有文件输入保持为空，不点击“确定”。
- `probe-task-center`：到达任务中心，只返回标题、表头和可见行数，不读取任务明细。
- `probe-completed-imports`：切换到已完成页签，只读取“自定义订单导入”的记录 ID、序号、名称、时间和状态；ID 必须同时匹配行属性与 ExtJS 数据记录模型。不读取操作人或操作列，不打开任务行或点击结果下载。
- `capture-task-baseline`：在 `prepared` 状态合并保存“执行中 + 已完成”两个页签经过双重验证的任务 ID 集合；文件选择开始后不得覆盖。
- `inspect-file-selection`：复核 manifest、文件哈希、任务基线和唯一的“订单信息上传”输入框，但不选择文件。
- `select-import-file`：当前用户明确授权后，使用精确授权口令选择一次文件，不点“确定”；动作前后持久化状态并记录脱敏网络元数据。
- `verify-manifest`：校验 Excel 的路径、大小和 SHA-256，不打开文件选择器。

## 登录人工验证

- 自动登录提交短信验证码后，只有精确登记的外部验证URL可以进入同浏览器人工接管；当前登记项是`account.ose.aliyun.com/verify.html`。查询串不会进入回执，未知域名、相似域名、其他路径和未知弹窗仍失败关闭。
- 人工接管只暂停原Playwright worker并保持同一个context/profile，不启动第二个浏览器。状态回执必须包含`requiresUserAction=true`和`userAction.kind=browser_control`，供Agent主动调用平台的浏览器控制入口；仅发送文字提示不算完成交接。
- 平台允许时，用户可通过VNC/远程桌面完成点击、键盘输入和拖动。执行器不读取验证内容、不代做验证，也不把验证页面文本、截图、Cookie或查询参数写入日志。
- Linux窗口置前属于可替换的显示适配层：Playwright先`bringToFront`，已有`xdotool`时再按唯一专用window class精确置前；工具不存在、零个或多个匹配窗口时不猜测。
- 默认等待15分钟。worker、浏览器或VNC会话中断时停止并返回人工验证中断；禁止自动重发短信、重启登录或删除持久profile。恢复时先只读检查登录状态。

## 上传状态机

在上传模块实现前，禁止选择文件和提交。实现后至少区分：

```text
prepared
file_selection_started
write_request_maybe_sent
submit_authorized
write_request_sent
reconciling
task_id_observed
post_write_verified / needs_manual_review / retryable_failed / failed_closed
```

文件选择可能触发自动上传，因此从 `file_selection_started` 起不得刷新页面或盲目重新选择文件。写请求已经发出或无法确定是否发出时，禁止自动重提；必须先到任务中心查重。推荐在写入前保存已完成任务 ID 基线，写入后只接受时间窗内唯一新增、标题匹配且 ID 双重核验通过的任务记录。

真实选择文件必须获得用户当次授权，并在动作前完成任务基线和 manifest 复核。选择尝试一旦开始，异常也按“可能已写入”处理，禁止刷新或重复选择。

最终成功不能只依据点击、Toast、网络 200、弹窗关闭或任务状态“执行完成”。第一次确定后必须核对完整字段映射，第二次确定后至少要求任务中心唯一新增任务 ID、任务名称和状态一致，再打开该任务的“查看结果”回读汇总。只有导入总数大于零、成功数等于总数且失败数为零，才进入 `post_write_verified`；部分成功必须人工确认，全部失败可在明确闭环后进入 `retryable_failed`。结果明细只输出脱敏错误代码、来源行号和通用说明，不输出收件人、手机号、地址或原始失败行。已经点击第二次确定但短时未出现新增任务时保持 `reconciling`，只允许稍后再次只读查询任务中心，不得重新提交。下一次尝试使用新 operation ID、关联 `retryOf`、重建基线并重新取得用户授权。已 `post_write_verified` 的同哈希文件永久阻止重复上传。

同一对话可以一次授权多个明确列出的 manifest，但必须逐批闭环。每批使用独立 operation ID、文件哈希和任务基线；前一批未完成核验时不得启动下一批。隔离用于防止串批，不限制 Agent 理解“全部上传”“只上传其中两批”等自然语言。

## 暂态恢复

页面级“加载失败、系统开小差、网络异常、请稍后重试”只允许在写入前使用任务级共享预算恢复。顺序为组件重新加载、页面 reload、重新打开业务页面并按结构化参数重建状态。预算耗尽或进入可能写入状态后 fail-close。
