# 业务规则覆盖门禁

## 目的

代码测试通过只能证明“已配置的规则执行正确”，不能证明SOP中的规则已经全部进入系统。业务规则覆盖门禁用独立规则目录解决这一问题：凡是当前来源适用的规则，都必须明确标记为已实现、待确认、待开发或不适用。

每条规则的原始文件、SHA-256、PDF页码、样本工作表范围和用户决策记录见 [RULE_SOURCE_LEDGER.md](RULE_SOURCE_LEDGER.md)。覆盖门禁不得用当前代码行为替代来源证据。

## 文件边界

- `config/business_rules/shangyu_sop_rule_catalog_v1.json`：SOP事实目录。记录规则ID、来源页、模块、执行阶段和适用范围，不保存公司运行值。
- `config/platform_rules/<platform>/<source>.json`：来源到平台的规则包。记录每条适用规则的状态、确认信息和实现引用。
- `src/auto_shipped/rules/coverage.py`：只负责比对目录与规则包，返回结构化阻断问题；不执行物流、备注或模板写入。
- `src/auto_shipped/platforms/guanyi/`：执行已经确认的管易业务规则并渲染模板。

## 状态定义

| 状态 | 含义 | 是否通过门禁 |
|---|---|---|
| `implemented` | 规则已确认且有可核验实现引用 | 是 |
| `not_applicable` | 已确认当前范围不适用 | 是 |
| `pending_confirmation` | 规则含义或取值等待业务确认 | 否 |
| `pending_implementation` | 规则已知但代码尚未实现 | 否 |
| `partial` | 只实现了规则的一部分 | 否 |

`implemented` 和 `not_applicable` 只有在 `confirmed=true` 时才有效。`implemented` 还必须提供规则目录允许的 `implementation_ref`，避免仅修改状态文字就绕过门禁。

## 阶段

- `conversion`：来源订单转换成管易Excel前检查。
- `upload`：选择文件或点击确定前检查。
- `post_upload`：任务中心核验和订单审核前检查。
- `post_fulfillment`：百礼汇物流回填等后续动作前检查。
- `manual_entry`：仅用于保留PDF中的手工新增订单规则，不进入自定义导入链路。

转换服务当前强制执行 `conversion` 门禁。浏览器写入仍未启用；在实现“确定”提交前，应把同一门禁接入 `upload` 阶段。

## 当前恬田状态

转换阶段当前已放行：

- `GY-LOGISTICS-SELECTION`：原表指定物流优先；未指定时按当前包装规则选择韵达或中通重货。未来取得重量公式后替换独立物流策略。
- `GY-DELIVERY-INSTRUCTION-ADDRESS`：给快递员的配送指令追加到收货地址末尾，地址已有相同指令时不重复追加；所有已实现来源引用同一共享模块。
- `GY-JD-ZTO-CONTACT-SUFFIX`：仅尚舆自营京东中通触发；姓名四位码追加到联系电话和联系手机，地址码存在时交叉核验，缺失或冲突时阻断。
- `GY-SELLER-REMARK-POLICY`：备注分流后的非快递员备注写入卖家备注；组合器支持按固定顺序追加系统提示、中文分号连接并精确去重。
- `GY-PRODUCT-MAPPING`：当前恬田SKU `2026DFYUMI001` 已确认映射为 `JTW8E1 / 6974768564811`。

文字玉米在`GY-PRODUCT-MAPPING`内先读取独立包装语义库：RZD的`根`映射彩袋单棒装，自营的`袋`映射8棒家庭装；未登记表达返回`CONFIRM_PACKAGE_SEMANTICS`。三种裸棒由普通发货禁用门禁阻断。用户确认以后沿用的新含义只保存到本机待登记库，所有者发布前不参与转换。

白标商品使用独立版本化SKU库 `config/catalog/white_label_skus_v1.json`。转换服务在商品精确映射后读取该库；当前3个SKU任一命中时，整单卖家备注追加“白标商品”。库不可读、状态异常或存在重复键时转换会停止。

上传阶段已明确阻断：

- `GY-IMPORT-OPTIONS`：业务已确认自定义导入页面两项都勾选；当前执行器尚未完成两项的精确定位、回读和真实验收。
- `GY-UPLOAD-SUBMISSION`：点击“确定”尚未实现。

## 审计命令

```bash
PYTHONPATH=src python -m auto_shipped.cli audit-rules --phase conversion
PYTHONPATH=src python -m auto_shipped.cli audit-rules --phase upload
PYTHONPATH=src python -m auto_shipped.cli audit-rules --phase all
```

退出码 `0` 表示所选阶段完整；退出码 `2` 表示存在待确认、待开发、遗漏或配置错误。输出不包含收件人信息。
