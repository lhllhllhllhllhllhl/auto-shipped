---
name: shangyu-order-converter
description: 识别尚舆收到的公司原始订单文件，按显式来源路由、商品映射和平台规则生成经过校验的导入 Excel，并用独立执行器处理管易登录、预检和只读导入页导航。适用于恬田等发货订单处理和管易自动化准备；不确定项必须追问，未实现上传前不提交、审核或发货。
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
- 默认只生成并校验管易导入 Excel，不代表已经上传。
- 当前不得点击管易“确定”、审核或发货，也不得绕过详细执行说明中的授权门禁。
- 不修改用户原始订单，不在聊天或日志中展示完整收件信息。
- 管易凭证、短信验证码、Cookie 和飞书登录状态不随仓库迁移。
- 一次收到多个订单文件时默认合并输出；只有用户明确要求分开时才分别生成。

新电脑迁移、操作系统差异和本机凭证建立方式见[可迁移运行时说明](skill/shangyu-order-converter/references/portability.md)。
