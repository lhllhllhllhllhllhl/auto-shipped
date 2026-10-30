---
name: shangyu-order-converter
description: 识别尚舆收到的公司原始订单Excel、聊天文字或截图订单；已知模板用确定性脚本处理，未知Excel或非结构化订单先经Agent提取和用户确认，再按商品映射与平台规则生成经过校验的导入文件。也用于管易登录、受控上传、结果核验和上传后独立授权的订单审核；缺失业务事实必须追问，未授权前不提交或审核，始终不自动发货。
---

# 尚舆订单转换

把员工提供的公司原始订单转换成经过校验的管易自定义订单导入文件。Skill 只负责调度；来源识别、解析、商品映射、平台规则、模板渲染和浏览器操作均由 Skill 内置运行时的确定性程序负责。开发时可用 `SHANGYU_AUTO_SHIPPED_ROOT` 显式覆盖内置运行时。

开发或维护规则前必须先读[系统模块治理目录](runtime/auto-shipped/docs/MODULE_GOVERNANCE.md)。它由机器目录生成，用于定位唯一权威模块；禁止在说明文字、临时脚本或其他模块中复制同一职责。

## 环境检查

首次在一台电脑使用，或运行时报告依赖缺失时，先执行：

```bash
python3 <skill-dir>/scripts/doctor.py
```

完整迁移和初始化规则见 [references/portability.md](references/portability.md)。只有用户要求配置新电脑或同意安装缺失依赖时，才运行 `bootstrap_runtime.py`。凭证和登录会话不随 Skill 迁移。

## 执行流程

1. 确认用户提供的是订单Excel，还是聊天文字/截图。Excel走现有识别链路；文字或截图按[文字与截图订单入口](references/text-order-intake.md)生成草稿，并默认用延后确认模式进入统一转换。路由优先级固定为“当前文件唯一命中的已登记固定模板/来源 > 用户在当前批次明确的固定流程 > 已登记公司的共享兜底 > 追问公司或必要事实”。固定模板证据只来自当前文件，优先于对话历史、上一批公司、上一批来源提示和自适应计划；这些批次级参数不得跨文件或跨轮复用。路由不唯一、关键字段缺失或原文拆分歧义仍立即追问；其余提取结果与商品候选统一放到最终待确认预览中一次确认。只要Agent已确认内容属于非Excel订单，且用户没有指定公司或规则包，草稿就默认使用`routing_mode=registered_manual_channels`，由脚本逐单匹配已登记来源规则；`fallback_enabled`公司可按简称匹配共享兜底，不要求用户额外说“这是手工订单”。一次收到多个来源时默认合并生成一个管易 Excel。只有用户明确说“分开”“分别输出”或同等意思时，才使用分开模式。批次编排仍会先逐来源独立识别、转换和预检，只有全部通过且目标平台与模板一致时才生成一个合并 Excel。
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

   统一入口第一次处理每个Excel时不得携带上一批的`source-profile-hint`、`company-hint`或`adaptive-plan-hint`，必须先让当前文件独立完成固定模板识别。只有当前文件未命中固定模板，且当前用户回答或本文件结构确实需要时，才为这个文件创建并传入与其SHA-256绑定的提示或`*_自适应来源计划.json`。固定模板唯一高置信命中时，运行时会忽略误传的自适应计划；误传其他来源提示则失败关闭，禁止套用旧对话规则。仍未命中时通过无敏感信息结构探针识别表头和列类型，并只返回真正缺失的业务问题。Agent根据用户回答更新该计划后，使用`--adaptive-plan-hint '<原始订单.xlsx>=<计划.json>'`重新运行。计划的数据合同见`runtime/auto-shipped/contracts/adaptive_source_plan.schema.json`。

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

   Agent在这条链路中的职责止于识别请求、整理未知Excel/文字/截图为标准输入，以及展示程序返回的追问或结果。商品映射、组合展开、数量、店铺、平台单号、会员、物流、白标、备注和管易默认字段一律由统一入口中的确定性模块生成，Agent不得自行拼装Excel或跳过某个判断。转换器会根据当前来源规则包自动生成本批内部必做目录：静态覆盖门禁先确认“应执行哪些规则”，运行门禁再要求每条规则覆盖全部适用订单或来源商品，保存后还要回读成品并逐格核对确定性业务结果，最后才执行平台预检。任一项缺失时返回结构化错误且不交付文件；内部门禁不增加普通用户确认。

6. 读取命令输出的 JSON，并严格按 `status` 处理：

   - `ready`、退出码 `0`：确认 `upload_manifest` 存在且状态为 `ready`，再准备 `outputs` 中的 Excel、manifest 和摘要，简要说明识别来源、来源配置、订单数和输出行数。店铺、物流和商品结果必须取自`workflow_execution.business_summary`，不得凭对话记忆另写或漏写；存在中通重货等非默认物流时必须在摘要中明确告知。若`mapping_identity_notices`非空，必须在成品摘要后追加非阻断的“编码映射名称说明”小节，逐项说明来源编码/条码已唯一命中哪个正式SKU，以及来源名称只是经规范化确认的等义写法；这是执行结果告知，不再询问确认。若`package_semantics_notices`非空，必须追加非阻断的“包装规则说明”小节，说明系统已经把哪些玉米表达按1件8棒家庭装处理；这是执行结果告知，不再询问确认。若`store_assignment_notices`非空，必须追加非阻断的“店铺兜底说明”小节，逐项说明哪些订单因“已映射非玉米、且未命中更高优先级店铺规则”已直接进入淘橘子；这同样不要再询问用户是否确认。若 `mapping_reuse_bundle.status=followup_available`，必须在同一次回复中追加一个非阻断的“新增映射复用”小节，按编号询问哪些继续仅本次、哪些以后沿用；商品名称别名按其 `future_scope_options` 询问当前来源或所有来源。回复先展示处理摘要和这些文字小节，最后再用“导出文件”小节放置 Excel 与 manifest 链接；文件已经生成，只是展示位置在文字小节下方。这些小节不延迟成品生成，也不授权持久化或上传；用户不回复时映射保持仅本次。若 `detection.schema_warnings` 非空，必须区分普通新列提醒与`STALE_ADAPTIVE_PLAN_IGNORED`等路由保护提示并如实告知，不能静默忽略。生成文件、转换请求和 manifest 都不是上传授权；只有用户查看结果后，在当前对话的新指令中明确指定“上传这批/这些批次”，才进入浏览器上传流程。
   - `preview_ready`、退出码 `0`：先确认待确认 Excel 已经生成且 `upload_manifest=null`。聊天回复先展示同一次运行的`decision_bundle`；若`mapping_identity_notices`非空，再单列非阻断的“编码映射名称说明”，只告知已安全接受的等义名称，不把它混入待确认编号；若`store_assignment_notices`非空，再单列非阻断的“店铺兜底说明”，只告知已使用的非玉米→淘橘子兜底。最后用“待确认 Excel”小节附上文件链接，不得因为链接后置而漏交文件。文字/截图的最终提取核对、路由与单号路径、唯一商品候选全部放进同一个编号区；员工在当前订单处理对话中可以使用已确认草稿展示用户本次提供的完整收件信息，`masked_order_preview`继续作为结果载荷和无法安全展示完整值时的降级摘要。不能先问后生成Excel。能沉淀的项目统一询问“仅本次使用，还是以后沿用”。接受“全部仅本次”“第1项以后沿用，其余仅本次”等自然语言回答。用户只说“全部确认”而没有说明复用范围时，先按仅本次完成本批；变成 `ready` 后仍必须展示 `mapping_reuse_bundle`，让用户决定是否把其中某些映射以后沿用。未明确选择 `future` 的项目一律不得写正式库。完成相应临时/正式登记后重新运行转换，只有变成 `ready` 才可交付上传 manifest。
   - `needs_input`、退出码 `2`：只向用户提出 `clarifications` 中真正阻断当前批次的问题。非阻断的商品候选、收件核对和复用选择延后到阻断解决后的统一预览，不得在本轮混入。不要生成占位文件，也不要把候选商品映射当成已确认映射。
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

