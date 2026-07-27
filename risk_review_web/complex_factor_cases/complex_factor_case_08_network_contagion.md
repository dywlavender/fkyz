# 复杂因子加工案例 08：图结构传播 + 社区发现 + 多跳关联风险传递

## 案例编号
CF-008

## 因子名称
关联方网络多跳风险传染因子

## 业务场景
企业关联风险穿透：构建以目标企业为中心的多层关联图（股权穿透、人员任职、共同联系方式、共同地址、资金往来、担保链条），通过图算法进行风险标签传播、社区发现、结构洞识别，量化关联方风险对主体的传染程度。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `graphResponse.targetEnterprise.enterpriseId` | 目标企业 ID，图的中心节点 |
| `graphResponse.targetEnterprise.riskScore` | 目标企业自身风险评分（0-100） |
| `graphResponse.targetEnterprise.riskLabels[]` | 目标企业已有风险标签（种子标签） |
| `graphResponse.nodes[]` | 图节点数组（含目标企业及所有关联方） |
| `graphResponse.nodes[].nodeId` | 节点唯一标识 |
| `graphResponse.nodes[].nodeType` | 节点类型（企业/自然人/账户/设备/地址/电话） |
| `graphResponse.nodes[].enterpriseId` | 企业节点的统一社会信用代码 |
| `graphResponse.nodes[].personId` | 自然人节点的身份证号（脱敏） |
| `graphResponse.nodes[].riskScore` | 节点自身风险评分（0-100，可能为空） |
| `graphResponse.nodes[].riskLabels[]` | 节点风险标签数组（如：失信被执行人、涉诉、税务异常、欺诈黑名单等） |
| `graphResponse.edges[]` | 图边数组（关联关系） |
| `graphResponse.edges[].edgeId` | 边唯一标识 |
| `graphResponse.edges[].sourceNodeId` | 起点节点 ID |
| `graphResponse.edges[].targetNodeId` | 终点节点 ID |
| `graphResponse.edges[].relationType` | 关系类型（股权/任职/担保/资金往来/贸易往来/共同地址/共同电话/共同设备/共同联系人） |
| `graphResponse.edges[].weight` | 关系强度权重（0-1，股权按持股比例、资金往来按金额占比等） |
| `graphResponse.edges[].direction` | 方向性（单向/双向） |
| `graphResponse.edges[].attributes.amount` | 关联金额（如担保金额、往来金额，单位：元） |
| `graphResponse.edges[].attributes.shareRatio` | 持股比例（股权关系专用，0-1） |
| `graphResponse.edges[].attributes.guaranteeType` | 担保类型（连带/一般/抵押/质押） |
| `configResponse.propagationConfig` | 传播配置：各关系类型传播衰减系数、最大跳数、风险阈值 |
| `configResponse.communityConfig` | 社区发现配置：算法类型、分辨率参数、最小社区大小 |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | iteration | **构建异构多层图**：节点为 `nodes[]`，边为 `edges[]`。双向关系生成两条反向边。按关系类型分层：第 1 层=强关联（股权/担保/任职）、第 2 层=中关联（资金/贸易往来）、第 3 层=弱关联（共同属性）。为每条边赋予传播衰减系数：`decay = config.propagationConfig.decayByRelation[relationType]`（股权=0.7、任职=0.6、担保=0.8、资金=0.6、贸易=0.5、共同属性=0.3）。 |
| 2 | iteration | **实体解析去重（多源融合）**：<br>• 自然人：按（姓名+证件号后 4 位+手机号后 4 位）任意两项匹配合并<br>• 企业：按（统一社会信用代码/名称+注册地址）匹配合并<br>• 账户：按（账号+开户行+户名）匹配合并<br>合并时：riskScore 取最大值，riskLabels 取并集，边权重取最大值，保留原始 nodeId 映射关系。 |
| 3 | branch | **风险源种子节点识别**：节点满足任一条件视为风险源：<br>• 自身 riskScore ≥ 70<br>• riskLabels 包含 "失信被执行人"、"重大税务违法"、"欺诈黑名单"、"重大违法违规"<br>• 为目标企业且 targetEnterprise.riskScore ≥ 60<br>风险源初始传播强度 = 自身 riskScore / 100。无风险评分节点按标签严重度赋初始值：失信=0.9、重大税务=0.85、欺诈=0.95、涉诉=0.5。 |
| 4 | iteration | **带衰减的多源风险传播（BFS 变体）**：从每个风险源并行启动广度优先传播，最大跳数 = `config.propagationConfig.maxHops`（默认 4）。<br>• 第 0 跳（风险源自身）：传播强度 = 初始强度<br>• 第 k 跳：传播强度 = 上一跳强度 × 边权重 × 边衰减系数 × 节点接收系数（企业=1.0、自然人=0.8、账户=0.6、设备/地址/电话=0.4）<br>• 同一节点可被多条路径到达，取所有路径传播强度的最大值作为该节点最终受传播风险值<br>• 传播强度 < 0.01 剪枝停止<br>• 记录每条有效传播路径：路径节点序列、累积衰减、跳数、关键边类型 |
| 5 | aggregation | **目标企业视角的反向风险聚合**（风险向中心传播）：<br>• 直接关联风险 = Σ(一跳邻居节点的最终受传播风险值 × 边权重)<br>• 间接关联风险 = Σ(二跳及以上节点的最终受传播风险值 × 边权重 × 0.5^跳数)<br>• 高风险关联方数 = COUNT(最终受传播风险值 ≥ 0.5 的关联节点)<br>• 最大单体传播风险 = MAX(关联节点最终受传播风险值)<br>• 风险传播路径数 = 统计所有到达目标企业的有效传播路径条数<br>• 关键传播桥接节点 = 介数中心性前 5 的非目标节点（按传播路径经过次数） |
| 6 | iteration | **社区发现（Louvain/Leiden 算法）**：在无向加权图上运行社区发现，权重 = 边权重 × 关系类型系数（强关联=1.0、中关联=0.7、弱关联=0.3）。分辨率参数 = `config.communityConfig.resolution`（默认 1.0）。过滤节点数 < `config.communityConfig.minSize`（默认 3）的社区。为每个社区计算：社区风险均值、社区内边密度、跨社区边数、是否包含目标企业、是否包含风险源。 |
| 7 | derivation | **结构洞与关键节点识别**：<br>• 结构洞节点：约束系数最小的前 10% 节点（连接不同社区的桥接者）<br>• 关键传播节点：传播路径经过次数最多的前 10 个节点<br>• 风险汇聚节点：入向传播强度和最高的前 10 个节点 |
| 8 | derivation | **传染风险综合指标计算**：<br>• 综合传染风险指数 = (直接关联风险 × 0.45 + 间接关联风险 × 0.25 + 目标企业自身风险/100 × 0.15 + 社区风险贡献 × 0.15) × 100，保留两位小数<br>  其中社区风险贡献 = 目标企业所在社区风险均值 × 社区内边密度<br>• 关联风险贡献度 = 直接关联风险 / (直接关联风险 + 间接关联风险 + 0.001)，保留四位小数<br>• 结构性传染标识：若存在 ≥ 3 条长度 ≥ 3 的传播路径汇聚到目标企业 → true<br>• 桥接风险标识：若目标企业为结构洞节点或连接 ≥ 2 个高风险社区 → true |
| 9 | branch | **风险等级判定**：<br>• 综合传染风险指数 ≥ 70 → "高传染风险"<br>• 50-69 → "中传染风险"<br>• 30-49 → "低传染风险"<br>• < 30 → "可控"<br>• 一票否决：若直接关联含失信被执行人/欺诈黑名单 → 直接 "高传染风险" |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_008_NETWORK_CONTAGION_RISK",
  "factorName": "关联方网络多跳风险传染因子",
  "referenceDate": "2024-12-31",
  "targetEnterprise": {"enterpriseId": "TARGET_001", "selfRiskScore": 42, "riskLabels": []},
  "graphStats": {
    "totalNodes": 234,
    "totalEdges": 567,
    "mergedNodes": 18,
    "riskSources": 12,
    "maxHops": 4,
    "layers": {"strong": 145, "medium": 289, "weak": 133}
  },
  "propagationToTarget": {
    "directRisk": 31.2,
    "indirectRisk": 14.8,
    "highRiskAssociates": 7,
    "maxSinglePropagation": 0.68,
    "propagationPaths": 34,
    "keyBridgeNodes": [
      {"nodeId": "PER_045", "betweenness": 0.12, "role": "实际控制人"},
      {"nodeId": "ENT_078", "betweenness": 0.09, "role": "核心供应商"}
    ]
  },
  "communityAnalysis": {
    "totalCommunities": 15,
    "targetCommunity": {"communityId": "COM_03", "size": 28, "riskMean": 0.45, "density": 0.31, "hasRiskSource": true},
    "highRiskCommunities": 3,
    "crossCommunityEdges": 42
  },
  "structuralAnalysis": {
    "structuralHoleNodes": 5,
    "targetIsStructuralHole": false,
    "keyPropagators": ["PER_045", "ENT_078", "ENT_033"],
    "riskConvergenceNodes": ["ENT_078", "PER_012"]
  },
  "indicators": {
    "compositeContagionIndex": 56.73,
    "associativeRiskContribution": 0.6782,
    "structuralContagionFlag": true,
    "bridgeRiskFlag": false
  },
  "riskLevel": "中传染风险",
  "topRiskPaths": [
    {"path": ["TARGET_001", "ENT_078", "PER_045", "ENT_999"], "strength": 0.34, "hops": 3, "keyRelations": ["股权", "任职", "担保"]},
    {"path": ["TARGET_001", "ENT_033", "ENT_056"], "strength": 0.41, "hops": 2, "keyRelations": ["资金往来", "担保"]}
  ]
}
```