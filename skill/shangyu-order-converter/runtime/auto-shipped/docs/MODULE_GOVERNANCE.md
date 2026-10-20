# 系统模块治理目录

> 本文由 `config/governance/system_module_catalog_v1.json` 生成；JSON 是唯一权威来源。

当前共 **14 个一级模块**。

## 修改前必读

- 固定模板优先；只有公司已登记且没有可用固定流程时，才进入通用兜底。
- 公司识别、商品映射、店铺、物流、单号和输出分别只有一个权威模块。
- 转换模块只输出管易Excel、上传预检manifest和摘要；浏览器模块不读取原始订单或判断业务规则。
- 缺少必要事实时停止并集中追问；能安全形成预览的决定放在结果小节确认。
- 历史兼容实现必须单独登记为legacy，不能伪装成新订单活动实现。

## 模块清单

| 模块 | 职责 | 边界 | 依赖 |
|---|---|---|---|
| `source_intake` | 接收已知Excel、未知Excel、文字和截图草稿，并校验收件、商品、数量等必要字段；Skill调用默认表示待发货，文件标题不单独决定用途。 | 不决定公司业务规则、店铺或商品目标SKU。 | 无 |
| `company_workflow_routing` | 解析公司身份，先选择固定流程，无固定流程时仅对fallback_enabled公司选择共享兜底。 | 不解析商品、不分配店铺、不上传管易。 | source_intake |
| `adaptive_excel` | 只读探测表头、标签和结构，形成字段映射计划；行式订单和页尾统一收件信息的标签式商品表共用配置化解析，只对无法确定的必要字段追问；共享兜底只按公司库显式声明复用同公司旧来源映射。 | 不得凭文件外观猜公司；不得绕过公司与工作流路由。 | company_workflow_routing |
| `product_resolution` | 按编码、条码、正式映射、规范名称和候选优先级解析管易商品及规格，并展开已确认组合商品。 | 低可信相似名称只能给候选；不得自行创造商品代码。 | source_intake |
| `mapping_governance` | 同步飞书正式映射，保存本批替换，串行新增用户确认的长期映射并校验冲突；用staged记录和mapping_revision读指针发布，诊断并恢复未封口写操作。 | 本批临时替换不得伪装成正式映射；已有映射的替换必须走管理员治理；写锁只阻断后续写入，不得阻断读取已提交版本。 | product_resolution、feishu_integration |
| `store_assignment` | 按固定店铺、来源渠道、SKU关联或共享商品类别兜底分配唯一店铺，并输出不阻断转换的兜底告知证据。 | 店铺优先级固定为固定/渠道、公司或来源SKU、manual_all、商品类别；已映射玉米兜底到光明满元气，非玉米兜底到淘橘子并在结果小节告知；同单跨店停止，更高优先级的SAM及自营天猫京东不受覆盖。 | product_resolution、company_workflow_routing |
| `order_identity` | 执行订单编号优先、来源单号次之、最后才按已登记前缀和飞书中央日序号生成。 | 不同前缀分别计数；同一来源重跑复用原号段；不得临时猜测前缀。 | company_workflow_routing、feishu_integration |
| `order_business_policies` | 集中执行买家会员、价格金额、仓库、支付、订单类型、物流、联系方式后缀、白标和备注分流。 | 共享默认值只定义一次；公司差异通过规则包引用或显式覆盖。 | product_resolution、store_assignment |
| `conversion_delivery` | 合并生成管易导入Excel、上传预检manifest和脱敏/决策摘要。 | 生成文件不代表获得上传授权；不读取浏览器页面状态。 | order_business_policies、order_identity |
| `feishu_integration` | 统一身份探测、表格读写、映射同步、中央序号、publishing恢复和错误分类。 | 业务模块不得各自拼接不兼容的lark-cli身份探针或直接并发写同一revision表。 | 无 |
| `browser_upload` | 在用户明确指定批次并授权后选择已确认Excel、填写导入表单并提交。 | 不读取原始订单，不判断公司、商品、店铺或物流；不得把GitHub推送当作上传授权。 | conversion_delivery |
| `post_upload_reconciliation` | 使用唯一新增任务核验指定批次的管易导入结果。 | 只确认导入结果，不继承上传授权、不执行订单审核、发货、作废或删除。 | browser_upload |
| `order_audit` | 仅在指定导入批次确认全部上传成功后建立审核操作，并在用户另行批准后筛选待审核订单、分批审核和回读核验；默认审核当前全部待审核订单，只审核本批为特殊范围。 | 当前不得继承上传授权或自动连跑；不读取原始订单、不重判公司和商品，不执行发货、作废或删除；真实审核须先完成本地与豆包只读探针。 | post_upload_reconciliation |
| `configuration_governance` | 校验配置结构、规则实现注册、模块唯一归属、依赖环、弃用污染和运行时打包完整性。 | 治理目录只引用权威配置，不复制可执行业务值。 | company_workflow_routing、adaptive_excel、mapping_governance、conversion_delivery、post_upload_reconciliation、order_audit |