静态规则覆盖通过后，转换服务还会在本批处理报告的`workflow_execution`中记录无收件隐私的运行证据。`status=complete`只由程序根据订单/商品覆盖、成品Excel回读和平台预检生成，Agent不能填写或替代；缺少运行校验器的新规则、漏掉任一适用订单/商品、落表值与程序结果不一致都会失败关闭。该证据只属于本批处理报告，不写回Skill、正式映射或共享业务规则，也不改变`preview_ready`、正式映射复用和上传授权边界。

详细状态含义见内置运行时的 `docs/BUSINESS_RULE_COVERAGE.md`。

## 不确定项

- 只有高置信度来源识别才继续；来源不明或多重匹配时追问。
- 已登记公司的未知Excel格式不得直接判为“不支持”。先生成一次性自适应字段计划；能从表头唯一确定的列直接映射，原表缺少的业务事实集中追问。自适应计划与当前文件SHA-256绑定，文件变化后必须重新分析。
- 文字或截图订单必须经过Agent提取草稿、确定性校验和最终一次用户确认。默认使用`text_order.py validate/prepare --defer-confirmation`，把普通核对并入最终`preview_ready`决策；关键字段缺失、`inferred`字段、路由不唯一或`uncertainties`仍在准备阶段阻断。原文明示字段标为`explicit`，用户确认字段标为`user_confirmed`。用户未指定公司或规则包时，文字订单默认按每笔订单唯一命中的已登记平台/业务标记选规则包；这只是已登记手工来源路由，不能扩展成任意平台到公司的猜测。未命中或多重命中只追问该笔来源，不重复询问规则包已有的店铺、普通买家会员和默认物流。最终待确认预览同时覆盖提取结果、路由结果和商品候选，并通过`order_number_plan`说明每笔采用订单编号、来源单号或已登记缺号生成规则；已有生成规则时不再反问用户补号或选择编号方式。不再增加“是否手工订单”的独立确认。收件人、电话、地址只做一次批次级核对，因为OCR或人工拆分错误会直接造成错发；员工在当前订单处理对话中可以查看用户本次提供的完整值，长期日志和共享产物仍保持脱敏。不得逐字段重复确认，也不得在后续重跑再次询问已经确认且内容未变的草稿。用户确认识别结果仍不能替代商品映射与业务规则门禁。
- 用户把订单文字、截图或Excel交给本Skill处理时，默认意图就是生成发货导入文件；文件标题中的“采购、入仓、备货”等词只描述来源版式，不得单独触发用途追问。只有用户明确要求分析非发货资料，或缺少收件人、联系方式、地址、商品、数量等真正必要事实时才停止。
- 商品映射必须精确且已确认后才可生成正式成品。名称相似只能作为候选；唯一候选还必须通过共享可信度门禁，名称、规格、关键语义属性与候选间距都足够时才可生成不可上传的待确认预览。候选虽唯一但可信度不足、来源明确属性在候选中缺失或冲突，以及多个候选，都必须立即追问。任何候选都禁止自动确认或自动写表。
- 用户正在回答一个商品映射待确认项时，后续给出的代码、条码、商品名称或规格名称默认属于“目标管易商品线索”，不得回写或替换原订单的来源商品字段。必须把上次转换结果保存为JSON，并运行`resolve_product_decision.py --decision-bundle <结果.json> --decision-id <D-...> --target-hint <用户原话>`；解析器会按规格代码、商品代码、条码、商品/规格名称和相似候选逐层查找有效目标目录。只有用户明确说“原订单/来源字段提取错了”时，才重新生成文字草稿或修正来源字段。
- 非结构化商品先保留`source_item_text`原文，并由Agent尽量把品牌、商品主体、规格、数量和单位拆入Schema，不允许模型直接决定管易代码。确定性商品短语规范化模块再用版本化词典生成`品牌+商品主体+规格`身份；它与Agent把词放进名称还是规格栏无关，但不会丢弃未知附加词。数量和单位保持独立，规范化与语义校验都不得改变数量。当前来源或全局正式名称映射的规范身份唯一相同时可直接命中；跨来源相同规范身份仍只是`preview_safe`候选，模糊或多目标仍追问。
- 商品代码、条码和来源专用规则按公司/来源隔离，并统一使用“来源商品标识+来源规格（可空）”结构；明确商品名称别名允许登记为全局正式别名。匹配顺序为当前来源专用正式映射→全局精确名称别名→其他来源完全同名候选→正式别名建立的目标商品系列内规格候选→普通相似名称候选。来源专用规则始终优先于全局别名。同一规范化来源名称至少有两个不同的飞书正式目标SKU、且目标代码共享不少于四位字母前缀时，才允许建立系列上下文；新颜色/软硬等规格必须在该系列中语义完整且唯一命中，结果仍为`preview_safe`待确认候选。一个旧映射、短代码前缀、多个系列或多个规格都不能建立安全系列。来源有规格时先匹配完整组合；SAM在父商品编码配对未知时允许使用同一结构中的规格单独映射。
- 代码/条码正式映射始终高于来源名称。代码唯一命中后，来源名称若只是词序、字段边界或已登记同义词不同，且规范商品主体与关键规格完全一致，直接生成`ready`成品，并通过`mapping_identity_notices`在“编码映射名称说明”小节告知，不得追问。名称明显指向另一类商品、颜色、规格或包装数量时，不改写来源代码、不改选目标，也不中途阻断出表：先生成无manifest的`preview_ready` Excel，再用`SOURCE_NAME_DIFFERS_FROM_CONFIRMED_CODE_MAPPING`集中要求本批确认。用户确认后用本批指纹覆盖消除提示；长期修改仍按否决已有正式映射流程转管理员。缺少名称不制造冲突。
- 来源提供规格时，规格是商品身份的一部分，只能命中完全相同的“来源标识+规格”正式映射，禁止降级到仅编码或仅名称映射。来源确实没有规格时，才允许命中经正式确认的无规格映射。
- 来源只有商品代码、而同名管易商品代码存在多个规格时，第一次必须列出管易商品/规格候选并让用户选择，同时询问“仅本次使用”还是“以后沿用”。候选、旧本机覆盖或模型推理都不能绕过确认。若正式库没有该映射键，用户明确“以后沿用”后可受控直接新增并同步正式快照；完全重复幂等，同键不同目标必须转管理员。仅本次确认只能创建`reuse_scope=current_batch`且与当前来源文件`sha256:`完全一致的临时新增映射。
- 管易目标规格可以留空，但只限该商品代码在当前有效目标目录中只有一个唯一规格（包括真正无规格的商品）；发布器会规范化为实际唯一组合。若同一管易商品代码有多个规格而目标规格为空，必须阻断并追问。
- 商品别名仍登记在同一正式映射表，不另建别名库。`identifier_type=product_name`可选择仅当前来源沿用，或在用户明确选择“以后所有来源沿用”后登记为全局别名；全局行固定使用`company_id=* / source_profile_id=*`。商品代码、条码、套餐简称、数量单位等不得全局化。其他来源完全同名且只指向一个目标时只能生成无manifest待确认预览；已由至少两个正式SKU证明的稳定代码系列，可为相同来源名称的新规格生成系列内唯一候选。相似名称与系列候选都必须输出评分、候选间距和关键语义属性证据；只有唯一且达到`preview_safe`的候选才能进入预览，低可信候选即使只有一个也阻断。短英文片段不得因包含关系被提升为高相似候选。该模块没有自动写表或生成可上传成品的权限。
- 文字玉米的`根/袋/盒/份/套`必须经过正式包装语义库。全局已确认`1袋=8根装=8根家庭装=8棒=1件8棒家庭装`：包装表达中的数字8是每件内含根数，不是订单数量；没有额外`×N`时数量填1，绝不能拆成8件单棒。转换直接执行，并通过`package_semantics_notices`在结果“包装规则说明”小节告知，不再阻断确认。`根`的全局候选仍是彩袋单棒装，但某公司第一次出现且尚未登记时必须追问；RZD已登记`根→彩袋单棒装`。来源专用规则优先于全局包装规则。三种裸棒属于试吃商品，普通发货一律阻断，不能靠名称或编码绕过。
- 用户确认新映射时优先在预览决策区一次问清“仅本次使用”还是“以后沿用”。若用户只确认正确但未说明复用范围，先按仅本次生成成品，并在成功结果的`mapping_reuse_bundle`中再次以非阻断小节列出；不得因为转换已经`ready`就遗忘复用问题。明确以后沿用且正式库不存在该键时，运行`feishu_mapping.py add ... --confirmation-token CONFIRM_ADD_NEW_OFFICIAL_MAPPING`；命令只有返回`created/already_exists`且正式快照同步成功，才可声称未来复用。冲突、替换、删除、合并或公司身份变更不得直接写，转待确认/反馈和管理员处理。
- 用户已经明确“以后沿用”而受控正式新增因CLI身份、权限、网络或接口错误失败时，不得再次询问复用范围，也不得把临时覆盖描述成正式登记成功。目标商品已经明确且本批指纹可用时，可为当前批次保存临时映射并继续生成预览；同时必须返回正式写入的准确错误代码与待补录状态。网关恢复后才重试同一个幂等正式新增操作。
- 用户为“正式库尚无映射”的候选选择仅本次使用时，使用`save_product_mapping.py`显式指定本次临时`--overlay`、`--reuse-scope current_batch`、当前来源`--source-fingerprint sha256:<64位哈希>`及口令`CONFIRM_USE_PRODUCT_MAPPING_FOR_CURRENT_BATCH`，随后仅在本次重跑中传入该`--mapping-overlay`。文字/截图来源使用计划中的稳定订单事实指纹`order_source_sha256`，而不是标准化XLSX的ZIP字节哈希；相同订单内容重生成文件时临时映射仍有效，订单内容改变时自动失效。
- 用户否决“已经存在的飞书正式映射”时，不得把新目标写成普通临时新增映射或覆盖正式表。先取得用户对当前批次替换的明确确认，再运行`save_batch_mapping_replacement.py`，传入当前来源文件、公司/来源键、预期旧正式目标、新目标、当前飞书用户ID和口令`CONFIRM_REPLACE_OFFICIAL_MAPPING_FOR_CURRENT_BATCH`。脚本把记录写入本机私有状态，转换入口会自动读取；只有该来源文件SHA-256和旧正式目标都一致时生效，其他批次自动忽略。随后经用户同意提交纠错反馈，并明确提示联系管理员完成长期修正。
- 公司可以用公司ID、全称、已登记简称或别名识别。固定工作流始终优先；公司库显式标记为`fallback_enabled`时，文字/截图可走共享手工单规则，未知Excel可生成一次性自适应字段计划。未登记公司、未启用兜底公司或缺少必要事实仍须停止；一次性计划不得自动提升为永久模板。
- 用户在对话中对某个候选做一次性确认，不等于授权永久修改公司规则或商品主档。需要持久化时明确说明并单独更新版本化配置。
- 用户否决系统结果或人工修改成品时，先保证当前批次正确。用户同意留存后，运行`feishu_mapping.py submit-feedback ... --confirmation-token CONFIRM_SUBMIT_CORRECTION_FEEDBACK`写入独立反馈箱；没有成功回读`feedback_id`不得说已登记。反馈箱不参与转换；缺失键的完整商品映射可按受控直接新增进入正式库，已有键的变更仍须管理员处理。详细流程见[用户纠错反馈治理](runtime/auto-shipped/docs/CORRECTION_FEEDBACK_GOVERNANCE.md)。
- 发现SOP新增或遗漏规则时，先把它登记到规则目录和对应来源规则包；禁止只在模板渲染器中追加条件分支。
- 新规则先判断是跨公司共享还是公司特有。共享规则写入`runtime/auto-shipped/config/platform_rules/guanyi/policy_modules_v1.json`并由公司规则包引用；公司特有规则才留在对应规则包。详细边界见[规则模块结构](runtime/auto-shipped/docs/RULE_MODULE_STRUCTURE.md)。同一个规则目标不得同时内联和引用。
- 店铺顺序固定为：固定/渠道规则 > 当前公司或来源的SKU关系 > `manual_all`关系 > 商品类别兜底。最后一级把已映射玉米分配到`光明满元气`、非玉米分配到`淘橘子`；非玉米先出表，再通过`store_assignment_notices`告知，不增加确认。共享的`generic_company_manual_v1`不作为店铺作用域，避免多家公司相同SKU互相污染；自营天猫/京东、SAM等更高优先级规则不受覆盖。
- 来源备注先经过跨公司共享备注分流模块：送货上门、不准放门口、驿站/快递柜、派送联系等明确给快递员的指令追加到收货地址末尾；其余备注才进入卖家备注。文字入口能明确提取时优先写入`source_extensions.delivery_instruction`；不得在公司规则包或临时脚本重复维护关键词。
- 普通来源买家会员填写`张`。SAM有跟团号时使用`sam+商品类型缩写+上海时区当日YYMMDD+_+跟团号`；无跟团号时使用`sam+商品类型缩写+YYMMDD+_+最少三位弹性序号`。新序号通过飞书中央日序号台账占号，并按`用途+完整前缀+业务日期`隔离；同一来源重跑复用原日期和原区间。当前`YM=玉米`、`SH=SHOWA/尚和/尚合手套`。SHOWA手套先按来源品牌和商品主体语义归类，来源不完整时只允许已确认的商品代码+规格代码对兜底；商品类型未登记、代码型新SKU缺少可靠来源语义或一单包含多个类型时仍必须追问。商品映射与SAM类型归类是两个阶段，正式映射成功不等于可以猜测未登记类型。
- 组合商品展开与普通商品映射分离。一条来源商品可能对应多个目标商品时，Agent可以根据原文和有效商品目录提出组成候选，但必须让用户确认每个目标商品、规格、数量倍数以及仅本次还是以后沿用；未确认不得出表，也不得把多个目标写成普通一对一正式映射。“仅本次”运行`save_item_expansion.py`，记录只能在来源文件SHA-256完全一致时生效；“以后沿用”运行`feishu_mapping.py add-expansion ... --confirmation-token CONFIRM_ADD_ITEM_EXPANSION_RULE`，写入飞书正式工作簿独立的“组合商品规则”Sheet。完全重复幂等，同一来源键组成不同必须转管理员。SAM白糯、花糯、黄糯8+1已迁入该中央规则表，来源规则包只保留执行策略，不再复制组成明细。
- 公司从旧专用文字入口迁移到共享兜底时，只有公司库`generic_fallback.mapping_source_profile_aliases`显式列出的旧入口允许复用该公司已有的一对一映射和组合展开；当前入口始终优先，多个兼容入口指向不同目标时必须停止确认。禁止按相似公司名或全局扫描旧入口自动继承。KLD共享兜底显式兼容`kld_text_v1`，因此既有9根装拆分规则继续有效。
- 白标判断只在商品映射完成后执行，按内置版本化白标库中的商品代码+规格代码精确匹配；任一商品命中时整单卖家备注追加“白标商品”。禁止按名称相似度判断。当前权威库由`商品白标.xlsx`的131条商品规格完整替换生成，旧3条临时记录不再生效；增删SKU只修改白标库，不得把名单复制到来源解析器或公司规则分支。

