# 可迁移运行时

本 Skill 内置 `runtime/auto-shipped`，包含订单转换源码、平台与来源配置、管易模板、当前商品资料快照和管易浏览器执行器。`runtime-manifest.json` 记录全部内置文件的 SHA-256；在新电脑运行前使用 `scripts/doctor.py` 校验。

运行时发现顺序为：显式指定项目目录、当前目录中的开发项目、与当前Skill清单完全一致的状态运行时、Skill内置最新版。旧状态目录校验不一致时不会被静默使用，`doctor.py`会提示重新运行`bootstrap_runtime.py`；这样既能使用状态目录内安装好的依赖，也不会让旧规则覆盖新版Skill。

## 新电脑初始化

1. 把完整的 `shangyu-order-converter` Skill 文件夹安装到新电脑的 Codex Skills 目录。
2. 执行：

   ```bash
   python3 <skill-dir>/scripts/doctor.py
   ```

3. 如果仅缺 Python/Node 依赖，在用户同意安装依赖后执行：

   ```bash
   python3 <skill-dir>/scripts/bootstrap_runtime.py
   ```

4. 在新电脑本机重新配置管易凭证：

   ```bash
   python3 <skill-dir>/scripts/guanyi_browser.py credential-web
   ```

5. 共享商品映射优先调用新电脑Agent已有的飞书电子表格能力；若没有原生能力，安装`lark-cli`并用员工自己的飞书用户身份登录，再运行：

   ```bash
   python3 <skill-dir>/scripts/feishu_mapping.py status
   python3 <skill-dir>/scripts/feishu_mapping.py provision --operation-id <稳定操作ID>
   ```

6. 再运行 `doctor.py`、浏览器 `probe` 和只读 `navigate-import` 验收。

运行时自动识别操作系统：macOS 使用 Keychain；Windows 使用当前 Windows 用户的 DPAPI 加密凭证文件。两者都必须在新电脑重新配置，不能回退到明文配置。Linux 当前只支持转换，不启用自动登录。

macOS 已完成真实登录和导航验收。Windows 已实现路径选择、虚拟环境、Chrome 发现和 DPAPI 提供器，并通过平台路由单元测试；首次拿到真实 Windows 电脑时仍必须完成 `doctor → credential-web → probe → navigate-import` 实机验收后，才能启用自动登录。

## 随 Skill 迁移

- Python 转换源码与数据合同。
- 公司识别、平台路由和管易模板配置。
- 管易官方自定义导入模板。
- 打包时登记的商品资料快照。
- Playwright 浏览器执行器与稳定层。
- 飞书正式/待确认映射工作簿绑定、用户路由合同和`lark-cli`备用适配器。
- 环境检查、依赖初始化和运行入口。

## 不随 Skill 迁移

- 账号、密码、短信验证码。
- macOS Keychain 项目或 Windows DPAPI 凭证文件。
- Chrome 登录会话和 Cookie。
- 员工提交的原始订单和收件信息。
- 转换结果、上传回执、失败截图和运行日志。
- 当前电脑通过用户确认新增的商品映射覆盖层。它默认位于状态目录，不会随 Skill 文件夹自动同步。
- 飞书用户授权令牌；新电脑必须以实际员工身份重新授权，不能复制其他人的授权状态。

这些状态必须在新电脑本机重新建立。不要复制浏览器 profile、Keychain 数据库或 DPAPI 文件来绕过登录验证；DPAPI 文件离开原 Windows 用户后也无法解密。

来源匹配政策和身份保护随新版 Skill 重新发布；一对一商品目标映射由每台电脑在转换前从飞书正式映射表同步。本机旧映射和环境变量共享映射不再由员工日常转换入口自动加载。新映射先进入个人待确认Sheet，只有所有者发布到正式映射表后才会同步到其他电脑。

## 开发更新

开发项目修改后，由维护者在项目根目录运行：

```bash
python3 <skill-dir>/scripts/package_runtime.py --source <project-root>
```

然后运行 `doctor.py`、Skill 校验、Python 测试、Node 测试和管易只读实机验收。普通员工运行任务时不要调用打包命令。