## 权威来源与实现归属

### 输入识别与字段完整性 (`source_intake`)

权威路径：`src/auto_shipped/services/text_intake.py`、`src/auto_shipped/source_adapters`
实现引用：`source.recipient.required_fields`
历史兼容实现：无
共享策略：无

### 公司、固定流程与通用兜底路由 (`company_workflow_routing`)

权威路径：`config/companies/company_registry_v1.json`、`config/routing/order_routes_v1.json`、`src/auto_shipped/companies/registry.py`、`src/auto_shipped/routing/registry.py`
实现引用：无
历史兼容实现：无
共享策略：无

### 未知Excel自适应解析 (`adaptive_excel`)

权威路径：`src/auto_shipped/services/adaptive.py`、`src/auto_shipped/source_adapters/adaptive_excel.py`、`src/auto_shipped/source_adapters/labeled_table.py`、`contracts/adaptive_source_plan.schema.json`
实现引用：无
历史兼容实现：无
共享策略：无

### 商品目录、映射与组合展开 (`product_resolution`)

权威路径：`src/auto_shipped/catalog`、`config/catalog/external_sku_mappings.json`、`config/catalog/package_semantics_v1.json`
实现引用：`guanyi.product_catalog.confirmed_exact`、`guanyi.item_expansion.confirmed_source_components`
历史兼容实现：无
共享策略：无

### 正式、待确认与本批临时映射治理 (`mapping_governance`)

权威路径：`src/auto_shipped/integrations/feishu`、`contracts/official_product_mapping.schema.json`、`contracts/current_batch_mapping_replacement.schema.json`
实现引用：无
历史兼容实现：无
共享策略：无

### 店铺分配 (`store_assignment`)

权威路径：`src/auto_shipped/platforms/guanyi/store_assignment.py`、`config/platform_rules/guanyi/policy_modules_v1.json`
实现引用：`guanyi.store_assignment.fixed`、`guanyi.store_assignment.by_product`、`guanyi.store_assignment.by_source_channel`、`guanyi.store_assignment.by_manual_route`、`guanyi.store_assignment.by_manual_route_then_product`
历史兼容实现：无
共享策略：`store.product_family_fallback.v1`、`store.taojuzi_manual_channels.v1`、`store.sam_fixed.v1`、`store.manual_linked_routes.v1`、`store.generic_company_manual_fallback.v1`

### 平台单号与中央日序号 (`order_identity`)

权威路径：`src/auto_shipped/services/order_number_allocations.py`、`src/auto_shipped/integrations/feishu/daily_sequence_ledger.py`、`src/auto_shipped/platforms/guanyi/custom_import.py`
实现引用：`guanyi.platform_order.preferred_order_identity`、`guanyi.platform_order.affixed_preferred_order_identity`、`guanyi.platform_order.prefix_daily_sequence`
历史兼容实现：`guanyi.platform_order.prefix_minute_sequence`
共享策略：无