## 输出与权限边界

- 当前输出是“管易自定义订单导入 Excel”，不是已经上传或审核的订单。
- 多文件批次成功时只交付一个合并后的管易 Excel、一个对应预检 manifest 和一个不含收件信息的处理报告；单文件暂存结果只存在于临时目录，不作为交付物。
- 转换结束不得自动登录管易或选择文件。只有用户查看转换结果后另行明确指定上传批次，才使用独立浏览器执行模块；审核、作废和发货始终禁止。
- 员工在当前订单处理对话中核对本批订单时，可以展示用户本次提供的完整姓名、手机号和地址；处理报告、manifest、GitHub、纠错反馈和其他长期日志仍不得保存这些完整值。不得把“当前对话可见”扩大成跨任务留存或对外共享。
- 不修改原始 Excel，不在 Skill 中保存账号、密码或会话。公司业务规则只能保存在内置运行时的版本化配置中，不得散落到Skill说明文字、临时脚本或模板渲染器中。
- 内置运行时命令缺模块、版本不一致或doctor失败时，必须修复/重新打包运行时或停止并报告；不得为了继续当前订单而改`PATH`、直接导入仓库`src`、手写低层CLI调用或凭对话记忆重建公司/来源参数。这样做会绕开固定模板、正式映射和安全门禁。

