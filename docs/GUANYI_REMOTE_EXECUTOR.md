# 管易远端执行适配层

## 目的

远端适配层把“订单在哪里转换”和“管易浏览器在哪里运行”解耦。订单转换仍只
生成管易 Excel、上传预检 manifest 和摘要；`browser/guanyi` 仍是唯一的登录、
导航、文件选择、提交和任务中心实现。适配层不复制这些逻辑，只负责把一个明确
批次安全送到专用执行端，并返回机器可读状态。

```text
转换结果（Excel + manifest）
            │
            ▼
统一远端任务协议（无原始订单、无业务判断、无本机路径）
            │
      ┌─────┴─────┐
      ▼           ▼
  SSH 传输      HTTPS 传输
  （当前）       （预留）
      └─────┬─────┘
            ▼
服务器暂存与哈希复核
            │
            ▼
复用 browser/guanyi 唯一状态机
            │
            ▼
登录 → 待审核表单 → 基线 → 受控提交 → 任务中心核验
```

当前版本已经把远端暂存、操作准备、临时登录交接、受控提交和只读结果核验接到
同一状态机。远端层没有页面选择器，也不解释订单；它只把经过授权的命令交给
正式 `guanyi_browser.py` 入口。真实提交仍要求用户在当前对话明确授权具体批次。

## 模块

| 模块 | 职责 | 不负责 |
|---|---|---|
| `remote/protocol.py` | 版本化任务合同、文件描述、操作 ID 与安全边界 | 网络、浏览器、业务规则 |
| `remote/ssh_transport.py` | 用 OpenSSH stdin 私密传输元数据和文件 | 解释订单、执行页面动作 |
| `remote/client.py` | 编排暂存、准备、登录交接、提交与核验 | 自动重试、猜测最新文件 |
| `remote/server_runner.py` | 私有落盘、授权门禁、调用正式浏览器入口、保存脱敏状态 | 页面选择器、业务规则、凭据保存 |
| `guanyi_remote.py` | Skill 的统一调用入口 | 保存服务器私钥或管易凭证 |

SSH 是第一个传输插件，不是业务架构。以后增加 HTTPS 时，只新增一个实现同一
`RemoteTransport` 接口的传输模块；任务合同、服务端暂存、`browser/guanyi`
状态机和订单转换均不改变。

## 外部配置

默认读取 `~/.config/auto-shipped/guanyi-remote.json`，也可用
`GUANYI_REMOTE_CONFIG` 或 `--config` 指定。参考
`config/integrations/guanyi_remote_client.example.json`。

配置只保存主机、SSH 用户和私钥路径，不保存私钥正文、管易账号密码、Cookie
或验证码。生产私钥必须由执行环境的 Secret/Vault 或私有文件系统提供；不得
打包进 Skill 或提交 GitHub。切换服务器只改外部配置并在新主机运行部署脚本。

## 调用

只读检查服务器：

```bash
python3 <skill-dir>/scripts/guanyi_remote.py doctor
```

用户明确要求上传指定批次后，才允许先做远端暂存：

```bash
python3 <skill-dir>/scripts/guanyi_remote.py stage \
  /absolute/path/to/上传预检.json \
  /absolute/path/to/管易自定义订单导入.xlsx \
  CONFIRM_REMOTE_BATCH_STAGING
```

该命令只传输并核验文件，不打开浏览器，也不构成文件选择或提交授权。返回
`remote_preflight_ready` 才表示服务器已复核文件名、大小和 SHA-256。查询时
必须使用返回的精确操作 ID：

```bash
python3 <skill-dir>/scripts/guanyi_remote.py status <operation-id>
python3 <skill-dir>/scripts/guanyi_remote.py prepare <operation-id>
```

`prepare` 复用现有 manifest 和 operation-state 模块建立浏览器操作 ID，不打开
浏览器，也不构成提交授权。

只读检查当前服务器会话：

```bash
python3 <skill-dir>/scripts/guanyi_remote.py probe
```

需要登录时启动一个短时登录 operation。账号密码和短信验证码只能从标准输入
传输，不能出现在命令参数、环境变量或文件中：

```bash
python3 <skill-dir>/scripts/guanyi_remote.py login-start
printf '%s\n' '{"username":"<本次账号>","password":"<本次密码>"}' | \
  python3 <skill-dir>/scripts/guanyi_remote.py login-credentials \
  <login-operation-id> CONFIRM_TEMPORARY_CREDENTIALS_SUBMISSION
python3 <skill-dir>/scripts/guanyi_remote.py login-status <login-operation-id>
python3 <skill-dir>/scripts/guanyi_remote.py login-sms-request \
  <login-operation-id> CONFIRM_REQUEST_GUANYI_SMS
printf '%s\n' '<本次验证码>' | \
  python3 <skill-dir>/scripts/guanyi_remote.py login-sms-submit <login-operation-id>
```

只有用户在当前对话明确授权这个远端 operation 对应的具体批次后，才能执行：

```bash
python3 <skill-dir>/scripts/guanyi_remote.py execute \
  <remote-operation-id> CONFIRM_GUANYI_IMPORT_SUBMISSION
```

`execute` 仍由现有状态机依次完成待审核表单、任务基线、文件选择、两次确定、
字段映射核对和任务中心回读。返回 `reconciling`、连接中断、超时或任何可能已
写入的异常后，禁止再次执行 `execute`，只能运行：

```bash
python3 <skill-dir>/scripts/guanyi_remote.py reconcile <remote-operation-id>
```

不得扫描目录或按时间猜测“最新文件”。相同操作 ID 与不同哈希冲突时必须停止；
暂存请求重跑幂等。SSH 在提交前断开不构成成功；进入提交后断开则按“可能已
写入”处理，只能核验，不能盲目重提。

## 安全边界

- 远端请求不接受本机路径，只接受文件名、大小和 SHA-256。
- 服务器为每个操作建立 `0700` 私有目录，文件为 `0600`。
- 传输走 SSH stdin，不建立临时公网下载链接，不开放业务、CDP 或 VNC 端口。
- 客户端启用 host key 校验，禁止 `StrictHostKeyChecking=no`。
- 服务端命令是固定白名单，不接受任意 shell 命令。
- 远端运行器不含页面逻辑，只能调用正式 Skill 的登录、单批导入和结果核验入口。
- 审核、发货、作废和删除没有远端命令。
- manifest 本身继续保持 `upload_authorized=false`；暂存授权不能升级为提交授权。
- 账号密码和验证码只经 SSH stdin 进入当前登录进程，不写入 job state、日志、
  配置、环境变量或命令参数。
- `execute` 在调用浏览器前先落盘 `submit_maybe_sent`；结果不明确时结构性阻止重提。
- 日志和响应不返回 Excel 内容、收件人、手机号或详细地址。

## 服务器迁移

迁移时在新 Ubuntu 主机运行 `deploy/guanyi-server/deploy.sh`，完成 doctor 后更新
外部连接配置。浏览器 profile 不从旧主机复制；应在新服务器重新建立可信登录
会话。旧主机在确认没有在途 operation 后再下线。
