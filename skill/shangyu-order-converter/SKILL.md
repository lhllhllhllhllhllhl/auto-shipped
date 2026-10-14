---
name: shangyu-order-converter
description: 识别尚舆收到的公司原始订单Excel、聊天文字或截图订单；已知模板用确定性脚本处理，未知Excel或非结构化订单先经Agent提取和用户确认，再按商品映射与平台规则生成经过校验的导入文件。也用于管易登录、受控上传和结果核验；缺失业务事实必须追问，未授权前不提交，始终不审核或发货。
---

# 尚舆订单转换

把员工提供的公司原始订单转换成经过校验的管易自定义订单导入文件。Skill 只负责调度；来源识别、解析、商品映射、平台规则、模板渲染和浏览器操作均由 Skill 内置运行时的确定性程序负责。开发时可用 `SHANGYU_AUTO_SHIPPED_ROOT` 显式覆盖内置运行时。

## 环境检查

首次在一台电脑使用，或运行时报告依赖缺失时，先执行：

```bash
python3 <skill-dir>/scripts/doctor.py
```

完整迁移和初始化规则见 [references/portability.md](references/portability.md)。只有用户要求配置新电脑或同意安装缺失依赖时，才运行 `bootstrap_runtime.py`。凭证和登录会话不随 Skill 迁移。

## 执行流程

1. 确认用户提供的是订单Excel，还是聊天文字/截图。Excel走现有识别链路；文字或截图先按[文字与截图订单入口](references/text-order-intake.md)生成草稿、脱敏预览并取得用户确认，再进入统一转换。只要Agent已确认内容属于非Excel订单，且用户没有指定公司或规则包，草稿就默认使用`routing_mode=registered_manual_channels`，由脚本逐单匹配已登记来源规则；不要求用户额外说“这是手工订单”，也不要先把整段锁成一个公司。一次收到多个来源时默认合并生成一个管易 Excel。只有用户明确说“分开”“分别输出”或同等意思时，才使用分开模式。批次编排仍会先逐来源独立识别、转换和预检，只有全部通过且目标平台与模板一致时才生成一个合并 Excel。
2. 默认使用系统登记的分层目标商品资料：业务优先目录先查，未命中再查管易商品主档；飞书正式映射仍单独负责“来源商品→目标代码”。员工日常只需提交公司原始订单。两份目录独立版本化和替换；只有对应系统资料缺失、不可读或用户明确提供新版资料时才要求补充 CSV。
3. 为本次运行新建输出目录，不覆盖原始订单或历史输出。
4. 标准商品转换前必须完成飞书正式映射读取：

   - Agent有原生飞书电子表格能力时，优先读取治理配置中固定的正式映射Sheet和库信息Sheet，按[飞书映射治理](runtime/auto-shipped/docs/FEISHU_MAPPING_GOVERNANCE.md)生成标准载荷，再运行`feishu_mapping.py sync-native --payload <载荷.json>`。把命令返回的`output`作为统一转换入口的`--official-mapping-snapshot`。
   - 没有原生飞书能力时，统一转换入口会自动使用`lark-cli`只读同步正式表；不得要求员工手工导出映射表。
   - 两种读取方式都不可用、无权限或校验失败时，转换必须返回`FEISHU_OFFICIAL_MAPPING_UNAVAILABLE`并停止。不得继续使用空映射，也不得向用户重新询问已经发布的映射。

