# 复杂因子加工案例 02：多层嵌套数组遍历 + 动态阈值分层加权聚合

## 案例编号
CF-002

## 因子名称
多层嵌套交易结构动态阈值分层加权风险因子

## 业务场景
供应链金融场景下，核心企业的多层供应商交易网络嵌套极深（核心企业 → 一级供应商 → 二级供应商 → ... → N级供应商），每层交易明细需按动态风险阈值分层加权聚合，计算全链路风险敞口。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `supplyChainResponse.coreEnterprise.enterpriseId` | 核心企业唯一标识，作为根节点锚定 |
| `supplyChainResponse.coreEnterprise.creditRating` | 核心企业信用等级（AAA/AA/A/BBB/BB/B/CCC），作为动态阈值基准 |
| `supplyChainResponse.supplyChainTiers[]` | 供应链层级数组，每层包含该层级所有供应商节点 |
| `supplyChainResponse.supplyChainTiers[].tierLevel` | 层级序号（1=一级供应商，2=二级...），决定衰减因子 |
| `supplyChainResponse.supplyChainTiers[].suppliers[]` | 该层级下的供应商节点数组 |
| `supplyChainResponse.supplyChainTiers[].suppliers[].supplierId` | 供应商唯一标识 |
| `supplyChainResponse.supplyChainTiers[].suppliers[].creditRating` | 供应商信用等级，用于动态阈值计算 |
| `supplyChainResponse.supplyChainTiers[].suppliers[].transactions[]` | 该供应商与上游的交易明细数组 |
| `supplyChainResponse.supplyChainTiers[].suppliers[].transactions[].transId` | 交易唯一标识 |
| `supplyChainResponse.supplyChainTiers[].suppliers[].transactions[].transDate` | 交易日期（YYYY-MM-DD） |
| `supplyChainResponse.supplyChainTiers[].suppliers[].transactions[].amount` | 交易金额（元） |
| `supplyChainResponse.supplyChainTiers[].suppliers[].transactions[].transType` | 交易类型（采购/回款/预付/退货） |
| `supplyChainResponse.supplyChainTiers[].suppliers[].transactions[].settleStatus` | 结算状态（已结算/部分结算/未结算/争议中） |
| `supplyChainResponse.supplyChainTiers[].suppliers[].subSuppliers[]` | 下级供应商数组（递归嵌套，最深可达 6 层），结构同 suppliers |
| `supplyChainResponse.referenceDate` | 参考基准日（YYYY-MM-DD），用于计算账龄 |
| `riskConfigResponse.riskThresholds.byRating` | 按信用等级分档的风险阈值配置表（含基础阈值、衰减系数、权重） |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | iteration | 从核心企业层级（tierLevel=0，视为根节点）开始，递归深度优先遍历 `supplyChainTiers[].suppliers[]` 及其 `subSuppliers[]` 形成完整供应链树。为每个供应商节点记录：路径深度 `depth`（核心企业为 0，一级为 1...）、所属层级 `tierLevel`、信用等级 `creditRating`、累计衰减因子 `decayFactor = Π(0.85 ^ tierLevel)`。 |
| 2 | condition_filter | 对每个供应商节点的 `transactions[]` 执行筛选：仅保留 `transDate` 在 [referenceDate - 365 days, referenceDate] 内、`settleStatus ∈ {已结算, 部分结算}`、`transType ∈ {采购, 回款, 预付}` 的记录。 |
| 3 | derivation | 计算每个供应商节点的动态风险阈值：<br>• 基础阈值 = `riskConfigResponse.riskThresholds.byRating[coreEnterprise.creditRating].baseThreshold`<br>• 节点阈值 = 基础阈值 × `decayFactor` × `riskConfigResponse.riskThresholds.byRating[node.creditRating].ratingMultiplier`<br>• 权重 = `riskConfigResponse.riskThresholds.byRating[node.creditRating].weight` × `decayFactor` |
| 4 | iteration | 遍历步骤 2 筛选后的每条交易记录，计算：<br>• 账龄天数 = `referenceDate - transDate`<br>• 账龄权重 = `max(0, 1 - 账龄天数 / 365)`<br>• 交易风险值 = `amount × 账龄权重 × 类型系数`，其中 采购=1.0、回款=0.3、预付=0.8、退货=-0.5<br>• 加权风险贡献 = `交易风险值 × 节点权重` |
| 5 | aggregation | 按供应链路径（从核心企业到叶子节点的完整路径）分组聚合：<br>• 路径风险敞口 = Σ(加权风险贡献)<br>• 路径交易笔数 = COUNT<br>• 路径最大单笔 = MAX(amount)<br>• 路径层级深度 = MAX(depth) |
| 6 | branch | 分层风险判定：<br>• 若路径风险敞口 > 节点动态阈值（取路径上所有节点阈值的最小值），标记为"高风险路径"；<br>• 若路径层级深度 > 4 且 路径风险敞口 > 阈值 × 0.6，标记为"深层穿透风险"；<br>• 否则标记为"正常路径"。 |
| 7 | aggregation | 全链路汇总指标：<br>• 总风险敞口 = Σ(所有路径风险敞口)<br>• 高风险路径数 = COUNT(高风险路径)<br>• 深层穿透风险路径数 = COUNT(深层穿透风险)<br>• 最高风险路径敞口 = MAX(路径风险敞口)<br>• 平均路径深度 = AVG(路径层级深度)<br>• 风险集中度 = 最高风险路径敞口 / 总风险敞口（分母为 0 返回默认值）<br>所有金额指标保留两位小数，比率保留四位小数。 |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_002_NESTED_DYNAMIC_WEIGHTED_RISK",
  "factorName": "多层嵌套交易结构动态阈值分层加权风险因子",
  "referenceDate": "2024-12-31",
  "coreEnterprise": {"enterpriseId": "CE_001", "creditRating": "AA"},
  "pathRiskDetails": [
    {
      "path": ["CE_001", "SUP_1001", "SUP_2003", "SUP_3012"],
      "depth": 3,
      "riskExposure": 2850000.00,
      "txnCount": 47,
      "maxSingle": 450000.00,
      "riskLevel": "高风险路径",
      "threshold": 2500000.00
    }
  ],
  "summary": {
    "totalRiskExposure": 12500000.00,
    "highRiskPathCount": 3,
    "deepPenetrationRiskCount": 1,
    "maxPathRiskExposure": 3200000.00,
    "avgPathDepth": 2.4,
    "riskConcentration": 0.2560
  }
}
```