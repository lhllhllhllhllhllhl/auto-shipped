---
name: shangyu-order-converter
description: 识别尚舆收到的公司原始订单文件；已知模板用确定性脚本处理，用户明确公司时可自适应解析未知Excel，再按商品映射和平台规则生成经过校验的导入文件。也用于管易登录、预检和只读导入页导航；缺失业务事实必须追问，未授权前不提交、审核或发货。
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

1. 确认用户提供了订单 Excel。一个文件直接转换；一次收到多个文件时默认合并生成一个管易 Excel。只有用户明确说“分开”“分别输出”或同等意思时，才使用分开模式。批次编排仍会先逐文件独立识别、转换和预检，只有全部通过且目标平台与模板一致时才生成一个合并 Excel。
2. 默认使用系统登记的管易商品资料。员工日常只需提交公司原始订单；只有系统商品资料缺失、不可读或用户明确提供新版资料时才要求补充 CSV。
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

   用户明确提供新版商品资料时，额外传入 `--catalog <商品信息.csv>`，只覆盖本次运行，不自动替换系统配置。

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

   - `ready`、退出码 `0`：确认 `upload_manifest` 存在且状态为 `ready`，再返回 `outputs` 中的 Excel，并简要说明识别来源、订单数和输出行数。若 `detection.schema_warnings` 非空，必须同时提醒用户来源Excel出现了未登记新列，不能静默忽略。manifest 不是上传授权。
   - `needs_input`、退出码 `2`：向用户提出 `clarifications` 中的问题。不要生成占位文件，也不要把候选商品映射当成已确认映射。
   - `failed` 或退出码 `1`：说明具体错误和可执行的补救步骤，不把失败结果描述为可上传文件。

   批次入口还执行以下整体门禁：内容完全相同的重复文件直接阻断；不同文件生成相同平台单号时阻断；任一文件返回追问时整批不出 Excel；任一文件预检或哈希校验失败时整批失败。不得拿已经通过的子文件另行拼成半批次。

转换服务在商品映射和模板生成前强制执行 `conversion` 业务规则覆盖门禁。`BUSINESS_RULE_*` 追问表示适用SOP规则缺失、待确认、待实现或实现引用不可核验；不得用临时默认值绕过。需要审计当前规则状态时运行：

```bash
python3 <skill-dir>/scripts/audit_rules.py --phase conversion
python3 <skill-dir>/scripts/audit_rules.py --phase upload
```

来源识别后还必须通过公司库校验。公司库只索引稳定公司ID、来源、路由、平台规则和商品映射范围，不复制业务规则。需要检查公司记录和模块引用时运行：

```bash
python3 <skill-dir>/scripts/audit_companies.py
```

返回非 `ready` 时不得转换或上传，也不得临时绕过公司登记。

详细状态含义见内置运行时的 `docs/BUSINESS_RULE_COVERAGE.md`。

## 不确定项

- 只有高置信度来源识别才继续；来源不明或多重匹配时追问。
- 已登记公司的未知Excel格式不得直接判为“不支持”。先生成一次性自适应字段计划；能从表头唯一确定的列直接映射，原表缺少的业务事实集中追问。自适应计划与当前文件SHA-256绑定，文件变化后必须重新分析。
- 文件用途不明时，先问批次用途，再决定是否请求收件信息。
- 商品映射必须精确且已确认。名称相似只能作为候选，禁止自动确认。
- 商品匹配按公司隔离。已确认来源编码、已确认名称别名，以及标准化后对管易商品资料的唯一精确名称匹配可以确定性通过；模糊名称、多个规格或多个候选必须追问。
- 用户确认新映射后，先问清是“仅本次使用”还是“以后沿用”。只有用户明确要求以后沿用，才运行飞书待确认提案命令；待确认提案不会参与转换，必须由所有者发布到正式映射表后再同步。
- 未登记的新公司仍应追问并先进入公司库；已登记公司的未知模板允许使用一次性自适应字段计划，但不得把一次性计划自动提升为永久模板。
- 用户在对话中对某个候选做一次性确认，不等于授权永久修改公司规则或商品主档。需要持久化时明确说明并单独更新版本化配置。
- 发现SOP新增或遗漏规则时，先把它登记到规则目录和对应来源规则包；禁止只在模板渲染器中追加条件分支。
- 普通来源买家会员填写`张`。SAM买家会员使用`sam+商品类型缩写+上海时区当日YYMMDD+_+跟团号`；当前`YM=玉米`、`SH=尚和手套`。缺跟团号、商品类型未登记或一单包含多个类型时必须追问，禁止猜测缩写。
- 组合商品展开与普通商品映射分离。SAM白糯8+1来源项先按已确认规则展开为8根装和单根装，再分别校验管易商品主档；不得把同一来源编码的多个目标商品写成普通一对一映射冲突。

