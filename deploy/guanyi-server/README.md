# 管易专用服务器部署

这个目录只负责把现有尚舆订单 Skill 的管易浏览器执行器部署到一台
Ubuntu 服务器。它不复制登录、导航、文件选择、提交或任务中心逻辑；服务器
仍运行 `browser/guanyi` 中唯一的一套状态机。

## 部署结果

- 服务器专用系统用户：`guanyi-runner`
- 只读发布目录：`/opt/auto-shipped/releases/<git-sha>`
- 当前版本链接：`/opt/auto-shipped/current`
- 私有持久状态：`/var/lib/auto-shipped`
- root私有加密凭据：`/etc/auto-shipped/credentials`
- 非敏感环境配置：`/etc/auto-shipped/guanyi-server.env`
- 无公网浏览器、CDP、VNC 或业务 API 端口
- Chromium 由本机 Xvfb `:99` 承载；单一常驻进程与profile跨任务保留，任务FIFO串行
- 重复部署不会重启健康的 Xvfb，因此不会仅因发布新代码而关闭常驻浏览器
- 服务器不可人工接管浏览器；额外验证会明确要求先在可信本机完成，不在隐藏桌面等待
- 私有远端暂存入口通过 SSH 固定命令调用，不监听新的网络端口

账号、密码、验证码、Cookie、原始订单和导入文件都不进入 Git。账号密码可由
root私有加密文件和窄权限helper保存；验证码仍只在当前常驻进程内存中。浏览器
profile只保存在服务器的私有状态目录。服务器更换时不要复制profile来绕过
平台登录验证；在新服务器重新配置凭据并建立可信会话。

## 首次部署

客户端需要：Git、SSH、SCP，以及已在服务器绑定的 SSH 私钥。服务器需要
Ubuntu 22.04 或 24.04、x86_64、可访问 Ubuntu/NodeSource/Google 软件源，并且
SSH 用户可以免交互执行 `sudo`。

```bash
deploy/guanyi-server/deploy.sh \
  --host <server-ip-or-hostname> \
  --user ubuntu \
  --identity ~/.ssh/auto_shipped_guanyi_server
```

该命令会：

1. 运行幂等的 `provision.sh` 安装系统依赖并建立隔离目录；
2. 用 `git archive` 发布指定提交，不上传工作区未提交文件；
3. 运行 Skill 的 `bootstrap_runtime.py` 安装私有 Python/Node 运行时；
4. 运行 `doctor.sh` 验收版本、目录、Xvfb 和 Skill 完整性。

首次部署后，由管理员从stdin配置服务器凭据（命令行中不要出现真实值）：

```bash
printf '%s\n' '{"username":"<账号>","password":"<密码>"}' | \
  ssh <server> 'sudo -n /usr/local/sbin/set-guanyi-credentials set'
```

可用`sudo -n /usr/local/sbin/set-guanyi-credentials status`只读检查；更换账号密码
重复执行`set`即可原子替换。业务用户不需要知道服务器或密码。

部署完成后，调用端可按
[`docs/GUANYI_REMOTE_EXECUTOR.md`](../../docs/GUANYI_REMOTE_EXECUTOR.md)
配置 SSH 传输适配器。远端命令支持 `health`、接收文件、manifest 校验、操作
准备、临时登录交接、受控单批提交和只读结果核验。页面动作仍全部复用
`browser/guanyi`；远端层不提供审核、发货、作废或删除命令。

默认环境模板为中国大陆服务器使用 npm 镜像以降低下载超时；`npm ci` 仍按照
`package-lock.json` 校验精确版本与完整性。需要切回官方源时，只修改服务器上的
`NPM_CONFIG_REGISTRY`，不需要修改业务代码。

部署默认使用当前 `HEAD`。指定已提交版本可加 `--ref <commit-or-tag>`。如果只想
检查 SSH 和 sudo，不安装任何内容，可运行：

```bash
deploy/guanyi-server/preflight.sh \
  --host <server-ip-or-hostname> \
  --user ubuntu \
  --identity <private-key-path>
```

## 换服务器

1. 新建或准备 Ubuntu x86_64 服务器，只开放 SSH；不要开放 9222、5900、6080。
2. 给部署账号绑定一把项目专用 SSH 公钥。
3. 从可信维护电脑运行上面的 `deploy.sh`。
4. `doctor` 通过后，再单独执行登录和只读导航验收。
5. 最后才把内部执行端配置切到新主机；旧主机先保留，确认无在途 operation 后下线。

服务器地址、SSH 用户和私钥路径属于执行端配置，不写死在业务代码中。生产环境
可以把这些值保存在 Agent 平台的 Secret/Vault；没有秘密存储时，私钥只保存在
维护电脑或受控执行机，并通过绝对路径传给部署/调用适配器。

## 日常升级

代码合并并推送后，重新运行 `deploy.sh --ref <已审核提交>`。发布采用新目录和
原子 `current` 链接，保留已有 `SHANGYU_SKILL_STATE_ROOT`、浏览器 profile、
operation 和依赖目录。部署脚本不会删除 profile，也不会执行管易登录或订单上传。

## 文件职责

| 文件 | 职责 |
|---|---|
| `preflight.sh` | 只读检查 SSH、Ubuntu、架构、sudo 和基础资源 |
| `provision.sh` | 幂等安装系统依赖、用户、目录、凭据helper、环境文件和 Xvfb 服务 |
| `install-release.sh` | 安装一个已提交版本并初始化 Skill 私有运行时 |
| `doctor.sh` | 只读检查服务器运行环境与 Skill 完整性 |
| `deploy.sh` | 在维护电脑上编排前述步骤 |
| `remote-job.sh` | 固定白名单的 SSH 远端入口；只把受控动作交给正式浏览器状态机 |
| `guanyi-server.env.example` | 不含秘密的服务端环境模板 |
| `auto-shipped-xvfb.service` | 无公网监听的虚拟显示服务 |
| `set-guanyi-credentials.sh` | 从stdin原子写入root私有加密凭据；支持状态和删除 |
| `guanyi-credential-helper*.sh` | 仅允许执行器读取解密后的单行凭据JSON |
