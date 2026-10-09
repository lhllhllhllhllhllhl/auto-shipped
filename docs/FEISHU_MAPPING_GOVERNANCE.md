# 飞书商品映射治理

状态：核心合同、用户路由、正式同步和所有者发布器均已实现。发布器先对全部待确认提案执行公司/来源、商品主档、重复键、目标冲突和正式版本预检；任一冲突整批不写。写入采用`publishing`失败关闭状态、逐项写后回读、独立发布日志、版本递增和最终正式快照校验。待确认提案仍不能直接参与转换。

## 1. 两个工作簿

当前绑定：

```text
正式映射表 QsLYsongYhoIdXtaL2GcB8Rinwe
  正式映射 13abc6
  发布日志 jl3rEi
  库信息   CPVHDe

待确认映射表 PH82sw3sBhbiJAtVPIScA96Yn3b
  __个人Sheet模板 eb2307
  __用户路由       U8k7Mz
```

绑定信息同时保存在`config/integrations/feishu_mapping_governance_v1.json`。业务代码只使用固定`sheet_id`，不依赖Sheet标题或位置。

### 正式映射工作簿

- 是运行时权威数据源。
- 普通员工和Agent只读。
- 只有所有者或受控发布器可以写入。
- 待确认内容不得直接参与订单转换。

### 待确认映射工作簿

- 所有内部协作者可以编辑，因此不是安全权限边界。
- 每个飞书用户对应一个个人Sheet，Agent按稳定用户ID自动路由。
- 用户路由表由Agent自动创建和修复，不要求管理员提前登记每名员工。
- 正式发布前必须由所有者重新查重、校验并确认。

## 2. Agent能力假设

优先使用Agent原生飞书电子表格能力，`lark-cli`只是备用适配器。待确认读写实现统一的`FeishuSheetGateway`合同：

```text
get_current_identity
list_user_routes / upsert_user_route
list_managed_pending_sheets
create_pending_sheet_from_template
write_pending_sheet_metadata
list_pending_proposals / append_pending_proposal
```

待确认写入必须使用当前员工的飞书`user`身份。共享`bot`身份无法区分员工，必须返回`FEISHU_USER_IDENTITY_REQUIRED`。

正式映射只读同步额外支持Agent原生载荷桥：Agent从固定Sheet ID读取后，必须提交以下结构给确定性校验器，不能直接把模型理解结果交给转换器：

```json
{
  "metadata": {
    "repository_status": "active",
    "mapping_revision": "2"
  },
  "records": [
    {
      "mapping_id": "...",
      "mapping_key": "...",
      "company_id": "...",
      "source_profile_id": "...",
      "identifier_type": "product_code",
      "source_value": "...",
      "source_spec": "...",
      "target_platform": "guanyi",
      "product_code": "...",
      "spec_code": "...",
      "status": "active",
      "mapping_version": "...",
      "confirmed_by": "...",
      "confirmed_at": "...",
      "evidence": "..."
    }
  ]
}
```

桥接命令：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py sync-native --payload <原生读取载荷.json>
```

校验器会核对库状态、版本、字段、`mapping_key`、重复键、管易商品/规格唯一性并生成带SHA-256的原子快照。

## 3. 待确认工作簿结构

```text
__用户路由
__个人Sheet模板
待确认_<显示名>_<用户哈希>
```

程序只使用稳定`sheet_id`，Sheet标题仅供人查看。

### 用户路由字段

```text
feishu_user_id
sheet_id
sheet_title
status
schema_version
provision_key
provision_operation_id
created_at
last_used_at
conflict_sheet_ids
```

`status`允许`creating / active / conflict / disabled`。路由表是可重建索引，不是唯一事实来源。

### 个人Sheet元数据

每个个人Sheet必须保存：

```text
owner_user_id
schema_version
provision_key
created_by_operation
status
```

Agent每次写入前同时核对路由表和Sheet元数据。路由缺失但唯一元数据匹配时可以自动修复；同一用户出现多个Sheet时停止并返回`USER_PENDING_SHEET_CONFLICT`，禁止自动删除或随意选择。

## 4. 自动注册流程

```text
获取当前飞书user_id
  -> 查询用户路由
  -> 路由有效且owner一致：复用
  -> 路由缺失：扫描Sheet元数据
       -> 唯一匹配：修复路由
       -> 多个匹配：冲突停止
       -> 无匹配：复制模板创建Sheet
  -> 写owner元数据
  -> 创建后重新扫描
  -> 唯一回读成功后登记active路由