## 输出与权限边界

- 当前输出是“管易自定义订单导入 Excel”，不是已经上传或审核的订单。
- 多文件批次成功时只交付一个合并后的管易 Excel、一个对应预检 manifest 和一个不含收件信息的处理报告；单文件暂存结果只存在于临时目录，不作为交付物。
- 不自动登录管易，不点击上传、审核、作废或发货。用户另行明确要求这些操作时，再使用独立执行模块和对应授权。
- 不在聊天、日志或处理摘要中展示完整姓名、手机号和详细地址。向用户交付生成文件即可。
- 不修改原始 Excel，不在 Skill 文件中保存账号、密码、会话或公司业务规则。

## 商品映射库与多用户边界

- Skill 内置版本化来源匹配政策和来源身份保护，但不内置自动生效的一对一商品目标映射。
- 飞书正式映射表是员工日常转换的权威映射源；转换前必须同步正式快照。转换入口不再自动加载本机旧映射或环境变量共享映射。
- 一次性调试覆盖只能由维护者显式传入，不得作为员工正常入口的隐式状态，也不得声称已同步给其他用户。
- 保存命令只登记商品标识、公司来源、管易商品代码和规格代码，不写入订单、收件人或地址信息。
- 飞书共享映射采用两个工作簿：正式映射只读消费，待确认映射按当前飞书用户ID自动路由到个人Sheet。待确认工作簿不是权限安全边界，任何提案必须经所有者发布后才能进入正式映射。
- 飞书侧优先使用Agent原生电子表格能力，`lark-cli`仅作备用。原生读取结果必须通过`sync-native`确定性校验后才能成为转换覆盖层，禁止让模型直接把表格文本当作已确认映射。待确认写入必须获取稳定的用户身份；共享机器人身份不能用于个人Sheet路由。
- 当前飞书工作簿已初始化并绑定固定Sheet ID，`lark-cli`待确认网关和正式映射快照已可用；运行状态为`shared_mapping_runtime_ready_publisher_pending`。正式库可同步并由转换入口自动叠加，待确认提案仍不会生效；所有者审核发布器接入前，不得把待确认内容说成已正式发布。
- 处理飞书映射提案或接入工作簿时，先读 [飞书映射治理](runtime/auto-shipped/docs/FEISHU_MAPPING_GOVERNANCE.md)。
- 飞书只读健康检查运行`python3 <skill-dir>/scripts/feishu_mapping.py status`；它验证当前用户身份、正式库版本、路由和个人Sheet数量，不写表格。
- 每次开始依赖商品映射的转换前必须完成当次正式库读取。统一入口默认自动运行`python3 <skill-dir>/scripts/feishu_mapping.py sync`；若Agent已使用原生飞书能力读取，则运行`sync-native`并把返回快照显式传给`--official-mapping-snapshot`。读取会校验库状态、版本、哈希、映射键和商品主档并原子更新本机快照；失败时停止转换，不得静默使用旧快照或待确认提案。
- 无来源规格的编码映射必须通过来源配置中的身份保护；当前恬田`2026DFYUMI001`还必须匹配已确认来源品名。编码相同但品名缺失或变化时必须追问。
- 来源指定物流仍优先，但必须先通过来源规则包的别名表转换为管易标准名称；未登记名称必须追问。不得把任意来源文本直接写入“物流公司”。
- 来源联系方式必须是11位大陆手机号或已支持的座机格式。超过15位的数值型订单号或科学计数法订单号可能已经损失精度，必须要求用户将来源列改为文本后重新导出。
- 管易输出中的平台单号继续使用Excel文本类型和`@`格式。27位纯数字平台单号已于2026-10-05完成一次真实手工上传并由用户确认成功；这只证明输出文本格式兼容，不允许放宽对来源数值精度的阻断。
- 所有由本系统生成的管易平台单号必须在来源基础单号末尾统一追加`A`，用于区分人工订单。公司前缀、复制或日期序号由各来源规则包负责；`A`由管易公共平台配置负责，新增来源不得重复实现。上传预检必须阻断缺少`A`的系统成品。
- 首次使用可运行`python3 <skill-dir>/scripts/feishu_mapping.py provision --operation-id <稳定操作ID>`。该命令按当前`feishu_user_id`创建或复用个人Sheet，并对路由与owner元数据做写后回读。
- 只有用户明确确认“该映射以后沿用”后，才运行`python3 <skill-dir>/scripts/feishu_mapping.py submit ... --confirmation-token CONFIRM_SUBMIT_PENDING_MAPPING`。提交只进入待确认库，不会自动成为正式映射；不得把确认口令用于用户未授权的候选。

