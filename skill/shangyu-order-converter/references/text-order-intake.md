# 文字与截图订单入口

当员工直接发送聊天文字、截图或复制的地址单，而不是Excel时，使用“提取→确定性校验→最终预览集中确认”的入口。普通核对不再单独打断一次；文字识别仍不能绕过公司、商品、物流或平台规则。

## Agent提取

1. 从文字或截图按订单分段。截图可以由Agent视觉能力读取；不要把OCR结果当作已确认事实。
2. 生成符合`runtime/auto-shipped/contracts/text_order_draft.schema.json`的本次临时JSON。草稿可以包含收件信息，但只能保存在本次输出目录，不得进入Skill、规则文档、日志或聊天正文。
3. 对以下关键字段标注`field_provenance`：平台/渠道、来源单号、收件人、联系方式、地址、商品及数量。原文明示使用`explicit`，用户回答确认后使用`user_confirmed`；模型推断使用`inferred`，缺失使用`missing`。
4. 任何真正的拆分歧义都放进`uncertainties`，但只能填写Schema登记的`code`、`field`和`reason`，不得由Agent撰写`question`。程序只会从受控代码生成追问。正式商品映射是否缺失由转换层在同步飞书正式映射后判断，不得在草稿中以品牌、型号、是否在售等问题代替映射查询。典型歧义包括：一串数字无法确定是手机号还是订单号、未确认公司时无法判断“京东中通”的业务含义、公司尚未登记根/袋的包装含义、同一段里出现多个地址。公司已确认是尚舆自营时，“京东中通”已登记为`source_channel=京东`且`source_carrier=京东中通`，不再追问。
5. 商品短语必须原样保留在必填字段`source_item_text`，并尽量拆成品牌、商品主体、规格、数量和单位。例如“kikulon超软黄色搓澡巾1条”可写为`source_brand=kikulon`、`source_product_name=搓澡巾`、`source_spec=超软黄色`、`quantity=1`、`unit=条`；即使Agent拆成`source_product_name=kikulon搓澡巾`、`source_spec=超软黄色`，确定性规范化模块也会生成相同的`kikulon / 搓澡巾 / 黄色|超软`身份。`滑溜 m号3双`会由确定性入口规范为`滑溜 / M / 3 / 双`，不再追问品牌或型号。“条”“双”是单位，不是规格；数量和单位不会进入商品身份。Agent不得直接填写管易代码；未知附加词会保留在规范规格中，不能因忽略它而自动命中旧商品。
6. 玉米文字如“黄糯2根”“花糯1袋”应拆成品类、订单数量和原始包装表达：商品名称分别为“黄糯”“花糯”，数量分别为2和1，`source_spec`与`unit`分别填写“根”或“袋”。“黄糯8根装”“花糯8根家庭装”“白糯8棒”中的数字8是每件包装内含根数；如果原文没有另写`×N`、`N袋`等订单数量，`quantity`必须填1，并把“8根装/8根家庭装/8棒”保留在`source_spec`或`source_item_text`，禁止提取成数量8。不要由Agent直接选择SKU。后续正式包装语义库会把RZD的`根`解析为彩袋单棒装，并把任意来源的`1袋/8根装/8根家庭装/8棒`默认解析为1件8棒家庭装；该默认只在结果小节告知，不再追问。其他未登记包装表达仍追问一次。裸棒普通发货禁用。
7. 形如`13800138000-3940`的联系方式是平台隐私号整体。完整写入`recipient.contact`，不得截断尾部数字；脱敏预览仍只显示掩码。
8. 用户确认属于尚舆自营后，天猫和京东分别写入`source_channel=天猫`或`京东`，消息中的平台单号写入`source_order_no`。店铺由后续规则包处理，Agent不要在草稿里拼接店铺名或改写平台单号。若自营京东订单明确写`京东中通`，必须保留收货人姓名末尾的四位码；可同时把提取值写入`source_extensions.jd_zto_contact_suffix`用于交叉核验，但不能用该字段代替姓名原文。地址末尾也有四位码时必须一致；缺失或冲突时写入`uncertainties`并追问，不得自行补数。转换层会把联系方式输出为`原号码-四位码`。
9. `RZD`是荣之达公司证据。没有来源单号时不要让Agent自行拼号，也不要在草稿中填`order_number_policy`；来源配置会通过飞书中央台账生成`RZD+YYYYMMDD+最少三位日序号`，管易公共层保留该基础单号，不追加尾缀。不同日期分别计数、同一来源重跑复用原号段。RZD的`根`表示彩袋单棒装，数量按根数原样写入；`袋`等未登记表达继续追问。玉米商品通过共享店铺模块进入“光明满元气”。
10. Agent确认内容属于非Excel订单、用户又没有指定公司或规则包时，只生成一个草稿，`company_id=null`、`rule_source_profile_id=null`、`routing_mode=registered_manual_channels`、`routing_authority=deterministic_router`，并且不得填写`order_number_policy`。每笔订单仍各自填写原文明示的`source_channel`。脚本会逐笔匹配已登记来源配置，按公司和规则包分组，并返回不含隐私的`routing_evidence`；随后统一批次入口默认合并为一个管易Excel。Agent即使抢填公司或规则包，普通草稿也会被自动路由覆盖。只有用户在当前对话明确指定公司或固定流程时，才可写`routing_mode=single_profile`与`routing_authority=user_explicit`；若渠道与用户指定规则冲突，程序只返回一个`TEXT_ORDER_PROFILE_CHANNEL_MISMATCH`来源确认，不继续追问商品、店铺或平台单号。
    - 路由优先匹配固定来源配置；固定来源不命中时，只有公司库中`fallback_enabled`公司的简称、全称或别名才能命中共享`generic_company_manual_v1`。
    - 例如`KLD`缺少来源单号时，由共享来源配置生成`KLD+YYYYMMDD+最少三位飞书中央日序号`。不要为KLD复制一份来源配置或在草稿中手工拼号。
    - 店铺优先级为固定/渠道规则、当前公司或来源的精确SKU关系、`manual_all`关系、商品类别兜底。前面均未命中时，已完成映射的玉米进入“光明满元气”，非玉米直接进入“淘橘子”；非玉米不中途追问，生成Excel后读取`store_assignment_notices`，只在“店铺兜底说明”小节告知用户。同一订单若因此跨两个店铺仍停止追问。SAM固定店铺与自营天猫/京东渠道店铺不受该兜底覆盖。