5. 默认使用统一入口执行：

   ```bash
   python3 <skill-dir>/scripts/convert_orders.py \
     --source <原始订单.xlsx> \
     --output-dir <本次输出目录>
   ```

   用户已经在当前对话中明确说明公司时，传入对应来源配置，例如：

   ```bash
   python3 <skill-dir>/scripts/convert_orders.py \
     --source <原始订单.xlsx> \
     --source-profile-hint '<原始订单.xlsx>=tiantian_warehouse_v2' \
     --output-dir <本次输出目录>
   ```

   已知模板仍会校验Excel格式。用户只明确了公司、尚不知道具体模板时，不要强行传一个已登记来源配置；改用公司提示：

   ```bash
   python3 <skill-dir>/scripts/convert_orders.py \
     --source <原始订单.xlsx> \
     --company-hint '<原始订单.xlsx>=<company_id>' \
     --output-dir <本次输出目录>
   ```

   统一入口会先尝试该公司的已登记模板。仍未命中时自动创建`*_自适应来源计划.json`，通过无敏感信息结构探针识别表头和列类型，并只返回真正缺失的业务问题。Agent根据用户回答更新该计划后，使用`--adaptive-plan-hint '<原始订单.xlsx>=<计划.json>'`重新运行。计划的数据合同见`runtime/auto-shipped/contracts/adaptive_source_plan.schema.json`。

   需要单独检查未知模板时可以运行：

   ```bash
   python3 <skill-dir>/scripts/adaptive_source.py inspect --source <原始订单.xlsx>
   python3 <skill-dir>/scripts/adaptive_source.py propose \
     --source <原始订单.xlsx> \
     --company <company_id> \
     --output <计划.json>
   ```

   结构探针只能输出工作表、表头、非空数量和数据类型，禁止输出数据行。字段名称和结构可以由Agent推断；原表没有的商品、数量、公司、订单用途或单号规则不得推断。只有用户明确确认本批补充值后，才允许在计划中加入`batch_defaults`或`date_sequence`，并设置`assumption_confirmation_token=CONFIRM_ADAPTIVE_BATCH_ASSUMPTIONS`。

   没有公司级证据、用户也没有明确公司时，必须让转换器返回追问，不得仅凭通用表头自动路由。

   用户明确提供新版管易商品主档时，额外传入 `--catalog <商品信息.csv>`；提供新版业务优先目录时传入`--preferred-catalog <优先商品目录.csv>`。二者都只覆盖本次运行，不自动替换系统配置。

   多文件默认合并时执行：

   ```bash
   python3 <skill-dir>/scripts/convert_orders.py \
     --source <原始订单1.xlsx> \
     --source <原始订单2.xlsx> \
     --batch-name <本次批次名称> \
     --output-dir <本次输出目录>
   ```

   只有用户明确要求分别生成时，才在同一命令加 `--separate`。某份文件需要用户明确来源时，可重复传入 `--source-profile-hint '<文件名或完整路径>=<来源配置ID>'`。批次按用户提供文件的顺序合并，不在不同目标平台或不同目标模板之间混合。

6. 读取命令输出的 JSON，并严格按 `status` 处理：

   - `ready`、退出码 `0`：确认 `upload_manifest` 存在且状态为 `ready`，再返回 `outputs` 中的 Excel、manifest 和脱敏摘要，简要说明识别来源、订单数和输出行数，然后停止等待用户确认。若 `detection.schema_warnings` 非空，必须同时提醒用户来源Excel出现了未登记新列，不能静默忽略。生成文件、转换请求和 manifest 都不是上传授权；只有用户查看交付结果后，在当前对话的新指令中明确指定“上传这批/这些批次”，才进入浏览器上传流程。
   - `needs_input`、退出码 `2`：向用户提出 `clarifications` 中的问题。不要生成占位文件，也不要把候选商品映射当成已确认映射。
   - `failed` 或退出码 `1`：说明具体错误和可执行的补救步骤，不把失败结果描述为可上传文件。

   批次入口还执行以下整体门禁：内容完全相同的重复文件直接阻断；不同文件生成相同平台单号时阻断；任一文件返回追问时整批不出 Excel；任一文件预检或哈希校验失败时整批失败。不得拿已经通过的子文件另行拼成半批次。

转换服务在商品映射和模板生成前强制执行 `conversion` 业务规则覆盖门禁。`BUSINESS_RULE_*` 追问表示适用SOP规则缺失、待确认、待实现或实现引用不可核验；不得用临时默认值绕过。需要审计当前规则状态时运行：

```bash
python3 <skill-dir>/scripts/audit_rules.py --phase conversion
python3 <skill-dir>/scripts/audit_rules.py --phase upload
```

规则包只对身份、版本和覆盖声明执行轻量Schema校验，公司特有业务字段仍可扩展。标记为`implemented`的实现引用必须在内置实现注册表中绑定真实代码入口和测试文件；注册缺失时停止转换。

来源识别后还必须通过公司库校验。公司库只索引稳定公司ID、来源、路由、平台规则和商品映射范围，不复制业务规则。需要检查公司记录和模块引用时运行：

```bash
python3 <skill-dir>/scripts/audit_companies.py
```

返回非 `ready` 时不得转换或上传，也不得临时绕过公司登记。

详细状态含义见内置运行时的 `docs/BUSINESS_RULE_COVERAGE.md`。

## 不确定项

