# 飞书商品映射治理

状态：核心合同、用户路由、正式同步、受控直接新增和所有者发布器均已实现。商品名称映射已增加程序管理的规范品牌、商品主体、规格和Schema版本；运行时兼容旧15列表，并提供只读迁移计划与所有者确认后的19列表迁移。用户明确“以后沿用”且只是新增一个不存在的映射键时，可通过受控命令直接追加正式映射；完全重复幂等跳过，同键不同目标、替换、删除、合并和公司身份变更一律阻断并转管理员。正式写入采用单写者锁、`staged`记录和`mapping_revision`原子读指针：发布未封口只暂停后续写入，不阻断订单继续读取上一个已提交版本。待确认提案仍不能直接参与转换。

用户否决、人工改写和系统结果纠错由独立反馈箱承接；它不是第三个映射库，也不直接参与转换。完整边界见[用户纠错反馈治理](CORRECTION_FEEDBACK_GOVERNANCE.md)。

## 1. 两个映射工作簿

当前绑定：

```text
正式映射表 QsLYsongYhoIdXtaL2GcB8Rinwe
  正式映射         13abc6
  手工单商品店铺   vcPamF
  组合商品规则     CKbJZi
  发布日志         jl3rEi
  库信息           CPVHDe

待确认映射表 PH82sw3sBhbiJAtVPIScA96Yn3b
  __个人Sheet模板 eb2307
  __用户路由       U8k7Mz
```

绑定信息同时保存在`config/integrations/feishu_mapping_governance_v1.json`。业务代码只使用固定`sheet_id`，不依赖Sheet标题或位置。

### 正式映射工作簿

- 是运行时权威数据源。
- “正式映射”Sheet负责把不同公司/来源的商品编码、名称别名和规格映射到唯一管易商品+规格。
- 正式映射保持同一张表：普通行按公司/来源生效；`company_id=*`且`source_profile_id=*`的`product_name`行表示全局精确名称别名。全局行不允许使用商品代码或条码，来源专用行优先于全局行。
- “手工单商品店铺”Sheet负责把`来源作用域+管易商品代码+规格代码`关联到管易店铺；它不是另一份商品映射，也不复制来源别名。
- “组合商品规则”Sheet负责把一个来源商品身份关联到两个或以上管易商品+规格及数量倍数；它不允许伪装成多条一对一映射。
- 普通员工和Agent通过受控命令读取；只有“缺失键的无冲突新增”允许以当前飞书用户身份追加。
- 已有键的替换、删除、合并、异常状态修复和公司身份配置仍只有所有者/管理员处理。
- 待确认内容不得直接参与订单转换。

### 待确认映射工作簿

- 所有内部协作者可以编辑，因此不是安全权限边界。
- 每个飞书用户对应一个个人Sheet，Agent按稳定用户ID自动路由。
- 用户路由表由Agent自动创建和修复，不要求管理员提前登记每名员工。
- 现在主要承接冲突、替换、历史异常和需要管理员判断的例外；普通无冲突新增不再绕行待确认表。
- 待确认内容正式发布前仍必须由所有者重新查重、校验并确认。

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