### 会员、金额、物流、备注与默认值 (`order_business_policies`)

权威路径：`src/auto_shipped/platforms/guanyi/order_policies.py`、`src/auto_shipped/platforms/guanyi/custom_import.py`、`config/platform_rules/guanyi/policy_modules_v1.json`、`config/catalog/white_label_skus_v1.json`
实现引用：`guanyi.buyer_member.policy`、`guanyi.buyer_member.sam_pattern`、`guanyi.contact_suffix.jd_zhongtong_recipient_marker`、`guanyi.defaults.order_flags`、`guanyi.defaults.order_type`、`guanyi.defaults.payment_method`、`guanyi.defaults.warehouse_name`、`guanyi.defaults.zero_amounts`、`guanyi.logistics.corn_packaging_policy`、`guanyi.logistics.default_yunda`、`guanyi.note_routing.courier_instruction_to_address`、`guanyi.seller_remark.rule_composition`、`guanyi.seller_remark.white_label_sku_library`
历史兼容实现：无
共享策略：`buyer.normal_zhang.v1`、`defaults.standard_sales.v1`、`logistics.source_override_then_corn_packaging.v1`、`logistics.source_override_then_default_yunda.v1`、`note.courier_instruction_to_address.v1`、`remark.source_then_system.v1`

### 管易Excel、manifest与摘要交付 (`conversion_delivery`)

权威路径：`src/auto_shipped/services/convert.py`、`src/auto_shipped/services/batch_convert.py`、`src/auto_shipped/platforms/guanyi/preflight.py`、`contracts/upload_batch.schema.json`
实现引用：无
历史兼容实现：无
共享策略：无

### 飞书CLI统一网关 (`feishu_integration`)

权威路径：`src/auto_shipped/integrations/feishu/lark_cli_gateway.py`、`config/integrations/feishu_mapping_governance_v1.json`、`config/integrations/feishu_daily_sequence_v1.json`
实现引用：无
历史兼容实现：无
共享策略：无

### 管易受控浏览器上传 (`browser_upload`)

权威路径：`browser/guanyi/navigation.mjs`、`browser/guanyi/submission.mjs`、`browser/guanyi/operation-state.mjs`
实现引用：`browser.guanyi.prepare_import_form.pending_review`、`browser.guanyi.import_options.verified_current_ui`、`browser.guanyi.submit_import.guarded`
历史兼容实现：无
共享策略：无

### 上传后任务核验 (`post_upload_reconciliation`)

权威路径：`browser/guanyi/operation-state.mjs`
实现引用：`browser.guanyi.task_reconciliation.unique_verified_id`
历史兼容实现：无
共享策略：无

### 上传后订单审核 (`order_audit`)

权威路径：`browser/guanyi/order-audit.mjs`、`browser/guanyi/order-audit-state.mjs`、`browser/guanyi/audit-policy.mjs`、`config/platform_profiles/guanyi_order_audit_v1.json`、`contracts/guanyi_order_audit_policy.schema.json`、`contracts/guanyi_order_audit_operation.schema.json`
实现引用：`browser.guanyi.order_audit.guarded`
历史兼容实现：无
共享策略：无

### Schema、规则覆盖与模块审计 (`configuration_governance`)

权威路径：`config/governance/system_module_catalog_v1.json`、`contracts/system_module_catalog.schema.json`、`src/auto_shipped/rules/module_catalog.py`、`scripts/audit_modules.py`
实现引用：无
历史兼容实现：无
共享策略：无

## 审计

```bash
python3 scripts/audit_modules.py
```

审计必须确保：所有实现和共享策略恰好归属一个模块、权威路径存在、依赖无环、弃用模块不污染活动规则。