## 管易浏览器执行器

管易网页能力位于内置运行时的 `browser/guanyi`，与订单转换模块分离。通过统一入口运行：

```bash
python3 <skill-dir>/scripts/guanyi_browser.py login
python3 <skill-dir>/scripts/guanyi_browser.py login-auto
python3 <skill-dir>/scripts/guanyi_browser.py login-auto-sms
python3 <skill-dir>/scripts/guanyi_browser.py probe
python3 <skill-dir>/scripts/guanyi_browser.py navigate-import
python3 <skill-dir>/scripts/guanyi_browser.py prepare-import-form
python3 <skill-dir>/scripts/guanyi_browser.py probe-task-center
python3 <skill-dir>/scripts/guanyi_browser.py probe-completed-imports
python3 <skill-dir>/scripts/guanyi_browser.py capture-task-baseline "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py inspect-file-selection "<operation-id>"
python3 <skill-dir>/scripts/guanyi_browser.py select-import-file "<operation-id>" "CONFIRM_FILE_SELECTION_MAY_UPLOAD"
python3 <skill-dir>/scripts/guanyi_browser.py verify-manifest "/path/to/上传预检.json"
python3 <skill-dir>/scripts/guanyi_browser.py prepare-operation "/path/to/上传预检.json"
```

浏览器几何、截图、暂态恢复和未来写入边界见 [references/browser-safety.md](references/browser-safety.md)。

- `login` 使用专用 Chrome 配置，由用户手动登录。
- `login-auto` 只允许读取系统安全凭证提供器：macOS Keychain 或当前 Windows 用户的 DPAPI 加密存储。不得把凭证写入 Skill、源码、普通配置、命令或日志。未配置时运行 `credential-web` 产生的一次性 `127.0.0.1` 页面；不要从聊天内容代写。
- 登录后的持久会话优先于重复读取凭证；不勾选管易“记住账户”，不启用 Chrome 保存密码。
- 出现短信验证时先输出 `needs_verification`，询问用户当前是否可以接收短信。只有用户当次明确确认后，才运行 `login-auto-sms` 点击“获取验证码”。管易随后出现“验证码将发送到手机号码为…”提示时，执行器只点击该已知弹窗的“知道了”，关闭后再等待用户验证码。用户把本次验证码提供给 Agent 后，通过该进程的标准输入提交；验证码只用于本次登录，不写入钥匙串、Skill、配置、命令输出或处理报告。
- `probe` 检查登录和页面状态，只关闭已登记的安全通知。
- `navigate-import` 导航到“自定义导入”页面后停止，禁止选择文件。
- `prepare-import-form` 导航到“订单导入 → 自定义导入”，只允许把订单状态设为“待审核”并回读确认；文件输入必须保持为空，禁止点击“确定”。
- `probe-task-center` 只读导航到任务中心并返回标题、表头和可见行数，不打开任务行。未登录时返回 `needs_login`，不得自动触发短信。
- `probe-completed-imports` 只读取已完成“自定义订单导入”任务的记录 ID、序号、名称、时间和状态；任务 ID 必须通过页面行属性与 ExtJS 数据模型一致性校验。禁止读取操作人和操作列、打开任务行或点击结果下载；ID 证据不足时不得把上传操作标记为已核验成功。
- `capture-task-baseline` 只允许在 `prepared` 状态合并保存“执行中 + 已完成”两个页签经过核验的任务 ID 集合；文件选择开始后禁止重新建立或覆盖基线。上传后只有唯一新增且双重核验通过的任务 ID 才可与操作记录关联，多条新增任务必须停止并追问。
- `inspect-file-selection` 重新校验 manifest、Excel 哈希、操作状态和任务基线，进入待审核表单并唯一定位“订单信息上传”输入框；它不得选择文件。
- `select-import-file` 是受控写入边界。只有用户在当前对话中明确同意本次真实文件选择后才可运行，并必须传入精确授权口令 `CONFIRM_FILE_SELECTION_MAY_UPLOAD`。执行器在动作前写入 `file_selection_started`，文件输入变更尝试后立即进入 `write_request_maybe_sent`，仅记录不含查询串和请求正文的网络元数据；禁止点击“确定”、刷新、重新选文件或盲目重试。
- `verify-manifest` 只校验预检 manifest 与 Excel 的路径、大小和 SHA-256；失败时停止。即使通过，也不得据此选择文件或上传。
- `prepare-operation` 在 manifest 通过后建立 `prepared` 操作 ID 和本地状态记录；它仍不代表上传授权，不得跳级到写入状态。
- 返回 `popup_unknown` 或 `navigation_failed` 时停止并报告，不使用坐标或盲目重试。
- 返回 `geometry_blocked` 时停止；不得在不稳定布局下继续点击，也不得用重复点击代替修复。
- 当前阶段已实现带当次授权门禁的文件选择，但尚未启用“确定”、审核或发货命令；不要用其他临时浏览器步骤绕过此边界。

