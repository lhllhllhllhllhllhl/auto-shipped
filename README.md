# 尚舆发货自动化

这是发货自动化系统的模块化核心。当前已覆盖离线解析、规则覆盖门禁、商品映射、模板生成、上传前预检，以及受控的管易登录与上传准备；“确定”提交、审核和发货仍保持关闭。

总体目标架构、模块边界和阶段验收见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。该设计把 Agent/Skill 作为轻量入口，将来源识别、结构化追问、显式路由、最小中间订单和平台专属转换拆开。第一版的目标输出统一为管易自定义订单单模板；百礼汇作为专用后台订单来源接入，不是订单导入目标平台。

业务规则讨论稿见 [docs/ORDER_AUTOMATION_RULES_V0.1.md](docs/ORDER_AUTOMATION_RULES_V0.1.md)，完整判断顺序和优先级见 [docs/RULE_DECISION_FLOW_V0.1.md](docs/RULE_DECISION_FLOW_V0.1.md)，原始文件指纹、PDF页码、样本范围、用户确认和规则证据对照见 [docs/RULE_SOURCE_LEDGER.md](docs/RULE_SOURCE_LEDGER.md)。规则不得只存在于聊天上下文中。

真实转换测试使用 [docs/CONVERSION_TEST_RECORD_TEMPLATE.md](docs/CONVERSION_TEST_RECORD_TEMPLATE.md) 记录文件哈希、规则版本、数量对账、手工导入结果和业务验收结论；只有明确通过才能进入自动上传阶段。

## 作为Agent Skill安装

需要通过“上传 `.zip` 或 `.skill` 文件”安装时，请直接下载并上传 [`dist/shangyu-order-converter.zip`](dist/shangyu-order-converter.zip)。该压缩包的第一层就是 `SKILL.md`；不要上传 GitHub 自动生成的整个仓库 ZIP，因为项目根目录不是 Skill 根目录。

仓库中的 `skill/shangyu-order-converter` 是可直接迁移的 Skill 源目录，内置确定性运行时、规则、模板、当前商品资料快照和 SHA-256 清单。支持从 GitHub 子目录安装的 Agent，可以直接安装该目录。新电脑安装后执行：

```bash
python3 <skill-dir>/scripts/doctor.py
```

如果检查报告仅提示依赖缺失，在用户同意安装后执行：

```bash
python3 <skill-dir>/scripts/bootstrap_runtime.py
```

新电脑不会继承管易账号密码、短信验证码、浏览器Cookie或飞书登录令牌。macOS/Windows都必须以实际员工身份重新建立凭证和登录会话；Linux当前只支持Excel转换。详细步骤见 `skill/shangyu-order-converter/references/portability.md`。

这个仓库包含公司内部业务规则和飞书工作簿绑定，应保持私有。真实订单、收件信息、转换结果、浏览器profile和运行日志不进入Git。

修改 Skill 后使用以下命令重新生成上传包：

```bash
python3 scripts/build_skill_archive.py
```

命令同时生成 `dist/shangyu-order-converter.zip.sha256`，用于核对下载文件是否完整。

## 当前主链路

```text
来源文件或后台订单连接器
  -> 自动识别来源
  -> 公司库确认公司归属与已启用工作流
  -> 显式路由到管易
  -> 来源适配器
  -> 最小中间订单 ParsedOrder
  -> SOP业务规则覆盖门禁
  -> 已确认组合商品展开
  -> 管易商品映射与业务规则
  -> 管易自定义订单导入.xlsx
```

信息不足或规则未确认时，链路返回结构化追问，不生成可上传文件。入口 Skill 只调用服务层，不承载业务规则。管易 API、管易浏览器和其他平台以后作为独立执行器接入。

## 公司库

公司总索引位于 [config/companies/company_registry_v1.json](config/companies/company_registry_v1.json)。它登记稳定公司ID、别名、来源格式、处理工作流、模块引用和当前能力状态，但不复制字段转换、商品映射、物流或平台上传规则。一家公司以后可以拥有多个来源格式和多个平台工作流。

