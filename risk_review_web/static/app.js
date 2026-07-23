const DEFAULT_FALLBACK_TEXT = "字段缺失、格式错误或数据异常时返回默认值";
const CLIENT_INSTANCE_ID = (() => {
  const key = "riskReviewClientInstanceId";
  let value = localStorage.getItem(key);
  if (!value) {
    value = (globalThis.crypto?.randomUUID?.() || ("client-" + Date.now() + "-" + Math.random().toString(16).slice(2)));
    localStorage.setItem(key, value);
  }
  return value;
})();

const state = {
  apis: [],
  examples: [],
  businessRules: [],
  selectedBusinessRuleId: null,
  selectedExampleId: null,
  history: [],
  validationResults: {},
  activeValidationExampleId: null,
  regressionTaskId: null,
  stopRegressionPending: false,
  currentReviewTaskId: null,
  stopReviewPending: false,
  rawResult: "",
  step1: "",
  step2: "",
  busy: false,
  // Two-phase state
  phase1Done: false,
  phase2Done: false,
  standardFileId: null,     // backend-stored standard file identifier
  standardSummaryFileId: null,
  standardChecksum: null,
  standardModalText: "",
  claudeDebugLogs: [],
  historyDeleteChecked: {},
  historyPage: 1,
  historyPageSize: 10,
  validationListSignature: "",
  validationFiltersSignature: "",
  validationResultsDirty: false,
  validationChart: null,
  readinessChart: null,
  batchPreviewTable: null,
  modalView: "issues",
  modalStep1Text: "",
  modalStep2Text: "",
  pool: null,

  fullReviewMode: false,

};

function renderGlobalPool(pool) {
  if (!pool) return;
  state.pool = pool;
  const el = $("#globalPoolStatus");
  if (!el) return;
  const active = Number(pool.activeWorkers || 0);
  const limit = Number(pool.maxWorkers || 0);
  const queued = Number(pool.queuedJobs || 0);
  el.classList.toggle("busy", active > 0 && active < limit);
  el.classList.toggle("full", limit > 0 && active >= limit);
  el.querySelector("b").textContent = active + " / " + limit;
  el.querySelector("em").textContent = "等待 " + queued;
}

async function refreshGlobalPool() {
  try {
    const res = await fetch("/api/pool/status");
    if (!res.ok) return;
    const data = await res.json();
    renderGlobalPool(data.pool);
    const input = $("#batchConcurrency");
    if (input && data.pool?.maxWorkers) input.max = String(data.pool.maxWorkers);
  } catch (_) {}
}

try {
  state.history = JSON.parse(localStorage.getItem("riskReviewHistoryV2") || "[]");
} catch {
  state.history = [];
}

try {
  state.validationResults = JSON.parse(localStorage.getItem("riskReviewValidationResultsV2") || "{}");
} catch {
  state.validationResults = {};
}

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const BUSINESS_RULE_TYPE_LABELS = {
  business_mandatory: "业务口径强制",
  exception_fallback: "异常兜底语义",
  default_assumption: "默认假设",
  confirmation: "待确认补充",
};

function saveHistory() {
  localStorage.setItem("riskReviewHistoryV2", JSON.stringify(state.history.slice(0, 60)));
  renderHistoryBadge();
}

function saveValidationResults() {
  localStorage.setItem("riskReviewValidationResultsV2", JSON.stringify(state.validationResults || {}));
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

function formatDate(ms) {
  return new Date(ms).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function selectedApis() {
  return state.apis.filter((api) => api.selected).map((api) => api.name);
}

function setPage(page) {
  $$(".page").forEach((el) => el.classList.remove("active"));
  $(`#page-${page}`).classList.add("active");
  $$(".nav-btn").forEach((btn) => btn.classList.toggle("active", btn.dataset.page === page));
  const titles = { single: "单因子评审", batch: "批量评审", history: "评审历史", docs: "接口文档库", rules: "业务知识规则" };
  titles.examples = "验证集工作台";
  $("#pageTitle").textContent = titles[page] || "单因子评审";
  const single = page === "single";
  $("#loadExampleBtn").classList.toggle("hidden", !single);
  $("#clearBtn").classList.toggle("hidden", !single);
  $("#copyAllBtn").classList.toggle("hidden", !single || !state.rawResult);
  if (page === "examples") loadExamples().catch((err) => addLog(String(err.message || err), "err"));
  if (page === "history") renderHistory();
  if (page === "docs") renderDocsPage();
  if (page === "rules") loadBusinessRules().catch((err) => setBusinessRuleStatus(String(err.message || err), "err"));
  if (page === "examples") setTimeout(resizeValidationChart, 80);
  if (page === "single") setTimeout(resizeReadinessChart, 80);
}

function renderHistoryBadge() {
  const badge = $("#historyBadge");
  badge.textContent = state.history.length;
  badge.classList.toggle("hidden", state.history.length === 0);
}

async function loadApis() {
  const res = await fetch("/api/apis");
  const data = await res.json();
  const selected = new Set(state.apis.filter((api) => api.selected).map((api) => api.name));
  state.apis = (data.apis || []).map((api) => ({ ...api, selected: selected.has(api.name), contentLoaded: Boolean(api.content) }));
  renderDocChips();
  renderDocsPage();
}

async function ensureApiContent(api) {
  if (!api || api.contentLoaded) return api;
  const res = await fetch("/api/api-doc?name=" + encodeURIComponent(api.name));
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "接口文档加载失败");
  Object.assign(api, data.api || {}, { contentLoaded: true });
  return api;
}

async function loadExamples() {
  const res = await fetch("/api/examples");
  const data = await res.json();
  state.examples = data.examples || [];
  renderValidationWorkbench();
}

async function loadBusinessRules() {
  const res = await fetch("/api/business-rules");
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "业务知识规则加载失败");
  state.businessRules = data.rules || [];
  renderBusinessRulesList();
}

function renderBusinessRulesList() {
  const el = $("#businessRulesList");
  if (!el) return;
  if (!state.businessRules.length) {
    el.innerHTML = '<div class="muted-box">暂无规则，点击新增规则创建。</div>';
    return;
  }
  el.innerHTML = state.businessRules.map((rule, index) => {
    const active = rule.id === state.selectedBusinessRuleId ? "active" : "";
    const text = rule.text || "";
    const label = BUSINESS_RULE_TYPE_LABELS[rule.type || "business_mandatory"] || "业务口径强制";
    return '<button class="example-item ' + active + '" data-rule-id="' + escapeHtml(rule.id) + '" type="button">' +
      '<div><strong>规则 ' + (index + 1) + ' · ' + escapeHtml(label) + '</strong><span>' + escapeHtml(text.slice(0, 80)) + '</span></div>' +
    '</button>';
  }).join("");
  el.querySelectorAll("[data-rule-id]").forEach((btn) => {
    btn.addEventListener("click", () => selectBusinessRule(btn.dataset.ruleId));
  });
}

function selectBusinessRule(id) {
  const rule = state.businessRules.find((item) => item.id === id);
  if (!rule) return;
  state.selectedBusinessRuleId = id;
  if ($("#businessRuleType")) $("#businessRuleType").value = rule.type || "business_mandatory";
  $("#businessRuleText").value = rule.text || "";
  $("#businessRuleEditHint").textContent = "正在编辑：" + id;
  setBusinessRuleStatus("", "info");
  renderBusinessRulesList();
}

function clearBusinessRuleForm() {
  state.selectedBusinessRuleId = null;
  if ($("#businessRuleType")) $("#businessRuleType").value = "business_mandatory";
  if ($("#businessRuleText")) $("#businessRuleText").value = "";
  if ($("#businessRuleEditHint")) $("#businessRuleEditHint").textContent = "新增规则";
  setBusinessRuleStatus("", "info");
  renderBusinessRulesList();
}

function setBusinessRuleStatus(message, kind = "info") {
  const el = $("#businessRuleStatus");
  if (!el) return;
  el.textContent = message || "";
  el.className = "example-status " + kind;
}

async function saveBusinessRule() {
  const text = $("#businessRuleText").value.trim();
  const type = $("#businessRuleType")?.value || "business_mandatory";
  if (!text) { setBusinessRuleStatus("请填写规则说明。", "err"); return; }
  const id = state.selectedBusinessRuleId || "";
  const existing = state.businessRules.find((item) => item.id === id) || {};
  setBusinessRuleStatus("正在保存...", "info");
  const res = await fetch(id ? "/api/business-rules/" + encodeURIComponent(id) : "/api/business-rules", {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, text, version: existing.version || "" }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "保存规则失败");
  state.businessRules = data.rules || [];
  selectBusinessRule((data.rule || {}).id);
  setBusinessRuleStatus("规则已保存。", "ok");
}