- 只有高置信度来源识别才继续；来源不明或多重匹配时追问。
- 已登记公司的未知Excel格式不得直接判为“不支持”。先生成一次性自适应字段计划；能从表头唯一确定的列直接映射，原表缺少的业务事实集中追问。自适应计划与当前文件SHA-256绑定，文件变化后必须重新分析。
- 文字或截图订单必须先经过Agent提取草稿、确定性校验和一次用户确认。原文明示字段标为`explicit`，用户确认字段标为`user_confirmed`；`inferred`或`missing`字段不得进入转换。用户未指定公司或规则包时，文字订单默认按每笔订单唯一命中的已登记平台/业务标记选规则包；这只是已登记手工来源路由，不能扩展成任意平台到公司的猜测。未命中或多重命中只追问该笔来源，不重复询问规则包已有的店铺、普通买家会员和默认物流。脱敏订单预览确认同时覆盖提取结果与路由结果，不再增加“是否手工订单”的独立确认。用户确认识别结果仍不能替代商品映射与业务规则门禁。
- 文件用途不明时，先问批次用途，再决定是否请求收件信息。
- 商品映射必须精确且已确认。名称相似只能作为候选，禁止自动确认。
- 商品匹配按公司和来源配置隔离，并统一使用“来源商品标识+来源规格（可空）”结构。来源有规格时先匹配完整组合；SAM在父商品编码配对未知时允许使用同一结构中的规格单独映射。其他来源继续使用各自登记的商品编码、名称或条码策略。模糊名称、多个规格或多个候选必须追问。
- 来源提供规格时，规格是商品身份的一部分，只能命中完全相同的“来源标识+规格”正式映射，禁止降级到仅编码或仅名称映射。来源确实没有规格时，才允许命中经正式确认的无规格映射。
- 来源只有商品代码、而同名管易商品代码存在多个规格时，第一次必须列出管易商品/规格候选并让用户选择，同时询问“仅本次使用”还是“以后沿用”。候选、旧本机覆盖或模型推理都不能绕过这次确认。只有用户明确选择“以后沿用”，且提案经所有者发布到飞书正式映射库并重新同步后，未来批次才允许自动命中；正式快照记录会被运行时标记为`reuse_scope=future`。仅本次确认不得提交待确认表或长期覆盖层，只能创建`reuse_scope=current_batch`且与当前源文件`sha256:`指纹完全一致的临时覆盖；文件换了就会再次追问。
- 管易目标规格可以留空，但只限该商品代码在当前有效目标目录中只有一个唯一规格（包括真正无规格的商品）；发布器会规范化为实际唯一组合。若同一管易商品代码有多个规格而目标规格为空，必须阻断并追问。
- 各公司的来源商品别名仍登记在同一映射表，使用`identifier_type=product_name`并按公司/来源隔离，不另建别名库。相似名称模块只负责排序候选，没有写表和出单权限。
- 文字玉米的`根/袋/盒/份/套`必须经过正式包装语义库。`根`的全局候选是彩袋单棒装，但某公司第一次出现且尚未登记时必须追问；RZD已登记`根→彩袋单棒装`，自营已登记`袋→8棒家庭装`。三种裸棒属于试吃商品，普通发货一律阻断，不能靠名称或编码绕过。
- 用户确认新映射后，先问清是“仅本次使用”还是“以后沿用”。只有用户明确要求以后沿用，才运行飞书待确认提案命令；待确认提案不会参与转换，必须由所有者发布到正式映射表后再同步。
- 用户选择“仅本次使用”时，使用`save_product_mapping.py`显式指定本次临时`--overlay`、`--reuse-scope current_batch`、当前来源`--source-fingerprint sha256:<64位哈希>`及口令`CONFIRM_USE_PRODUCT_MAPPING_FOR_CURRENT_BATCH`，随后仅在本次重跑中传入该`--mapping-overlay`。临时覆盖不得复制到状态目录、其他文件或后续批次。
- 公司可以用公司ID、全称、已登记简称或别名识别。只登记了身份、尚未登记转换工作流的公司只能确认“是哪家公司”，仍须停止并补齐模板、目标平台与业务规则；已登记公司的未知模板允许使用一次性自适应字段计划，但不得把一次性计划自动提升为永久模板。
- 用户在对话中对某个候选做一次性确认，不等于授权永久修改公司规则或商品主档。需要持久化时明确说明并单独更新版本化配置。
- 用户否决系统结果或人工修改成品时，先保证当前批次正确。用户同意留存后，运行`feishu_mapping.py submit-feedback ... --confirmation-token CONFIRM_SUBMIT_CORRECTION_FEEDBACK`写入独立反馈箱；没有成功回读`feedback_id`不得说已登记。反馈箱不参与转换；完整商品映射若要长期沿用，仍需另行提交待确认映射并取得`proposal_id`。详细流程见[用户纠错反馈治理](runtime/auto-shipped/docs/CORRECTION_FEEDBACK_GOVERNANCE.md)。
- 发现SOP新增或遗漏规则时，先把它登记到规则目录和对应来源规则包；禁止只在模板渲染器中追加条件分支。
- 新规则先判断是跨公司共享还是公司特有。共享规则写入`runtime/auto-shipped/config/platform_rules/guanyi/policy_modules_v1.json`并由公司规则包引用；公司特有规则才留在对应规则包。详细边界见[规则模块结构](runtime/auto-shipped/docs/RULE_MODULE_STRUCTURE.md)。同一个规则目标不得同时内联和引用。
- 来源备注先经过跨公司共享备注分流模块：送货上门、不准放门口、驿站/快递柜、派送联系等明确给快递员的指令追加到收货地址末尾；其余备注才进入卖家备注。文字入口能明确提取时优先写入`source_extensions.delivery_instruction`；不得在公司规则包或临时脚本重复维护关键词。
- 普通来源买家会员填写`张`。SAM有跟团号时使用`sam+商品类型缩写+上海时区当日YYMMDD+_+跟团号`；无跟团号时使用`sam+商品类型缩写+整批生成分钟YYMMDDHHmm+_+最少两位弹性序号`。分钟序号复用共享本机占号模块并按用途隔离，同一来源重跑复用。当前`YM=玉米`、`SH=尚和手套`；商品类型未登记或一单包含多个类型时仍必须追问。
- 组合商品展开与普通商品映射分离。SAM白糯8+1来源项先按已确认规则展开为8根装和单根装，再分别校验管易商品主档；不得把同一来源编码的多个目标商品写成普通一对一映射冲突。
- 白标判断只在商品映射完成后执行，按内置版本化白标库中的商品代码+规格代码精确匹配；任一商品命中时整单卖家备注追加“白标商品”。禁止按名称相似度判断。当前权威库由`商品白标.xlsx`的131条商品规格完整替换生成，旧3条临时记录不再生效；增删SKU只修改白标库，不得把名单复制到来源解析器或公司规则分支。

