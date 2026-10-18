# 可迁移运行时

本 Skill 内置 `runtime/auto-shipped`，包含订单转换源码、平台与来源配置、管易模板、当前商品资料快照和管易浏览器执行器。`runtime-manifest.json` 记录全部内置文件的 SHA-256；在新电脑运行前使用 `scripts/doctor.py` 校验。

运行时发现顺序为：显式指定项目目录、当前目录中的开发项目、与当前Skill清单完全一致的状态运行时、Skill内置最新版。旧状态目录校验不一致时不会被静默使用，`doctor.py`会提示重新运行`bootstrap_runtime.py`；普通复制目录即使通过`SHANGYU_AUTO_SHIPPED_ROOT`显式指定，也不能绕过浏览器入口的清单校验。这样既能使用状态目录内安装好的依赖，也不会让旧规则或临时修改覆盖新版Skill。

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

运行时自动识别操作系统：macOS 使用 Keychain；Windows 使用当前 Windows 用户的 DPAPI 加密凭证文件。两者都必须在新电脑重新配置，不能回退到明文配置。Linux/云端优先由平台 Secret/Vault 配置绝对路径 `GUANYI_CREDENTIAL_HELPER`；该助手接受 `get` 参数并通过私有标准输出返回账号密码 JSON，Skill 不负责写入 Vault。没有安全凭证入口时仍先复用已有浏览器会话；会话失效且用户明确接受对话历史会保留密码时，可使用 `login-agent-start`。平台有私有stdin时优先使用`login-credentials-submit`把凭据交给原worker；否则才使用operation返回的一次性`127.0.0.1`页面。项目不得把聊天凭据复制到临时脚本、文件、环境变量、命令参数或日志。

云端有两类浏览器接入：平台提供可信本机CDP端点时，项目Playwright直接控制平台内置浏览器的同一context；没有该能力时，才由项目启动独立headed Chromium。两类接入共用短时登录operation、内存控制通道和同一业务状态机，不得相互回退或建立第二套登录实现。凭据与验证码只进入当前登录进程，不写文件或日志。

豆包工作固定使用前一类接入，具体流程见[豆包平台浏览器接入](doubao-browser-takeover.md)：用户看到的Browser页面就是Playwright正在回读的页面，但阿里云额外验证不在云端处理。执行器停止并释放锁后，用户或管理员必须先在自己的可信电脑完成同账号登录与验证；确认进入管易首页后，豆包只读探测并最多重试一次。其他平台只有明确提供同一浏览器的交互入口时才允许人工接管；否则停止并报告。

默认状态目录不可写时，运行时会回退到当前系统用户独占的临时目录并在`doctor.py`中标记`persistent=false`；这能保证单次云端任务继续初始化，但不承诺跨云工作区保留profile。平台有明确的可写持久目录时，应通过`SHANGYU_SKILL_STATE_ROOT`指定该目录。`bootstrap_runtime.py`始终复制完整且经清单验证的运行时，不允许只复制`browser/guanyi`；只读Skill来源的权限不会原样阻断安装，复制完成后只对私有状态副本恢复当前用户写权限。

同一持久profile由进程锁保证单一Playwright所有者。登录worker运行期间只能轮询同一operation的`login-status`，不得再启动`probe`、导航、上传或第二个登录。人机交接的每一等待阶段默认获得10分钟，验证码提交后的页面判定窗口默认45秒；`code_submitted`是等待态，不是重试信号。验证码错误、风控、凭据拒绝、未知弹窗/额外步骤和结果不明确都会失败关闭，禁止自动重发短信。

登记的同浏览器人工验证阶段默认等待15分钟。独立headed Linux浏览器可使用已有的`xdotool`精确置前，但豆包平台CDP路径不使用它。平台重置导致worker或浏览器连接消失时返回中断，恢复后的第一步是只读检查当前会话。Skill更新和运行时bootstrap只替换版本化运行时，不删除独立的`guanyi-browser-profile`；Agent不得把“更新运行时”扩展成清理profile。

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
- 荣之达平台单号的本机分钟占号记录。它默认位于Skill状态目录，只解决同一电脑上的同分钟续号与重跑复用；更换电脑时不要宣称继承了旧电脑的占号状态。
- 当前电脑通过用户确认新增的商品映射覆盖层。它默认位于状态目录，不会随 Skill 文件夹自动同步。
- 飞书用户授权令牌；新电脑必须以实际员工身份重新授权，不能复制其他人的授权状态。

这些状态必须在新电脑本机重新建立。不要复制浏览器 profile、Keychain 数据库或 DPAPI 文件来绕过登录验证；DPAPI 文件离开原 Windows 用户后也无法解密。

荣之达当前按单电脑、低并发场景设计。若业务改为多电脑同时生成订单，应先把本机占号接口替换为中央原子分配器；不能靠复制本机占号文件或单独读取某台电脑状态解决并发。

来源匹配政策和身份保护随新版 Skill 重新发布；一对一商品目标映射由每台电脑在转换前从飞书正式映射表同步。Agent具备原生飞书电子表格能力时优先生成并校验标准载荷；否则统一转换入口自动调用`lark-cli`备用读取器。本机旧映射、环境变量共享映射和过期缓存都不作为读取失败时的静默兜底。两条读取路径均失败时，转换以`FEISHU_OFFICIAL_MAPPING_UNAVAILABLE`停止并提示完成飞书授权或同步，不会把已发布映射误判成新映射重新询问。用户明确“以后沿用”的纯新增可通过受控命令直接写入正式表并在写后同步；同键冲突、替换或删除才进入个人待确认Sheet和管理员流程。

## 开发更新

开发项目修改后，由维护者在项目根目录运行：

```bash
python3 <skill-dir>/scripts/package_runtime.py --source <project-root>
```

然后运行 `doctor.py`、Skill 校验、Python 测试、Node 测试和管易只读实机验收。普通员工运行任务时不要调用打包命令。
