# 订单规则模块结构

## 目标

每条可执行业务规则只有一个权威定义。公司、路由、证据文档和代码只引用它，不各自复制一份值。

完整的13个一级模块、依赖和实现归属见[系统模块治理目录](MODULE_GOVERNANCE.md)。该文档由`config/governance/system_module_catalog_v1.json`生成；每次增删模块、实现注册或共享策略后必须运行`python3 scripts/audit_modules.py --write-doc`。

## 分层与唯一职责

| 层 | 权威位置 | 只负责 |
| --- | --- | --- |
| 公司身份 | `config/companies/company_registry_v1.json` | 稳定公司ID、法定名称、简称、来源和工作流索引；简称只有被规则显式引用时才成为输出前缀 |
| 来源识别与原始单号 | `config/source_profiles/` | 文件/文字来源证据、字段结构、来源侧单号生成 |
| 路由 | `config/routing/order_routes_v1.json` | 来源配置进入哪个目标平台和规则包 |
| 跨公司共享策略 | `config/platform_rules/guanyi/policy_modules_v1.json` | 玉米店铺、普通买家会员、公共默认值、物流和备注等唯一可执行定义 |
| 公司平台规则包 | `config/platform_rules/guanyi/<source>.json` | 公司特有单号、字段显示、套餐规则，以及对共享模块的引用 |
| 商品身份 | `config/catalog/`与正式飞书映射 | 来源商品如何唯一变成管易商品和规格 |
| 规则覆盖门禁 | `config/business_rules/shangyu_sop_rule_catalog_v1.json` | 每条路由必须具备哪些规则，不保存重复业务值 |
| 规则包核心合同 | `contracts/platform_rule_pack.schema.json` | 只校验身份、版本和覆盖声明；允许公司业务字段继续扩展 |
| 实现注册表 | `config/business_rules/implementation_registry_v1.json` | 把`implementation_ref`绑定到真实代码入口和测试文件 |
| 当前证据 | `docs/RULE_SOURCE_LEDGER.md` | 当前规则的来源与确认时间，不参与运行时取值；旧版本由Git保存 |

## 当前共享模块

| module_id | 规则 |
| --- | --- |
| `store.corn_to_guangming_manyuanqi.v1` | 恬田、NDD、荣之达的已确认玉米商品进入“光明满元气”；非玉米或无法唯一判断时追问 |
| `store.sam_fixed.v1` | SAM订单固定进入`sam`店铺；仍通过统一店铺策略执行 |
| `store.manual_linked_routes.v1` | JZDZ、DRJY先按来源/SKU关系表分配店铺，缺失时追问 |
| `store.generic_company_manual_fallback.v1` | 无固定流程的已登记公司先按`company_id`查SKU店铺关系，再查`manual_all`；仅已确认玉米可兜底到“光明满元气”，其他商品追问 |
| `buyer.normal_zhang.v1` | 普通来源买家会员填写“张”；SAM不引用该模块 |
| `defaults.standard_sales.v1` | 金额、仓库、支付、订单类型和标记的唯一公共默认值；SAM、NDD只覆盖运费为空 |
| `logistics.source_override_then_corn_packaging.v1` | 来源指定物流优先；否则执行已确认玉米包装规则 |
| `note.courier_instruction_to_address.v1` | 把送货上门、不准放门口等快递员指令追加到收货地址末尾，其余来源备注留给卖家备注模块 |
| `remark.source_then_system.v1` | 接收分流后的非快递员备注，系统提示后写，中文分号拼接并去重 |

## 公司差异只放在哪里