11. 送货上门、不准放门口、驿站/快递柜、派送联系等明确给快递员的配送指令，优先写入`source_extensions.delivery_instruction`，保留原文，不拼进地址。确定性转换层会统一追加到收货地址末尾并去重；其余来源备注继续写`source_note`。同一段同时包含配送指令和其他备注时，Agent应按语义拆开；无法可靠拆分时放入`uncertainties`追问，不得猜测。

## 校验与确认

日常默认只调用一次统一入口；它会顺序完成草稿准备、正式映射同步和转换，并统一返回`trace_id`、`release_id`与阶段耗时：

```bash
python3 <skill-dir>/scripts/text_order.py process \
  --draft <草稿.json> \
  --output-dir <本次输出目录> \
  --defer-confirmation
```

120秒内必须返回结构化结果、精准追问或`TEXT_ORDER_STAGE_TIMEOUT`。超时后不得换profile、泛化追问或盲目重跑。以下`validate`与`prepare`保留给分阶段诊断。

先运行：

```bash
python3 <skill-dir>/scripts/text_order.py validate \
  --draft <草稿.json> \
  --defer-confirmation
```

命令只返回脱敏预览和集中追问。没有公司级证据时必须问公司；已登记公司简称可作为公司证据，单独的电商平台名称不能替代公司来源。脱敏预览中的`order_number_plan`按“订单编号 > 来源单号 > 已登记自动生成规则”说明本单将如何编号。两种单号都缺失但来源配置已有确认过的中央日序号规则时，只向用户展示将采用的规则，不再要求用户补充单号或重新选择；没有已登记生成规则时才追问，且不得由Agent临时决定。

Agent只能原样转述命令返回的`clarifications`，不得补充品牌、型号、是否在售、店铺或补平台单号等自行推断的问题。

