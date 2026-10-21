# 管易专用服务器部署

这个目录只负责把现有尚舆订单 Skill 的管易浏览器执行器部署到一台
Ubuntu 服务器。它不复制登录、导航、文件选择、提交或任务中心逻辑；服务器
仍运行 `browser/guanyi` 中唯一的一套状态机。

## 部署结果

- 服务器专用系统用户：`guanyi-runner`
- 只读发布目录：`/opt/auto-shipped/releases/<git-sha>`
- 当前版本链接：`/opt/auto-shipped/current`
- 私有持久状态：`/var/lib/auto-shipped`
- 非敏感环境配置：`/etc/auto-shipped/guanyi-server.env`
- 无公网浏览器、CDP、VNC 或业务 API 端口
- Chromium 由本机 Xvfb `:99` 承载，浏览器 profile 跨任务保留

账号、密码、验证码、Cookie、原始订单和导入文件都不进入 Git。浏览器
profile 只保存在服务器的私有状态目录。服务器更换时不要复制 profile 来绕过
平台登录验证；在新服务器重新建立可信会话。

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
| `provision.sh` | 幂等安装系统依赖、用户、目录、环境文件和 Xvfb 服务 |
| `install-release.sh` | 安装一个已提交版本并初始化 Skill 私有运行时 |
| `doctor.sh` | 只读检查服务器运行环境与 Skill 完整性 |
| `deploy.sh` | 在维护电脑上编排前述步骤 |
| `guanyi-server.env.example` | 不含秘密的服务端环境模板 |
| `auto-shipped-xvfb.service` | 无公网监听的虚拟显示服务 |