## 输出与权限边界

- 当前输出是“管易自定义订单导入 Excel”，不是已经上传或审核的订单。
- 多文件批次成功时只交付一个合并后的管易 Excel、一个对应预检 manifest 和一个不含收件信息的处理报告；单文件暂存结果只存在于临时目录，不作为交付物。
- 转换结束不得自动登录管易或选择文件。只有用户查看转换结果后另行明确指定上传批次，才使用独立浏览器执行模块；审核、作废和发货始终禁止。
- 不在聊天、日志或处理摘要中展示完整姓名、手机号和详细地址。向用户交付生成文件即可。
- 不修改原始 Excel，不在 Skill 中保存账号、密码或会话。公司业务规则只能保存在内置运行时的版本化配置中，不得散落到Skill说明文字、临时脚本或模板渲染器中。

## 商品映射库与多用户边界

- Skill 内置版本化来源匹配政策和来源身份保护，但不内置自动生效的一对一商品目标映射。
- 飞书正式映射表是员工日常转换的权威映射源；转换前必须同步正式快照。转换入口不再自动加载本机旧映射或环境变量共享映射。
- 一次性调试覆盖只能由维护者显式传入，不得作为员工正常入口的隐式状态，也不得声称已同步给其他用户。
- 保存命令只登记商品标识、公司来源、管易商品代码和规格代码，不写入订单、收件人或地址信息。
- 飞书共享映射采用两个工作簿：正式映射只读消费，待确认映射按当前飞书用户ID自动路由到个人Sheet。待确认工作簿不是权限安全边界，任何提案必须经所有者发布后才能进入正式映射。
- 飞书侧优先使用Agent原生电子表格能力，`lark-cli`仅作备用。原生读取结果必须通过`sync-native`确定性校验后才能成为转换覆盖层，禁止让模型直接把表格文本当作已确认映射。待确认写入必须获取稳定的用户身份；共享机器人身份不能用于个人Sheet路由。
- 当前飞书工作簿、待确认网关、正式快照和所有者发布器均已接通。待确认提案仍不会自动生效；只有所有者明确要求发布时，才先运行`feishu_mapping.py plan-publish`，确认无冲突后以计划返回的`operation_id`运行`publish --confirmation-token CONFIRM_PUBLISH_ALL_PENDING_MAPPINGS`。完全重复跳过，同键不同目标整批阻断，禁止覆盖；发布完成后必须回读正式版本、active映射、发布日志和待确认状态。
- 处理飞书映射提案或接入工作簿时，先读 [飞书映射治理](runtime/auto-shipped/docs/FEISHU_MAPPING_GOVERNANCE.md)。
- 开发维护线程回收用户纠错时，运行`python3 <skill-dir>/scripts/feishu_mapping.py collect-feedback --status new`只读汇总；反馈不得直接改写正式映射或业务规则。
- 飞书只读健康检查运行`python3 <skill-dir>/scripts/feishu_mapping.py status`；它验证当前用户身份、正式库版本、路由和个人Sheet数量，不写表格。
- 每次开始依赖商品映射的转换前必须完成当次正式库读取。统一入口默认自动运行`python3 <skill-dir>/scripts/feishu_mapping.py sync`；若Agent已使用原生飞书能力读取，则运行`sync-native`并把返回快照显式传给`--official-mapping-snapshot`。读取会校验库状态、版本、哈希、映射键和商品主档并原子更新本机快照；失败时停止转换，不得静默使用旧快照或待确认提案。
- 正式映射表不按公司另建结构，也不另设“规格编码类型”：每行都有来源商品标识与来源规格两栏，至少填写一个；没有规格就把来源规格留空。完整组合优先于规格单独映射。无来源规格的编码映射必须通过来源配置中的身份保护；当前恬田`2026DFYUMI001`还必须匹配已确认来源品名。编码相同但品名缺失或变化时必须追问。
- 来源指定物流仍优先，但必须先通过来源规则包的别名表转换为管易标准名称；未登记名称必须追问。不得把任意来源文本直接写入“物流公司”。
- 来源联系方式必须是11位大陆手机号、带平台隐私号尾缀的手机号，或已支持的座机格式；隐私号尾缀必须完整保留。尚舆自营京东中通还要读取姓名后的四位码并由规则层输出`号码-四位码`，姓名缺码或与地址/已有尾码冲突时追问。超过15位的数值型订单号或科学计数法订单号可能已经损失精度，必须要求用户将来源列改为文本后重新导出。
- 管易输出中的平台单号继续使用Excel文本类型和`@`格式。27位纯数字平台单号已于2026-10-05完成一次真实手工上传并由用户确认成功；这只证明输出文本格式兼容，不允许放宽对来源数值精度的阻断。
- 平台单号的订单身份统一按`订单编号 > 来源单号`选择；两者都没有时才执行来源规则包中已确认的日期/分钟序号规则。订单分组、去重和平台单号必须共用同一结果。公司前缀由来源规则包负责，`A`由管易公共平台配置统一追加；上传预检必须阻断缺少`A`的系统成品。
- 首次使用可运行`python3 <skill-dir>/scripts/feishu_mapping.py provision --operation-id <稳定操作ID>`。该命令按当前`feishu_user_id`创建或复用个人Sheet，并对路由与owner元数据做写后回读。
- 只有用户明确确认“该映射以后沿用”后，才运行`python3 <skill-dir>/scripts/feishu_mapping.py submit ... --confirmation-token CONFIRM_SUBMIT_PENDING_MAPPING`。提交只进入待确认库，不会自动成为正式映射；不得把确认口令用于用户未授权的候选。

