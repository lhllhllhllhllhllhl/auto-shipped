# 用户纠错反馈治理

状态：V1 已实现。该模块记录“系统给过什么结果、用户改成什么、是否希望长期复用”，但不直接修改正式映射、公司规则或订单成品。

## 1. 三层数据边界

```text
用户纠错
  -> 先修正当前批次
       -> 若正式库没有该键，且用户确认以后沿用：受控直接新增正式映射
       -> 若否决/替换已有正式映射：当前批次临时替换 + 纠错反馈箱
            -> 管理员审核后修正正式映射
```

- 纠错反馈箱：用于让开发维护线程看到真实问题，不能参与转换。
- 待确认映射表：承接冲突、替换和需要管理员判断的例外，必须有可回读的`proposal_id`。
- 正式映射表：无冲突新增可由受控命令写入；已有键的变更仍只有管理员处理。

因此，“本批已改对”“反馈已登记”“正式映射已生效”是三个不同状态。只有写后回读返回`feedback_id`才能说反馈已登记；只有受控新增返回`created/already_exists`并同步快照，或管理员发布完成后，才能说所有用户会自动复用。

## 2. 飞书反馈箱

配置位于`config/integrations/feishu_mapping_governance_v1.json`的`correction_feedback_repository`。当前使用用户提供的飞书电子表格`Sheet1`，固定`sheet_id=917774`。

合同位于`contracts/correction_feedback.schema.json`，共20列：

```text
feedback_id / issue_key / feedback_type / company_id / source_profile_id
source_field / source_value / source_spec / system_result / corrected_result
reuse_scope / related_proposal_id / evidence / skill_version
submitted_by / submitted_at / submission_operation_id
status / maintainer_note / fixed_version
```

禁止写入收件人、电话、地址、完整订单文件内容或其他订单隐私。商品编码、规格编码、公司ID、规则名和不含个人信息的错误说明可以写入。

## 3. 提交与幂等

运行时必须先得到用户对本次反馈留存的明确确认：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py submit-feedback \
  --feedback-type mapping_corrected \
  --company-id sam \
  --source-profile sam_order_v1 \
  --source-field 商品编码+规格编码 \
  --source-value '<来源编码>' \
  --source-spec '<来源规格>' \
  --system-result '<系统原结果>' \
  --corrected-result '<用户确认结果>' \
  --reuse-scope long_term \
  --evidence '<确认依据>' \
  --skill-version '<版本>' \
  --operation-id '<稳定操作ID>' \
  --confirmation-token CONFIRM_SUBMIT_CORRECTION_FEEDBACK
```

`feedback_id`由当前飞书用户、操作ID和问题键确定。同一操作重试不会重复追加；写入后必须精确回读。没有成功回读时只能说“当前批次已修正，反馈尚未登记”。

## 4. 商品规格规则

商品映射只有一套通用行结构：`来源商品标识 + 来源规格（可空） → 管易商品代码 + 管易规格代码`。不要求所有来源商品都有规格：

- 来源有商品标识和规格：使用完整组合，只能精确命中相同规格。
- 父商品编码配对未知、但规格编码稳定且来源策略允许：来源商品标识留空，来源规格填写规格码；当前仅SAM开启这一能力。
- 来源没有规格：使用经正式确认的`code_only`或`name_only`映射；必要时继续受来源品名身份保护。

来源已经提供规格时，不得退回仅编码或仅名称映射。完整组合与规格单独映射同时存在时，完整组合优先。

这是匹配安全规则，不是要求人为给每个无规格商品制造规格。SAM等多规格来源只要传入规格，就会严格按组合键解析。

## 5. 开发维护线程

维护者可只读汇总尚未处理的反馈：

```bash
python3 <skill-dir>/scripts/feishu_mapping.py collect-feedback --status new
```

汇总按`issue_key`聚合重复报告，不读取订单隐私。维护者核对证据后，分别更新正式映射、公司/平台规则或代码测试，再在飞书表中人工维护`status / maintainer_note / fixed_version`。V1不自动改状态，避免开发脚本误覆盖人工结论。