async function deleteBusinessRule() {
  const id = state.selectedBusinessRuleId;
  if (!id) { setBusinessRuleStatus("请先选择规则。", "err"); return; }
  const res = await fetch("/api/business-rules/" + encodeURIComponent(id), { method: "DELETE" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "删除规则失败");
  state.businessRules = data.rules || [];
  clearBusinessRuleForm();
  setBusinessRuleStatus("规则已删除。", "ok");
}

function renderExamplesList() {
  const el = $("#examplesList");
  if (!el) return;
  if (!state.examples.length) {
    el.innerHTML = '<div class="muted-box">暂无案例，点击新增创建。</div>';
    state.validationListSignature = "empty";
    return;
  }
  const query = ($("#validationSearch")?.value || "").toLowerCase().trim();
  const kindFilter = $("#validationKindFilter")?.value || "";
  const statusFilter = $("#validationStatusFilter")?.value || "";
  const filtered = state.examples.filter((item) => {
    const result = state.validationResults[item.id];
    const display = getValidationDisplayStatus(result);
    const haystack = [item.title, item.kind, item.factorName, (item.apiNames || []).join("、"), item.requirement, item.expected].join("\n").toLowerCase();
    if (query && !haystack.includes(query)) return false;
    if (kindFilter && item.kind !== kindFilter) return false;
    if (statusFilter && display.filterStatus !== statusFilter) return false;
    return true;
  });
  if (!filtered.length) {
    el.innerHTML = '<div class="muted-box">没有匹配的验证用例。</div>';
    state.validationListSignature = "none:" + [query, kindFilter, statusFilter].join("|");
    return;
  }
  const signature = filtered.map((item) => {
    const display = getValidationDisplayStatus(state.validationResults[item.id]);
    const active = item.id === state.selectedExampleId ? "1" : "0";
    return JSON.stringify([item.id, active, display.className, display.label, item.title || "", item.factorName || ""]);
  }).join("|");
  if (signature === state.validationListSignature && el.children.length) return;
  state.validationListSignature = signature;
  el.innerHTML = filtered.map((item) => {
    const active = item.id === state.selectedExampleId ? "active" : "";
    const result = state.validationResults[item.id];
    const display = getValidationDisplayStatus(result);
    return '<button class="example-item ' + active + '" data-example-id="' + escapeHtml(item.id) + '" type="button">' +
      '<div><strong>' + escapeHtml(item.title || item.id) + '</strong><span>' + escapeHtml(item.factorName || "") + '</span></div>' +
      '<em class="validation-badge ' + display.className + '">' + display.label + '</em>' +
    '</button>';
  }).join("");
}

function getValidationDisplayStatus(result) {
  if (!result) return { className: "untested", label: "未验证", filterStatus: "untested" };
  if (result.verdict === "pass") return { className: "pass", label: "通过", filterStatus: "pass" };
  if (result.verdict === "warn") return { className: "warn", label: "待确认", filterStatus: "pass" };
  if (result.verdict === "fail") return { className: "fail", label: "不通过", filterStatus: "fail" };
  if (result.status === "error") return { className: "fail", label: "异常", filterStatus: "fail" };
  if (result.status === "pass") return { className: "pass", label: "通过", filterStatus: "pass" };
  if (result.status === "fail") return { className: "fail", label: "未通过", filterStatus: "fail" };
  return { className: "untested", label: "未验证", filterStatus: "untested" };
}

function selectExample(id) {
  const example = state.examples.find((item) => item.id === id);
  if (!example) return;
  const sections = splitDraftSections(example);
  state.selectedExampleId = id;
  $("#exampleId").value = example.id || "";
  $("#exampleTitle").value = example.title || "";
  $("#exampleKind").value = example.kind || "";
  $("#exampleFactorName").value = example.factorName || "";
  $("#exampleApiNames").value = (example.apiNames || []).join("、");
  $("#exampleRequirement").value = example.requirement || "";
  $("#exampleParamFields").value = sections.paramFields || "";
  $("#exampleProcLogic").value = sections.procLogic || "";
  $("#exampleDefaultValues").value = DEFAULT_FALLBACK_TEXT;
  $("#exampleExpected").value = example.expected || "";
  $("#exampleEditHint").textContent = "正在编辑：" + (example.title || example.id);
  renderValidationResultPanel();
  renderExamplesList();
}

function clearExampleForm() {
  state.selectedExampleId = null;
  ["#exampleId", "#exampleTitle", "#exampleKind", "#exampleFactorName", "#exampleApiNames", "#exampleRequirement", "#exampleParamFields", "#exampleProcLogic", "#exampleDefaultValues", "#exampleExpected"].forEach((id) => {
    const el = $(id);
    if (el) el.value = "";
  });
  $("#exampleEditHint").textContent = "新增案例";
  renderValidationResultPanel();
  renderExamplesList();
}

function splitDraftSections(example) {
  const draft = example?.draft || "";
  const findSec = (tag) => {
    const match = draft.match(new RegExp("【" + tag + "】\\s*([\\s\\S]*?)(?=\\n\\n【|\\n【[^】]+】|$)"));
    return match ? match[1].trim() : "";
  };
  return {
    paramFields: example?.paramFields || findSec("因子加工输入字段"),
    procLogic: example?.procLogic || findSec("加工逻辑"),
    defaultValues: example?.defaultValues || findSec("默认值"),
  };
}

function buildDraftFromParts(paramFields, procLogic, defaultValues) {
  const parts = [];
  if (paramFields) parts.push("【因子加工输入字段】\n" + paramFields);
  if (procLogic) parts.push("【加工逻辑】\n" + procLogic);
  if (parts.length) parts.push("【默认值】\n" + DEFAULT_FALLBACK_TEXT);
  return parts.join("\n\n");
}

const buildExampleDraftFromParts = buildDraftFromParts;

function collectExampleForm() {
  const paramFields = $("#exampleParamFields").value.trim();
  const procLogic = $("#exampleProcLogic").value.trim();
  const defaultValues = $("#exampleDefaultValues").value.trim();
  return {
    id: $("#exampleId").value.trim(),
    title: $("#exampleTitle").value.trim(),
    kind: $("#exampleKind").value.trim(),
    factorName: $("#exampleFactorName").value.trim(),
    apiNames: $("#exampleApiNames").value.trim(),
    requirement: $("#exampleRequirement").value.trim(),
    paramFields,
    procLogic,
    defaultValues,
    draft: buildExampleDraftFromParts(paramFields, procLogic, defaultValues),
    expected: $("#exampleExpected").value.trim(),
    version: (state.examples.find((item) => item.id === (state.selectedExampleId || $("#exampleId").value.trim())) || {}).version || "",
  };
}

function setExampleStatus(message, kind = "info") {
  const el = $("#exampleStatus");
  if (!el) return;
  el.textContent = message || "";
  el.className = "example-status " + kind;
}

async function saveExample() {
  const payload = collectExampleForm();
  const id = payload.id || state.selectedExampleId || "";
  const missing = [];
  if (!payload.title) missing.push("案例标题");
  if (!payload.factorName) missing.push("因子名");
  if (!payload.requirement) missing.push("需求内容");
  if (!payload.apiNames) missing.push("接口文档");
  if (!payload.draft) missing.push("待评审内容");
  if (missing.length) {
    setExampleStatus("请补充：" + missing.join("、"), "err");
    return;
  }
  setExampleStatus("正在保存...", "info");
  $("#saveExampleBtn").disabled = true;
  const res = await fetch(id ? "/api/examples/" + encodeURIComponent(id) : "/api/examples", {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  try {
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "保存案例失败");
    state.examples = data.examples || [];
    selectExample((data.example || {}).id);
    renderValidationWorkbench();
    setExampleStatus("案例已保存。", "ok");
    addLog("案例已保存", "ok");
  } finally {
    $("#saveExampleBtn").disabled = false;
  }
}

async function deleteExample() {
  const id = $("#exampleId").value.trim() || state.selectedExampleId;
  if (!id) return;
  setExampleStatus("正在删除...", "info");
  const res = await fetch("/api/examples/" + encodeURIComponent(id), { method: "DELETE" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "删除案例失败");
  state.examples = data.examples || [];
  delete state.validationResults[id];
  saveValidationResults();
  clearExampleForm();
  renderValidationWorkbench();
  setExampleStatus("案例已删除。", "ok");
  addLog("案例已删除", "ok");
}

function renderValidationWorkbench() {
  renderValidationFilters();
  renderValidationStats();
  renderExamplesList();
  renderValidationResultPanel();
}

function renderValidationFilters() {
  const select = $("#validationKindFilter");
  if (!select) return;
  const current = select.value;
  const kinds = [...new Set(state.examples.map((item) => item.kind).filter(Boolean))].sort();
  const signature = kinds.join("|");
  if (signature === state.validationFiltersSignature) return;
  state.validationFiltersSignature = signature;
  select.innerHTML = '<option value="">全部类型</option>' + kinds.map((kind) => {
    return '<option value="' + escapeHtml(kind) + '">' + escapeHtml(kind) + '</option>';
  }).join("");
  select.value = kinds.includes(current) ? current : "";
}

function renderValidationStats() {
  const total = state.examples.length;
  let passed = 0;
  let failed = 0;
  state.examples.forEach((item) => {
    const display = getValidationDisplayStatus(state.validationResults[item.id]);
    if (display.filterStatus === "pass") passed++;
    if (display.filterStatus === "fail") failed++;
  });
  const untested = total - passed - failed;
  if ($("#validationTotal")) $("#validationTotal").textContent = total;
  if ($("#validationPassed")) $("#validationPassed").textContent = passed;
  if ($("#validationFailed")) $("#validationFailed").textContent = failed;
  if ($("#validationUntested")) $("#validationUntested").textContent = Math.max(0, untested);
  renderValidationStatusChart({ total, passed, failed, untested: Math.max(0, untested) });
}

function renderValidationStatusChart(stats) {
  const el = $("#validationStatusChart");
  if (!el || typeof echarts === "undefined") return;
  if (!state.validationChart) state.validationChart = echarts.init(el, null, { renderer: "canvas" });
  const total = Number(stats.total || 0);
  const data = [
    { name: "通过", value: Number(stats.passed || 0), itemStyle: { color: "#1f8a4c" } },
    { name: "未通过", value: Number(stats.failed || 0), itemStyle: { color: "#c24133" } },
    { name: "未验证", value: Number(stats.untested || 0), itemStyle: { color: "#94a3b8" } },
  ];
  const hint = $("#validationChartHint");
  if (hint) hint.textContent = total ? "共 " + total + " 条" : "等待数据";
  state.validationChart.setOption({
    animationDuration: 450,
    tooltip: {
      trigger: "item",
      borderWidth: 0,
      padding: [6, 8],
      textStyle: { fontSize: 11 },
      formatter: "{b}<br/>{c} 条 ({d}%)",
    },
    legend: {
      orient: "vertical",
      right: 0,
      top: "center",
      itemWidth: 9,
      itemHeight: 9,
      textStyle: { color: "#64748b", fontSize: 11 },
    },
    series: [{
      type: "pie",
      radius: ["52%", "74%"],
      center: ["36%", "50%"],
      avoidLabelOverlap: true,
      minAngle: 6,
      label: {
        show: true,
        position: "center",
        formatter: total ? "{total|" + total + "}\n{label|用例}" : "{label|暂无数据}",
        rich: {
          total: { fontSize: 22, fontWeight: 800, color: "#111827", lineHeight: 26 },
          label: { fontSize: 11, color: "#64748b", lineHeight: 18 },
        },
      },
      labelLine: { show: false },
      data: total ? data : [{ name: "暂无数据", value: 1, itemStyle: { color: "#e5e7eb" } }],
    }],
  });
  state.validationChart.resize();
}

function resizeValidationChart() {
  if (state.validationChart) state.validationChart.resize();
}

function reviewReadinessState() {
  const factor = Boolean($("#factorCnName")?.value.trim());
  const requirement = Boolean($("#requirement")?.value.trim());
  const apis = selectedApis().length > 0;
  const draft = Boolean($("#procLogic")?.value.trim() || $("#paramFields")?.value.trim());
  return {
    factor,
    requirement,
    apis,
    draft,
    readyCount: [factor, requirement, apis, draft].filter(Boolean).length,
    total: 4,
  };
}

function updateReadinessCheck(key, ready, readyText, emptyText) {
  const el = document.querySelector('[data-ready-key="' + key + '"]');
  if (!el) return;
  el.classList.toggle("ready", Boolean(ready));
  const detail = el.querySelector("em");
  if (detail) detail.textContent = ready ? readyText : emptyText;
}

function updateReviewReadiness() {
  const status = reviewReadinessState();
  const percent = Math.round((status.readyCount / status.total) * 100);
  updateReadinessCheck("factor", status.factor, "已填写", "待填写");
  updateReadinessCheck("requirement", status.requirement, "已填写", "待填写");
  updateReadinessCheck("apis", status.apis, "已选择 " + selectedApis().length + " 份", "待选择");
  updateReadinessCheck("draft", status.draft, "已填写", "待填写");
  const hint = $("#readinessHint");
  if (hint) hint.textContent = percent === 100 ? "输入已就绪，可以开始评审" : "已完成 " + status.readyCount + " / " + status.total + " 项";
  const el = $("#readinessChart");
  if (!el || typeof echarts === "undefined") return;
  if (!state.readinessChart) state.readinessChart = echarts.init(el, null, { renderer: "canvas" });
  state.readinessChart.setOption({
    animationDuration: 350,
    series: [{
      type: "gauge",
      startAngle: 210,
      endAngle: -30,
      min: 0,
      max: 100,
      radius: "92%",
      center: ["50%", "56%"],
      progress: {
        show: true,
        width: 10,
        roundCap: true,
        itemStyle: { color: percent === 100 ? "#207245" : "#275da8" },
      },
      axisLine: { lineStyle: { width: 10, color: [[1, "#e7e8ec"]] } },
      pointer: { show: false },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      anchor: { show: false },
      detail: {
        valueAnimation: true,
        offsetCenter: [0, "6%"],
        formatter: percent + "%",
        color: "#111827",
        fontSize: 22,
        fontWeight: 800,
      },
      title: {
        offsetCenter: [0, "42%"],
        color: "#64748b",
        fontSize: 11,
      },
      data: [{ value: percent, name: "READY" }],
    }],
  });
  state.readinessChart.resize();
}

function resizeReadinessChart() {
  if (state.readinessChart) state.readinessChart.resize();
}

function expectedMatchesVerdict(example, verdict) {
  const expected = String(example?.expected || "");
  if (!expected.trim()) return true;
  const allowed = new Set();
  if (/不通过|不符合|失败|fail/i.test(expected)) allowed.add("fail");
  if (/建议|修改|warn/i.test(expected)) {
    allowed.add("warn");
    allowed.add("pass");
  }
  const positiveExpected = expected
    .replace(/不通过|不符合|失败|fail/ig, "")
    .replace(/建议修改后通过|建议|修改|warn/ig, "");
  if (/通过|符合|pass/i.test(positiveExpected) || /✅\s*(通过|符合)/.test(expected)) allowed.add("pass");
  if (allowed.size) return allowed.has(verdict);
  return true;
}

function buildValidationRecord(example, reportText, standardText = "", standardFileId = "", reviewFileId = "") {
  const verdict = detectVerdict(reportText);
  const expectedOk = expectedMatchesVerdict(example, verdict);
  return {
    status: expectedOk ? "pass" : "fail",
    verdict,
    expectedOk,
    expected: example?.expected || "",
    summary: collectStats(reportText),
    time: Date.now(),
    raw: String(reportText || ""),
    reportPreview: String(reportText || "").slice(0, 600),
    standardText: String(standardText || ""),
    standardFileId: standardFileId || "",
    reviewFileId: reviewFileId || "",
  };
}

function renderValidationResultPanel() {
  const el = $("#validationResultBody");
  if (!el) return;
  let id = state.selectedExampleId;
  if (!id) {
    const latest = state.examples
      .map((item) => ({ id: item.id, time: state.validationResults[item.id]?.time || 0 }))
      .filter((item) => item.time)
      .sort((a, b) => b.time - a.time)[0];
    if (latest?.id) {
      state.selectedExampleId = latest.id;
      id = latest.id;
    }
  }
  if (!id) {
    el.innerHTML = "选择用例后展示最近一次验证记录。";
    return;
  }
  const result = state.validationResults[id];
  if (!result) {
    el.innerHTML = '<span class="validation-muted">尚未验证。可点击“验证当前用例”。</span>';
    return;
  }
  const verdictMap = { pass: "通过", warn: "存在待确认项", fail: "不通过", idle: "待确认" };
  const verdictText = verdictMap[result.verdict] || result.verdict || "未知";
  const statusText = result.verdict === "pass" ? "✅ 通过" : result.verdict === "warn" ? "⚠️ 存在待确认项" : result.verdict === "fail" ? "❌ 不通过" : "🔵 待确认";
  el.innerHTML = `
    <div class="validation-result-row"><b>${statusText}</b><span>${formatDate(result.time)}</span></div>
    <div class="validation-result-row"><span>实际结论</span><strong>${escapeHtml(verdictText)}</strong></div>
    <div class="validation-result-row"><span>期望匹配</span><strong>${result.expectedOk ? "匹配" : "不匹配"}</strong></div>
    <div class="validation-result-row"><span>问题数</span><strong>不符合 ${result.summary?.fail || 0} / 待确认 ${result.summary?.pending || 0} / 建议 ${result.summary?.suggest || 0}</strong></div>
    <div class="example-actions" style="margin-top:8px">
      <button class="mini-btn" id="viewValidationStandardBtn" type="button" ${result.standardText || result.standardFileId ? "" : "disabled"}>查看标准规范</button>
      <button class="mini-btn" id="viewValidationReviewBtn" type="button" ${result.raw || result.reportPreview ? "" : "disabled"}>查看评审报告</button>
    </div>
  `;
  $("#viewValidationStandardBtn")?.addEventListener("click", () => openValidationArtifact("standard"));
  $("#viewValidationReviewBtn")?.addEventListener("click", () => openValidationArtifact("review"));
}

function recordValidationResult(example, reportText, standardText = state.step1, standardFileId = state.standardFileId, reviewFileId = "") {
  if (!example?.id || !reportText) return;
  state.selectedExampleId = example.id;
  state.validationResults[example.id] = buildValidationRecord(example, reportText, standardText, standardFileId, reviewFileId);
  state.validationListSignature = "";
  saveValidationResults();
  renderValidationStats();
  renderExamplesList();
  renderValidationResultPanel();
}

function getSelectedExample() {
  const id = $("#exampleId")?.value.trim() || state.selectedExampleId;
  return state.examples.find((item) => item.id === id);
}

function runSelectedValidation() {
  const example = getSelectedExample();
  if (!example) {
    setExampleStatus("请先选择一个验证用例。", "err");
    return;
  }
  state.activeValidationExampleId = example.id;
  applyExample(example);
  addLog("开始验证用例：" + (example.title || example.id), "info");
  setTimeout(() => runFullReview(), 120);
}

function recordCurrentValidationResult() {
  const example = getSelectedExample();
  if (!example) {
    setExampleStatus("请先选择一个验证用例。", "err");
    return;
  }
  if (!state.step2) {
    setExampleStatus("当前没有可记录的评审报告，请先完成评审。", "err");
    return;
  }
  recordValidationResult(example, state.step2);
  setExampleStatus("已记录当前评审结果。", "ok");
}

async function openValidationArtifact(kind) {
  const id = state.selectedExampleId;
  const result = id ? state.validationResults[id] : null;
  if (!result) return;
  const title = kind === "standard" ? "验证结果 · 标准加工规范" : "验证结果 · 评审报告";
  openMarkdownModal(title, "", kind === "standard" ? "Phase 1" : "Phase 2", kind === "standard" ? "s1" : "s2");
  $("#standardModalBody").innerHTML = '<div class="empty-pane">正在加载' + (kind === "standard" ? "标准规范" : "评审报告") + '...</div>';
  try {
    let text = "";
    if (kind === "standard") {
      text = result.standardText || "";
      if (!text && result.standardFileId) {
        const res = await fetch("/outputs/" + encodeURIComponent(result.standardFileId));
        if (!res.ok) throw new Error("标准规范文件加载失败");
        text = await res.text();
        result.standardText = text;
        saveValidationResults();
      }
    } else {
      text = result.raw || result.reportPreview || "";
    }
    state.standardModalText = text || "暂无内容";
    renderStandardModal("rendered");
  } catch (err) {
    $("#standardModalBody").innerHTML = '<div class="empty-pane">加载失败：' + escapeHtml(String(err.message || err)) + '</div>';
  }
}

async function runRegressionValidation() {
  if (!state.examples.length) await loadExamples();
  if (!state.examples.length) {
    setExampleStatus("没有可验证的用例。", "err");
    return;
  }
  if (state.busy) {
    addLog("当前有任务运行中，请稍后再试。", "err");
    return;
  }
  state.busy = true;
  const btn = $("#runRegressionBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "回归验证中...";
  }
  if ($("#stopRegressionBtn")) $("#stopRegressionBtn").style.display = "";
  if ($("#stopRegressionBtn")) {
    $("#stopRegressionBtn").disabled = false;
    $("#stopRegressionBtn").textContent = "■ 停止回归";
  }
  updateRegressionProgress({ total: state.examples.length, done: 0, pass: 0, fail: 0, error: 0, cacheHit: 0, active: true });
  addLog("一键回归验证开始，共 " + state.examples.length + " 条", "info");
  try {
    const res = await fetch("/api/validation/regression", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clientId: CLIENT_INSTANCE_ID,
        skipStageValidation: shouldSkipStageValidation(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "回归验证启动失败");
    state.regressionTaskId = data.taskId;
    if (state.stopRegressionPending) {
      await stopRegressionValidation();
    }
    await pollRegressionTask(data.taskId);
  } catch (err) {
    addLog(String(err.message || err), "err");
    setExampleStatus(String(err.message || err), "err");
  } finally {
    if (state.validationResultsDirty) {
      saveValidationResults();
      state.validationResultsDirty = false;
    }
    state.busy = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = "⚡ 一键回归验证";
    }
    state.regressionTaskId = null;
    state.stopRegressionPending = false;
    if ($("#stopRegressionBtn")) $("#stopRegressionBtn").style.display = "none";
  }
}

function updateRegressionProgress(info) {
  const wrap = $("#regressionProgress");
  const bar = $("#regressionProgressBar");
  const text = $("#regressionProgressText");
  if (!wrap || !bar || !text) return;
  const total = Math.max(0, Number(info.total || 0));
  const done = Math.max(0, Number(info.done || 0));
  const visualDone = Math.max(done, Number(info.visualDone || 0));
  const percent = total ? Math.min(100, Math.round((visualDone / total) * 100)) : 0;
  wrap.style.display = info.active || done || total ? "" : "none";
  bar.style.width = percent + "%";
  const currentText = info.currentTitle ? "；当前 " + info.currentIndex + "/" + total + "：" + info.currentTitle : "";
  text.textContent = (info.label || "回归验证") + "：" + done + "/" + total + "（" + percent + "%）" +
    "；通过 " + (info.pass || 0) +
    "，未通过 " + (info.fail || 0) +
    "，异常 " + (info.error || 0) +
    "，跳过 " + (info.skip || 0) +
    "，缓存命中 " + (info.cacheHit || 0) +
    currentText;
}

async function stopRegressionValidation() {
  if (!state.regressionTaskId) {
    if (state.busy) {
      state.stopRegressionPending = true;
      updateRegressionProgress({ total: state.examples.length, done: 0, pass: 0, fail: 0, error: 0, cacheHit: 0, active: true, label: "等待停止" });
      if ($("#stopRegressionBtn")) {
        $("#stopRegressionBtn").disabled = true;
        $("#stopRegressionBtn").textContent = "停止中...";
      }
      setExampleStatus("已收到停止请求，任务创建后会立即停止。", "info");
      return;
    }
    setExampleStatus("当前没有正在运行的回归验证。", "err");
    return;
  }
  const taskId = state.regressionTaskId;
  if ($("#stopRegressionBtn")) {
    $("#stopRegressionBtn").disabled = true;
    $("#stopRegressionBtn").textContent = "停止中...";
  }
  updateRegressionProgress({ total: state.examples.length, done: 0, pass: 0, fail: 0, error: 0, cacheHit: 0, active: true, label: "正在停止" });
  setExampleStatus("正在请求停止回归验证...", "info");
  try {
    const res = await fetch("/api/tasks/" + encodeURIComponent(taskId) + "/cancel", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "停止回归验证失败");
    state.stopRegressionPending = false;
    setExampleStatus("已请求停止，正在中止当前 Claude 调用并停止后续执行。", "info");
    addLog("已请求停止一键回归验证", "info");
  } catch (err) {
    if ($("#stopRegressionBtn")) {
      $("#stopRegressionBtn").disabled = false;
      $("#stopRegressionBtn").textContent = "■ 停止回归";
    }
    setExampleStatus(String(err.message || err), "err");
    addLog(String(err.message || err), "err");
  }
}

async function pollRegressionTask(taskId) {
  while (true) {
    const res = await fetch("/api/tasks/" + taskId);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "任务不存在");
    const task = data.task || {};
    renderGlobalPool(data.pool);
    renderTaskLogs(task.logs || []);
    const result = task.result || {};
    const items = result.items || [];
    let changed = false;
    items.forEach((item) => {
      if (!item.id || !["pass", "fail", "error"].includes(item.status)) return;
      const nextRecord = {
        status: item.status === "pass" ? "pass" : "fail",
        verdict: item.verdict || "idle",
        expectedOk: Boolean(item.expectedOk),
        expected: item.expected || "",
        summary: item.summary || {},
        time: item.time || Date.now(),
        raw: item.raw || "",
        reviewFileId: item.reviewFileId || "",
        reportPreview: item.reportPreview || item.raw || "",
        standardText: item.standardText || "",
        standardFileId: item.standardFileId || "",
        cacheHit: Boolean(item.cacheHit),
      };
      const prevRecord = state.validationResults[item.id] || {};
      const prevSignature = [prevRecord.status, prevRecord.verdict, prevRecord.expectedOk, prevRecord.time, prevRecord.reviewFileId, prevRecord.standardFileId].join("|");
      const nextSignature = [nextRecord.status, nextRecord.verdict, nextRecord.expectedOk, nextRecord.time, nextRecord.reviewFileId, nextRecord.standardFileId].join("|");
      if (prevSignature !== nextSignature) {
        state.validationResults[item.id] = nextRecord;
        changed = true;
      }
    });
    if (changed) {
      state.validationListSignature = "";
      state.validationResultsDirty = true;
      renderValidationStats();
      renderExamplesList();
      renderValidationResultPanel();
    }
    const summary = result.summary || {};
    const done = (summary.pass || 0) + (summary.fail || 0) + (summary.error || 0) + (summary.skip || 0);
    const total = summary.total || items.length || state.examples.length;
    const runningItem = items.find((item) => item.status === "running");
    const visualDone = runningItem ? done + 0.5 : done;
    updateRegressionProgress({
      ...summary,
      done,
      total,
      visualDone,
      active: task.status === "queued" || task.status === "running" || task.status === "cancelling",
      label: task.status === "cancelling" ? "正在停止" : (task.status === "queued" ? ("线程池排队" + (task.queuePosition ? "第 " + task.queuePosition + " 位" : "")) : "回归验证"),
      currentIndex: runningItem ? (runningItem.index || 0) + 1 : summary.currentIndex,
      currentTitle: runningItem ? (runningItem.title || runningItem.factorName || runningItem.id || "") : summary.currentTitle,
    });
    if (summary.total) {
      setExampleStatus("回归进度：" + done + "/" + summary.total + "，通过 " + (summary.pass || 0) + "，未通过 " + (summary.fail || 0) + "，异常 " + (summary.error || 0) + "，跳过 " + (summary.skip || 0) + "，缓存命中 " + (summary.cacheHit || 0), "info");
    }
    if (task.status === "queued" || task.status === "running" || task.status === "cancelling") {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      continue;
    }
    if (task.status !== "done" && task.status !== "cancelled") throw new Error(taskFailureMessage(result, "回归验证失败：" + task.status));
    if (state.validationResultsDirty) {
      saveValidationResults();
      state.validationResultsDirty = false;
    }
    const finalSummary = result.summary || {};
    const latestItem = [...items].filter((item) => item.id && ["pass", "fail", "error"].includes(item.status)).pop();
    if (latestItem?.id) {
      state.selectedExampleId = latestItem.id;
      selectExample(latestItem.id);
    }
    const ok = (finalSummary.fail || 0) === 0 && (finalSummary.error || 0) === 0;
    const stopped = task.status === "cancelled";
    updateRegressionProgress({ ...finalSummary, done: (finalSummary.pass || 0) + (finalSummary.fail || 0) + (finalSummary.error || 0) + (finalSummary.skip || 0), total: finalSummary.total || items.length, active: false, label: stopped ? "已停止" : "已完成" });
    setExampleStatus((stopped ? "回归已停止：" : "回归完成：") + "通过 " + (finalSummary.pass || 0) + " / 未通过 " + (finalSummary.fail || 0) + " / 异常 " + (finalSummary.error || 0) + " / 跳过 " + (finalSummary.skip || 0) + " / 缓存命中 " + (finalSummary.cacheHit || 0) + "。已选中最近用例，可在下方查看结果。", stopped ? "info" : (ok ? "ok" : "err"));
    addLog((stopped ? "回归验证已停止：" : "回归验证完成：") + "通过 " + (finalSummary.pass || 0) + "，未通过 " + (finalSummary.fail || 0) + "，异常 " + (finalSummary.error || 0) + "，跳过 " + (finalSummary.skip || 0), stopped ? "info" : (ok ? "ok" : "err"));
    return;
  }
}



function renderDocChips() {
  const el = $("#docChips");
  if (!state.apis.length) {
    el.innerHTML = `<span class="muted-box">暂无文档，请检查 raw/api 目录</span>`;
    $("#docCountHint").textContent = "";
    return;
  }
  el.innerHTML = state.apis.map((api, index) => `
    <button class="doc-chip ${api.selected ? "on" : ""}" data-chip-index="${index}" type="button">${escapeHtml(api.name)}</button>
  `).join("");
  el.querySelectorAll("[data-chip-index]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const api = state.apis[Number(chip.dataset.chipIndex)];
      api.selected = !api.selected;
      renderDocChips();
      updateReviewReadiness();
    });
  });
  const count = selectedApis().length;
  $("#docCountHint").textContent = `已选 ${count}/${state.apis.length} 份`;
  updateReviewReadiness();
}