## 包装语义与跨用户回收

- 正式包装语义位于`runtime/auto-shipped/config/catalog/package_semantics_v1.json`，随Skill发布并由所有用户共享；本机提案不能覆盖正式库。
- 用户确认包装表达仅本次使用时不保存。只有用户明确说“以后沿用”时，才运行`python3 <skill-dir>/scripts/package_semantics.py save ... --confirmation-token CONFIRM_PACKAGE_SEMANTICS_REUSE`，保存到当前电脑独立的待登记目录。
- 当主系统所有者要求“把待登记包装规则发给我”“导出包装规则”或同等意思时，运行`python3 <skill-dir>/scripts/package_semantics.py export --output <本次输出目录>/包装语义待登记提案.json`并交付该文件。导出内容不含订单号、收件人、电话或地址。
- 待登记提案状态始终为`pending_owner_review`；所有者审核、写入正式库、增加测试并重新发布Skill后，才允许所有用户复用。完整流程见[包装语义规则与回收治理](runtime/auto-shipped/docs/PACKAGE_SEMANTICS_GOVERNANCE.md)。

## 管易浏览器执行器

管易网页能力位于内置运行时的 `browser/guanyi`，与订单转换模块分离。通过统一入口运行：

```bash
python3 <skill-dir>/scripts/guanyi_browser.py login
python3 <skill-dir>/scripts/guanyi_browser.py login-auto
python3 <skill-dir>/scripts/guanyi_browser.py login-auto-start
python3 <skill-dir>/scripts/guanyi_browser.py login-agent-start
python3 <skill-dir>/scripts/guanyi_browser.py login-credentials-ready "<login-operation-id>" "CONFIRM_TEMPORARY_CREDENTIALS_FILLED"
python3 <skill-dir>/scripts/guanyi_browser.py login-status "<login-operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py login-sms-request "<login-operation-id>" "CONFIRM_REQUEST_GUANYI_SMS"
python3 <skill-dir>/scripts/guanyi_browser.py login-sms-submit "<login-operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py login-auto-sms
python3 <skill-dir>/scripts/guanyi_browser.py probe
python3 <skill-dir>/scripts/guanyi_browser.py navigate-import
python3 <skill-dir>/scripts/guanyi_browser.py prepare-import-form
python3 <skill-dir>/scripts/guanyi_browser.py probe-task-center
python3 <skill-dir>/scripts/guanyi_browser.py probe-completed-imports
python3 <skill-dir>/scripts/guanyi_browser.py capture-task-baseline "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py inspect-file-selection "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py select-import-file "<operation-id>" "CONFIRM_FILE_SELECTION_MAY_UPLOAD"
python3 <skill-dir>/scripts/guanyi_browser.py submit-import "<operation-id>" "CONFIRM_GUANYI_IMPORT_SUBMISSION"
python3 <skill-dir>/scripts/guanyi_browser.py reconcile-operation "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py resolve-no-task "<operation-id>" "CONFIRM_CLOSE_NO_TASK_OPERATION"
python3 <skill-dir>/scripts/guanyi_browser.py verify-manifest "/path/to/上传预检.json"
python3 <skill-dir>/scripts/guanyi_browser.py prepare-operation "/path/to/上传预检.json"
```