| 来源 | 公司规则包只保留的差异 |
| --- | --- |
| 恬田 | 公司简称引用+优先订单身份、恬田字段显示；当前公司库简称为`TT`，玉米店铺等引用共享模块 |
| NDD | 公司简称引用+优先订单身份、福利套餐四展开、NDD字段显示；当前公司库简称为`NDD`，玉米店铺等引用共享模块 |
| 荣之达 | 公司简称引用+YYYYMMDD+最少三位飞书中央日序号；当前公司库简称为`RZD`，玉米店铺等引用共享模块 |
| 尚舆自营 | 使用订单编号优先、来源单号回退；店铺由自营渠道决定；京东中通的姓名四位码追加联系方式也只保留在自营规则包 |
| SAM | 使用优先订单身份、SAM买家会员和组合商品展开策略；具体组合组成从飞书中央规则表读取。无跟团号会员按商品类型完整前缀使用飞书中央日序号，店铺`sam`引用统一店铺模块，运费仅做字段级覆盖 |
| 通用兜底公司（当前FZ/KLD/MT） | 公司库只绑定共享兜底并提供简称；没有复制工作流。缺号时按简称+YYYYMMDD+中央日序号，店铺按独立共享模块决定 |

## 防止规则重新散开

1. 引用共享模块的字段不得同时在公司规则包内联；解析器检测到双重权威立即阻断。
2. 公司库不得复制店铺、物流、商品或字段值。
3. 文档只记录证据和模块ID，不作为运行时取值来源。
4. 修改共享规则时只改模块并运行全部来源回归；公司例外才留在公司规则包。
5. 新规则先判断是“跨公司共用”还是“公司特有”，再选择共享模块或公司规则包，禁止直接写进Excel渲染器。
6. 规则包可增加新业务字段，但核心身份、版本和覆盖声明必须通过Schema；标记为`implemented`的引用还必须存在于实现注册表。
7. 一条来源商品拆成多个目标商品不写入飞书一对一映射；长期组成事实统一写入正式工作簿的“组合商品规则”Sheet，来源规则包只声明执行策略。首次只能提出候选并等待用户确认目标SKU、数量倍数和复用范围；仅本次记录必须绑定来源文件SHA-256，长期新增必须走受控写入、版本锁、日志和回读。

## 自营京东中通联系方式例外

执行器`apply_contact_suffix_policy`只提供通用的“按配置提取、核验并追加尾码”能力；触发范围、四位码表达式和冲突处理由`self_operated_text_v1.json`唯一配置。当前只有“公司=尚舆自营、渠道=京东、来源物流=京东中通”触发。该例外不写入共享策略库，也不硬编码在Excel列渲染逻辑中。

## 备注字段分流

备注先经过共享`note_routing_policy`，再进入具体目标字段：

1. 明确给快递员的配送指令追加到“收货地址”末尾，使用中文括号包裹；地址已经包含相同文字时不重复追加。
2. 非快递员备注继续交给共享`seller_remark_policy`，与白标等系统提示按固定顺序合并。
3. 同一来源备注包含两类信息时按分隔符拆分，例如`抓紧发货；不准放门口`分别进入卖家备注和收货地址。
4. 文字入口可以在`source_extensions.delivery_instruction`提供显式结构化指令；Excel来源仍由同一共享关键词策略处理。
5. 关键词、包裹符号和分隔符只在共享模块定义一次；公司规则包不得复制。

## 中央日序号边界

当前RZD、淘橘子、JZDZ、DRJY、通用兜底平台单号与SAM无跟团号会员名称统一使用飞书中央日序号：按“用途+完整前缀+业务日期”隔离，最小宽度3，超过999自然扩位，第二天按新日期重新从001开始；同一来源重跑复用原号段。旧分钟分配器只保留历史批次复跑兼容，不得用于新订单。

## 包装语义模块

`config/catalog/package_semantics_v1.json`是文字订单包装含义的唯一正式权威。它保存商品品类词、各来源已经确认的`根/袋/盒/份/套`语义和普通发货禁用SKU。商品映射配置不再重复保存包装语义。

用户Agent本机待登记提案位于Skill状态目录，不参与转换。所有者回收导出包、审核并更新正式库后，才随新Skill版本分发。详细治理见`docs/PACKAGE_SEMANTICS_GOVERNANCE.md`。