function renderDocsPage() {
  const preview = $("#docsPreview");
  const tree = $("#docsTree");
  if (!tree) return;
  if (!state.apis.length) {
    tree.innerHTML = '<div class="docs-tree-empty">暂无接口文档。</div>';
    if (preview) preview.innerHTML = '<div class="docs-empty">暂无接口文档</div>';
    return;
  }
  const query = (document.getElementById("docsSearch")?.value || "").toLowerCase().trim();
  const filtered = query ? state.apis.filter(a => [a.name, a.path, a.provider, a.category, (a.keywords || []).join(" ")].join(" ").toLowerCase().includes(query)) : state.apis;
  const groups = {};
  filtered.forEach((api) => {
    const cat = api.category || "其他";
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(api);
  });
  const catOrder = ["本系统接口", "外部接口", "其他"];
  const entries = Object.entries(groups).sort((a, b) => {
    const ia = catOrder.indexOf(a[0]), ib = catOrder.indexOf(b[0]);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
  const total = filtered.length;
  const treeHtml = `${
    query ? `<div class="docs-tree-empty">搜索结果：${total} 条</div>` : ""
  }${
    entries.map(([cat, apis]) => {
      const collapsed = localStorage.getItem("docsCat_" + cat) === "1";
      return `<div class="docs-tree-cat ${collapsed ? "collapsed" : ""}">
        <div class="docs-tree-cat-head" data-cat="${escapeHtml(cat)}">
          <span class="docs-tree-cat-arrow">\u25bc</span>
          <span>${escapeHtml(cat)}</span>
          <span class="docs-tree-cat-count">${apis.length}</span>
        </div>
        <div class="docs-tree-items">${apis.map((api, idx) => {
          const method = (api.method || "").toLowerCase();
          const methodClass = method === "get" ? "get" : method === "post" ? "post" : method === "put" ? "put" : method === "delete" ? "del" : "";
          const dataIdx = state.apis.indexOf(api);
          return `<div class="docs-tree-item" data-doc-index="${dataIdx}">
            ${method ? `<span class="docs-tree-item-method ${methodClass}">${method.toUpperCase()}</span>` : ""}
            <span class="docs-tree-item-name">${escapeHtml(api.name)}</span>
            ${api.version ? `<span class="docs-tree-item-version">${escapeHtml(api.version)}</span>` : ""}
          </div>`;
        }).join("")}</div>
      </div>`;
    }).join("")
  }`;
  tree.innerHTML = treeHtml || '<div class="docs-tree-empty">无匹配结果</div>';
  tree.querySelectorAll(".docs-tree-cat-head").forEach((head) => {
    head.addEventListener("click", () => {
      const catEl = head.closest(".docs-tree-cat");
      catEl.classList.toggle("collapsed");
      const cat = head.dataset.cat;
      localStorage.setItem("docsCat_" + cat, catEl.classList.contains("collapsed") ? "1" : "0");
    });
  });
  tree.querySelectorAll(".docs-tree-item").forEach((item) => {
    item.addEventListener("click", () => {
      tree.querySelectorAll(".docs-tree-item.active").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
      previewDoc(Number(item.dataset.docIndex));
    });
  });
}

async function previewDoc(index) {
  const api = state.apis[index];
  const preview = $("#docsPreview");
  if (!preview) return;
  preview.innerHTML = '<div class="docs-empty">正在加载接口文档...</div>';
  try { await ensureApiContent(api); } catch (err) { preview.innerHTML = '<div class="docs-empty">' + escapeHtml(err.message || err) + '</div>'; return; }
  const methodColor = (m) => { const l = (m || "").toLowerCase(); return l === "get" ? "#1e40af" : l === "post" ? "#166534" : l === "put" ? "#92400e" : l === "delete" ? "#991b1b" : "#666"; };
  const method = api.method || "";
  const scenes = Array.isArray(api.scenes) ? api.scenes : [];
  const keywords = Array.isArray(api.keywords) ? api.keywords : [];
  const content = api.content || "";
  const sections = extractDocSections(content);
  const contentHtml = sections.map(s => {
    if (s.type === "table") {
      return parseDocTable(s.raw);
    }
    return `<div class="docs-preview-section"><div class="docs-preview-section-title">${escapeHtml(s.heading || "")}</div><div class="docs-preview-body">${escapeHtml(s.raw || "").replace(/\n/g, "<br>")}</div></div>`;
  }).join("");
  preview.innerHTML = `
    <div class="docs-preview-content">
      <div class="docs-preview-header">
        <div class="docs-preview-title">${escapeHtml(api.name)}</div>
        <div class="docs-preview-meta">
          ${method ? `<span class="docs-badge method" style="background:${methodColor(method)};color:#fff">${method.toUpperCase()}</span>` : ""}
          ${api.version ? `<span class="docs-badge version">v${escapeHtml(api.version)}</span>` : ""}
          ${api.status ? `<span class="docs-badge status ${escapeHtml(api.status)}">${escapeHtml(api.status)}</span>` : ""}
          ${api.provider ? `<span class="docs-badge provider">${escapeHtml(api.provider)}</span>` : ""}
          ${scenes.slice(0, 3).map(s => `<span class="docs-badge scene">${escapeHtml(s)}</span>`).join("")}
        </div>
        ${api.url ? `<div class="docs-preview-url">${escapeHtml(api.url)}</div>` : ""}
      </div>
      ${contentHtml || "<div class='docs-preview-body'>无内容</div>"}
    </div>
  `;
}

function extractDocSections(text) {
  const sections = [];
  const lines = text.split("\n");
  let current = null;
  let inFrontmatter = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (i === 0 && line.trim() === "---") { inFrontmatter = true; continue; }
    if (inFrontmatter) { if (line.trim() === "---") inFrontmatter = false; continue; }
    if (/^##\s+/.test(line) || /^###\s+/.test(line)) {
      if (current) sections.push(current);
      current = { heading: line.replace(/^#+\s*/, "").trim(), body: [], type: "text", raw: "" };
      continue;
    }
    if (/^\|.+\|\s*$/.test(line.trim()) && current) {
      current.type = "table";
      current.raw += (current.raw ? "\n" : "") + line;
      continue;
    }
    if (current && current.type === "table" && !/^\|/.test(line.trim())) {
      current.type = "table";
      continue;
    }
    if (!current) current = { heading: "", body: [], type: "text", raw: "" };
    current.body.push(line);
    current.raw += "\n" + line;
  }
  if (current) sections.push(current);
  return sections;
}

function parseDocTable(raw) {
  const rows = raw.trim().split("\n").filter(r => r.trim());
  if (rows.length < 2) return escapeHtml(raw);
  const headerCells = rows[0].split("|").slice(1, -1).map(c => c.trim());
  const dataRows = rows.slice(1).filter(r => !/^\|\s*-+\s*\|/.test(r));
  const html = dataRows.map(r => {
    const cells = r.split("|").slice(1, -1).map(c => c.trim());
    return "<tr>" + cells.map((c, ci) => `<td>${escapeHtml(c)}</td>`).join("") + "</tr>";
  }).join("");
  if (!html) return escapeHtml(raw);
  return `<div class="docs-preview-section"><table><tr>${headerCells.map(h => `<th>${escapeHtml(h)}</th>`).join("")}</tr>${html}</table></div>`;
}

function applyExample(example) {
  if (!example) {
    addLog("未找到验证集案例", "err");
    return;
  }
  $("#factorCnName").value = example.factorName || "";
  $("#requirement").value = example.requirement || "";
  const sections = splitDraftSections(example);
  $("#paramFields").value = sections.paramFields || "";
  $("#procLogic").value = sections.procLogic || "";
  $("#defaultValues").value = DEFAULT_FALLBACK_TEXT;
  const names = new Set(example.apiNames || []);
  state.apis.forEach((api) => { api.selected = names.has(api.name); });
  renderDocChips();
  setPage("single");
  addLog("已加载案例：" + (example.title || example.id || ""), "ok");
}

async function loadExample() {
  if (!state.examples.length) await loadExamples();
  if (!state.examples.length) {
    addLog("未找到验证集案例", "err");
    return;
  }
  if (state.examples.length === 1) {
    applyExample(state.examples[0]);
    return;
  }
  openExamplePicker();
}

function openExamplePicker() {
  const el = $("#examplePickerList");
  el.innerHTML = state.examples.map((item) => {
    return '<button class="example-picker-item" data-example-id="' + escapeHtml(item.id) + '" type="button">' +
      '<strong>' + escapeHtml(item.title || item.id) + '</strong>' +
      '<span>' + escapeHtml(item.factorName || "") + '</span>' +
    '</button>';
  }).join("");
  el.querySelectorAll("[data-example-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const example = state.examples.find((item) => item.id === btn.dataset.exampleId);
      closeExamplePicker();
      applyExample(example);
    });
  });
  openModal("examplePickerModal");
}