## 商品映射库与多用户边界

- Skill 内置版本化来源匹配政策和来源身份保护，但不内置自动生效的一对一商品目标映射。
- 飞书正式映射表是员工日常转换的权威映射源；转换前必须同步正式快照。转换入口不再自动加载本机旧映射或环境变量共享映射。
- 一次性调试覆盖只能由维护者显式传入，不得作为员工正常入口的隐式状态，也不得声称已同步给其他用户。
- 保存命令只登记商品标识、公司来源、管易商品代码和规格代码，不写入订单、收件人或地址信息。
- 飞书共享映射采用两个工作簿：正式工作簿中的“正式映射”Sheet保存来源别名到管易商品/规格，“手工单商品店铺”Sheet保存来源作用域与目标SKU到店铺的关系；普通用户只能通过受控命令新增不存在且无冲突的记录。待确认映射工作簿按当前飞书用户ID路由到个人Sheet，主要承接冲突和管理员例外。待确认工作簿不是权限安全边界，任何提案必须经所有者发布后才能进入正式映射。
- 飞书侧优先使用Agent原生电子表格能力，`lark-cli`仅作备用。原生读取结果必须通过`sync-native`确定性校验后才能成为转换覆盖层，禁止让模型直接把表格文本当作已确认映射。待确认写入必须获取稳定的用户身份；共享机器人身份不能用于个人Sheet路由。
- 当前飞书工作簿、受控直接新增、待确认网关、正式快照和所有者发布器均已接通。纯新增使用`feishu_mapping.py add`，完全重复跳过、同键不同目标阻断，写入后必须回读并同步快照。待确认提案仍不会自动生效；只有所有者明确要求发布时才运行`plan-publish`和带确认口令的`publish`。
- 处理飞书映射提案或接入工作簿时，先读 [飞书映射治理](runtime/auto-shipped/docs/FEISHU_MAPPING_GOVERNANCE.md)。
- 开发维护线程回收用户纠错时，运行`python3 <skill-dir>/scripts/feishu_mapping.py collect-feedback --status new`只读汇总；反馈不得直接改写正式映射或业务规则。
- 飞书只读健康检查运行`python3 <skill-dir>/scripts/feishu_mapping.py status`；它验证当前用户身份、正式库版本、路由和个人Sheet数量，不写表格。
- `mapping_revision`是正式库唯一读指针；`publish_operation_id`只锁后续正式写入，不锁订单转换。新增记录先写为下一版本`staged`，版本指针切换前读取器必须忽略，切换后可读取并继续完成状态封口。发现非空写锁或旧版遗留`repository_status=publishing`时，转换仍同步当前已提交版本，不得误报“正式映射不可用”；只暂停新的长期映射写入，并运行`feishu_mapping.py diagnose-recovery`。只有诊断可恢复且当前用户为owner时才可使用固定确认口令运行`recover`，不得手工清锁或改版本。
- Python侧飞书CLI调用统一通过共享客户端执行和解析；商品映射、中央日序号等业务网关不得各自实现身份探针。共享客户端优先通过`lark-cli auth status --verify`取得用户身份；旧版CLI缺少`auth`子命令时，依次尝试`lark-cli whoami --as user`和用户身份的`/open-apis/authen/v1/user_info`只读接口取得稳定`open_id`。随后业务网关必须对自己的目标工作簿执行真实只读校验。Agent不得临时创建CLI垫片或改写`PATH`绕过身份探针。仅有dry-run中的`identity=user`、但没有稳定`open_id`时仍失败关闭，不能参与正式写入或新占号。
- 每次开始依赖商品映射的转换前必须完成当次正式库读取。统一入口默认自动运行`python3 <skill-dir>/scripts/feishu_mapping.py sync`；若Agent已使用原生飞书能力读取，则运行`sync-native`并把返回快照显式传给`--official-mapping-snapshot`。读取会校验库状态、版本、哈希、映射键和商品主档并原子更新本机快照；失败时停止转换，不得静默使用旧快照或待确认提案。
- 正式映射表不按公司另建结构，也不另设“规格编码类型”：每行都有来源商品标识与来源规格两栏，至少填写一个；没有规格就把来源规格留空。`product_name`行额外由程序维护`canonical_source_brand / canonical_source_product / canonical_source_spec / canonical_schema_version`，规范商品主体必填，品牌和规格按来源事实可空；代码/条码行以代码为身份，未提供可靠来源名称时四个规范字段允许全空，禁止从管易目标名称反推来源身份。现有15列正式表可继续只读同步并在快照中派生规范字段；19列表迁移必须先运行`plan-canonical`检查冲突，再由所有者明确确认运行`migrate-canonical`。完整组合仍优先于规格单独映射。
- 每个转换结果的`mapping_evidence`只保存本批不含收件信息的映射证据：商品原文、初始拆分、规范身份、正式映射ID、目标SKU、正式库版本和快照哈希。它不写回规则库或飞书，也不随Skill发布累积。
- 来源指定物流仍优先，但必须先通过来源规则包的别名表转换为管易标准名称；未登记名称必须追问。不得把任意来源文本直接写入“物流公司”。
- 来源联系方式必须是11位大陆手机号、带平台隐私号尾缀的手机号，或已支持的座机格式；隐私号尾缀必须完整保留。尚舆自营京东中通还要读取姓名后的四位码并由规则层输出`号码-四位码`，姓名缺码或与地址/已有尾码冲突时追问。超过15位的数值型订单号或科学计数法订单号可能已经损失精度，必须要求用户将来源列改为文本后重新导出。
- 管易输出中的平台单号继续使用Excel文本类型和`@`格式。27位纯数字平台单号已于2026-10-05完成一次真实手工上传并由用户确认成功；这只证明输出文本格式兼容，不允许放宽对来源数值精度的阻断。
- 平台单号的订单身份统一按`订单编号 > 来源单号`选择；两者都没有时才执行来源规则包中已确认的中央日序号规则。新规则格式为`来源前缀+业务日期YYYYMMDD+最少三位弹性序号`，次日从001重新开始；同一来源跨日重跑仍复用原号。订单分组、去重和平台单号必须共用同一结果。公司前缀由来源规则包负责；自2026-10-15起全局不再追加机器人尾缀`A`，预检也不再要求该尾缀。
- 店铺优先级统一由店铺分配模块执行：固定店铺或渠道店铺 > 当前公司/来源的精确SKU—店铺关系 > `manual_all`关系 > 商品类别兜底。最后一级中，已映射玉米进入`光明满元气`，已映射非玉米进入`淘橘子`；非玉米兜底不中途追问，先出表并通过`store_assignment_notices`在结果小节告知。同一订单最终跨两个店铺时仍停止追问。SAM固定店铺和自营天猫/京东渠道店铺属于更高优先级，不受类别兜底覆盖。
- JZDZ和DRJY在员工交互中按与公司并列的手工业务来源识别，底层归属`self_operated`。完成商品映射后，店铺先读取正式快照中的`manual_store_routes`：具体来源作用域优先，未登记时回退`manual_all`，再进入上述商品类别兜底。用户明确“以后沿用”后，纯新增关系可运行`python3 <skill-dir>/scripts/feishu_mapping.py add-store-route ... --confirmation-token CONFIRM_ADD_MANUAL_STORE_ROUTE`受控写入；同键换店必须联系管理员。
- 首次使用可运行`python3 <skill-dir>/scripts/feishu_mapping.py provision --operation-id <稳定操作ID>`。该命令按当前`feishu_user_id`创建或复用个人Sheet，并对路由与owner元数据做写后回读。
- 只有用户明确确认“该映射以后沿用”后，才允许持久化。正式库缺少该键时运行`python3 <skill-dir>/scripts/feishu_mapping.py add ... --confirmation-token CONFIRM_ADD_NEW_OFFICIAL_MAPPING`；冲突或需要替换时不得直接写，提交例外提案/纠错并提示联系管理员。不得把确认口令用于用户未授权的候选。

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
python3 <skill-dir>/scripts/guanyi_browser.py login-credentials-submit "<login-operation-id>" "CONFIRM_TEMPORARY_CREDENTIALS_SUBMISSION"
python3 <skill-dir>/scripts/guanyi_browser.py login-status "<login-operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py login-sms-request "<login-operation-id>" "CONFIRM_REQUEST_GUANYI_SMS"
python3 <skill-dir>/scripts/guanyi_browser.py login-sms-submit "<login-operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py login-auto-sms
python3 <skill-dir>/scripts/guanyi_browser.py resident-start
python3 <skill-dir>/scripts/guanyi_browser.py resident-status
python3 <skill-dir>/scripts/guanyi_browser.py resident-probe
python3 <skill-dir>/scripts/guanyi_browser.py resident-execute-import "<operation-id>" "CONFIRM_GUANYI_IMPORT_SUBMISSION"
python3 <skill-dir>/scripts/guanyi_browser.py resident-reconcile "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py probe
python3 <skill-dir>/scripts/guanyi_browser.py navigate-import
python3 <skill-dir>/scripts/guanyi_browser.py prepare-import-form
python3 <skill-dir>/scripts/guanyi_browser.py probe-task-center
python3 <skill-dir>/scripts/guanyi_browser.py probe-completed-imports
python3 <skill-dir>/scripts/guanyi_browser.py capture-task-baseline "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py inspect-file-selection "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py select-import-file "<operation-id>" "CONFIRM_FILE_SELECTION_MAY_UPLOAD"
python3 <skill-dir>/scripts/guanyi_browser.py submit-import "<operation-id>" "CONFIRM_GUANYI_IMPORT_SUBMISSION"
python3 <skill-dir>/scripts/guanyi_browser.py execute-import "<operation-id>" "CONFIRM_GUANYI_IMPORT_SUBMISSION"
python3 <skill-dir>/scripts/guanyi_browser.py reconcile-operation "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py resolve-no-task "<operation-id>" "CONFIRM_CLOSE_NO_TASK_OPERATION"
python3 <skill-dir>/scripts/guanyi_browser.py probe-order-audit
python3 <skill-dir>/scripts/guanyi_browser.py prepare-order-audit "<verified-import-operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py prepare-order-audit "<verified-import-operation-id>" current_batch
python3 <skill-dir>/scripts/guanyi_browser.py execute-order-audit "<audit-operation-id>" "CONFIRM_GUANYI_ORDER_AUDIT"
python3 <skill-dir>/scripts/guanyi_browser.py reconcile-order-audit "<audit-operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py verify-manifest "/path/to/上传预检.json"
python3 <skill-dir>/scripts/guanyi_browser.py prepare-operation "/path/to/上传预检.json"
python3 <skill-dir>/scripts/guanyi_browser.py verify-manifest "/current/path/上传预检.json" "/current/path/管易自定义订单导入.xlsx"
python3 <skill-dir>/scripts/guanyi_browser.py prepare-operation "/current/path/上传预检.json" "/current/path/管易自定义订单导入.xlsx"
python3 <skill-dir>/scripts/guanyi_remote.py doctor
python3 <skill-dir>/scripts/guanyi_remote.py stage "/current/path/上传预检.json" "/current/path/管易自定义订单导入.xlsx" "CONFIRM_REMOTE_BATCH_STAGING"
python3 <skill-dir>/scripts/guanyi_remote.py status "<remote-operation-id>"
python3 <skill-dir>/scripts/guanyi_remote.py prepare "<remote-operation-id>"
python3 <skill-dir>/scripts/guanyi_remote.py probe
python3 <skill-dir>/scripts/guanyi_remote.py login-start
python3 <skill-dir>/scripts/guanyi_remote.py login-status "<login-operation-id>"
python3 <skill-dir>/scripts/guanyi_remote.py login-credentials "<login-operation-id>" "CONFIRM_TEMPORARY_CREDENTIALS_SUBMISSION"
python3 <skill-dir>/scripts/guanyi_remote.py login-sms-request "<login-operation-id>" "CONFIRM_REQUEST_GUANYI_SMS"
python3 <skill-dir>/scripts/guanyi_remote.py login-sms-submit "<login-operation-id>"
python3 <skill-dir>/scripts/guanyi_remote.py execute "<remote-operation-id>" "CONFIRM_GUANYI_IMPORT_SUBMISSION"
python3 <skill-dir>/scripts/guanyi_remote.py reconcile "<remote-operation-id>"
python3 <skill-dir>/scripts/guanyi_remote.py resolve-no-task "<remote-operation-id>" "CONFIRM_CLOSE_NO_TASK_OPERATION"
python3 <skill-dir>/scripts/guanyi_remote.py prepare-retry "<remote-operation-id>"
```

浏览器几何、截图、暂态恢复和未来写入边界见 [references/browser-safety.md](references/browser-safety.md)。

- `login` 使用专用 Chrome 配置，由用户手动登录。
- `login-auto` 只读取安全凭证提供器：macOS Keychain、当前 Windows 用户的 DPAPI，或云端平台 Secret/Vault 提供的只读外部凭证助手。不得把凭证写入 Skill、源码、普通配置、环境变量、命令参数或日志。本地未配置时可运行 `credential-web` 产生一次性 `127.0.0.1` 页面。
- 云端没有安全凭证入口且用户明确选择便利性优先时，运行 `login-agent-start`。只有返回 `waiting_for_credentials` 后，才向用户说明密码会保留在平台对话历史；用户明确接受并提供本次账号密码后，优先运行`login-credentials-submit`，把单行账号密码JSON通过私有stdin交给同一控制通道。平台没有私有stdin时才打开operation返回的一次性`127.0.0.1`页面。不得转存、复述、写入临时脚本、文件、环境变量、剪贴板脚本或命令参数，也不得跨任务复用。
- 登录会话只在当前浏览器进程内可靠复用；持久profile可以降低重复登录次数，但每次仍以页面状态回读为准。不勾选管易“记住账户”，不启用 Chrome 保存密码。
- 本地同一终端可以继续使用 `login-auto-sms`。云端或跨消息场景使用 `login-auto-start`；使用临时对话凭据时则从 `login-agent-start` 进入同一个 operation：出现短信验证时返回短时登录操作 ID 和 `needs_verification`，但不发送短信。只有用户当次明确确认可以接收后，才运行 `login-sms-request`；用户随后把本次验证码提供给 Agent，优先通过`login-sms-submit`的私有stdin交给原登录进程，平台没有该能力时才使用一次性`smsInputUrl`页面。验证码只存在于进程内存，不写入凭证库、Skill、配置、命令参数、日志或处理报告。
- 浏览器业务核心只有一套，Playwright负责统一控制DOM和状态机；接入层只决定连接哪个浏览器。macOS/Windows使用项目持久profile，可信Linux平台可连接唯一的本机CDP context。连接异常时失败关闭，禁止在同一次操作中静默另启浏览器。
- 豆包工作云电脑必须先阅读并执行[豆包平台浏览器接入](references/doubao-browser-takeover.md)。它是豆包登录顺序、人工验证和故障恢复的唯一操作指引；本文件不再重复这些平台细节。
- 登录结果只接受结构化状态：`code_submitted`是等待态；`authenticated`后仍须只读`probe`得到`home_ready`。未知页面、未知弹窗、连接中断或结果不明确都停止，禁止重发短信、重启登录、清理profile或关闭平台默认context。
- 同一浏览器会话任意时刻只允许一个执行器所有者。登录worker运行期间只能查询同一`login-status`，不得另启`probe`、导航或第二个登录；冲突必须返回`browser_session_busy`。
- 自管服务器使用`resident-start`建立唯一长期浏览器owner。返回`resident_ready`后，远端`probe`、`execute`、`reconcile`和`resolve-no-task`都必须进入该worker的FIFO队列；任务结束不得关闭context。该owner在同一context内保持一个专用上传页和一个只读任务中心页，不得启动第二个浏览器或第二个写入页。worker或服务器重启后可重新建立会话，但任何已进入可能写入状态的批次仍只能核验，不能重提。
- `probe` 检查登录和页面状态，只关闭已登记的安全通知。
- `navigate-import` 导航到“自定义导入”页面后停止，禁止选择文件。
- `prepare-import-form` 导航到“订单导入 → 自定义导入”，只允许把订单状态设为“待审核”并回读确认；文件输入必须保持为空，禁止点击“确定”。
- `probe-task-center` 只读导航到任务中心并返回标题、表头和可见行数，不打开任务行。未登录时返回 `needs_login`，不得自动触发短信。
- `probe-completed-imports` 只读取已完成“自定义订单导入”任务的记录 ID、序号、名称、时间和状态；任务 ID 必须通过页面行属性与 ExtJS 数据模型一致性校验。禁止读取操作人和操作列、打开任务行或点击结果下载；ID 证据不足时不得把上传操作标记为已核验成功。
- `capture-task-baseline` 只允许在 `prepared` 状态合并保存“执行中 + 已完成”两个页签经过核验的任务 ID 集合；文件选择开始后禁止重新建立或覆盖基线。上传后只有唯一新增且双重核验通过的任务 ID 才可与操作记录关联，多条新增任务必须停止并追问。
- `inspect-file-selection` 重新校验 manifest、Excel 哈希、操作状态和任务基线，进入待审核表单并唯一定位“订单信息上传”输入框；它不得选择文件。
- `select-import-file` 是受控写入边界。只有用户在当前对话中明确同意本次真实文件选择后才可运行，并必须传入精确授权口令 `CONFIRM_FILE_SELECTION_MAY_UPLOAD`。执行器在动作前写入 `file_selection_started`，文件输入变更尝试后立即进入 `write_request_maybe_sent`，仅记录不含查询串和请求正文的网络元数据；禁止点击“确定”、刷新、重新选文件或盲目重试。
- `submit-import` 已实现“选择文件 → 第一次确定 → 完整核对自定义字段对应 → 第二次确定”的同一写入边界。只有用户在当前对话明确授权当前批次时才可传入 `CONFIRM_GUANYI_IMPORT_SUBMISSION`。文件选择只允许出现已登记的管易取 OSS 地址和指定 OSS 暂存请求；第一次确定只允许调用已登记的模板读取接口；字段表必须遍历全部虚拟滚动行，系统字段与Excel字段逐行同名、一一对应且必填字段齐全；第二次确定只允许调用已登记的最终导入接口。任一证据缺失、重复、非成功或出现额外写请求都停止并先查任务中心。
- 正常生产上传优先使用 `execute-import`：它只在用户已经确认当前批次后启动。会话不可用时先在当前进程读取安全凭证并自动登录，再完成待审核表单和受控提交；同一context内的只读任务中心页负责建立基线并最长等待45秒识别唯一任务ID或最终结果。过程中不退出登录、不另启浏览器；若登录要求短信验证，只返回 `needs_verification`，不发送短信、不选择文件，Agent先走标准短信交接再重新执行当前已授权批次。若已绑定唯一任务ID但任务仍在执行，返回`task_id_observed`并允许后续不同批次进入FIFO写入队列；若仍未绑定唯一任务则保持`reconciling`，退出后只能运行只读`reconcile-operation`，不得重新提交。
- 上传结果必须到达`post_write_verified`后，才能运行`prepare-order-audit`建立独立审核操作。当前上传授权绝不自动转成审核授权；用户须在上传成功后另行明确批准，执行时传入`CONFIRM_GUANYI_ORDER_AUDIT`。默认范围是`all_pending`（审核当前全部待审核订单），只有用户明确要求“只审核本批”时才使用`current_batch`，并由manifest携带的平台单号清单限制范围。策略保留`combined_import_and_audit`切换口，但当前`combined_mode_enabled=false`，不得用一次上传授权连续审核。
- `probe-order-audit`只读定位订单查询中的审核列、平台单号列、筛选框、全选框和审核按钮；不得筛选、勾选或点击审核。本地与豆包环境必须分别完成只读探针并确认真实待审核状态词后，`execute-order-audit`生产门禁才可启用。`reconcile-order-audit`只能回读剩余待审核数量，不得勾选订单或重提审核。发货、作废和删除不在审核模块能力范围内。
- 正式 `guanyi_browser.py` 入口只有在 `GY-UPLOAD-SUBMISSION` 达到 `implemented + confirmed` 时才会为本次进程开启 Node 写入层；规则回退、实现引用失效或打包不一致都会结构性拒绝 `submit-import` 和 `execute-import`。`GUANYI_CONTROLLED_WRITE_VALIDATION` 只保留给维护者未来重新验收，不参与普通生产流程，也永远不能代替当前批次的用户提交授权。
- `reconcile-operation` 只读查询任务中心。已经点击第二次确定但短时没有新增任务时保持 `reconciling`，不得重提；`needs_manual_review`也允许再次只读核验，以便页面证据更新后重新判断，但仍不得再次提交。从最终请求记录时间起满5分钟仍无唯一新增任务时进入`quarantined`：原批次永久禁止自动重传并保留只读续查，但允许后续不同批次用全新基线继续。只有尚未进入最终提交且任务中心确认无新增任务时才可进入 `retryable_failed`。唯一新增且未被其他operation认领的任务 ID、任务名称和“执行完成”状态只证明任务结束，必须继续打开“查看结果”，回读总数、成功数、格式错误数和非法数据数；结果总数还必须等于manifest订单数，才可继续判定。只有全部成功才返回 `verified`，部分成功或数量不符返回 `needs_manual_review`，全部失败返回 `retryable_failed`。多条新增、任务 ID 证据不足、统计矛盾或未知状态进入 `needs_manual_review`。
- `resolve-no-task`只处理“文件已暂存或前置页面失败、最终导入请求明确未发出”的尝试。它必须在同一常驻浏览器中重新读取任务中心，并以精确口令`CONFIRM_CLOSE_NO_TASK_OPERATION`确认基线后没有新增任务；若最终请求已经发出或任务ID证据不足，必须拒绝关闭。出现多条新增任务时同样不得关闭或重传原批次；只有全部候选任务ID已经在任务中心可见并进入`tasks_observed_ambiguous`后，才可用下一批的新基线继续后续不同批次。
- Agent 必须把最终任务 ID、导入总数、成功数、失败分类和需要人工处理的来源行号返回给用户；不得返回或记录结果窗口中的收件人、手机号、详细地址或原始失败整行。未登记的失败文本统一脱敏为人工查看提示。
- 同一 Excel 哈希只有在已 `post_write_verified` 或仍有未闭环操作时阻止新操作。`retryable_failed` 和明确失败关闭后允许创建新操作；新记录必须关联 `retryOf`、递增 `attemptNumber`、重建任务基线并重新取得本次上传授权。
- 同一对话可以一次授权多个明确列出的已转换批次。Agent必须先回显manifest/operation清单，再由同一浏览器owner按FIFO逐批写入；每批独立绑定路径、SHA-256、操作ID和任务基线，相邻提交默认至少间隔60秒。上传页只负责待审核表单和两段提交，同一context内的任务中心页只读刷新并统一回读。前一批绑定唯一任务ID后即返回`task_id_observed`并允许后续不同批次继续，不必等待任务完成；任务结果由只读页继续轮询，完成后仍须回读总数、成功数和失败分类才能进入`verified`。前一尝试只有在可信失败闭环后才可用`prepare-retry`创建新browser operation、重建基线并重新取得上传授权，最多安全重试2次；两次仍失败则`deferred`到队尾。尚未看见任何可信任务的`reconciling`或未知写入结果仍为`block_upload_queue`；若任务中心已经看见多条新增任务，则当前批次进入`tasks_observed_ambiguous`，保持人工复核且永久禁止重传，但这些任务会进入下一批的新基线，因此允许后续不同批次继续。已有唯一任务ID的执行中或需人工查看结果同样不阻塞。满5分钟仍无唯一任务时进入`quarantined`并只放行后续不同批次，原批次不得重传。已被其他operation认领的任务ID不得重复认领；不得扫描目录猜测“最新文件”。
- `verify-manifest` 校验预检 manifest 与当前 Excel 的文件名、大小和 SHA-256；失败时停止。同一环境可只传manifest，跨电脑或附件被平台复制到新路径时，必须把用户明确交付的当前Excel路径作为第二个参数传入。不得改写或重建原manifest、伪造原路径、扫描目录或猜测“最新文件”。即使通过，也不得据此选择文件或上传。
- `prepare-operation` 在 manifest 通过后建立 `prepared` 操作 ID 和本地状态记录；记录同时保留manifest中的原始文件位置和当前环境经过哈希核验的绑定路径。它仍不代表上传授权，不得跳级到写入状态。
- 专用服务器执行采用[管易远端执行适配层](runtime/auto-shipped/docs/GUANYI_REMOTE_EXECUTOR.md)。用户明确要求上传指定批次后，Agent才可用`guanyi_remote.py stage`和精确口令`CONFIRM_REMOTE_BATCH_STAGING`把该Excel与manifest送往已配置执行端；随后`prepare`只建立操作，不打开浏览器。`login-start`启动或复用唯一常驻worker：优先从服务器root私有加密凭据helper读取账号密码，helper未配置时才走`stdin临时凭据 → 当次短信确认 → stdin验证码`。只有用户在当前对话明确授权该远端operation对应的具体批次，才可运行`execute ... CONFIRM_GUANYI_IMPORT_SUBMISSION`。远端层只做传输、授权、FIFO排队、60秒提交节流和调度；登录、导航、待审核表单、任务基线、文件选择、字段核对、提交与任务中心回读全部复用现有`browser/guanyi`状态机，禁止第二套实现。进入可能已写入状态后先`reconcile`；`task_id_observed`或结构化`retryPolicy.queueDisposition=continue_other_batches`允许继续后续不同批次，`retry_same_batch`才可`prepare-retry`，`defer_to_tail`移到队尾，`block_upload_queue`必须停止。放行后续批次不授权重传原批次。远端不开放审核、发货、作废或删除。
- 返回 `popup_unknown` 或 `navigation_failed` 时停止并报告，不使用坐标或盲目重试。
- 返回 `geometry_blocked` 时停止；不得在不稳定布局下继续点击，也不得用重复点击代替修复。
- 自有 Linux headed Chromium 的原生垂直滚动条可以占用稳定的页面宽度；Geometry Guard只在三次采样一致、无水平滚动、缩放正常且client/visual viewport差值互相一致时把不超过24px的差值识别为系统滚动条。豆包`platform_cdp`浏览器的窗口由平台托管，不要求匹配自有浏览器的`1440×850`目标；最大化窗口只有在三次采样稳定、有效页面区域达到最小尺寸且缩放正常时才放行。宽高两个原生滚动条沟槽还须由两方向document/visual证据分别一致证明且各不超过24px；执行器不得修改平台窗口，也不得全局放宽几何容差。
- 正式Skill入口只接受与`runtime-manifest.json`一致的内置/状态运行时，或相关浏览器与Skill入口文件没有未提交修改的Git开发工作区；通过`SHANGYU_AUTO_SHIPPED_ROOT`指向的普通复制目录同样必须通过清单校验。修改后的临时副本不得登录、选择文件或提交。
- 2026-10-14 已完成真实上传闭环验收：两段确定、48行字段映射核验、唯一新增任务识别和结果汇总回读均通过，任务 `705668612` 为总数2、成功2、失败0。生产上传只对当前对话中明确授权的具体批次开放。订单审核已建立独立模块和二次授权边界，但在本地与豆包只读探针均完成前真实审核仍关闭；发货、作废和删除始终不在本模块能力范围内。

## 当前能力

- 公司库：当前登记9个公司/来源身份，其中恬田、SAM、NDD、尚舆自营、荣之达、百礼汇以及孚泽标签式商品表具备固定工作流；孚泽`FZ`还保留未知格式兜底，上海世茂国际广场`KLD`、美天`MT`已启用受控共享兜底。转换入口可用公司ID、全称、简称或别名识别公司，并校验来源归属、固定工作流/兜底绑定、路由、平台规则、共享策略引用和商品映射范围的一致性。固定模板始终优先；公司简称只有被规则明确引用时才用于平台单号或买家会员前缀，不会仅因存在简称就自动改写输出。
- 恬田仓库订单：已支持“格式指纹 + 公司证据”识别、解析、路由和管易单模板转换。平台单号引用公司库简称`TT`并拼接订单编号优先值，订单编号为空时回退来源单号，不追加公共尾缀。店铺与NDD、荣之达共同引用商品类别兜底：玉米进入光明满元气，非玉米进入淘橘子并在结果小节告知；未映射商品或一单多店铺时追问。
- 荣之达文字订单：`RZD`作为公司证据；只有订单编号和来源单号都缺失时，才生成`RZD+YYYYMMDD+最少三位弹性序号`，不追加公共尾缀。飞书中央台账按日连续占号，`001…999`保持三位，`1000`自然扩位；同一来源重跑复用原号。飞书不可用时只能复用本机已回读确认的旧占号，新批次必须停止，禁止离线从001起号。
- NDD礼品订单：公司最终模板v1.2（Roy版）位于`runtime/auto-shipped/assets/templates/ndd/`，首列为“订单编号”，不含“商品小计”；通过`Sheet1`固定示例标记自动识别。解析时忽略第2、3行示例，真实订单从后续行读取，并优先用每条订单的“套餐配套商品信息”校验组成。福利套餐四展开为黄糯8根装和白糯8根装各1件；平台单号为`NDD`+订单编号优先值，不追加公共尾缀，整单按16根走中通重货。
- 孚泽标签式玉米商品表：即使标题写“采购订单”或文件名写“入仓”，只要页尾存在收货地址和联系人，就按一张待发货订单解析商品表与页尾收件信息；缺少来源单号时使用`FZ+YYYYMMDD+飞书中央日序号`。字段缺失才追问，不再询问“采购还是发货”。
- 百礼汇：已支持供应商后台固定Excel`代发订单信息`。只处理`代发采购/待发货`，采购单号直接作为平台单号，店铺固定百礼汇，`SKU/69码`分别按管易商品代码/规格代码在有效目录中精确校验；冲突或不存在时停止，不按相似名称猜测。转换只生成管易Excel、manifest和摘要；后台取数、管易上传与物流回填仍是独立授权模块。
- 管易网页：Playwright 执行器已具备持久会话、安全凭证适配、跨消息短信验证交接、登记验证页的同浏览器人工接管、Geometry Guard、只读截图不变量、跨框架状态识别、已知通知处理、导入页导航、“待审核”表单准备、任务中心结构探针、上传前任务 ID 基线、文件选择、两段确定、字段映射核验、唯一新增任务识别和结果汇总回读。2026-10-14 已完成总数2、成功2、失败0的真实全量成功闭环验收；生产入口仍要求用户对明确批次的当次上传授权。
- 可迁移性：Skill 已内置确定性运行时、模板、规则、商品资料快照和 SHA-256 清单；新 Windows/macOS 电脑会自动选择目录和凭证提供器，但仍需安装依赖、重新配置本机凭证并建立登录会话。云端只读Skill副本经清单校验后会完整复制到私有可写状态目录，并在副本上恢复当前用户写权限再安装依赖。
- 上传前预检：转换成功时生成不含收件信息的 manifest，并由浏览器执行器复核当前 Excel 的文件名、大小和 SHA-256；跨环境时显式绑定当前路径并保留原始路径证据，manifest 不包含上传授权。
- 多文件合并：统一入口已将“合并为一个管易Excel”设为多文件默认行为；只有用户明确要求分开时才启用`--separate`。每份原始 Excel 先走完整单文件链路，全部通过后才按输入顺序合并；重复源文件、跨文件平台单号冲突、目标模板不一致或任一追问都会 fail-close，不产生部分成品。
- 文字/截图订单：V0.1已具备Agent提取合同、关键字段来源标记、脱敏预览、集中追问、用户确认门禁，以及标准化Excel和一次性计划生成。天猫/京东只证明渠道，不能单独证明“尚舆自营”；公司上下文不明确时先向用户确认。自营订单确认后按渠道选旗舰店；自营玉米`袋`匹配8棒家庭装，`根`尚未登记时追问；自营文本`京东中通`按京东旗舰店并把物流原样填写为`京东中通`，同时把姓名后的四位码追加到联系电话和联系手机，缺失或冲突时停止追问。荣之达`根`匹配彩袋单棒装。裸棒在普通发货中禁用。
- 白标商品：独立版本化SKU库已接入所有当前管易转换工作流；当前权威版本含131条精确商品代码+规格代码，并完整替换旧3条临时记录。库异常时停止，命中时复用统一卖家备注组合器，不影响来源识别和普通商品映射。
- 其他来源：只有在对应来源适配器、路由、规则和测试均存在时才允许自动转换。
