# 包装语义规则与回收治理

## 两层存储

包装语义不和商品映射、订单数据混存：

1. 正式规则库：`config/catalog/package_semantics_v1.json`。只有主系统所有者审核、更新版本并重新发布Skill后，才会被所有用户自动使用。
2. 用户Agent待登记库：默认位于跨平台状态目录的`state/package-semantics/pending/`。每条提案一个不可变JSON，只含公司ID、来源配置、商品品类、原始表达、包装含义和数量换算，不含订单号、姓名、电话或地址。

待登记提案不会自动参与订单转换，不能替代正式规则。

## 首次确认

普通发货中，`根`的全局候选是彩袋单棒装，但尚未登记该表达的公司第一次出现时必须追问。`袋`、`盒`、`份`、`套`等表达必须按来源登记。用户只确认本次使用时不保存；用户明确确认以后沿用时才允许写入待登记库。

裸棒商品与文字表达在普通发货中一律阻断。未来如需试吃发货，应建立独立订单类型和规则，不得绕过禁用库。

## 用户Agent操作

保存一条以后沿用的提案：

```bash
python3 <skill-dir>/scripts/package_semantics.py save \
  --company-id <company_id> \
  --source-profile <source_profile_id> \
  --product-family <product_family> \
  --source-expression <根或袋等表达> \
  --semantic-type <single_stick_packaged|eight_stick_family_pack|pack_content_count|bundle> \
  --quantity-strategy <same_as_source|divide_by_sticks_per_target_unit|requires_bundle_expansion> \
  --sticks-per-target-unit <可选正整数> \
  --confirmation-token CONFIRM_PACKAGE_SEMANTICS_REUSE
```

查看待回收数量和内容：

```bash
python3 <skill-dir>/scripts/package_semantics.py list
```

当所有者要求“把待登记包装规则发给我”时，导出到本次输出目录并把该JSON作为文件交付：

```bash
python3 <skill-dir>/scripts/package_semantics.py export \
  --output <本次输出目录>/包装语义待登记提案.json
```

导出包用`rule_key`标识同一公司、品类和表达，用`proposal_id`标识具体候选。相同提案重复保存会复用同一ID；同一`rule_key`存在不同含义时，导出包会列入`conflicting_rule_keys`，由所有者决定，Agent不得自行合并。

## 主系统登记流程

1. 所有者收集用户Agent导出的提案文件。
2. 校验公司、来源配置、品类、表达、包装类型和换算方式。
3. 对冲突提案要求业务复核。
4. 把批准项写入`package_semantics_v1.json`的对应来源规则并提升版本号。
5. 在规则来源台账中记录当前确认来源和日期；旧版本交给Git历史保存。
6. 增加商品匹配、数量换算、物流和禁用SKU回归测试。
7. 重新打包和发布Skill。新版本安装后，所有用户共享正式规则。

任何提案在第7步之前都保持`pending_owner_review`，不得自动生效。