function closeExamplePicker() {
  closeModal("examplePickerModal");
}

function loadSelectedExampleToReview() {
  const id = $("#exampleId").value.trim() || state.selectedExampleId;
  const example = state.examples.find((item) => item.id === id);
  applyExample(example);
}



function clearForm() {
  ["#factorCnName", "#requirement", "#paramFields", "#procLogic", "#defaultValues"].forEach(function(id) { $(id).value = ""; });
  $("#defaultValues").value = DEFAULT_FALLBACK_TEXT;
  state.apis.forEach(function(api) { api.selected = false; });
  renderDocChips();
  state.rawResult = "";
  state.step1 = "";
  state.step2 = "";
  state.phase1Done = false;
  state.phase2Done = false;
  state.standardFileId = null;
  state.standardSummaryFileId = null;
  state.standardChecksum = null;
  $("#resultArea").classList.add("hidden");
  $("#copyAllBtn").classList.add("hidden");
  $("#viewStandardBtn").disabled = true;
  $("#copyStep1Btn").disabled = true;
  resetOverview();
  setReviewPhaseState(1, "phase1Idle");
  setReviewPhaseState(2, "phase2Waiting");
  $("#rerunPhase2Btn").classList.add("hidden");
  resetReviewStopState();
  updateReviewReadiness();
}

function showRunning() {
  $("#resultArea").classList.remove("hidden");
  $("#step1Out").innerHTML = '<div class="md">' + markdownToHtml("") + '</div>';
  const step2Running = Boolean(state.fullReviewMode);
  $("#step2Out").innerHTML = '<div class="empty-pane">' + (step2Running ? "正在评审中，请稍候..." : "等待 Phase 2 评审报告...") + '</div>';
  $("#logOut").innerHTML = "";
  $("#viewStandardBtn").disabled = true;
  $("#copyStep1Btn").disabled = true;
  setStepState("step1", "run", "生成中...");
  setStepState("step2", step2Running ? "run" : "", step2Running ? "评审中..." : "等待中");
  $("#elapsedText").textContent = "";
  clearClaudeDebugLogs();
  $("#verdictBanner").className = "verdict idle";
  $("#verdictBanner").textContent = "评审中...";
  ["#failCount", "#pendingCount", "#suggestCount"].forEach(function(id) { $(id).textContent = "—"; });
}

function showPhase2Running() {
  $("#resultArea").classList.remove("hidden");
  state.step2 = "";
  state.phase2Done = false;
  clearClaudeDebugLogs();
  $("#step2Out").innerHTML = '<div class="empty-pane">正在评审中，请稍候...</div>';
  setStepState("step2", "run", "评审中...");
  $("#elapsedText").textContent = "";
  $("#verdictBanner").className = "verdict idle";
  $("#verdictBanner").textContent = "评审中...";
  ["#failCount", "#pendingCount", "#suggestCount"].forEach(function(id) { $(id).textContent = "—"; });
  $("#copyAllBtn").classList.add("hidden");
}