## 当前能力

- 公司库：已建立独立公司注册库并登记恬田、SAM与NDD，转换入口会校验来源归属、公司工作流、路由、平台规则和商品映射范围的一致性。一个公司可登记多个来源格式；未登记公司或引用断裂时停止并报告。SAM当前可高置信度识别、解析和路由；白糯8+1组合展开、`YM/SH`商品类型和买家会员格式已实现，价格与支付金额固定为0、运费留空、支付方式固定为网银在线、订单类型固定为销售订单，核心样本已可生成Excel供员工验收；自动上传仍关闭。
- 恬田仓库订单：已支持“格式指纹 + 公司证据”识别、显式来源提示、解析、路由和管易单模板转换。当前登记的自动公司证据是“渠道名称”精确等于`GW_12233787_代发仓-恬田`；只有通用仓库表头而没有该证据时必须追问。基础平台单号使用`TT`+来源单号，公共平台层追加`A`；买家会员固定填写`张`，只有手机号码时同时填写“联系电话”和“联系手机”。店铺不再按恬田来源固定：当前仅对已确认商品`JTW8E1 / 6974768564811`精确分配“光明满元气”，未命中或一单多店铺时必须追问。商品映射按公司隔离，当前编码还受来源品名保护；模糊名称、品名变化或多个候选仍追问。订单创建时间使用原始下单时间，商品名称和规格名称使用管易商品资料标准值，省、市、区及现有显式默认字段继续输出。物流先标准化来源指定名称，再执行“来源指定优先；8棒家庭装合计达到2件或玉米10根/10棒礼盒走中通重货，其余韵达”，以后可独立替换为重量公式。
- NDD礼品订单：员工明确说明来源为NDD后，后台导出模板继续走`ndd_order_v1`脚本快线；其他NDD Excel进入自适应通道。福利套餐四仍按已确认规则展开为黄糯8根装和白糯8根装各1件，基础平台单号为`NDD`+来源订单号，公共平台层追加`A`，整单按16根走中通重货。原表没有商品、数量或来源单号时必须集中追问，确认后才可写入批次默认值或日期顺序号。
- 玉米入仓订单：能识别，但用途不明时必须先追问。
- 百礼汇：路由已确认为“专用后台取单后导入管易”，后台连接器尚待样本和取单方式确认。
- 管易网页：Playwright 只读执行器已具备持久会话、系统安全凭证、短信验证交接、Geometry Guard、只读截图不变量、跨框架状态识别、已知通知处理、导入页导航、“待审核”表单准备、任务中心结构探针、上传前任务 ID 基线、唯一新增任务匹配和文件输入框就绪检查；导入页、任务中心与文件输入框定位均已完成真实只读验收。普通 Chrome 只用于人工参考和只读探查，不作为正式执行环境。
- 可迁移性：Skill 已内置确定性运行时、模板、规则、商品资料快照和 SHA-256 清单；新 Windows/macOS 电脑会自动选择目录和凭证提供器，但仍需安装依赖、重新配置本机凭证并建立登录会话。
- 上传前预检：转换成功时生成不含收件信息的 manifest，并由浏览器执行器复核 Excel 路径、大小和 SHA-256；manifest 不包含上传授权。
- 多文件合并：统一入口已将“合并为一个管易Excel”设为多文件默认行为；只有用户明确要求分开时才启用`--separate`。每份原始 Excel 先走完整单文件链路，全部通过后才按输入顺序合并；重复源文件、跨文件平台单号冲突、目标模板不一致或任一追问都会 fail-close，不产生部分成品。
- 其他来源：只有在对应来源适配器、路由、规则和测试均存在时才允许自动转换。
