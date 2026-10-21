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

当前版本只实现图中“服务器暂存与哈希复核”之前的安全链路，不提供远端登录或
上传命令。这样可以先验证传输、身份绑定和可替换服务器配置，不把尚未验收的
远端调用误当成生产上传能力。

## 模块

| 模块 | 职责 | 不负责 |
|---|---|---|
| `remote/protocol.py` | 版本化任务合同、文件描述、操作 ID 与安全边界 | 网络、浏览器、业务规则 |
| `remote/ssh_transport.py` | 用 OpenSSH stdin 私密传输元数据和文件 | 解释订单、执行页面动作 |
| `remote/client.py` | 按 `init → manifest → artifact → verify` 编排 | 自动重试、猜测最新文件 |
| `remote/server_runner.py` | 私有落盘、SHA-256 复核、调用现有 manifest 校验器 | 登录、导航、选择文件、提交 |
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
```

不得扫描目录或按时间猜测“最新文件”。相同操作 ID 与不同哈希冲突时必须停止；
同一请求重跑是幂等的。SSH 断开不会触发浏览器动作，也不得被解释成上传成功。

## 安全边界

- 远端请求不接受本机路径，只接受文件名、大小和 SHA-256。
- 服务器为每个操作建立 `0700` 私有目录，文件为 `0600`。
- 传输走 SSH stdin，不建立临时公网下载链接，不开放业务、CDP 或 VNC 端口。
- 客户端启用 host key 校验，禁止 `StrictHostKeyChecking=no`。
- 服务端命令是固定白名单，不接受任意 shell 命令。
- 当前远端运行器没有登录、导航、文件选择、提交、审核、发货、作废或删除命令。
- manifest 本身继续保持 `upload_authorized=false`；暂存授权不能升级为提交授权。
- 日志和响应不返回 Excel 内容、收件人、手机号或详细地址。

## 服务器迁移

迁移时在新 Ubuntu 主机运行 `deploy/guanyi-server/deploy.sh`，完成 doctor 后更新
外部连接配置。浏览器 profile 不从旧主机复制；应在新服务器重新建立可信登录
会话。旧主机在确认没有在途 operation 后再下线。