function setStopReviewVisible(visible) {
  const btn = $("#stopReviewBtn");
  if (!btn) return;
  btn.classList.toggle("hidden", !visible);
  if (visible) {
    btn.disabled = false;
    btn.textContent = "■ 停止评审";
  }
}

function resetReviewStopState() {
  state.currentReviewTaskId = null;
  state.stopReviewPending = false;
  setStopReviewVisible(false);
}

function markReviewStopping() {
  const btn = $("#stopReviewBtn");
  if (btn) {
    btn.classList.remove("hidden");
    btn.disabled = true;
    btn.textContent = "停止中...";
  }
}

function setCurrentReviewTask(taskId) {
  state.currentReviewTaskId = taskId;
  setStopReviewVisible(true);
  if (state.stopReviewPending) {
    stopCurrentReview();
  }
}

async function stopCurrentReview() {
  if (!state.busy && !state.currentReviewTaskId) {
    addLog("当前没有正在运行的评审任务。", "err");
    return;
  }
  if (!state.currentReviewTaskId) {
    state.stopReviewPending = true;
    markReviewStopping();
    addLog("已收到停止请求，任务创建后会立即停止。", "info");
    return;
  }
  const taskId = state.currentReviewTaskId;
  markReviewStopping();
  addLog("正在请求停止当前评审...", "info");
  try {
    const res = await fetch("/api/tasks/" + encodeURIComponent(taskId) + "/cancel", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "停止评审失败");
    state.stopReviewPending = false;
    addLog("已请求停止当前评审，正在中止 Claude 调用", "info");
  } catch (err) {
    state.stopReviewPending = false;
    setStopReviewVisible(true);
    addLog(String(err.message || err), "err");
  }
}

function setPhaseStatus(phase, status, text) {
  const el = $(`#phase${phase}Status`);
  if (!el) return;
  el.className = `phase-status ${status}`;
  const dot = el.querySelector(".dot");
  const dotMap = { "": "", ok: "ok", run: "run", err: "err", ready: "" };
  dot.className = `dot ${dotMap[status] || ""}`.trim();
  el.childNodes.forEach(n => { if (n.nodeType === 3) n.textContent = " " + text; });
}

function setPhaseBtnState(phase, state) {
  const btn = $(`#phase${phase}Btn`);
  if (!btn) return;
  btn.className = `phase-btn ${state}`;
  btn.disabled = state === "done" || state === "disabled";
}

function setReviewPhaseState(phase, state, text) {
  const config = {
    phase1Idle: { btn: "primary", btnText: "▷ 生成标准规范", status: "", statusText: "等待 Phase 1", disabled: false },
    phase1Running: { btn: "disabled", btnText: "生成中...", status: "run", statusText: "Phase 1 生成中...", disabled: true },
    phase1Done: { btn: "done", btnText: "标准规范已生成", status: "ok", statusText: "Phase 1 完成", disabled: true },
    phase1Error: { btn: "primary", btnText: "▷ 生成标准规范", status: "err", statusText: "Phase 1 失败", disabled: false },
    phase2Waiting: { btn: "", btnText: "▷ 生成评审报告", status: "", statusText: "等待 Phase 1 完成", disabled: true },
    phase2Ready: { btn: "primary", btnText: "▷ 生成评审报告", status: "ready", statusText: "待评审内容就绪后即可生成评审报告", disabled: false },
    phase2Running: { btn: "disabled", btnText: "评审中...", status: "run", statusText: "Phase 2 评审中...", disabled: true },
    phase2Done: { btn: "primary", btnText: "▷ 生成评审报告", status: "ok", statusText: "Phase 2 完成 ✓", disabled: false },
    phase2Error: { btn: "primary", btnText: "▷ 生成评审报告", status: "err", statusText: "Phase 2 失败", disabled: false },
  }[state];
  if (!config) return;
  setPhaseBtnState(phase, config.btn);
  const btn = $(`#phase${phase}Btn`);
  if (btn) {
    btn.textContent = config.btnText;
    btn.disabled = config.disabled;
  }
  setPhaseStatus(phase, config.status, text || config.statusText);
}

async function runPhase1() {
  if (state.busy) return;
  const factorCnName = $("#factorCnName").value.trim();
  const requirement = $("#requirement").value.trim();
  const apiNames = selectedApis();
  if (!factorCnName || !requirement || !apiNames.length) {
    addLog("请填写因子中文名、需求内容，并至少选择一个接口文档。", "err");
    return;
  }
  state.busy = true;
  setStopReviewVisible(true);
  setReviewPhaseState(1, "phase1Running");
  showRunning();
  addLog("Phase 1 开始：" + factorCnName, "info");
  addLog("关联接口：" + apiNames.join("、"), "info");

  try {
    const res = await fetch("/api/review/phase1", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clientId: CLIENT_INSTANCE_ID,
        factorName: factorCnName,
        requirement,
        apiNames,
        debugClaude: isClaudeDebugEnabled(),
        skipStageValidation: shouldSkipStageValidation(),
      }),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.error || "Phase 1 请求失败");
    }
    const data = await res.json();
    setCurrentReviewTask(data.taskId);
    await pollTask(data.taskId, Date.now(), { phase: 1, factorCnName });
  } catch (err) {
    renderPhase1Error(String(err.message || err));
    state.activeValidationExampleId = null;
    state.busy = false;
    resetReviewStopState();
    setReviewPhaseState(1, "phase1Error");
  }
}

function onPhase1Done(resultText, standardFileId, checksum, meta) {
  state.phase1Done = true;
  state.standardFileId = standardFileId;
  state.standardSummaryFileId = meta?.standardSummaryFileId || null;
  state.standardChecksum = checksum;
  state.step1 = resultText;

  renderStepPanels();
  setStepState("step1", "ok", "已生成");
  $("#viewStandardBtn").disabled = false;
  $("#copyStep1Btn").disabled = false;
  setReviewPhaseState(1, "phase1Done");
  setReviewPhaseState(2, "phase2Ready");
  $("#rerunPhase2Btn").classList.add("hidden");

  addLog(meta?.cacheHit ? "Phase 1 命中缓存，已复用标准规范" : "Phase 1 标准规范已生成", "ok");
  state.busy = false;

  // Auto-chain to Phase 2 in full review mode
  if (state.fullReviewMode) {
    state.fullReviewMode = false;
    setTimeout(function() { runPhase2(); }, 300);
  }
}

async function loadStandardText() {
  if (state.standardFileId) {
    const res = await fetch("/outputs/" + encodeURIComponent(state.standardFileId));
    if (res.ok) {
      const text = await res.text();
      state.step1 = text;
      renderStepPanels();
      return text;
    }
  }
  return state.step1 || "";
}

function buildPhase2Draft() {
  const paramFields = $("#paramFields").value.trim();
  const procLogic = $("#procLogic").value.trim();
  const defaultValues = $("#defaultValues").value.trim();
  return buildDraftFromParts(paramFields, procLogic, defaultValues);
}

async function runPhase2() {
  if (state.busy) return;
  if (!state.phase1Done) {
    addLog("请先完成 Phase 1（生成标准规范）。", "err");
    return;
  }
  const factorCnName = $("#factorCnName").value.trim();
  const requirement = $("#requirement").value.trim();
  const apiNames = selectedApis();
  const draft = buildPhase2Draft();
  if (!draft) {
    addLog("请填写待评审准出内容（因子加工输入字段、加工逻辑）。", "err");
    return;
  }
  state.busy = true;
  setStopReviewVisible(true);
  showPhase2Running();
  setReviewPhaseState(2, "phase2Running");
  addLog("Phase 2 开始：" + factorCnName, "info");

  try {
    const res = await fetch("/api/review/phase2", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clientId: CLIENT_INSTANCE_ID,
        factorName: factorCnName,
        requirement,
        apiNames,
        draft,
        standardFileId: state.standardFileId,
        debugClaude: isClaudeDebugEnabled(),
        skipStageValidation: shouldSkipStageValidation(),
      }),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.error || "Phase 2 请求失败");
    }
    const data = await res.json();
    setCurrentReviewTask(data.taskId);
    await pollTask(data.taskId, Date.now(), { phase: 2, factorCnName });
  } catch (err) {
    renderPhase2Error(String(err.message || err));
    state.activeValidationExampleId = null;
    state.busy = false;
    resetReviewStopState();
    setReviewPhaseState(2, "phase2Error");
  }
}

async function rerunPhase2() {
  if (state.busy) return;
  if (!state.phase1Done) {
    addLog("Phase 1 未完成，请先执行 Phase 1。", "err");
    return;
  }
  addLog("重新生成评审报告...", "info");
  await runPhase2();
}
async function runFullReview() {
  if (state.busy) return;
  
  // Validate Phase 1 inputs
  var factorCnName = $("#factorCnName").value.trim();
  var requirement = $("#requirement").value.trim();
  var apiNames = selectedApis();
  if (!factorCnName || !requirement || !apiNames.length) {
    addLog("请填写因子中文名、需求内容，并至少选择一个接口文档。", "err");
    return;
  }
  
  // Validate Phase 2 inputs
  var draft = buildPhase2Draft();
  if (!draft) {
    addLog("请填写待评审准出内容（因子加工输入字段、加工逻辑）。", "err");
    return;
  }
  
  addLog("一键评审开始：" + factorCnName, "info");
  state.fullReviewMode = true;
  state.step2 = "";
  state.phase2Done = false;
  
  // Phase 1 handles the rest (onPhase1Done will auto-trigger Phase 2)
  await runPhase1();
}



function onPhase2Done(resultText, meta) {
  state.phase2Done = true;
  state.step2 = resultText;

  renderStepPanels();
  setStepState("step2", "ok", "已生成");
  setReviewPhaseState(2, "phase2Done");
  $("#rerunPhase2Btn").classList.add("hidden");

  updateOverview(resultText);
  $("#copyAllBtn").classList.remove("hidden");

  const factorCnName = $("#factorCnName").value.trim();
  state.history.unshift({
    name: factorCnName,
    cn: factorCnName,
    time: Date.now(),
    verdict: detectVerdict(resultText),
    raw: "# Phase 1\n\n" + (state.step1 || "") + "\n\n# Phase 2\n\n" + resultText,
    s1: state.step1,
    s2: resultText,
    stats: collectStats(resultText),
    requirement: $("#requirement").value.trim(),
    paramFields: $("#paramFields").value.trim(),
    procLogic: $("#procLogic").value.trim(),
    defaultValues: $("#defaultValues").value.trim(),
    apiNames: state.apis.filter(function(a){return a.selected}).map(function(a){return a.name}),
    phase: 2,
  });
  state.historyPage = 1;
  saveHistory();
  if (state.activeValidationExampleId) {
    const example = state.examples.find((item) => item.id === state.activeValidationExampleId);
    if (example) {
      recordValidationResult(example, resultText, state.step1, state.standardFileId, meta?.reviewFileId || "");
      addLog("验证结果已记录：" + (example.title || example.id), "ok");
    }
    state.activeValidationExampleId = null;
  }
  addLog("Phase 2 评审报告已生成", "ok");
  state.busy = false;
}

function taskFailureMessage(result, fallback) {
  const target = result || {};
  const stderr = String(target.stderr || "").trim();
  if (stderr) return stderr;
  const explicitError = String(target.stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => /^(?:API Error|Error|错误)\s*[:：]/i.test(line));
  return explicitError || fallback;
}

async function pollTask(taskId, startedAt, meta) {
  while (true) {
    const res = await fetch("/api/tasks/" + taskId);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "任务不存在");
    const task = data.task;
    renderGlobalPool(data.pool);
    renderTaskLogs(task.logs || []);
    updateClaudeDebugLogs(task.debugLogs || []);
    if (task.status === "queued" || task.status === "running" || task.status === "cancelling") {
      if (task.status === "queued") {
        const position = task.queuePosition ? "，队列第 " + task.queuePosition + " 位" : "";
        const pool = data.pool || {};
        setPhaseStatus(meta.phase || 1, "run", "等待全局线程池" + position + "（" + (pool.activeWorkers || 0) + "/" + (pool.maxWorkers || 0) + "）");
      }
      await new Promise(function(resolve) { setTimeout(resolve, 1500); });
      continue;
    }
    const result = task.result || {};
    if (result.stderr) addLog(result.stderr, task.status === "done" ? "info" : "err");
    if (task.status === "done") {
      var phase = meta.phase || 1;
      if (phase === 1) {
        onPhase1Done(result.stdout || "", result.standardFileId, result.checksum, { ...meta, cacheHit: Boolean(result.cacheHit), standardSummaryFileId: result.standardSummaryFileId });
      } else {
        onPhase2Done(result.stdout || "", { ...meta, reviewFileId: result.reviewFileId });
      }
      var elapsed = Math.round((Date.now() - startedAt) / 1000);
      addLog("评审用时 " + elapsed + "s", "info");
      resetReviewStopState();
      return;
    }
    if (task.status === "cancelled") {
      renderReviewStopped(meta.phase || 1);
      resetReviewStopState();
      return;
    }
    if (task.status === "timeout") throw new Error("Claude Code 调用超时");
    throw new Error(taskFailureMessage(result, "评审失败：" + task.status));
  }
}