备用适配器与中央日序号网关共用`LarkCliClient`，统一执行命令、解析JSON和探测身份。客户端优先用`lark-cli auth status --verify`取得稳定用户身份。部分旧版CLI（已见`v1.0.93`）没有`auth`子命令；客户端先尝试`whoami --as user`读取`onBehalfOf.openId`，若该命令同样不存在，再通过用户身份调用只读`/open-apis/authen/v1/user_info`取得`open_id`。映射网关随后必须用同一user身份真实读取正式库元数据后才接受该身份；其他业务网关必须验证各自目标工作簿。Agent不得临时创建命令垫片或改写`PATH`绕过探针。只从dry-run看到`identity=user`不足以证明稳定用户ID，不能放行正式写入；三条身份路径都不可用时返回准确网关错误，不得继续声称长期复用成功。

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
      "evidence": "...",
      "canonical_source_brand": "...",
      "canonical_source_product": "...",
      "canonical_source_spec": "...",
      "canonical_schema_version": "1.0"
    }
  ],
  "manual_store_routes": [
    {
      "route_id": "...",
      "route_key": "...",
      "scope_id": "manual_all",
      "product_code": "...",
      "spec_code": "...",
      "store": "光明满元气",
      "status": "active",
      "route_version": "...",
      "confirmed_by": "...",
      "confirmed_at": "...",
      "evidence": "..."
    }
  ],
  "item_expansions": [
    {
      "rule_id": "...",
      "rule_key": "...",
      "company_id": "sam",
      "source_profile_id": "sam_order_v1",
      "identifier_type": "product_code",
      "source_value": "...",
      "source_spec": "...",
      "target_platform": "guanyi",
      "status": "active",
      "rule_version": "...",
      "components": [
        {"component_index": 1, "product_code": "...", "spec_code": "...", "quantity_multiplier": 1},
        {"component_index": 2, "product_code": "...", "spec_code": "...", "quantity_multiplier": 1}
      ]
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

当来源行提供了规格，规格是映射身份的一部分，只允许精确组合键命中，禁止退回无规格映射。来源确实没有规格时仍允许正式确认的仅编码或仅名称映射；两种情况由来源事实决定，不要求所有商品强行补规格。

来源没有规格、且其商品代码拟绑定到一个含多个规格的管易商品代码时，即使系统已有一个候选目标，也必须在首次使用前让用户选择具体规格并说明使用范围。用户选择“仅本次”时使用与当前源文件`sha256:`指纹绑定的临时新增映射；文件变化或下个批次不会复用。用户明确“以后沿用”且正式库尚无该键时，走受控直接新增。正式库已经有同键不同目标时不能直接改表：当前批次可使用独立的临时替换记录，长期修正必须联系管理员。只有正式快照会在本机运行时附加`reuse_scope=future`和`mapping_authority=feishu_official`。

`candidate_spec_code`和正式表的`spec_code`允许留空，但这不代表可以忽略规格。校验器只在`product_code`于当前管易商品主档中唯一对应一个商品/规格组合时补成规范值；如果存在多个规格，提案或正式同步都会阻断。商品别名继续用`identifier_type=product_name`写在同一张映射表中；模糊名称匹配只返回候选，不写入两个工作簿。

名称行的原始`source_value/source_spec`继续用于追溯和精确键；程序同时生成`canonical_source_brand / canonical_source_product / canonical_source_spec / canonical_schema_version`，使品牌、名称和规格被Agent放在不同字段时仍能得到同一身份。`product_name`行必须生成非空规范商品主体；品牌与规格按来源事实可空。代码/条码行仍以编码为身份，来源没有可靠名称时四个规范字段允许为空，禁止用目标管易目录反推来源名称。规范化只处理语言身份，不改变数量、单位或目标SKU。

正式映射单行Schema位于`contracts/official_product_mapping.schema.json`。作用范围由公司和来源两列确定，并由同步器和发布器共同校验；两个字段只能同时为`*`或同时为具体登记值，禁止混合，全局作用域只允许`identifier_type=product_name`。读取器同时接受旧15列表和新19列表；旧表同步时在本机快照中确定性派生规范字段。物理迁移必须先运行只读计划，确保没有同一来源范围下相同规范身份指向多个目标，再由所有者确认写入：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py plan-canonical
python3 <skill-dir>/scripts/feishu_mapping.py migrate-canonical \
  --operation-id <稳定操作ID> \
  --confirmation-token CONFIRM_MIGRATE_CANONICAL_MAPPING_SCHEMA
```

迁移写入P:S四列、递增`mapping_revision`、记录`MIGRATE_CANONICAL_SCHEMA`发布日志并回读完整19列表。发布新代码前不得提前迁移在线表；迁移失败时保留旧`mapping_revision`供转换读取，并保留写锁等待管理员核对，禁止部分静默成功或继续发起正式写入。

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
  --spec-code <管易规格代码；唯一单规格商品可省略> \
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

用户明确确认“以后沿用”后，若该键在正式库中不存在且目标商品唯一，使用受控直接新增：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py add \
  --company-id <company_id> \
  --source-profile <source_profile_id> \
  --identifier-type <product_code|barcode|product_name> \
  --source-value <来源值> \
  --source-spec <来源规格> \
  --product-code <管易商品代码> \
  --spec-code <管易规格代码；唯一单规格商品可省略> \
  --evidence <用户确认以后沿用的依据> \
  --operation-id <稳定操作ID> \
  --confirmation-token CONFIRM_ADD_NEW_OFFICIAL_MAPPING
```

命令会验证当前用户身份、公司/来源登记、有效商品目录、正式库版本和重复键；写入后回读映射、日志和新版本，并同步本机正式快照。完全重复返回`already_exists`且不增加版本；同键不同目标返回`needs_admin`且不写入。

用户已经授权“以后沿用”而受控新增因CLI身份、权限、网络或接口错误失败时，不得再次询问授权，也不得声称长期登记成功。目标已经确认且本批指纹可用时，可以降级为仅当前批次临时映射以完成预览，并保留准确错误代码和待补录状态；网关恢复后重试同一幂等新增操作。临时映射永远不能替代正式表的写后回读证据。

名称别名需要所有来源共享时，在同一命令增加`--mapping-scope global`；仅当前来源沿用时保持默认`--mapping-scope source`。全局新增仍执行相同的目标目录校验、重复/冲突门禁、版本锁、发布日志和写后回读。

一个来源商品需要展开成多个管易商品时，长期规则使用独立入口：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py add-expansion \
  --company-id <company_id> \
  --source-profile <source_profile_id> \
  --identifier-type <product_code|barcode|product_name> \
  --source-value <来源值> \
  --source-spec <来源规格> \
  --components-json '[{"product_code":"目标1","spec_code":"规格1","quantity_multiplier":1},{"product_code":"目标2","spec_code":"规格2","quantity_multiplier":1}]' \
  --evidence <用户确认组成并以后沿用的依据> \
  --operation-id <稳定操作ID> \
  --confirmation-token CONFIRM_ADD_ITEM_EXPANSION_RULE
```

该入口至少要求两个不重复目标，逐项校验有效商品目录；完全重复幂等，同一来源键已有不同组成时转管理员，禁止普通用户覆盖。写入过程与普通正式映射共用`mapping_revision`原子读指针、单写者锁、发布日志和回读快照。

用户只确认当前批次时不得写飞书正式表。使用：

```bash
python3 <skill-dir>/scripts/save_item_expansion.py \
  --company-id <company_id> \
  --source-profile <source_profile_id> \
  --identifier-type <product_code|barcode|product_name> \
  --source-value <来源值> \
  --source-spec <来源规格> \
  --source-fingerprint sha256:<64位文件哈希> \
  --components-json '<同上>' \
  --evidence <用户确认仅本批使用> \
  --output <本批输出目录/item-expansion.json> \
  --confirmation-token CONFIRM_USE_ITEM_EXPANSION_FOR_CURRENT_BATCH
```

转换重跑时只把该文件作为本批`--mapping-overlay`传入；来源文件指纹不同即不命中。

JZDZ、DRJY等固定手工业务来源完成商品映射后，再读取“手工单商品店铺”关系，`scope_id`使用具体来源配置；通用兜底公司则使用稳定`company_id`，不能使用共享的`generic_company_manual_v1`，以免多家公司相同SKU互相污染。两者均可回退`manual_all`，具体作用域优先。关系键只包含作用域和规范化后的管易商品+规格，不把公司别名或来源商品名重复存一遍。用户明确“以后沿用”后，纯新增关系使用：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py add-store-route \
  --scope-id <manual_all|jzdz_manual_v1|drjy_manual_v1|fallback_company_id> \
  --product-code <管易商品代码> \
  --spec-code <管易规格代码；唯一单规格商品可省略> \
  --store <光明满元气|淘橘子> \
  --evidence <用户确认依据> \
  --operation-id <稳定操作ID> \
  --confirmation-token CONFIRM_ADD_MANUAL_STORE_ROUTE
```

完全相同关系幂等跳过；同一作用域和SKU已经指向不同店铺、记录状态异常或一单商品落到不同店铺时失败关闭并转管理员，普通用户不能覆盖。

只有冲突、替换或其他例外才提交待确认提案供管理员治理：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py submit \
  --company-id <company_id> \
  --source-profile <source_profile_id> \
  --identifier-type <product_code|barcode|product_name> \
  --source-value <来源值> \
  --source-spec <来源规格> \
  --product-code <管易商品代码> \
  --spec-code <管易规格代码；唯一单规格商品可省略> \
  --evidence <用户确认依据> \
  --operation-id <稳定操作ID> \
  --confirmation-token CONFIRM_SUBMIT_PENDING_MAPPING
```

该命令自动使用当前飞书用户ID和正式库`mapping_revision`，不会让调用方手工伪造提交人或基准版本。它是例外队列，不是普通新增的默认路径。

用户否决一个已存在的正式映射时，正式表保持不变。当前批次如需继续，先同步正式快照，再运行`save_batch_mapping_replacement.py`；该记录保存在本机私有状态目录，绑定来源文件SHA-256，同时保存“预期旧正式目标”和“本批替换目标”。运行时只有文件哈希与旧目标都仍一致才使用替换；其他文件会忽略该记录。替换后应把非隐私纠错提交反馈箱，并明确提示用户联系管理员处理长期修正。

`sync`读取正式映射、手工单商品店铺和组合商品规则，不读取待确认提案。它会核验库状态、库版本、所有关系键、重复键、组合行连续性及管易商品/规格唯一性，再以原子替换方式写入状态目录；校验失败时不会覆盖上一次有效快照。Skill日常转换入口只自动叠加该正式快照，不再自动叠加本机旧映射或环境变量共享映射。

映射表始终使用同一基础结构：`source_value`存来源商品标识，`source_spec`存来源规格，至少填写一个；没有规格时`source_spec`留空。通常两者共同组成精确键。SAM这类规格编码稳定、但父商品编码配对尚未确认的来源，可在相同结构中让`source_value`留空、只填写`source_spec`；该能力必须由来源策略`allow_spec_only_mapping=true`显式开启。完整组合优先于规格单独映射，来源已经给出规格时禁止退回无规格映射。四个规范字段是程序管理的派生列，不由用户手工拼写，也不参与`mapping_key`，防止词典升级改变现有行主键。

匹配优先级不是“看到同名代码就自动选规格”，而是：来源完整组合正式编码/条码映射 → 来源无规格的正式长期编码映射 → 当前来源正式名称规范身份 → 全局正式名称规范身份 → 其他来源相同规范身份候选 → 唯一精确管易名称 → 相似名称候选。来源无规格但目标商品多规格时，第二步只有带正式长期复用证据的记录可以通过；其他情况停在候选确认。编码精确命中但名称明显变化时不改选目标：先生成无manifest预览，并把本批复核放入结果小节。

统一转换入口会在每次运行前自动调用备用`sync`，除非Agent显式提供本次由`sync-native`生成的`--official-mapping-snapshot`。正式读取或验证失败时，整批返回`FEISHU_OFFICIAL_MAPPING_UNAVAILABLE`；只有正式库成功读取且确实无匹配时，才允许返回普通商品或组合商品确认问题。组合展开不依赖一对一映射行，但长期组成与一对一映射共用同一受校验正式快照。

## 6. 风险边界

由于飞书电子表格不能为不同协作者提供可靠的单Sheet编辑隔离，用户可能手工修改他人的待确认Sheet。当前设计只防止Agent误写，不声称阻止恶意或人工越界编辑。

风险由以下边界控制：

- 待确认工作簿不参与订单转换。
- 正式工作簿与待确认工作簿分离。
- 正式工作簿只允许受控命令写；普通用户只能无冲突新增，所有者处理替换、删除和冲突。
- 发布器不信任待确认状态，必须重新校验映射、来源、冲突和正式库版本。
- 所有发布必须写入独立业务变更日志并回读正式映射。
- 商品映射、手工单店铺关联和组合规则共享正式库版本、单写者锁和日志；写入中断只锁住后续正式写入，转换继续读取当前`mapping_revision`对应的完整版本。
- 当前批次临时替换是独立本机记录，永远不写正式表或待确认表，也不改变正式版本。

所有者发布命令分为只读计划和确认发布：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py plan-publish
python3 <skill-dir>/scripts/feishu_mapping.py publish \
  --operation-id <计划返回的operation_id> \
  --confirmation-token CONFIRM_PUBLISH_ALL_PENDING_MAPPINGS
```

完全相同的正式映射会跳过且不增加重复行；同一`mapping_key`指向不同商品时整批阻断，禁止覆盖。新发布不再把整个库改成`publishing`：它只写入`publish_operation_id`作为单写者锁，并把新行写成下一版本的`staged`。读取端始终以`mapping_revision`为唯一提交边界，版本指针切换前忽略新行，切换后即使状态清理尚未完成也可读取该完整版本。

因此，未封口写入不再阻断订单转换，只阻断另一个正式写入。若写入中断，先只读诊断；只有目标记录、日志和目标版本快照全部一致时，所有者才可切换或确认版本指针、把记录封口为`active`并清除写锁。旧版遗留的`repository_status=publishing`也按相同版本规则只读兼容，不再让转换整体停摆：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py diagnose-recovery
python3 <skill-dir>/scripts/feishu_mapping.py recover \
  --confirmation-token CONFIRM_RECOVER_OFFICIAL_REPOSITORY
```

若诊断返回`blocked`，不得手工清除`publish_operation_id`或改动`mapping_revision`；应按操作ID继续核对缺失的记录或日志。转换可继续使用当前已提交版本，但新的长期映射写入必须等恢复完成。

如果未来需要强用户隔离，应把待确认仓库替换为每用户独立工作簿、飞书多维表格或数据库；`FeishuSheetGateway`上层业务合同无需改变。

## 7. 当前状态

已完成：

- 正式映射、手工单商品店铺、组合商品规则、发布日志、库信息五个Sheet已创建并回读校验。
- 待确认个人模板与用户路由Sheet已创建并回读校验。
- 当前飞书用户的个人Sheet与路由已通过适配器自动创建；相同用户重复执行会复用原Sheet，不会重复创建。
- 已确认的恬田与SAM两条编码映射已进入正式版本2。
- 2026-10-09通过所有者发布器把5条淘橘子文字单名称/规格映射从待确认库发布到正式版本3；正式库共7条active映射，5条来源提案状态和5条发布日志均经回读确认为`published`。
- 2026-10-12将映射结构统一为“来源商品标识+来源规格（可空）”；旧的独立`spec_code`类型提案已拒绝，不进入正式库。
- 2026-10-12通过所有者发布器把`SWSH8096`、`SWJC8140`、`SWJC8133`、`SWHL3610`四条SAM规格映射发布到正式版本4；0覆盖、0冲突，正式库共11条active映射。
- 正式版本4已同步为本机快照；恬田、SAM及淘橘子淘宝/KQYD文字单均可独立使用正式库完成转换。
- Agent原生飞书结果可通过`sync-native`载荷桥进入同一校验器；正式映射不可用时转换不再退化为商品确认问题。
- 2026-10-15启用受控直接新增：用户确认“以后沿用”的纯新增可直接进入正式表；重复幂等，同键冲突转管理员。用户否决已有正式映射时只允许文件哈希绑定的当前批次替换，并保留独立纠错反馈。
- 2026-10-15新增“手工单商品店铺”关系表并接入同一正式快照：JZDZ、DRJY在员工视角作为独立来源，底层归属自营；精确来源关系优先于`manual_all`，当前11个已知SKU已写入通用关系并回读校验。
- 2026-10-15新增“组合商品规则”关系表并接入同一正式快照：SAM白糯、花糯、黄糯8+1共3条规则、6条组件已写入并回读；正式库版本升至8，来源规则包不再复制这些组成事实。
- 2026-10-17增加商品短语规范身份、字段边界无关的正式名称映射匹配、批次映射证据和15→19列受控迁移工具。当前在线正式表仍保持旧15列兼容格式；只有新版代码进入稳定分支并完成`plan-canonical`冲突预检后，才执行所有者确认迁移。
