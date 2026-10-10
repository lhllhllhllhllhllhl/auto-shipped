---
name: shangyu-order-converter
description: 识别尚舆收到的公司原始订单文件；已知模板用确定性脚本处理，用户明确公司时可自适应解析未知Excel，再按商品映射和平台规则生成经过校验的导入文件。也用于管易登录、预检和只读导入页导航；缺失业务事实必须追问，未授权前不提交、审核或发货。
---

# 尚舆订单转换

本 GitHub 仓库根目录就是可安装的 Skill 根目录。不要把整个仓库再当作普通项目包装成另一个 Skill，也不需要寻找单独的发布 ZIP。

执行任何订单识别、Excel 转换、飞书映射或管易浏览器操作前，必须完整阅读并遵循[详细执行说明](skill/shangyu-order-converter/SKILL.md)。该文件是业务流程和权限边界的唯一详细入口；其中的 `<skill-dir>` 指本仓库内的 `skill/shangyu-order-converter` 目录。

首次在新电脑运行时，先执行：

```bash
python3 <repository-root>/skill/shangyu-order-converter/scripts/doctor.py
```

只有用户同意安装缺失依赖后，才可以执行：

```bash
python3 <repository-root>/skill/shangyu-order-converter/scripts/bootstrap_runtime.py
```

核心边界：

- 来源、公司或商品映射不确定时必须追问，禁止猜测。
- 公司可用全称、已登记简称或别名识别；简称只有被公司规则明确引用时才参与平台单号等输出。仅登记公司身份而未配置工作流时必须停止，不能把“识别成功”当作“允许转换”。
- 来源处理使用双通道：已登记模板走确定性脚本快线；用户已明确公司但模板未知时，先生成不含数据值的自适应字段计划，补齐必要信息后再进入同一`ParsedOrder`和平台规则链路。不得仅因模板陌生就要求用户更换文件。
- 默认只生成并校验管易导入 Excel，不代表已经上传。
- 当前不得点击管易“确定”、审核或发货，也不得绕过详细执行说明中的授权门禁。
- 不修改用户原始订单，不在聊天或日志中展示完整收件信息。
- 管易凭证、短信验证码、Cookie 和飞书登录状态不随仓库迁移。
- 一次收到多个订单文件时默认合并输出；只有用户明确要求分开时才分别生成。
- 标准商品转换前必须成功读取并验证飞书正式映射。Agent有原生飞书表格能力时优先使用；否则统一入口自动调用`lark-cli`备用读取器。两者均不可用时返回`FEISHU_OFFICIAL_MAPPING_UNAVAILABLE`并停止，禁止改问用户已发布的商品映射。
- 白标只按当前版本化白标库的商品代码+规格代码精确匹配；新权威表会整体替换旧版本，不叠加历史临时名单。

新电脑迁移、操作系统差异和本机凭证建立方式见[可迁移运行时说明](skill/shangyu-order-converter/references/portability.md)。