function splitSteps(text) {
  const source = String(text || "").trim();
  const markers = [
    /^##\s*Step\s*2\s*[—–-]\s*评审报告\s*$/mi,
    /^#{1,3}\s*Step\s*2\b.*$/mi,
    /^#{1,3}\s*评审报告\s*$/mi,
    /^#{1,3}\s*第二阶段.*$/mi,
  ];
  for (const marker of markers) {
    const match = source.match(marker);
    if (match && match.index > 0) {
      return { s1: source.slice(0, match.index).trim(), s2: source.slice(match.index).trim() };
    }
  }
  // Fallback: split at "## 3." or "## 汇总" or at first non-heading after Step 1
  const fallbacks = [/^##\s*3\.\s*汇总/m, /^##\s*(?:汇总|问题清单)/m, /^##\s*4\.\s*总体结论/m];
  for (const fb of fallbacks) {
    const idx = source.search(fb);
    if (idx > 0) return { s1: source.slice(0, idx).trim(), s2: source.slice(idx).trim() };
  }
  // Last resort: return full as Step 1, empty Step 2
  return { s1: source, s2: "" };
}

function renderPhase1Error(message) {
  $("#resultArea").classList.remove("hidden");
  $("#step2Out").innerHTML = "<div class=\"empty-pane\">Phase 1 失败：" + escapeHtml(message) + "</div>";
  setStepState("step1", "err", "失败");
  setReviewPhaseState(1, "phase1Error");
  addLog(message, "err");
}

function renderPhase2Error(message) {
  $("#resultArea").classList.remove("hidden");
  $("#step2Out").innerHTML = "<div class=\"empty-pane\">Phase 2 失败：" + escapeHtml(message) + "</div>";
  setStepState("step2", "err", "失败");
  setReviewPhaseState(2, "phase2Error");
  addLog(message, "err");
}

function renderReviewStopped(phase) {
  state.busy = false;
  state.fullReviewMode = false;
  state.stopReviewPending = false;
  state.activeValidationExampleId = null;
  $("#resultArea").classList.remove("hidden");
  if (phase === 1) {
    setStepState("step1", "err", "已停止");
    setReviewPhaseState(1, "phase1Error", "Phase 1 已停止");
    setReviewPhaseState(2, "phase2Waiting");
    $("#step2Out").innerHTML = '<div class="empty-pane">评审已停止，未生成评审报告。</div>';
  } else {
    setStepState("step2", "err", "已停止");
    setReviewPhaseState(2, state.phase1Done ? "phase2Ready" : "phase2Waiting", "Phase 2 已停止");
    $("#step2Out").innerHTML = '<div class="empty-pane">评审已停止，未生成评审报告。</div>';
  }
  $("#verdictBanner").className = "verdict idle";
  $("#verdictBanner").textContent = "评审已停止";
  addLog("当前评审已停止", "info");
}

function renderStepPanels() {
  $("#step1Out").innerHTML = '<div class="md">' + markdownToHtml(state.step1 || "") + '</div>';
  $("#viewStandardBtn").disabled = !state.step1;
  $("#copyStep1Btn").disabled = !state.step1;
  const step2Text = state.step2 || "## 评审报告\n\n等待 Phase 2 生成评审报告。";
  $("#step2Out").innerHTML = '<div class="md">' + markdownToHtml(step2Text) + '</div>';
}

function setStepState(prefix, dotClass, text) {
  $(`#${prefix}Dot`).className = `dot ${dotClass}`.trim();
  $(`#${prefix}Status`).textContent = text;
}

function collectStats(text) {
  const source = String(text || "");
  const statsMatch = source.match(/\|\s*问题统计\s*\|\s*([^|]+?)\s*\|/);
  if (statsMatch) {
    const statsText = statsMatch[1] || "";
    const failMatch = statsText.match(/不符合\s*(\d+)\s*项/);
    const pendingMatch = statsText.match(/待确认\s*(\d+)\s*项/);
    const suggestMatch = statsText.match(/建议\s*(\d+)\s*项/);
    if (failMatch || pendingMatch || suggestMatch) {
      return {
        fail: failMatch ? Number(failMatch[1]) : 0,
        pending: pendingMatch ? Number(pendingMatch[1]) : 0,
        suggest: suggestMatch ? Number(suggestMatch[1]) : 0,
      };
    }
  }
  const conclusionRows = [...source.matchAll(/\|\s*结论\s*\|\s*([^|]+?)\s*\|/g)].map((match) => match[1]);
  return {
    fail: conclusionRows.filter((row) => /❌\s*不符合/.test(row)).length,
    pending: conclusionRows.filter((row) => /🔵\s*待确认|⚠️\s*待确认/.test(row)).length,
    suggest: conclusionRows.filter((row) => /⚠️\s*建议/.test(row)).length,
  };
}

function detectVerdict(text) {
  const source = String(text || "");
  const overall = source.match(/\|\s*总体结论\s*\|\s*([^|]+?)\s*\|/);
  const target = overall ? overall[1] : source;
  if (/❌\s*不通过/.test(target)) return "fail";
  if (/⚠️\s*建议/.test(target)) {
    const stats = collectStats(source);
    if (stats.fail === 0 && stats.pending === 0 && stats.suggest > 0) return "pass";
    return "warn";
  }
  if (/✅\s*(通过|符合)/.test(target)) return "pass";
  return "idle";
}

function updateOverview(text) {
  const stats = collectStats(text);
  $("#failCount").textContent = stats.fail;
  $("#pendingCount").textContent = stats.pending;
  $("#suggestCount").textContent = stats.suggest;
  const verdict = detectVerdict(text);
  const banner = $("#verdictBanner");
  banner.className = `verdict ${verdict}`;
  const labels = {
    pass: "总体结论：✅ 通过",
    warn: "总体结论：⚠️ 存在待确认项",
    fail: "总体结论：❌ 不通过，需修改",
    idle: "总体结论：待确认",
  };
  banner.textContent = labels[verdict];
}

function resetOverview() {
  $("#verdictBanner").className = "verdict idle";
  $("#verdictBanner").textContent = "等待评审...";
  $("#failCount").textContent = "—";
  $("#pendingCount").textContent = "—";
  $("#suggestCount").textContent = "—";
}

function renderTaskLogs(logs) {
  if (!logs.length) return;
  $("#logOut").innerHTML = logs.map((log) => {
    const msg = log.message || "";
    const kind = /完成|返回/.test(msg) ? "ok" : /失败|异常|超时|未找到/.test(msg) ? "err" : "info";
    return `<div class="log-line"><span class="log-time">${formatTime(log.time)}</span><span class="log-msg ${kind}">${escapeHtml(msg)}</span></div>`;
  }).join("");
}

function addLog(message, kind = "info") {
  const el = $("#logOut");
  $("#resultArea").classList.remove("hidden");
  if (el.classList.contains("empty-pane")) el.classList.remove("empty-pane");
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  el.insertAdjacentHTML("beforeend", `<div class="log-line"><span class="log-time">${time}</span><span class="log-msg ${kind}">${escapeHtml(message)}</span></div>`);
  el.scrollTop = el.scrollHeight;
}

function stripYamlFrontmatter(markdown) {
  return String(markdown || "").replace(/^---\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, "").trim();
}

function markdownToHtml(markdown) {
  const cleanMarkdown = stripYamlFrontmatter(markdown);
  try {
    return marked.parse(cleanMarkdown, {
      gfm: true,
      breaks: true,
      langPrefix: "hljs language-",
      highlight: function (code, lang) {
        if (lang && hljs.getLanguage(lang)) {
          try { return hljs.highlight(code, { language: lang }).value; } catch (e) {}
        }
        try { return hljs.highlightAuto(code).value; } catch (e) {}
        return code;
      },
    });
  } catch (e) {
    return "<p>" + cleanMarkdown.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") + "</p>";
  }
}



function renderHistory() {
  const el = $("#historyList");
  const pager = $("#historyPager");
  const total = state.history.length;
  const pageSize = Math.max(1, Number(state.historyPageSize || 10));
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  state.historyPage = Math.min(Math.max(1, Number(state.historyPage || 1)), totalPages);
  const page = state.historyPage;
  const start = (page - 1) * pageSize;
  const pageRows = state.history.slice(start, start + pageSize);
  $("#historyTotalLabel").textContent = total ? "共 " + total + " 条 · 第 " + page + "/" + totalPages + " 页" : "";
  if ($("#historyPageSize")) $("#historyPageSize").value = String(pageSize);
  if (!total) {
    el.innerHTML = '<div class="muted-box">暂无评审历史。</div>';
    if (pager) pager.innerHTML = "";
    updateHistorySelectAll();
    updateHistoryDeleteBtn();
    return;
  }
  el.innerHTML = '<div class="history-table-head"><span></span><span>评审对象</span><span>结论</span><span>时间</span></div>' + pageRows.map(function(item, pageIndex) {
    const index = start + pageIndex;
    const cls = item.verdict === "pass" ? "pass" : item.verdict === "warn" ? "warn" : "fail";
    const label = item.verdict === "pass" ? "✅ 通过" : item.verdict === "warn" ? "⚠️ 待确认" : "❌ 不通过";
    var checked = state.historyDeleteChecked[index] ? "checked" : "";
    return '<article class="hist-item" data-history-index="' + index + '">' +
      '<div class="hist-top">' +
        '<input type="checkbox" class="hist-checkbox" data-hidx="' + index + '" ' + checked + '>' +
        '<div class="hist-title">' + escapeHtml(item.name) + (item.cn ? " · " + escapeHtml(item.cn) : "") + '<small>Phase 1 / Phase 2 已保存</small></div>' +
        '<span class="tag ' + cls + '">' + label + '</span>' +
        '<span class="hist-time">' + formatDate(item.time) + '</span>' +
      '</div>' +
      '</article>';
  }).join("");
  
  el.querySelectorAll("[data-history-index]").forEach(function(item) {
    item.addEventListener("click", function(e) {
      if (e.target.closest(".hist-checkbox")) return;
      openHistory(Number(this.dataset.historyIndex));
    });
  });
  
  el.querySelectorAll(".hist-checkbox").forEach(function(cb) {
    cb.addEventListener("change", function() {
      var hidx = Number(this.dataset.hidx);
      if (this.checked) {
        state.historyDeleteChecked[hidx] = true;
      } else {
        delete state.historyDeleteChecked[hidx];
      }
      updateHistoryDeleteBtn();
      updateHistorySelectAll();
    });
  });
  renderHistoryPager(totalPages);
  updateHistorySelectAll();
  updateHistoryDeleteBtn();
}

function renderHistoryPager(totalPages) {
  const pager = $("#historyPager");
  if (!pager) return;
  if (!state.history.length || totalPages <= 1) {
    pager.innerHTML = "";
    return;
  }
  pager.innerHTML =
    '<button class="mini-btn" data-history-page="prev" type="button" ' + (state.historyPage <= 1 ? "disabled" : "") + '>上一页</button>' +
    '<span>' + state.historyPage + ' / ' + totalPages + '</span>' +
    '<button class="mini-btn" data-history-page="next" type="button" ' + (state.historyPage >= totalPages ? "disabled" : "") + '>下一页</button>';
  pager.querySelectorAll("[data-history-page]").forEach(function(btn) {
    btn.addEventListener("click", function() {
      if (this.dataset.historyPage === "prev") state.historyPage -= 1;
      if (this.dataset.historyPage === "next") state.historyPage += 1;
      renderHistory();
    });
  });
}

function updateHistorySelectAll() {
  const cb = $("#historySelectAll");
  if (!cb) return;
  const total = state.history.length;
  const count = Object.keys(state.historyDeleteChecked).filter(function(key) {
    const index = Number(key);
    return Number.isInteger(index) && index >= 0 && index < total;
  }).length;
  cb.disabled = total === 0;
  cb.checked = total > 0 && count === total;
  cb.indeterminate = count > 0 && count < total;
}

function updateHistoryDeleteBtn() {
  var count = Object.keys(state.historyDeleteChecked).filter(function(key) {
    const index = Number(key);
    return Number.isInteger(index) && index >= 0 && index < state.history.length;
  }).length;
  var btn = $("#historyDeleteBtn");
  if (count > 0) {
    btn.style.display = "";
    $("#historyDeleteCount").textContent = count;
  } else {
    btn.style.display = "none";
  }
}

function deleteSelectedHistory() {
  var indices = Object.keys(state.historyDeleteChecked).map(Number).filter(function(index) {
    return Number.isInteger(index) && index >= 0 && index < state.history.length;
  }).sort(function(a, b) { return b - a; });
  if (!indices.length) return;
  if (!confirm("确定删除选中的 " + indices.length + " 条评审记录？")) return;
  for (var i = 0; i < indices.length; i++) {
    state.history.splice(indices[i], 1);
  }
  state.historyDeleteChecked = {};
  const maxPage = Math.max(1, Math.ceil(state.history.length / Math.max(1, Number(state.historyPageSize || 10))));
  state.historyPage = Math.min(state.historyPage, maxPage);
  saveHistory();
  updateHistoryDeleteBtn();
  renderHistory();
}

function toggleSelectAllHistory(checked) {
  state.historyDeleteChecked = {};
  if (checked) {
    state.history.forEach(function(_, index) {
      state.historyDeleteChecked[index] = true;
    });
  }
  renderHistory();
}

function updateHistoryPageSize(value) {
  const nextSize = Math.max(1, Number(value || 10));
  state.historyPageSize = nextSize;
  state.historyPage = 1;
  renderHistory();
}


function openHistory(index) {
  const item = state.history[index];
  if (!item) return;
  openReviewDetailModal(item);
}

function openReviewDetailModal(item) {
  const raw = item.raw || "";
  const parts = item.s1 || item.s2 ? { s1: item.s1 || "", s2: item.s2 || "" } : splitSteps(raw);
  state.modalStep1Text = parts.s1 || "";
  state.modalStep2Text = parts.s2 || "";
  $("#modalTitle").textContent = item.name || "评审详情";

  var inputs = $("#modalInputs");
  inputs.dataset.hasInputs = "";
  if (item.requirement || item.paramFields || item.procLogic || item.defaultValues || item.draft) {
    var lines = [];
    if (item.requirement) lines.push("需求：" + item.requirement);
    if (item.paramFields) lines.push("因子加工输入字段：" + item.paramFields);
    if (item.procLogic) lines.push("逻辑：" + item.procLogic);
    if (item.defaultValues) lines.push("默认值：" + item.defaultValues);
    if ((item.apiNames || []).length) lines.push("接口：" + (item.apiNames || []).join("、"));
    if (item.draft && !(item.paramFields || item.procLogic || item.defaultValues)) lines.push("待评审准出：" + item.draft);
    inputs.innerHTML = lines.map(function(l) { return escapeHtml(l); }).join("<br>");
    inputs.dataset.hasInputs = "1";
  } else {
    inputs.innerHTML = '<span class="validation-muted">暂无输入信息。</span>';
  }

  $("#modalStep1").innerHTML = '<div class="md">' + markdownToHtml(parts.s1) + '</div>';
  $("#modalStep2").innerHTML = '<div class="md">' + markdownToHtml(extractIssueFocusedMarkdown(parts.s2)) + '</div>';

  var stats = item.stats || collectStats(raw);
  var verdict = item.verdict || detectVerdict(raw);
  var v = $("#modalVerdict");
  v.className = "verdict " + verdict;
  var labels = {pass:"✅ 通过", warn:"⚠️ 存在待确认项", fail:"❌ 不通过，需修改", idle:"待确认"};
  v.textContent = labels[verdict] || "待确认";
  $("#modalFail").textContent = stats.fail || 0;
  $("#modalPending").textContent = stats.pending || 0;
  $("#modalSuggest").textContent = stats.suggest || 0;

  openModal("historyModal");
  setHistoryModalView("issues");
}

function closeHistoryModal() {
  closeModal("historyModal");
}

function extractIssueFocusedMarkdown(text) {
  const source = String(text || "").trim();
  if (!source) return "暂无输出内容";
  const lines = source.split("\n");
  const issueLines = [];
  let captureTable = false;
  for (const line of lines) {
    if (/不符合|待确认|建议|问题清单|总体结论/.test(line)) {
      issueLines.push(line);
      captureTable = /^\|/.test(line.trim());
      continue;
    }
    if (captureTable && /^\|/.test(line.trim())) {
      issueLines.push(line);
      continue;
    }
    captureTable = false;
  }
  return issueLines.length ? issueLines.join("\n") : source;
}

function setHistoryModalView(view) {
  state.modalView = view || "issues";
  const modal = $("#historyModal");
  if (!modal) return;
  modal.dataset.view = state.modalView;
  $$("#historyModal [data-modal-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.modalView === state.modalView);
  });
  const inputs = $("#modalInputs");
  if (inputs) {
    inputs.style.display = state.modalView === "inputs" || (state.modalView === "compare" && inputs.dataset.hasInputs === "1") ? "" : "none";
  }
  if ($("#modalStep1")) $("#modalStep1").innerHTML = '<div class="md">' + markdownToHtml(state.modalStep1Text || "") + '</div>';
  if ($("#modalStep2")) {
    const text = state.modalView === "issues" ? extractIssueFocusedMarkdown(state.modalStep2Text) : state.modalStep2Text;
    $("#modalStep2").innerHTML = '<div class="md">' + markdownToHtml(text || "暂无输出内容") + '</div>';
  }
}

