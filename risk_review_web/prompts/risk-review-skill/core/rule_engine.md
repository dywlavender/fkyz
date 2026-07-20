# Rule Engine v4

标准正文中的规则必须是已选定、可执行、可验证的确定口径，不是歧义清单。

## Rule Level

所有进入标准正文的业务规则统一使用：

| Level | 含义 |
|---|---|
| hard | 已选定、可执行、必须遵守并可验证的标准口径 |

不得把 `unclear`、`suggestion` 或“待确认后再定”写成主链规则。

## Basis Type

| basis_type | 含义 |
|---|---|
| requirement | 需求直接明确 |
| api_doc | 接口文档直接明确 |
| public_rule | 公共业务规则直接明确 |
| deterministic_inference | 可由输入唯一推导 |
| selected_interpretation | 存在多种实现时已选择的最可能口径 |

## Rule JSON Contract

每条规则必须包含：

- `rule_id`
- `dimension_ref`
- `source_section`
- `standard_level`，固定为 `hard`
- `basis_type`
- `basis_text`
- `evidence_ids`
- `selection_reason`，非选定解释时为空字符串
- `content`

`basis_type = selected_interpretation` 时，必须引用 `SEL-xxx` 并提供非空 `selection_reason`。
