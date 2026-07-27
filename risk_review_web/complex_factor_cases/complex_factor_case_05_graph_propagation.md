# 复杂因子加工案例 05：多源数据实体解析 + 图谱关联聚合 + 传播风险计算

## 案例编号
CF-005

## 因子名称
关联主体图谱传播风险因子

## 业务场景
企业群风险穿透：从核心企业出发，通过股权穿透、人员任职、担保链、贸易往来、资金往来五大维度构建关联图谱，识别关联主体，计算风险在图谱中的传播衰减，输出核心企业受关联方影响的综合传播风险指标。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `graphResponse.coreEnterprise.enterpriseId` | 核心企业唯一标识（图谱根节点） |
| `graphResponse.coreEnterprise.riskScore` | 核心企业自身风险评分（0-100） |
| `graphResponse.nodes[]` | 图谱节点数组（含核心企业及所有关联主体） |
| `graphResponse.nodes[].nodeId` | 节点唯一标识 |
| `graphResponse.nodes[].nodeType` | 节点类型（企业/自然人/账户/设备/地址） |
| `graphResponse.nodes[].riskScore` | 节点自身风险评分（0-100，可能为空） |
| `graphResponse.nodes[].riskLabels[]` | 节点风险标签数组（如：失信被执行人、涉诉、税务异常等） |
| `graphResponse.edges[]` | 图谱边数组（关联关系） |
| `graphResponse.edges[].edgeId` | 边唯一标识 |
| `graphResponse.edges[].sourceNodeId` | 起点节点 ID |
| `graphResponse.edges[].targetNodeId` | 终点节点 ID |
| `graphResponse.edges[].relationType` | 关系类型（股权/任职/担保/贸易往来/资金往来/共同地址/共同设备/共同联系人） |
| `graphResponse.edges[].weight` | 关系强度权重（0-1，股权按持股比例、资金往来按金额占比等） |
| `graphResponse.edges[].direction` | 方向性（单向/双向） |
| `graphResponse.edges[].attributes.amount` | 关联金额（如担保金额、往来金额，单位：元） |
| `graphResponse.edges[].attributes.shareRatio` | 持股比例（股权关系专用，0-1） |
| `graphResponse.edges[].attributes.guaranteeType` | 担保类型（连带/一般/抵押/质押） |
| `configResponse.propagationConfig` | 传播配置：各关系类型的传播衰减系数、最大跳数、风险阈值 |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | iteration | 构建邻接表表示的有向加权图：节点为 `nodes[]`，边为 `edges[]`。双向关系生成两条方向相反的边。为每条边赋予传播衰减系数：`decay = config.propagationConfig.decayByRelation[relationType]`（股权=0.7、任职=0.6、担保=0.8、贸易=0.5、资金=0.6、共同属性=0.3）。 |
| 2 | iteration | **实体解析去重**：对自然人节点，按（姓名+证件号+手机号）任意两项匹配合并；对企业节点，按（统一社会信用代码/名称+地址）匹配合并。合并时：风险评分取最大值，风险标签取并集，边权重取最大值。 |
| 3 | branch | **风险源识别**：节点满足任一条件视为风险源：<br>• 自身 riskScore ≥ 70<br>• riskLabels 包含 "失信被执行人"、"重大税务违法"、"欺诈黑名单"<br>• 为核心企业且 coreEnterprise.riskScore ≥ 60<br>风险源初始传播强度 = 自身 riskScore / 100。 |
| 4 | iteration | **图传播算法（带衰减的 BFS）**：从每个风险源出发，按广度优先搜索传播，最大跳数 = `config.propagationConfig.maxHops`（默认 4）：<br>• 第 0 跳（风险源自身）：传播强度 = 初始强度<br>• 第 k 跳：传播强度 = 上一跳强度 × 边权重 × 边衰减系数 × 节点接收系数（企业=1.0、自然人=0.8、账户=0.6、设备=0.4）<br>• 同一节点可能被多条路径到达，取所有路径传播强度的最大值作为该节点最终受传播风险值<br>• 传播强度 < 0.01 剪枝停止 |
| 5 | aggregation | **核心企业受传播风险聚合**（反向传播视角）：<br>• 直接关联风险 = Σ(一跳邻居节点的最终受传播风险值 × 边权重)<br>• 间接关联风险 = Σ(二跳及以上节点的最终受传播风险值 × 边权重 × 0.5^跳数)<br>• 高风险关联方数 = COUNT(最终受传播风险值 ≥ 0.5 的关联节点)<br>• 最大单体传播风险 = MAX(关联节点最终受传播风险值)<br>• 风险传播路径数 = 统计所有到达核心企业的有效传播路径条数 |
| 6 | derivation | **传播风险指标计算**：<br>• 综合传播风险指数 = (直接关联风险 × 0.5 + 间接关联风险 × 0.3 + 核心企业自身风险/100 × 0.2) × 100，保留两位小数<br>• 关联风险贡献度 = 直接关联风险 / (直接关联风险 + 间接关联风险 + 0.001)，保留四位小数<br>• 结构性风险标识：若存在 ≥ 3 条长度 ≥ 3 的传播路径汇聚到核心企业 → true |
| 7 | branch | **风险等级判定**：<br>• 综合传播风险指数 ≥ 70 → "高传播风险"<br>• 50-69 → "中传播风险"<br>• 30-49 → "低传播风险"<br>• < 30 → "可控" |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_005_GRAPH_PROPAGATION_RISK",
  "factorName": "关联主体图谱传播风险因子",
  "referenceDate": "2024-12-31",
  "coreEnterprise": {"enterpriseId": "CE_001", "selfRiskScore": 45},
  "graphStats": {
    "totalNodes": 156,
    "totalEdges": 342,
    "mergedNodes": 12,
    "riskSources": 8,
    "maxHops": 4
  },
  "propagationToCore": {
    "directRisk": 28.5,
    "indirectRisk": 12.3,
    "highRiskAssociates": 5,
    "maxSinglePropagation": 0.72,
    "propagationPaths": 23
  },
  "indicators": {
    "compositePropagationIndex": 58.45,
    "associativeRiskContribution": 0.6984,
    "structuralRiskFlag": true
  },
  "riskLevel": "中传播风险",
  "topRiskPaths": [
    {"path": ["CE_001", "ENT_045", "PER_012", "ENT_078"], "strength": 0.34, "hops": 3},
    {"path": ["CE_001", "ENT_033", "ENT_056"], "strength": 0.41, "hops": 2}
  ]
}
```