浏览器几何、截图、暂态恢复和未来写入边界见 [references/browser-safety.md](references/browser-safety.md)。

- `login` 使用专用 Chrome 配置，由用户手动登录。
- `login-auto` 只读取安全凭证提供器：macOS Keychain、当前 Windows 用户的 DPAPI，或云端平台 Secret/Vault 提供的只读外部凭证助手。不得把凭证写入 Skill、源码、普通配置、环境变量、命令参数或日志。本地未配置时可运行 `credential-web` 产生一次性 `127.0.0.1` 页面。
- 云端没有安全凭证入口且用户明确选择便利性优先时，运行 `login-agent-start`。只有返回 `waiting_for_credentials` 后，才向用户说明密码会保留在平台对话历史；用户明确接受并提供本次账号密码后，Agent 只通过私有 GUI 填入当前管易登录页，再运行 `login-credentials-ready`。不得转存、复述、写入剪贴板脚本或跨任务复用。执行器只检查字段是否非空并继续现有登录状态机，不读取或保存字段内容。
- 登录后的持久会话优先于重复读取凭证；不勾选管易“记住账户”，不启用 Chrome 保存密码。
- 本地同一终端可以继续使用 `login-auto-sms`。云端或跨消息场景使用 `login-auto-start`；使用临时对话凭据时则从 `login-agent-start` 进入同一个 operation：出现短信验证时返回短时登录操作 ID 和 `needs_verification`，但不发送短信。只有用户当次明确确认可以接收后，才运行 `login-sms-request`；用户随后把本次验证码提供给 Agent，再通过 `login-sms-submit` 标准输入交给仍在运行的原登录进程。验证码只存在于进程内存，不写入凭证库、Skill、配置、命令参数、日志或处理报告。
- `probe` 检查登录和页面状态，只关闭已登记的安全通知。
- `navigate-import` 导航到“自定义导入”页面后停止，禁止选择文件。
- `prepare-import-form` 导航到“订单导入 → 自定义导入”，只允许把订单状态设为“待审核”并回读确认；文件输入必须保持为空，禁止点击“确定”。
- `probe-task-center` 只读导航到任务中心并返回标题、表头和可见行数，不打开任务行。未登录时返回 `needs_login`，不得自动触发短信。
- `probe-completed-imports` 只读取已完成“自定义订单导入”任务的记录 ID、序号、名称、时间和状态；任务 ID 必须通过页面行属性与 ExtJS 数据模型一致性校验。禁止读取操作人和操作列、打开任务行或点击结果下载；ID 证据不足时不得把上传操作标记为已核验成功。
- `capture-task-baseline` 只允许在 `prepared` 状态合并保存“执行中 + 已完成”两个页签经过核验的任务 ID 集合；文件选择开始后禁止重新建立或覆盖基线。上传后只有唯一新增且双重核验通过的任务 ID 才可与操作记录关联，多条新增任务必须停止并追问。
- `inspect-file-selection` 重新校验 manifest、Excel 哈希、操作状态和任务基线，进入待审核表单并唯一定位“订单信息上传”输入框；它不得选择文件。
- `select-import-file` 是受控写入边界。只有用户在当前对话中明确同意本次真实文件选择后才可运行，并必须传入精确授权口令 `CONFIRM_FILE_SELECTION_MAY_UPLOAD`。执行器在动作前写入 `file_selection_started`，文件输入变更尝试后立即进入 `write_request_maybe_sent`，仅记录不含查询串和请求正文的网络元数据；禁止点击“确定”、刷新、重新选文件或盲目重试。
- `submit-import` 已实现“选择文件 → 第一次确定 → 完整核对自定义字段对应 → 第二次确定”的同一写入边界。只有用户在当前对话明确授权当前批次时才可传入 `CONFIRM_GUANYI_IMPORT_SUBMISSION`。文件选择只允许出现已登记的管易取 OSS 地址和指定 OSS 暂存请求；第一次确定只允许调用已登记的模板读取接口；字段表必须遍历全部虚拟滚动行，系统字段与Excel字段逐行同名、一一对应且必填字段齐全；第二次确定只允许调用已登记的最终导入接口。任一证据缺失、重复、非成功或出现额外写请求都停止并先查任务中心。
- 正式 `guanyi_browser.py` 入口只有在 `GY-UPLOAD-SUBMISSION` 达到 `implemented + confirmed` 时才会为本次进程开启 Node 写入层；规则回退、实现引用失效或打包不一致都会结构性拒绝 `submit-import`。`GUANYI_CONTROLLED_WRITE_VALIDATION` 只保留给维护者未来重新验收，不参与普通生产流程，也永远不能代替当前批次的用户提交授权。
- `reconcile-operation` 只读查询任务中心。已经点击第二次确定但短时没有新增任务时保持 `reconciling`，不得重提；只有尚未进入最终提交且任务中心确认无新增任务时才可进入 `retryable_failed`。唯一新增任务的 ID、任务名称和“执行完成”状态只证明任务结束，必须继续打开“查看结果”，回读总数、成功数、格式错误数和非法数据数；只有全部成功才返回 `verified`，部分成功返回 `needs_manual_review`，全部失败返回 `retryable_failed`。多条新增、任务 ID 证据不足、统计矛盾或未知状态进入 `needs_manual_review`。
- Agent 必须把最终任务 ID、导入总数、成功数、失败分类和需要人工处理的来源行号返回给用户；不得返回或记录结果窗口中的收件人、手机号、详细地址或原始失败整行。未登记的失败文本统一脱敏为人工查看提示。
- 同一 Excel 哈希只有在已 `post_write_verified` 或仍有未闭环操作时阻止新操作。`retryable_failed` 和明确失败关闭后允许创建新操作；新记录必须关联 `retryOf`、递增 `attemptNumber`、重建任务基线并重新取得本次上传授权。
- 同一对话可以一次授权多个明确列出的已转换批次。Agent必须先回显 manifest/operation 清单，再按顺序逐批提交；每批独立绑定路径、SHA-256、操作 ID 和任务基线，前一批未 `verified` 时不得开始下一批。不得扫描目录猜测“最新文件”。
- `verify-manifest` 只校验预检 manifest 与 Excel 的路径、大小和 SHA-256；失败时停止。即使通过，也不得据此选择文件或上传。
- `prepare-operation` 在 manifest 通过后建立 `prepared` 操作 ID 和本地状态记录；它仍不代表上传授权，不得跳级到写入状态。
- 返回 `popup_unknown` 或 `navigation_failed` 时停止并报告，不使用坐标或盲目重试。
- 返回 `geometry_blocked` 时停止；不得在不稳定布局下继续点击，也不得用重复点击代替修复。
- 2026-10-14 已完成真实全量成功闭环验收：两段确定、48行字段映射核验、唯一新增任务识别和结果汇总回读均通过，任务 `705668612` 为总数2、成功2、失败0。生产使用只对当前对话中明确授权的具体批次开放；不要用其他临时浏览器步骤绕过门禁。审核、发货、作废和删除不在本模块能力范围内。