全部字段明确后先继续生成最终待确认预览，不在这里单独索要一次确认。路由不唯一、关键字段缺失、字段来源为`inferred`或存在`uncertainties`时仍立即追问，不能延后。

## 生成转换输入

字段足够形成安全预览后运行：

```bash
python3 <skill-dir>/scripts/text_order.py prepare \
  --draft <草稿.json> \
  --output-dir <本次临时目录> \
  --defer-confirmation
```

普通单一来源命令生成一个标准化来源Excel、一个与该Excel哈希绑定的一次性自适应计划，以及一个不含收件隐私的路由凭证。混合手工订单会按路由组生成多组文件，并在JSON结果的`conversion_inputs`中明确给出每组的`company_id`、`rule_source_profile_id`、`source`、`adaptive_plan`和`text_intake_receipt`，Agent必须原样使用这些配对，不能自行猜测。它们都不是管易成品。分阶段诊断时把这些输入交给统一转换入口：

```bash
python3 <skill-dir>/scripts/convert_orders.py \
  --source <文字订单标准化.xlsx> \
  --company-hint '<文字订单标准化.xlsx>=<company_id>' \
  --adaptive-plan-hint '<文字订单标准化.xlsx>=<文字订单自适应计划.json>' \
  --text-intake-receipt '<文字订单标准化.xlsx>=<文字订单路由凭证.json>' \
  --output-dir <本次输出目录>
```

`TextOrders`来源没有路由凭证，或凭证与Excel、计划、公司、profile、来源配置哈希任一不一致时，统一入口必须失败关闭。

延后确认生成的自适应计划带`source_confirmation.status=pending`，并携带不持久化完整收件信息的`masked_order_preview`。统一转换入口仍会执行飞书正式商品映射、公司路由、平台规则和物流规则，并先生成完整的`preview_ready`预览，不生成上传 manifest。聊天回复先集中列出提取核对和通过共享可信度门禁的唯一商品候选，最后在“待确认 Excel”小节附上已经生成的文件链接；不要先发收件信息问题，也不要在预览Excel已存在时漏交文件。收件人、电话和地址只做一次批次级核对：员工当前订单处理对话可以从本次草稿展示完整值，长期日志与共享产物继续使用掩码；无误时用户回复一次即可，有误时只指出对应订单，不得逐字段或多轮重复确认。普通聊天文字与截图/OCR都适用，因为两者都经过Agent语义提取；已知固定模板Excel不进入本入口。用户在最终决策中确认文字提取后，把草稿`confirmation.token`设为`CONFIRM_TEXT_ORDER_DRAFT`，不用`--defer-confirmation`重新准备和转换。同一商品名称已有至少两个飞书正式SKU并形成稳定管易代码系列时，新颜色/软硬规格可先在该系列内生成唯一`preview_safe`候选，但仍随最终预览统一确认。低可信唯一候选、多个候选或关键语义属性冲突属于真正阻断项：整批不出Excel，所有阻断项必须合并在一轮提问中；此时只展示`clarifications`，不得夹带系列候选、收件信息核对或把低可信候选描述为暂用默认值。用户解决阻断项后再重跑，收件信息核对仍放在生成预览Excel后的统一小节。

如果用户确认候选正确但没有说明长期复用，先用本批临时映射重跑并交付`ready`成品。此时必须读取结果中的`mapping_reuse_bundle`，在成品之后追加一个非阻断小节，询问哪些继续仅本次、哪些以后沿用；用户不回复就保持仅本次。这个小节不能延迟成品交付，也不构成写飞书或上传管易的授权。文字标准化Excel仍用物理SHA校验计划防篡改，但临时映射绑定计划中的`order_source_sha256`订单事实指纹，因此相同内容重生成Excel时不会反复失效，订单内容改变时会自动失效。

多条文字订单默认合并成一个管易Excel。混合手工订单把`conversion_inputs`中的全部`source`与对应提示一次性交给统一批次入口。若用户同时提供文字单和原始Excel，可先把文字单准备为标准化来源Excel，再与其他来源一起交给统一批次入口。