function copyText(text, label) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => addLog(`${label}已复制`, "ok"));
}

async function copyStandardText() {
  const text = await loadStandardText();
  copyText(text, "标准规范");
}

async function openStandardModal() {
  if (!state.step1 && !state.standardFileId) return;
  openMarkdownModal("标准加工规范", "", "Phase 1", "s1");
  $("#standardModalBody").innerHTML = '<div class="empty-pane">正在加载完整标准规范...</div>';
  try {
    const text = await loadStandardText();
    state.standardModalText = text;
    renderStandardModal("rendered");
  } catch (err) {
    $("#standardModalBody").innerHTML = '<div class="empty-pane">标准规范加载失败：' + escapeHtml(String(err.message || err)) + '</div>';
  }
}

function openMarkdownModal(title, text, phaseLabel, phaseClass) {
  const label = $("#standardModalPhaseLabel");
  if (label) {
    label.textContent = phaseLabel || "详情";
    label.className = ("step-label " + (phaseClass || "s2")).trim();
  }
  $("#standardModalTitle").textContent = title || "详情";
  state.standardModalText = text || "";
  openModal("standardModal");
  renderStandardModal("rendered");
}

function renderStandardModal(mode) {
  const text = state.standardModalText || state.step1 || "";
  if (mode === "raw") {
    $("#standardModalBody").innerHTML = '<pre class="raw-modal-text">' + escapeHtml(text) + '</pre>';
    return;
  }
  $("#standardModalBody").innerHTML = '<div class="md">' + markdownToHtml(text) + '</div>';
}

function closeStandardModal() {
  closeModal("standardModal");
}

function isModalVisible(el) {
  return Boolean(el && el.classList.contains("modal-overlay") && el.style.display !== "none");
}

function refreshModalScrollLock() {
  const hasOpenModal = $$(".modal-overlay").some(isModalVisible);
  document.body.classList.toggle("modal-open", hasOpenModal);
}

function findScrollableModalTarget(start, modal) {
  let el = start;
  while (el && el !== modal) {
    const style = window.getComputedStyle(el);
    const canScrollY = /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight;
    if (canScrollY) return el;
    el = el.parentElement;
  }
  return null;
}

function containModalWheel(e) {
  const modal = e.currentTarget;
  const scroller = findScrollableModalTarget(e.target, modal);
  if (!scroller) {
    e.preventDefault();
    return;
  }
  const deltaY = e.deltaY;
  if (!deltaY) return;
  const atTop = scroller.scrollTop <= 0;
  const atBottom = Math.ceil(scroller.scrollTop + scroller.clientHeight) >= scroller.scrollHeight;
  if ((deltaY < 0 && atTop) || (deltaY > 0 && atBottom)) {
    e.preventDefault();
  }
}

function openModal(id) {
  const el = $("#" + id);
  if (el) el.style.display = "";
  refreshModalScrollLock();
}

function closeModal(id) {
  const el = $("#" + id);
  if (el) el.style.display = "none";
  refreshModalScrollLock();
}

function isClaudeDebugEnabled() {
  return Boolean($("#claudeDebugToggle")?.checked);
}

function shouldSkipStageValidation() {
  return Boolean($("#skipStageValidationToggle")?.checked);
}

function formatClaudeDebugLogs(logs) {
  return (logs || []).map(function(log) {
    var time = log.time ? formatTime(log.time) : new Date().toLocaleTimeString("zh-CN", { hour12: false });
    var stream = String(log.stream || "debug").toUpperCase();
    var message = String(log.message || "");
    return "[" + time + "] " + stream + "\n" + message;
  }).join("\n\n");
}

