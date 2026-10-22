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
常驻单浏览器协调器（FIFO串行）
            │
            ▼
复用 browser/guanyi 唯一状态机
            │
            ▼
登录 → 待审核表单 → 基线 → 受控提交 → 任务中心核验
```

当前版本已经把远端暂存、操作准备、常驻登录、受控提交和只读结果核验接到
同一状态机。服务器只有一个长期运行的浏览器owner；多个任务按FIFO排队，登录
成功后不会因为单批任务结束而关闭context。远端层没有页面选择器，也不解释
订单；它只把经过授权的命令交给正式 `guanyi_browser.py` 入口。真实提交仍要求
用户在当前对话明确授权具体批次。

## 模块

| 模块 | 职责 | 不负责 |
|---|---|---|
| `remote/protocol.py` | 版本化任务合同、文件描述、操作 ID 与安全边界 | 网络、浏览器、业务规则 |
| `remote/ssh_transport.py` | 用 OpenSSH stdin 私密传输元数据和文件 | 解释订单、执行页面动作 |
| `remote/client.py` | 编排暂存、准备、登录交接、提交与核验 | 自动重试、猜测最新文件 |
| `remote/server_runner.py` | 私有落盘、授权门禁、调用正式浏览器入口、保存脱敏状态 | 页面选择器、业务规则、明文凭据 |
| `resident-session.mjs` | 校验常驻命令、FIFO排队、保持单一浏览器owner | 页面选择器、订单规则、授权推断 |
| `guanyi_remote.py` | Skill 的统一调用入口 | 保存服务器私钥或管易凭证 |

SSH 是第一个传输插件，不是业务架构。以后增加 HTTPS 时，只新增一个实现同一
`RemoteTransport` 接口的传输模块；任务合同、服务端暂存、`browser/guanyi`
状态机和订单转换均不改变。

## 外部配置

默认读取 `~/.config/auto-shipped/guanyi-remote.json`，也可用
`GUANYI_REMOTE_CONFIG` 或 `--config` 指定。参考
`config/integrations/guanyi_remote_client.example.json`。

客户端配置只保存主机、SSH 用户和私钥路径，不保存私钥正文、管易账号密码、
Cookie或验证码。服务器可用部署目录提供的root私有加密凭据helper保存管易账号
密码；密文与主机密钥都只在`/etc/auto-shipped/credentials`，helper只有`get`
能力，秘密不会进入环境变量、命令参数、Git或Skill。切换服务器时重新部署并
单独配置新服务器凭据，不复制旧profile。

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

`login-start`启动或复用唯一常驻浏览器。已配置服务器凭据helper时自动读取；
没有配置时才进入`waiting_for_credentials`，从标准输入接收本次临时凭据：

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

管理员首次配置或更换服务器账号密码时，从维护终端把一行JSON通过SSH stdin交给
服务器的`set-guanyi-credentials set`；不要把真实值写进命令、脚本或聊天模板：

```bash
printf '%s\n' '{"username":"<账号>","password":"<密码>"}' | \
  ssh <server> 'sudo -n /usr/local/sbin/set-guanyi-credentials set'
```

常驻状态为`resident_ready`后，`probe`、`execute`和`reconcile`都进入同一个浏览器
队列；不得另启普通`probe`或第二个登录进程。

`resident-start`在创建常驻worker前执行与正式提交相同的`upload`规则审计，并把
当次审计通过标记传入worker。这样后续队列中的提交仍由同一Node写入门禁复核，
不会因为worker早于批次命令启动而误判为未验收。规则门禁在文件选择前拒绝时，
远端任务保持`prepared`且`execute_started=false`；确认运行时已修复后可以继续同一
operation，不能另建批次或把该拒绝当成可能已上传。

专用服务器默认设置`GUANYI_HUMAN_TAKEOVER_AVAILABLE=0`。如果服务器登录触发阿里云
额外验证，执行器不会等待一个用户看不到的桌面，而是以
`LOGIN_LOCAL_VERIFICATION_REQUIRED`安全停止，提示用户或管理员先在可信本机完成
一次管易登录验证，再重新建立服务器常驻会话。

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
- 账号密码只从root私有加密存储经只读helper进入当前常驻进程；未配置helper时才
  经SSH stdin临时输入。验证码只经SSH stdin进入当前进程。三者都不写入job state、
  日志、环境变量或命令参数。
- 常驻协调器是唯一浏览器owner，任务FIFO串行；服务器重启或worker失效后不复用
  陈旧控制通道，重新登录前先确认没有可能已写入但未核验的operation。
- `execute` 在调用浏览器前先落盘 `submit_maybe_sent`；结果不明确时结构性阻止重提。
- 日志和响应不返回 Excel 内容、收件人、手机号或详细地址。

## 服务器迁移

迁移时在新 Ubuntu 主机运行 `deploy/guanyi-server/deploy.sh`，完成 doctor 后更新
外部连接配置，并通过`set-guanyi-credentials`在新主机重新配置凭据。浏览器
profile不从旧主机复制；应在新服务器重新建立可信登录会话。旧主机在确认没有
在途operation后再下线。
