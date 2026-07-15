# Evidence Engine v4

Evidence ID 按来源类型固定：

| Evidence 前缀 | 来源类型 |
|---|---|
| REQ | 需求内容 |
| API | 接口文档 |
| PUB | 公共业务知识规则 |
| INF | 确定性推导 |
| SEL | Phase 1 选定解释 |

排序规则：REQ、API、PUB、INF、SEL，同类型按原文或选择顺序编号。

每条 hard 规则必须引用至少一个 Evidence ID。

使用 `SEL-xxx` 时，必须在「选定解释」章节写明原始歧义、选定口径和选择理由。选择理由应说明为什么该方案最贴近需求语义和可用接口字段，不能只写“根据经验”。