function isNearScrollBottom(el, threshold = 24) {
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

function renderClaudeDebugModal(options = {}) {
  var body = $("#claudeDebugBody");
  if (!body) return;
  const shouldFollow = options.forceScroll || isNearScrollBottom(body);
  if (!state.claudeDebugLogs.length) {
    body.textContent = isClaudeDebugEnabled()
      ? "暂无 Claude 调试输出，任务运行中会自动刷新。"
      : "调试输出未开启。打开“显示 Claude 调试输出”后重新运行评审即可查看。";
    if (shouldFollow) body.scrollTop = body.scrollHeight;
    return;
  }
  body.textContent = formatClaudeDebugLogs(state.claudeDebugLogs);
  if (shouldFollow) body.scrollTop = body.scrollHeight;
}

function updateClaudeDebugLogs(logs) {
  state.claudeDebugLogs = Array.isArray(logs) ? logs : [];
  renderClaudeDebugModal();
}

function clearClaudeDebugLogs() {
  state.claudeDebugLogs = [];
  renderClaudeDebugModal();
}

function openClaudeDebugModal() {
  renderClaudeDebugModal({ forceScroll: true });
  openModal("claudeDebugModal");
}

function closeClaudeDebugModal() {
  closeModal("claudeDebugModal");
}

$$(".nav-btn").forEach(function(btn) { btn.addEventListener("click", function() { setPage(btn.dataset.page); }); });
$$(".modal-overlay").forEach(function(modal) {
  modal.addEventListener("wheel", containModalWheel, { passive: false });
});
$("#loadExampleBtn").addEventListener("click", loadExample);
$("#clearBtn").addEventListener("click", clearForm);
$("#phase1Btn").addEventListener("click", runPhase1);
$("#phase2Btn").addEventListener("click", runPhase2);
$("#rerunPhase2Btn").addEventListener("click", rerunPhase2);
$("#fullReviewBtn").addEventListener("click", runFullReview);
$("#stopReviewBtn")?.addEventListener("click", stopCurrentReview);
$("#copyStep1Btn").addEventListener("click", copyStandardText);
$("#copyStep2Btn").addEventListener("click", function() { copyText(state.step2, "Step 2"); });
$("#copyAllBtn").addEventListener("click", function() { copyText(state.rawResult, "完整结果"); });
$("#viewStandardBtn").addEventListener("click", openStandardModal);
$("#standardModalRenderedBtn").addEventListener("click", function() { renderStandardModal("rendered"); });
$("#standardModalRawBtn").addEventListener("click", function() { renderStandardModal("raw"); });
$("#standardModalCloseBtn").addEventListener("click", closeStandardModal);
$("#standardModal").addEventListener("click", function(e) {
  if (e.target === this) closeStandardModal();
});
$("#openClaudeDebugBtn")?.addEventListener("click", openClaudeDebugModal);
$("#claudeDebugRefreshBtn")?.addEventListener("click", renderClaudeDebugModal);
$("#claudeDebugCloseBtn")?.addEventListener("click", closeClaudeDebugModal);
$("#claudeDebugModal")?.addEventListener("click", function(e) {
  if (e.target === this) closeClaudeDebugModal();
});
$("#newExampleBtn")?.addEventListener("click", clearExampleForm);
$("#saveExampleBtn")?.addEventListener("click", function() {
  saveExample().catch((err) => {
    setExampleStatus(String(err.message || err), "err");
    addLog(String(err.message || err), "err");
    $("#saveExampleBtn").disabled = false;
  });
});
$("#deleteExampleBtn")?.addEventListener("click", function() {
  deleteExample().catch((err) => {
    setExampleStatus(String(err.message || err), "err");
    addLog(String(err.message || err), "err");
  });
});
$("#loadSelectedExampleBtn")?.addEventListener("click", loadSelectedExampleToReview);
$("#newBusinessRuleBtn")?.addEventListener("click", clearBusinessRuleForm);
$("#saveBusinessRuleBtn")?.addEventListener("click", function() {
  saveBusinessRule().catch((err) => setBusinessRuleStatus(String(err.message || err), "err"));
});
$("#deleteBusinessRuleBtn")?.addEventListener("click", function() {
  deleteBusinessRule().catch((err) => setBusinessRuleStatus(String(err.message || err), "err"));
});
$("#runSelectedValidationBtn")?.addEventListener("click", runSelectedValidation);
$("#runRegressionBtn")?.addEventListener("click", function() {
  runRegressionValidation().catch((err) => {
    state.busy = false;
    const btn = $("#runRegressionBtn");
    if (btn) {
      btn.disabled = false;
      btn.textContent = "⚡ 一键回归验证";
    }
    setExampleStatus(String(err.message || err), "err");
    addLog(String(err.message || err), "err");
  });
});
$("#stopRegressionBtn")?.addEventListener("click", stopRegressionValidation);
$("#recordValidationBtn")?.addEventListener("click", recordCurrentValidationResult);
$("#validationSearch")?.addEventListener("input", renderExamplesList);
$("#validationKindFilter")?.addEventListener("change", renderExamplesList);
$("#validationStatusFilter")?.addEventListener("change", renderExamplesList);
["#factorCnName", "#requirement", "#paramFields", "#procLogic", "#defaultValues"].forEach((selector) => {
  $(selector)?.addEventListener("input", updateReviewReadiness);
});
$("#examplesList")?.addEventListener("click", function(e) {
  const btn = e.target.closest("[data-example-id]");
  if (btn) selectExample(btn.dataset.exampleId);
});
$("#examplePickerCloseBtn")?.addEventListener("click", closeExamplePicker);
$("#examplePickerModal")?.addEventListener("click", function(e) {
  if (e.target === this) closeExamplePicker();
});
$("#historyModal")?.addEventListener("click", function(e) {
  const viewBtn = e.target.closest("[data-modal-view]");
  if (viewBtn) setHistoryModalView(viewBtn.dataset.modalView);
});

renderHistoryBadge();
loadApis().catch((err) => addLog(String(err.message || err), "err"));
loadExamples().catch((err) => addLog(String(err.message || err), "err"));
loadBusinessRules().catch((err) => console.warn(err));
refreshGlobalPool();
setInterval(refreshGlobalPool, 3000);

$("#docsSearch")?.addEventListener("input", () => renderDocsPage());

// Batch review state
let batchState = { rows: [], tasks: [], done: 0, total: 0, active: false, renderSignature: "" };

function csvCell(value) {
  const text = String(value ?? "");
  return '"' + text.replace(/"/g, '""') + '"';
}

function downloadBatchTemplate() {
  const header = ["因子名", "需求内容", "接口文档", "因子加工输入字段", "加工逻辑"];
  const example = [
    "近12个月采购金额",
    "统计申请时点近12个月内医药企业采购金额总和。",
    "授信申请接口、ES_医药企业药品采购信息查询接口",
    "授信申请接口.applyTime | 确定统计终点\n" +
      "ES_医药企业药品采购信息查询接口.content[].kpyf | 过滤时间窗口\n" +
      "ES_医药企业药品采购信息查询接口.content[].inputAmount | 金额求和",
    "1. 以 applyTime 所属月作为统计终点。\n2. 遍历 content[] 全部明细。\n3. 筛选 kpyf 落在近12个月窗口内的记录。\n4. 对 inputAmount 求和输出。",
  ];
  const csv = "\ufeff" + [header, example].map(row => row.map(csvCell).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "risk-review-batch-template.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  addLog("批量评审模板已下载", "ok");
}

function handleBatchFile(file) {
  var name = file.name || "";
  var reader = new FileReader();
  reader.onload = function(e) {
    var bytes = new Uint8Array(e.target.result);
    var binary = "";
    for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    var base64 = btoa(binary);
    fetch("/api/parse-excel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, data: base64 })
    }).then(function(res) { return res.json(); }).then(function(data) {
      if (!data.items || !data.items.length) { addLog("文件解析失败或未识别到有效数据行", "err"); return; }
      batchState.rows = data.items;
      renderBatchPreview(data.items);
      $("#batchStartBtn").style.display = "";
      $("#batchProgress").style.display = "none";
      batchState.done = 0; batchState.total = 0; batchState.active = false; batchState.tasks = []; batchState.renderSignature = "";
    }).catch(function(err) {
      addLog("文件解析失败：" + String(err), "err");
    });
  };
  reader.readAsArrayBuffer(file);
}

function renderBatchPreview(rows) {
  const wrap = $("#batchTableWrap");
  if (!wrap) return;
  $("#batchPreview").style.display = "";
  const keys = ["factorName","requirement","apiDocs","paramFields","procLogic"];
  const labels = ["因子名","需求内容","接口文档","因子加工输入字段","加工逻辑"];
  if (state.batchPreviewTable) {
    state.batchPreviewTable.destroy();
    state.batchPreviewTable = null;
  }
  if (typeof Tabulator !== "undefined") {
    wrap.innerHTML = "";
    state.batchPreviewTable = new Tabulator(wrap, {
      data: rows,
      layout: "fitColumns",
      height: Math.min(420, Math.max(220, rows.length * 42 + 58)),
      pagination: rows.length > 12 ? "local" : false,
      paginationSize: 12,
      placeholder: "没有可预览的数据",
      movableColumns: true,
      resizableColumnFit: true,
      columns: keys.map((key, index) => ({
        title: labels[index],
        field: key,
        minWidth: key === "factorName" ? 130 : 160,
        widthGrow: ["requirement", "procLogic"].includes(key) ? 2 : 1,
        formatter: "textarea",
        headerSort: true,
      })),
    });
    $("#batchCount").textContent = "共 " + rows.length + " 条";
    return;
  }
  wrap.innerHTML = "<table class='batch-table'>" +
    "<tr>" + labels.map(l => "<th>" + l + "</th>").join("") + "</tr>" +
    rows.map(r => "<tr>" + keys.map(k => "<td title='" + escapeHtml(r[k] || "") + "'>" + escapeHtml(r[k] || "") + "</td>").join("") + "</tr>").join("") +
    "</table>";
  $("#batchCount").textContent = "共 " + rows.length + " 条";
}

async function startBatchReview() {
  if (batchState.active) return;
  const rows = batchState.rows;
  if (!rows.length) return;
  batchState.active = true;
  batchState.done = 0;
  batchState.total = rows.length;
  batchState.tasks = rows.map((row) => ({ factorName: row.factorName, status: "queued", raw: "" }));
  batchState.renderSignature = "";
  $("#batchStartBtn").style.display = "none";
  $("#batchProgress").style.display = "";
  renderBatchProgress();
  try {
    const res = await fetch("/api/review/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clientId: CLIENT_INSTANCE_ID,
        rows,
        concurrency: Number($("#batchConcurrency")?.value || 1),
        retry: Number($("#batchRetry")?.value || 0),
        resume: ($("#batchResume")?.value || "1") === "1",
        skipStageValidation: shouldSkipStageValidation(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "批量评审启动失败");
    batchState.taskId = data.taskId;
    pollBatchTasks();
  } catch (err) {
    batchState.active = false;
    addLog("批量评审启动失败：" + String(err.message || err), "err");
    $("#batchStartBtn").style.display = "";
  }
}

function renderBatchProgress() {
  const list = $("#batchProgressList");
  if (!list) return;
  const allItems = batchState.tasks;
  const counts = allItems.reduce((acc, item) => {
    const key = item.status || "queued";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const summary = $("#batchProgressSummary");
  if (summary) {
    const summaryHtml = [
      ["等待", counts.queued || 0],
      ["运行中", counts.running || 0],
      ["完成", counts.done || 0],
      ["失败", counts.error || 0],
      ["跳过", counts.skip || 0],
    ].map(([label, value]) => '<span class="batch-summary-pill">' + label + ' ' + value + '</span>').join("");
    if (summary.innerHTML !== summaryHtml) summary.innerHTML = summaryHtml;
  }
  $("#batchDone").textContent = batchState.done + "/" + batchState.total;
  const signature = allItems.map((t, index) => JSON.stringify([index, t.factorName || "", t.status || "", getBatchVerdict(t).verdict, t.raw ? "raw" : "", t.skipReason || ""])).join("|");
  if (signature === batchState.renderSignature) return;
  batchState.renderSignature = signature;
  list.innerHTML = allItems.map((t, index) => {
    const label = {running: "⏳ 运行中", done: "✅ 完成", error: "❌ 失败", skip: "⏭ 跳过"}[t.status] || "⏳ 等待";
    const verdict = getBatchVerdict(t);
    var detailBtn = (t.status === "done" || t.status === "error" || t.status === "skip") ? '<button class=\"mini-btn batch-detail-btn\" data-idx=\"' + index + '\" type=\"button\">详情</button>' : '';
    return '<div class=\"batch-progress-item\">' +
      '<span class=\"batch-progress-name\">' + escapeHtml(t.factorName || "(未命名)") + '</span>' +
      '<span class=\"batch-progress-status ' + t.status + '\">' + label + '</span>' +
      '<span class=\"batch-progress-verdict ' + verdict.verdict + '\">' + verdict.label + '</span>' +
      detailBtn +
      '</div>';
  }).join("");
}

function getBatchVerdict(item) {
  const status = item?.status || "";
  if (status === "queued" || status === "running") return { verdict: "idle", label: "总体结论：等待" };
  if (status === "skip") return { verdict: "idle", label: "总体结论：跳过" };
  if (status === "error") return { verdict: "fail", label: "总体结论：异常" };
  const verdict = item?.verdict || detectVerdict(item?.raw || item?.reportPreview || "");
  const labels = {
    pass: "总体结论：通过",
    warn: "总体结论：待确认",
    fail: "总体结论：不通过",
    idle: "总体结论：待确认",
  };
  return { verdict: verdict || "idle", label: labels[verdict] || labels.idle };
}

async function pollBatchTasks() {
  if (!batchState.taskId) { batchState.active = false; return; }
  try {
    const res = await fetch("/api/tasks/" + batchState.taskId);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "批量任务不存在");
    const task = data.task || {};
    renderGlobalPool(data.pool);
    const items = (task.result || {}).items || [];
    if (items.length) batchState.tasks = items;
    batchState.done = batchState.tasks.filter((t) => ["done", "error", "skip"].includes(t.status)).length;
    batchState.total = Math.max(batchState.total, batchState.tasks.length);
    renderBatchProgress();
    if (task.status === "queued" || task.status === "running") {
      setTimeout(pollBatchTasks, 2000);
      return;
    }
    batchState.active = false;
    addLog("批量评审完成：" + batchState.done + "/" + batchState.total, task.status === "done" ? "ok" : "err");
  } catch (e) {
    batchState.active = false;
    addLog("批量评审轮询失败：" + String(e.message || e), "err");
  }
}

function viewBatchDetail(idx) {
  var t = batchState.tasks[idx];
  if (!t) return;
  openReviewDetailModal({
    name: "批量评审详情 · " + (t.factorName || t.title || "未命名"),
    requirement: t.requirement || "",
    apiNames: t.apiNames || [],
    paramFields: t.paramFields || "",
    procLogic: t.procLogic || "",
    defaultValues: t.defaultValues || "",
    draft: t.draft || "",
    s1: t.standardText || "",
    s2: t.raw || t.reportPreview || t.skipReason || "暂无输出内容",
    raw: t.raw || t.reportPreview || t.skipReason || "",
    stats: t.summary || collectStats(t.raw || ""),
    verdict: t.verdict || detectVerdict(t.raw || ""),
  });
}

$("#batchFileInput")?.addEventListener("change", function(e) {
  if (e.target.files.length) handleBatchFile(e.target.files[0]);
});
$("#batchStartBtn")?.addEventListener("click", startBatchReview);
$("#batchProgressList")?.addEventListener("click", function(e) {
  const btn = e.target.closest(".batch-detail-btn");
  if (btn) viewBatchDetail(Number(btn.dataset.idx));
});
$("#batchTemplateBtn")?.addEventListener("click", function(e) {
  e.preventDefault();
  e.stopPropagation();
  downloadBatchTemplate();
});
// Click on upload area triggers file selection
$("#batchUpload")?.addEventListener("click", function(e) {
  if (e.target.tagName !== "INPUT" && e.target.tagName !== "LABEL" && e.target.tagName !== "BUTTON") {
    $("#batchFileInput")?.click();
  }
});

window.addEventListener("resize", resizeValidationChart);
window.addEventListener("resize", resizeReadinessChart);
updateReviewReadiness();

$("#historyDeleteBtn")?.addEventListener("click", deleteSelectedHistory);
$("#historySelectAll")?.addEventListener("change", function() {
  toggleSelectAllHistory(this.checked);
});
$("#historyPageSize")?.addEventListener("change", function() {
  updateHistoryPageSize(this.value);
});
$("#modalCloseBtn")?.addEventListener("click", closeHistoryModal);
$("#historyModal")?.addEventListener("click", function(e) {
  if (e.target === this || e.target.classList.contains("modal-wrap")) closeHistoryModal();
});