## 当前能力

- 公司库：当前登记8个公司身份，其中恬田、SAM、NDD、尚舆自营与荣之达具备可执行工作流；孚泽`FZ`、上海世茂国际广场`KLD`、美天`MT`仅完成身份登记，识别后仍会因缺少工作流而停止。转换入口可用公司ID、全称、简称或别名识别公司，并校验来源归属、工作流、路由、平台规则、共享策略引用和商品映射范围的一致性。公司简称只有被规则明确引用时才用于平台单号或买家会员前缀，不会仅因存在简称就自动改写输出。
- 恬田仓库订单：已支持“格式指纹 + 公司证据”识别、解析、路由和管易单模板转换。基础平台单号引用公司库简称`TT`并拼接订单编号优先值，订单编号为空时回退来源单号，公共平台层追加`A`。店铺与NDD、荣之达共同引用“已确认玉米商品→光明满元气”共享模块，非玉米、未映射商品或一单多店铺时追问。
- 荣之达文字订单：`RZD`作为公司证据；只有订单编号和来源单号都缺失时，才生成`RZD+上海时区整批生成分钟YYYYMMDDHHmm+最少两位弹性序号`，公共层追加`A`。`01…99`保持两位，`100`自动扩成三位。本机分钟占号记录使同一分钟第二批续号，并使同一来源重跑复用原号；当前按单电脑场景设计，不宣称处理多电脑并发。
- NDD礼品订单：公司最终模板v1.2（Roy版）位于`runtime/auto-shipped/assets/templates/ndd/`，首列为“订单编号”，不含“商品小计”；通过`Sheet1`固定示例标记自动识别。解析时忽略第2、3行示例，真实订单从后续行读取，并优先用每条订单的“套餐配套商品信息”校验组成。福利套餐四展开为黄糯8根装和白糯8根装各1件；基础平台单号为`NDD`+订单编号优先值，公共平台层追加`A`，整单按16根走中通重货。
- 玉米入仓订单：能识别，但用途不明时必须先追问。
- 百礼汇：路由已确认为“专用后台取单后导入管易”，后台连接器尚待样本和取单方式确认。
- 管易网页：Playwright 执行器已具备持久会话、安全凭证适配、跨消息短信验证交接、Geometry Guard、只读截图不变量、跨框架状态识别、已知通知处理、导入页导航、“待审核”表单准备、任务中心结构探针、上传前任务 ID 基线、文件选择、两段确定、字段映射核验、唯一新增任务识别和结果汇总回读。2026-10-14 已完成总数2、成功2、失败0的真实全量成功闭环验收；生产入口仍要求用户对明确批次的当次上传授权。
- 可迁移性：Skill 已内置确定性运行时、模板、规则、商品资料快照和 SHA-256 清单；新 Windows/macOS 电脑会自动选择目录和凭证提供器，但仍需安装依赖、重新配置本机凭证并建立登录会话。
- 上传前预检：转换成功时生成不含收件信息的 manifest，并由浏览器执行器复核 Excel 路径、大小和 SHA-256；manifest 不包含上传授权。
- 多文件合并：统一入口已将“合并为一个管易Excel”设为多文件默认行为；只有用户明确要求分开时才启用`--separate`。每份原始 Excel 先走完整单文件链路，全部通过后才按输入顺序合并；重复源文件、跨文件平台单号冲突、目标模板不一致或任一追问都会 fail-close，不产生部分成品。
- 文字/截图订单：V0.1已具备Agent提取合同、关键字段来源标记、脱敏预览、集中追问、用户确认门禁，以及标准化Excel和一次性计划生成。天猫/京东只证明渠道，不能单独证明“尚舆自营”；公司上下文不明确时先向用户确认。自营订单确认后按渠道选旗舰店；自营玉米`袋`匹配8棒家庭装，`根`尚未登记时追问；自营文本`京东中通`按京东旗舰店并把物流原样填写为`京东中通`，同时把姓名后的四位码追加到联系电话和联系手机，缺失或冲突时停止追问。荣之达`根`匹配彩袋单棒装。裸棒在普通发货中禁用。
- 白标商品：独立版本化SKU库已接入所有当前管易转换工作流；当前权威版本含131条精确商品代码+规格代码，并完整替换旧3条临时记录。库异常时停止，命中时复用统一卖家备注组合器，不影响来源识别和普通商品映射。
- 其他来源：只有在对应来源适配器、路由、规则和测试均存在时才允许自动转换。