转换入口会强制校验“来源配置归属公司”和“公司工作流引用的路由、平台规则、商品映射范围”一致。检查命令：

```bash
PYTHONPATH=src python -m auto_shipped.cli audit-companies
```

当前正式记录包括恬田、SAM和NDD。恬田Excel转换工作流已启用；SAM已接入识别、解析、白糯8+1组合展开及`sam+商品类型+当日日期+_跟团号`买家会员规则；NDD已接入福利套餐展开、平台单号和重货物流规则。三者均可生成Excel供员工验收，自动上传仍未启用。新公司必须先进入公司库并补齐独立模块，不能只在代码中临时增加判断。

## 飞书商品映射治理

飞书映射治理的核心合同和用户Sheet自动路由状态机已经实现，设计见 [docs/FEISHU_MAPPING_GOVERNANCE.md](docs/FEISHU_MAPPING_GOVERNANCE.md)。系统把正式映射和待确认提案放在两个工作簿：正式库只读消费，待确认库按当前飞书用户ID自动发现、创建或修复个人Sheet。Agent原生飞书能力优先，`lark-cli`只作为备用适配器。

两个实际工作簿现已初始化并绑定固定Sheet ID，已确认的恬田映射已写入正式库并回读校验。`lark-cli`待确认网关已经接通并完成用户Sheet创建、路由回读和幂等复用实测；正式映射也能只读同步为校验过的本机快照，转换入口会自动叠加该快照。当前状态是`shared_mapping_runtime_ready_publisher_pending`：员工读取、个人提案和转换消费已接通，所有者审核发布器尚未实现，待确认提案绝不会自动进入正式映射。

## Agent Skill 入口

发布包位于：

```text
skill/shangyu-order-converter
```

员工日常只需向 Skill 提供公司原始订单。Skill 默认读取登记的管易商品资料并调用统一转换服务；商品资料缺失或需要更新时才请求补充 CSV。一次提供多个Excel时默认合并成一个管易导入文件，只有用户明确要求分开时才分别生成。它根据结构化状态返回生成文件或向用户追问，不在 Skill 内复制公司字段规则，也不自动上传管易。

## 管易浏览器执行器

Playwright 执行器位于 `browser/guanyi`。它自动识别 Windows/macOS 的 Chrome、运行目录和安全凭证提供器，使用独立 Chrome 配置保存用户建立的登录会话，通过 Geometry Guard 固定 headed 原生窗口，并能跨框架检查页面状态、导航到“自定义导入”、设置“待审核”、读取任务中心和定位文件输入框。文件选择必须经过当次授权、manifest和任务基线校验；当前仍禁止点击“确定”、审核或发货，详见 [docs/GUANYI_BROWSER_EXECUTOR.md](docs/GUANYI_BROWSER_EXECUTOR.md)。

## 上传前预检

转换成功后会生成不含收件信息的上传预检 manifest。它记录模板配置版本、Excel 路径、SHA-256、文件大小、订单数、明细行数和逐项校验结果，但 `upload_authorized` 固定为 `false`。浏览器执行器可以只读验证 manifest 与 Excel 是否仍然一致；文件被修改后必须重新预检。

```bash
PYTHONPATH=src python -m auto_shipped.cli preflight \
  --file "/path/to/管易自定义订单导入.xlsx" \
  --output "/path/to/上传预检.json"

cd browser/guanyi
npm run verify-manifest -- "/path/to/上传预检.json"
npm run prepare-operation -- "/path/to/上传预检.json"
```

`prepare-operation` 在 manifest 与 Excel 仍一致时建立本地 `prepared` 操作 ID。它不打开文件选择器、不上传，也不携带上传授权；未来的文件选择、提交、任务中心回读和防重复均必须沿用该操作 ID。