```

创建操作使用`provision_operation_id`和由用户ID生成的`provision_key`。电子表格没有数据库唯一约束，因此并发创建不能假装原子成功；创建后发现重复必须停止，由所有者处理。

## 5. 待确认提案

提案合同位于`contracts/pending_product_mapping_proposal.schema.json`。核心字段：

```text
proposal_id
mapping_key
company_id
source_profile_id
identifier_type
source_value / source_spec
target_platform
candidate_product_code / candidate_spec_code
evidence
base_official_revision
submitted_by / submitted_at
submission_operation_id
proposal_status
```

`mapping_key`由公司、来源配置、标识类型、标准化来源值、来源规格和目标平台确定；`proposal_id`由用户、提交操作ID和`mapping_key`确定，同一操作重试保持幂等。

提交规则：

- 提交人必须等于当前飞书用户ID。
- 只追加，不覆盖既有行。
- 同一`proposal_id`和完全相同内容视为已提交。
- 同一`mapping_key`和相同目标视为已待确认。
- 同一`mapping_key`但目标不同追加为`conflict`并停止。
- 写入后必须按`proposal_id`唯一回读且内容完全一致。
- 这里只检查个人Sheet内部冲突；跨用户冲突由正式发布器全局检查。

可以先用确定性命令生成提案JSON，命令不会写入飞书：

```bash
PYTHONPATH=src python -m auto_shipped.cli build-mapping-proposal \
  --submitted-by <feishu_user_id> \
  --company-id tiantian \
  --source-profile tiantian_warehouse_v2 \
  --identifier-type product_code \
  --source-value <来源编码> \
  --product-code <管易商品代码> \
  --spec-code <管易规格代码> \
  --evidence <确认依据> \
  --official-revision <正式库版本> \
  --operation-id <本次提交操作ID>
```

已安装Skill可用以下入口检查和建立当前用户路由：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py status
python3 <skill-dir>/scripts/feishu_mapping.py sync
python3 <skill-dir>/scripts/feishu_mapping.py provision --operation-id <稳定操作ID>
```

用户明确确认“以后沿用”后，才允许提交待确认提案：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py submit \
  --company-id <company_id> \
  --source-profile <source_profile_id> \
  --identifier-type <product_code|barcode|product_name> \
  --source-value <来源值> \
  --source-spec <来源规格> \
  --product-code <管易商品代码> \
  --spec-code <管易规格代码> \
  --evidence <用户确认依据> \
  --operation-id <稳定操作ID> \
  --confirmation-token CONFIRM_SUBMIT_PENDING_MAPPING
```

该命令自动使用当前飞书用户ID和正式库`mapping_revision`，不会让调用方手工伪造提交人或基准版本。

`sync`只读取正式映射，不读取待确认提案。它会核验库状态、库版本、`mapping_key`、重复键、管易商品/规格唯一性，再以原子替换方式写入状态目录；校验失败时不会覆盖上一次有效快照。Skill日常转换入口只自动叠加该正式快照，不再自动叠加本机旧映射或环境变量共享映射。

统一转换入口会在每次运行前自动调用备用`sync`，除非Agent显式提供本次由`sync-native`生成的`--official-mapping-snapshot`。正式读取或验证失败时，普通商品返回`FEISHU_OFFICIAL_MAPPING_UNAVAILABLE`；只有正式库成功读取且确实无匹配时，才允许返回商品映射问题。已确认组合展开规则不依赖一对一正式映射，可继续按自身规则校验。

## 6. 风险边界

由于飞书电子表格不能为不同协作者提供可靠的单Sheet编辑隔离，用户可能手工修改他人的待确认Sheet。当前设计只防止Agent误写，不声称阻止恶意或人工越界编辑。

风险由以下边界控制：

- 待确认工作簿不参与订单转换。
- 正式工作簿与待确认工作簿分离。
- 正式工作簿只有所有者/发布器可写。
- 发布器不信任待确认状态，必须重新校验映射、来源、冲突和正式库版本。
- 所有发布必须写入独立业务变更日志并回读正式映射。

所有者发布命令分为只读计划和确认发布：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py plan-publish
python3 <skill-dir>/scripts/feishu_mapping.py publish \
  --operation-id <计划返回的operation_id> \
  --confirmation-token CONFIRM_PUBLISH_ALL_PENDING_MAPPINGS
```

完全相同的正式映射会跳过且不增加重复行；同一`mapping_key`指向不同商品时整批阻断，禁止覆盖。发布期间正式库状态改为`publishing`，订单转换会失败关闭；全部记录、日志、元数据和快照校验通过后才恢复`active`。

如果未来需要强用户隔离，应把待确认仓库替换为每用户独立工作簿、飞书多维表格或数据库；`FeishuSheetGateway`上层业务合同无需改变。

## 7. 当前状态

已完成：

- 正式映射、发布日志、库信息三个Sheet已创建并回读校验。
- 待确认个人模板与用户路由Sheet已创建并回读校验。
- 当前飞书用户的个人Sheet与路由已通过适配器自动创建；相同用户重复执行会复用原Sheet，不会重复创建。
- 已确认的恬田与SAM两条编码映射已进入正式版本2。
- 2026-10-09通过所有者发布器把5条淘橘子文字单名称/规格映射从待确认库发布到正式版本3；正式库共7条active映射，5条来源提案状态和5条发布日志均经回读确认为`published`。
- 正式版本3已同步为本机快照；恬田、SAM及淘橘子淘宝/KQYD文字单均可独立使用正式库完成转换。
- Agent原生飞书结果可通过`sync-native`载荷桥进入同一校验器；正式映射不可用时转换不再退化为商品确认问题。
