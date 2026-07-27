# 复杂因子加工案例 07：分层分位数映射 + 非线性评分函数 + 专家规则覆盖

## 案例编号
CF-007

## 因子名称
小微企业多维画像非线性评分因子

## 业务场景
普惠金融小微企业准入评分：从经营、财务、税务、银行、征信、司法、电商、社保八大维度提取 50+ 原始指标，经分层分位数标准化、非线性评分函数变换、专家规则硬覆盖，输出最终准入评分及分档建议。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `profileResponse.operating.*` | 经营维度原始指标（成立年限、员工数、专利数、认证资质等 8 项） |
| `profileResponse.financial.*` | 财务维度原始指标（营收、净利率、资产负债率、流动比率、现金流比等 10 项） |
| `profileResponse.tax.*` | 税务维度原始指标（纳税信用、增值税负率、所得税负率、发票异常率等 6 项） |
| `profileResponse.banking.*` | 银行维度原始指标（平均存款、流水波动、对公占比、授信用信率等 8 项） |
| `profileResponse.credit.*` | 征信维度原始指标（信用评分、查询次数、逾期记录、负债率等 6 项） |
| `profileResponse.judicial.*` | 司法维度原始指标（涉诉案件数、被执行金额、裁判文书风险等 5 项） |
| `profileResponse.ecommerce.*` | 电商维度原始指标（店铺等级、DSR评分、退款率、GMV增长率等 7 项） |
| `profileResponse.socialSecurity.*` | 社保维度原始指标（参保人数、缴费基数、连续缴费月数、增减员率等 6 项） |
| `configResponse.dimensionWeights` | 八维度权重配置（和为 1） |
| `configResponse.indicatorConfigs[]` | 每个指标的配置：分位数映射表、非线性函数类型/参数、专家规则 |
| `industryBenchmarkResponse.industryPercentiles` | 行业分位数基准表（按行业代码分组，含各指标 P10/P25/P50/P75/P90） |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | derivation | **指标级分位数标准化**（逐指标并行）：<br>对每个原始指标，按企业所属行业代码查找 `industryPercentiles` 对应分位数表。<br>• 若指标"越大越好"：分位数得分 = 线性插值映射到 [0,100]，P10→0、P50→50、P90→100，超出边界线性外推上限 [0,100]<br>• 若指标"越小越好"（如负债率、逾期率）：反向映射，P90→0、P50→50、P10→100<br>• 若指标"中间最好"（如增减员率）：双侧映射，P50→100，两侧对称下降<br>• 缺失值：若配置 `imputeStrategy="industry_median"` → 用行业 P50 补全后再映射；若 `"default_zero"` → 得分 0；若 `"exclude"` → 该维度权重重分配 |
| 2 | derivation | **指标级非线性评分变换**（按 `indicatorConfigs` 配置）：<br>• `func="linear"`：得分不变<br>• `func="log"`：得分 = 100 × log(1 + 原始值/基准) / log(1 + 上限/基准)，基准=P50<br>• `func="sqrt"`：得分 = 100 × sqrt(原始值/上限)<br>• `func="sigmoid"`：得分 = 100 / (1 + exp(-k×(原始值-P50)/σ))，k=配置参数<br>• `func="piecewise"`：分段函数，按配置的断点和斜率计算<br>• `func="step"`：阶跃函数，按配置阈值分档赋固定分<br>变换后得分再次截断到 [0,100]。 |
| 3 | aggregation | **维度级聚合**：每个维度内，按 `indicatorConfigs` 中指标权重加权平均得维度得分（保留两位小数）。若维度内全部指标缺失且配置 `dimensionMissingPolicy="zero"` → 维度得分 0；若 `"reweight"` → 该维度权重按比例分配给其他有数据维度。 |
| 4 | derivation | **专家规则硬覆盖（否决/加分/扣分）**：按优先级顺序执行，后执行可覆盖前执行：<br>• **一票否决**（优先级最高）：<br>  - 司法：被执行金额 > 100万 或 失信被执行人 → 最终评分 = 0，建议 "拒绝"<br>  - 征信：当前逾期 > 90天 或 近 2 年逾期累计 > 6 期 → 最终评分 = 0，建议 "拒绝"<br>  - 税务：纳税信用 D 级 → 最终评分 = 0，建议 "拒绝"<br>• **硬性扣分**（次优先级）：<br>  - 司法：涉诉案件数 > 10 → 扣 15 分<br>  - 征信：查询次数 6M > 10 → 扣 10 分<br>  - 银行：授信用信率 > 90% → 扣 8 分<br>• **条件加分**（优先级最低）：<br>  - 经营：高新技术企业认证 + 专利数 > 5 → 加 5 分<br>  - 电商：店铺等级 ≥ 钻石 + DSR ≥ 4.8 → 加 5 分<br>  - 社保：连续缴费 ≥ 36 月 + 参保人数增长 > 20% → 加 3 分<br>所有加扣分在维度得分层面操作，截断 [0,100]。 |
| 5 | aggregation | **总分计算**：最终评分 = Σ(维度得分 × 维度权重) + 专家规则净调整分，保留两位小数，截断 [0,100]。 |
| 6 | branch | **准入分档建议**：<br>• ≥ 80 → "A档-优先准入"<br>• 70-79 → "B档-标准准入"<br>• 60-69 → "C档-条件准入（需增信）"<br>• 50-59 → "D档-谨慎准入（需人工复核）"<br>• < 50 → "E档-拒绝"<br>• 一票否决触发 → 直接 "E档-拒绝" |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_007_MULTIDIM_NONLINEAR_SCORING",
  "factorName": "小微企业多维画像非线性评分因子",
  "referenceDate": "2024-12-31",
  "enterpriseId": "SME_001234",
  "industryCode": "C26",
  "dimensionScores": {
    "operating": {"rawScore": 72.5, "adjustedScore": 77.5, "weight": 0.15, "indicators": 8},
    "financial": {"rawScore": 65.2, "adjustedScore": 65.2, "weight": 0.25, "indicators": 10},
    "tax": {"rawScore": 88.0, "adjustedScore": 88.0, "weight": 0.15, "indicators": 6},
    "banking": {"rawScore": 58.3, "adjustedScore": 50.3, "weight": 0.15, "indicators": 8},
    "credit": {"rawScore": 71.5, "adjustedScore": 61.5, "weight": 0.10, "indicators": 6},
    "judicial": {"rawScore": 90.0, "adjustedScore": 90.0, "weight": 0.05, "indicators": 5},
    "ecommerce": {"rawScore": 82.0, "adjustedScore": 87.0, "weight": 0.10, "indicators": 7},
    "socialSecurity": {"rawScore": 75.0, "adjustedScore": 78.0, "weight": 0.05, "indicators": 6}
  },
  "expertRulesApplied": [
    {"type": "penalty", "rule": "授信用信率>90%", "dimension": "banking", "delta": -8.0},
    {"type": "penalty", "rule": "征信查询次数6M>10", "dimension": "credit", "delta": -10.0},
    {"type": "bonus", "rule": "高新技术企业+专利>5", "dimension": "operating", "delta": 5.0},
    {"type": "bonus", "rule": "社保连续36月+增长>20%", "dimension": "socialSecurity", "delta": 3.0}
  ],
  "finalScore": 68.42,
  "admissionGrade": "C档-条件准入（需增信）",
  "vetoTriggered": false
}
```