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

---

# Evidence Engine v4

Evidence ID 按来源类型固定：

| Evidence 前缀 | 来源类型 |
|---|---|
| REQ | 需求内容 |
| API | 接口文档 |
| PUB | 公共业务知识规则 |
| INF | 确定性推导 |
| SEL | 选定解释 |

排序规则：REQ、API、PUB、INF、SEL，同类型按原文或选择顺序编号。

每条 hard 规则必须引用至少一个 Evidence ID。

使用 `SEL-xxx` 时，必须在「选定解释」章节写明原始歧义、选定口径和选择理由。选择理由应说明为什么该方案最贴近需求语义和可用接口字段，不能只写“根据经验”。

---

# Freeze Rule

本阶段已冻结的输入、证据和规则集合不得在最终输出时临时扩充或改写。阶段专用冻结对象以本阶段规则为准。

---

# Deterministic Output v4

相同输入必须输出相同章节、相同对象顺序和相同判定口径。具体章节、编号和对象顺序以本阶段专用规则为准。
