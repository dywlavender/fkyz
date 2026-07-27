# 复杂因子加工案例 03：多条件分支衍生 + 单位换算 + 精度舍入 + 空值链路处理

## 案例编号
CF-003

## 因子名称
企业综合偿债能力多维衍生评分因子

## 业务场景
企业授信额度审批中，需从多源财务报表、银行流水、税务数据中提取关键指标，经过单位统一换算、精度标准化、多条件分支衍生计算，输出综合偿债能力评分。涉及复杂的分支逻辑、单位换算（万元/元/分）、精度控制（四舍五入/向下取整/向上取整）、空值链路兜底。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `financialReportResponse.balanceSheet.totalAssets` | 总资产（单位：万元，保留两位小数） |
| `financialReportResponse.balanceSheet.totalLiabilities` | 总负债（单位：万元，保留两位小数） |
| `financialReportResponse.balanceSheet.currentAssets` | 流动资产（单位：万元） |
| `financialReportResponse.balanceSheet.currentLiabilities` | 流动负债（单位：万元） |
| `financialReportResponse.incomeStatement.revenue` | 营业收入（单位：万元） |
| `financialReportResponse.incomeStatement.netProfit` | 净利润（单位：万元，可为负） |
| `financialReportResponse.incomeStatement.operatingCashFlow` | 经营性现金流（单位：万元） |
| `financialReportResponse.cashFlowStatement.freeCashFlow` | 自由现金流（单位：万元） |
| `taxResponse.enterpriseTaxInfo.annualTaxPayable` | 年度应纳税额（单位：元，整数） |
| `taxResponse.enterpriseTaxInfo.taxCreditRating` | 纳税信用等级（A/B/M/C/D） |
| `bankFlowResponse.accountSummary.avgDailyBalance` | 平均日存款余额（单位：元，保留两位小数） |
| `bankFlowResponse.accountSummary.maxSingleInflow` | 最大单笔流入（单位：元） |
| `bankFlowResponse.accountSummary.overdueCount_12M` | 近 12 个月逾期笔数（整数） |
| `bankFlowResponse.accountSummary.loanBalance` | 在贷余额（单位：元） |
| `creditReportResponse.enterpriseCredit.creditScore` | 企业征信评分（300-850 整数） |
| `creditReportResponse.enterpriseCredit.queryCount_6M` | 近 6 个月查询次数（整数） |
| `industryBenchmarkResponse.industryRatios.currentRatio_median` | 行业流动比率中位数（倍数，保留两位小数） |
| `industryBenchmarkResponse.industryRatios.debtToAsset_median` | 行业资产负债率中位数（百分比，保留两位小数） |
| `industryBenchmarkResponse.industryRatios.roa_median` | 行业 ROA 中位数（百分比） |
| `configResponse.scoringWeights` | 评分权重配置（含各指标权重、分档阈值、加分/扣分规则） |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | derivation | 单位统一换算至“元”（整数，向下取整）：<br>• 财务报表字段（万元）× 10000 → 元<br>• 税务字段（元）保持不变<br>• 银行流水字段（元）保持不变<br>• 行业基准比率字段保持原单位（倍数/百分比） |
| 2 | derivation | 计算核心财务比率（保留四位小数，四舍五入）：<br>• 资产负债率 = totalLiabilities / totalAssets（分母为 0 返回默认值）<br>• 流动比率 = currentAssets / currentLiabilities（分母为 0 返回默认值）<br>• 净利率 = netProfit / revenue（分母为 0 返回默认值）<br>• ROA = netProfit / totalAssets（分母为 0 返回默认值）<br>• 现金流负债比 = operatingCashFlow / totalLiabilities（分母为 0 返回默认值）<br>• 自由现金流比 = freeCashFlow / revenue（分母为 0 返回默认值） |
| 3 | branch | 行业对标分档（每项独立判断，互不影响）：<br>• 流动比率分档：≥ 行业中位数 × 1.2 → "优"；≥ 行业中位数 → "良"；≥ 行业中位数 × 0.8 → "中"；< 行业中位数 × 0.8 → "差"<br>• 资产负债率分档：≤ 行业中位数 × 0.8 → "优"；≤ 行业中位数 → "良"；≤ 行业中位数 × 1.2 → "中"；> 行业中位数 × 1.2 → "差"<br>• ROA 分档：≥ 行业中位数 × 1.2 → "优"；≥ 行业中位数 → "良"；≥ 行业中位数 × 0.8 → "中"；< 行业中位数 × 0.8 → "差" |
| 4 | derivation | 基础评分计算（每项 0-100 分，保留两位小数）：<br>• 流动比率得分 = 分档映射：优=95、良=80、中=60、差=30<br>• 资产负债率得分 = 分档映射：优=90、良=75、中=55、差=25<br>• ROA 得分 = 分档映射：优=95、良=80、中=60、差=30<br>• 现金流负债比得分 = min(100, max(0, 现金流负债比 × 200))<br>• 自由现金流比得分 = min(100, max(0, 自由现金流比 × 500))<br>• 纳税信用得分 = 映射：A=100、B=85、M=70、C=50、D=20<br>• 银行流水得分 = min(100, max(0, (avgDailyBalance / 1000000) × 10)) - overdueCount_12M × 5（下限 0）<br>• 征信评分得分 = (creditScore - 300) / 5.5（归一化到 0-100）<br>• 查询次数扣分 = max(0, queryCount_6M - 3) × 2 |
| 5 | aggregation | 加权综合得分 = Σ(各项得分 × 对应权重) - 查询次数扣分，权重来自 `configResponse.scoringWeights`。结果保留两位小数，四舍五入。 |
| 6 | branch | 等级判定分支：<br>• 加权综合得分 ≥ 85 → "AAA" 级，授信倍数 1.5<br>• 75 ≤ 得分 < 85 → "AA" 级，授信倍数 1.2<br>• 65 ≤ 得分 < 75 → "A" 级，授信倍数 1.0<br>• 55 ≤ 得分 < 65 → "BBB" 级，授信倍数 0.8<br>• 45 ≤ 得分 < 55 → "BB" 级，授信倍数 0.6<br>• 得分 < 45 → "B" 级，授信倍数 0.3 |
| 7 | branch | 否决性条件覆盖（优先于等级判定）：<br>• 若 taxCreditRating == "D" → 直接降为 "B" 级，授信倍数 0.1<br>• 若 overdueCount_12M ≥ 6 → 直接降为 "BB" 级，授信倍数不超过 0.5<br>• 若 creditScore < 450 → 直接降为 "B" 级，授信倍数 0.2<br>• 若 revenue == 0 或 totalAssets == 0 → 返回默认值 |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_003_MULTI_BRANCH_DERIVATION_SCORING",
  "factorName": "企业综合偿债能力多维衍生评分因子",
  "referenceDate": "2024-12-31",
  "unitConverted": {
    "totalAssets": 500000000,
    "totalLiabilities": 300000000,
    "currentAssets": 200000000,
    "currentLiabilities": 120000000,
    "revenue": 800000000,
    "netProfit": 50000000,
    "operatingCashFlow": 80000000,
    "freeCashFlow": 45000000
  },
  "financialRatios": {
    "debtToAssetRatio": 0.6000,
    "currentRatio": 1.6667,
    "netProfitMargin": 0.0625,
    "roa": 0.1000,
    "cashFlowToDebt": 0.2667,
    "fcfToRevenue": 0.0563
  },
  "industryBenchmarking "industryComparison": {
    "currentRatioGrade": "良",
    "debtToAssetGrade": "中",
    "roaGrade": "优"
  },
  "subScores": {
    "currentRatioScore": 80.00,
    "debtToAssetScore": 55.00,
    "roaScore": 95.00,
    "cashFlowToDebtScore": 53.33,
    "fcfToRevenueScore": 28.13,
    "taxCreditScore": 85.00,
    "bankFlowScore": 62.00,
    "creditScore": 81.82,
    "queryPenalty": 4.00
  },
  "weightedScore": 72.45,
  "finalGrade": "A",
  "creditMultiplier": 1.0,
  "vetoApplied": false
}
```