## 当前支持

- `tiantian_warehouse_v2`：先识别“仓库订单”格式，再要求渠道名称等公司级证据；没有证据时追问。按来源单号合并商品行并生成平台无关订单。
- `sam_order_v1`：识别SAM群接龙表；白糯8+1按已确认组成展开为8根装和单根装两行；买家会员按商品类型、上海时区当日日期和跟团号生成，未知类型或缺跟团号时阻断追问。
- `ndd_order_v1`：用户明确NDD来源后，识别礼品订单；福利套餐四展开为黄糯和白糯8根装，基础平台单号使用`NDD`前缀并继承系统尾缀`A`。
- `corn_inbound_order_v1`：识别玉米入仓样本并先向用户确认文件用途。
- `bailihui_backend_v1`：已确认“百礼汇后台取单 -> 管易”的路由，连接器等待后台取单方式和脱敏样本。
- 管易商品主数据索引：映射按来源公司隔离；日常转换只自动加载飞书正式映射快照，本地配置保存来源匹配政策和身份保护。新映射经明确授权后提交待确认Sheet，正式发布后才生效。
- 白标SKU库：`config/catalog/white_label_skus_v1.json` 独立维护需要白标备注的精确商品与规格；当前已建库，尚未接入转换服务。
- 管易自定义订单导入：复制已确认的48列模板，删除示例行，一件商品输出一行。
- 上传前预检：阻断表头变化、缺失必填值、公式、错误单元格、非法数值、标识列格式变化和同单订单字段不一致，并以哈希绑定 Excel。
- 阻断式不确定项：未确认的平台单号策略或 SKU 映射会进入问题列表，不允许被误判为可提交。
- SOP业务规则目录：任何适用规则缺失、待确认、待实现或实现引用不合法时阻断转换，详见 [docs/BUSINESS_RULE_COVERAGE.md](docs/BUSINESS_RULE_COVERAGE.md)。
- 默认脱敏预览：姓名、手机号和详细地址不会直接输出到终端。

旧的 `preview` 命令和双模板记录转换仍保留为兼容原型，不属于第一版正式输出路径。

## 本地预览

```bash
PYTHONPATH=src python -m auto_shipped.cli preview \
  --source "/path/to/仓库订单.xlsx" \
  --catalog "/path/to/商品信息.csv" \
  --profile config/source_profiles/tiantian_warehouse_v1.json
```

## 自动识别与转换

```bash
PYTHONPATH=src python -m auto_shipped.cli convert \
  --source "/path/to/原始订单.xlsx" \
  --catalog "/path/to/商品信息.csv" \
  --output-dir "/path/to/output"
```

Skill日常统一入口：

```bash
python3 <skill-dir>/scripts/convert_orders.py \
  --source "/path/to/订单1.xlsx" \
  --source "/path/to/订单2.xlsx" \
  --output-dir "/path/to/output"
```

多个来源默认合并为一个管易Excel。只有用户明确要求分开时才增加`--separate`。

## 业务规则审计

```bash
PYTHONPATH=src python -m auto_shipped.cli audit-rules --phase conversion
PYTHONPATH=src python -m auto_shipped.cli audit-rules --phase upload
PYTHONPATH=src python -m auto_shipped.cli audit-companies
```

退出码：

- `0`：生成管易自定义订单Excel和非敏感处理报告。
- `2`：需要用户确认或补充资料，标准输出为结构化追问 JSON。
- `1`：渲染或模板校验失败。

使用项目绑定的 Python 运行测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 安全边界

- 当前版本允许安全登录、只读导航和带授权的文件选择，但不点击“确定”、不审核、不发货。
- 所有候选但未确认的业务规则都产生阻断问题。
- 真实收件信息仅在内存中解析，默认预览会脱敏。
- 相同Excel哈希已有活动操作记录时阻止重复准备；未来“确定”提交仍必须要求幂等键和明确确认。
