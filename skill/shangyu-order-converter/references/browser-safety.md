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

## 未来上传状态机

在上传模块实现前，禁止选择文件和提交。实现后至少区分：

```text
prepared
file_selection_started
write_request_maybe_sent
write_request_sent
task_id_observed
post_write_verified
failed_closed
```

文件选择可能触发自动上传，因此从 `file_selection_started` 起不得刷新页面或盲目重新选择文件。写请求已经发出或无法确定是否发出时，禁止自动重提；必须先到任务中心查重。推荐在写入前保存已完成任务 ID 基线，写入后只接受时间窗内唯一新增、标题匹配且 ID 双重核验通过的任务记录。

真实选择文件必须获得用户当次授权，并在动作前完成任务基线和 manifest 复核。选择尝试一旦开始，异常也按“可能已写入”处理，禁止刷新或重复选择。

最终成功不能只依据点击、Toast 或弹窗关闭。至少要求任务中心状态成功；条件允许时再回读本批订单号和数量，与 manifest 一致。

## 暂态恢复

页面级“加载失败、系统开小差、网络异常、请稍后重试”只允许在写入前使用任务级共享预算恢复。顺序为组件重新加载、页面 reload、重新打开业务页面并按结构化参数重建状态。预算耗尽或进入可能写入状态后 fail-close。
