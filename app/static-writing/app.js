const projectSelect = document.querySelector("#projectSelect");
const projectProgress = document.querySelector("#projectProgress");
const projectInventory = document.querySelector("#projectInventory");
const createProjectBtn = document.querySelector("#createProjectBtn");
const deleteProjectBtn = document.querySelector("#deleteProjectBtn");
const projectCreateForm = document.querySelector("#projectCreateForm");
const projectIdInput = document.querySelector("#projectIdInput");
const projectTypeSelect = document.querySelector("#projectTypeSelect");
const submitProjectCreateBtn = document.querySelector("#submitProjectCreateBtn");
const cancelProjectCreateBtn = document.querySelector("#cancelProjectCreateBtn");
const projectActionStatus = document.querySelector("#projectActionStatus");
const fileTreeEl = document.querySelector("#fileTree");
const workspaceTitle = document.querySelector("#workspaceTitle");
const stageBar = document.querySelector("#stageBar");
const messagesEl = document.querySelector("#messages");
const fileEditor = document.querySelector("#fileEditor");
const fileEditorTitle = document.querySelector("#fileEditorTitle");
const fileEditorPath = document.querySelector("#fileEditorPath");
const fileEditorText = document.querySelector("#fileEditorText");
const fileEditorStatus = document.querySelector("#fileEditorStatus");
const closeFileBtn = document.querySelector("#closeFileBtn");
const saveFileBtn = document.querySelector("#saveFileBtn");
const rewriteFileBtn = document.querySelector("#rewriteFileBtn");
const wikiViewer = document.querySelector("#wikiViewer");
const wikiViewerTitle = document.querySelector("#wikiViewerTitle");
const wikiViewerPath = document.querySelector("#wikiViewerPath");
const wikiViewerSummary = document.querySelector("#wikiViewerSummary");
const wikiViewerList = document.querySelector("#wikiViewerList");
const wikiViewerContent = document.querySelector("#wikiViewerContent");
const wikiViewerStatus = document.querySelector("#wikiViewerStatus");
const closeWikiBtn = document.querySelector("#closeWikiBtn");
const composer = document.querySelector("#composer");
const shortFilmActions = document.querySelector("#shortFilmActions");
const storyboardBeatInput = document.querySelector("#storyboardBeatInput");
const visualPromptBtn = document.querySelector("#visualPromptBtn");
const storyboardImagesBtn = document.querySelector("#storyboardImagesBtn");
const messageInput = document.querySelector("#messageInput");
const aiToggle = document.querySelector("#aiToggle");
const providerChecks = document.querySelector("#providerChecks");
const missionCard = document.querySelector("#missionCard");
const auditCard = document.querySelector("#auditCard");
const sopCard = document.querySelector("#sopCard");
const costCard = document.querySelector("#costCard");
const invocationCard = document.querySelector("#invocationCard");
const harnessCard = document.querySelector("#harnessCard");
const trajectoryCard = document.querySelector("#trajectoryCard");
const reviewPacketCard = document.querySelector("#reviewPacketCard");
const recallCard = document.querySelector("#recallCard");
const skillsCard = document.querySelector("#skillsCard");
const lessonsCard = document.querySelector("#lessonsCard");
const wikiCard = document.querySelector("#wikiCard");
const sendBtn = document.querySelector("#sendBtn");
const chatBtn = document.querySelector("#chatBtn");
const doctorBtn = document.querySelector("#doctorBtn");
const chatModelSelect = document.querySelector("#chatModelSelect");
const writingModelSelect = document.querySelector("#writingModelSelect");
const reviewModelSelect = document.querySelector("#reviewModelSelect");
const imageModelSelect = document.querySelector("#imageModelSelect");

let currentProject = localStorage.getItem("writing.ui.project") || "";
let currentKind = "generic";
let latestFlowTask = "";
let providers = [];
let workflowSop = null;
let collaborationState = null;
let historyLoadedFor = "";
let activeStatusTab = "cost";
const statusTabSignatures = {};
let activeFile = null;
let fileTreeData = null;
let pendingModelRetry = null;
let activeWorkflowStatus = null;
let pendingWorkflowPersistTimer = null;
let restoredWorkflowFlow = null;
let pendingWorkflowRecovery = null;
let latestLessonSuggestions = [];
let latestHarnessSuggestions = [];
const adoptingLessonKeys = new Set();
const adoptedLessonKeys = new Set();
const collapsedFileDirs = new Set();
const AI_TOGGLE_KEY = "writing.ui.aiEnabled";
const PROVIDER_PREFS_KEY = "writing.ui.providerPrefs";
const MODEL_PREFS_KEY = "writing.ui.modelPrefs";
let modelRegistry = { models: [], image_models: [], roles: {} };
let workflowRegistry = { presets: {}, labels: {} };

const FALLBACK_STAGE_PRESETS = {
  draft: ["request_analyze", "need_audit", "draft_assemble", "prompt_refine", "provider_route", "generate", "pre_review", "model_review", "draft_finalize", "user_confirm"],
  provider: ["provider_fanout", "provider_confirm_gate", "provider_consensus", "provider_digest", "provider_merge"],
  followup: ["request_analyze", "need_audit", "context_followup", "provider_route", "generate", "pre_review", "model_review", "draft_finalize", "user_confirm"],
  provider_confirm: ["provider_confirm_gate", "provider_consensus", "provider_digest", "provider_merge", "generate", "pre_review", "model_review", "draft_finalize", "user_confirm"],
  intervention: ["submit", "memory_lookup", "llm_analysis", "knowledge_settle", "memory_write", "policy_update", "impact_analyze", "primary_write", "primary_artifact", "related_write", "related_pending", "invocation_finalize", "pending_clear", "cleanup", "complete"],
  reference_import: ["reference_import_validate", "reference_import_save", "reference_import_analyze", "reference_import_five_dim", "reference_import_index", "reference_import_refresh"],
  archive: ["archive_submit", "archive_write", "overwrite_confirm", "overwrite", "archive_refresh", "complete"],
};
const VISUAL_PROMPT_STAGES = [
  "visual_prompt_start", "visual_prompt_beat", "visual_prompt_scene", "visual_prompt_characters",
  "visual_prompt_turnaround", "visual_prompt_start_frame", "visual_prompt_middle_frame",
  "visual_prompt_end_frame", "visual_prompt_key_frame", "visual_prompt_done",
];
const IMAGE_GENERATION_STAGES = [
  "image_generate_start", "image_generate_scene", "image_generate_characters",
  "image_generate_start_frame", "image_generate_middle_frame", "image_generate_end_frame",
  "image_generate_key_frame", "image_generate_done",
];
const FALLBACK_NODE_LABELS = {
  request_analyze: "请求理解",
  need_audit: "需求审计",
  context_followup: "上下文续问",
  draft_assemble: "材料装配",
  prompt_refine: "专业提问",
  provider_route: "路由决策",
  provider_fanout: "网页模型",
  provider_confirm_gate: "确认材料",
  provider_consensus: "共识归纳",
  provider_digest: "五维评分",
  provider_merge: "融合生成",
  generate: "融合成稿",
  pre_review: "规则预审",
  model_review: "模型审查",
  draft_finalize: "定稿",
  user_confirm: "用户确认",
  visual_prompt_start: "开始生词",
  visual_prompt_beat: "节拍",
  visual_prompt_scene: "场景词",
  visual_prompt_characters: "人物词",
  visual_prompt_turnaround: "三视图",
  visual_prompt_start_frame: "开始帧",
  visual_prompt_middle_frame: "中间帧",
  visual_prompt_end_frame: "结束帧",
  visual_prompt_key_frame: "关键帧",
  visual_prompt_done: "生词完成",
  image_generate_start: "开始生图",
  image_generate_scene: "场景图",
  image_generate_characters: "人物图",
  image_generate_start_frame: "开始帧图",
  image_generate_middle_frame: "中间帧图",
  image_generate_end_frame: "结束帧图",
  image_generate_key_frame: "关键帧图",
  image_generate_done: "生图完成",
};
const UI_OPERATION_LABELS = {
  workspace_status: "读取项目状态",
  workspace_files: "刷新文件树",
  workspace_cost: "刷新成本",
  workspace_mission: "刷新任务",
  workspace_observe: "刷新观察",
  workspace_history: "加载历史",
  project_submit: "提交项目操作",
  project_create: "创建结构",
  project_delete: "移动到回收区",
  project_reload: "刷新工作台",
  file_open: "打开文件",
  file_read: "读取内容",
  file_render: "渲染编辑器",
  file_validate: "校验文件",
  file_write: "保存文件",
  file_update_analyze: "联动分析",
  file_update_proposals: "待确认建议",
  file_update_apply: "采纳建议",
  file_update_reject: "拒绝建议",
  file_reload: "刷新文件树",
  chat_submit: "提交聊天",
  chat_model: "模型回答",
  chat_persist: "保存对话",
  provider_launch: "启动网页模型",
  provider_wait: "等待网页模型",
  provider_persist: "保存结果",
  provider_open: "打开浏览器内核",
  provider_pin: "固定会话",
  provider_reset: "重置会话",
  provider_refresh: "刷新状态",
  provider_confirm: "确认材料",
  provider_resume: "恢复生成",
  provider_consensus: "共识归纳",
  provider_digest: "逐篇五维评分",
  provider_merge: "融合生成",
  reference_import_validate: "校验上传",
  reference_import_save: "保存原文",
  reference_import_analyze: "五维抽取",
  reference_import_five_dim: "写入五维库",
  reference_import_index: "重建索引",
  reference_import_refresh: "刷新盘点",
  submit: "提交中",
  memory_lookup: "记忆查找",
  llm_analysis: "LLM 分析中",
  knowledge_settle: "知识沉淀",
  memory_write: "长期记忆写入",
  policy_update: "采纳策略更新",
  impact_analyze: "影响范围分析",
  primary_write: "主要文件改写",
  primary_artifact: "结构文件保存",
  related_write: "关联文件改写",
  related_pending: "关联文件待确认",
  invocation_finalize: "任务状态归档",
  pending_clear: "清理待确认记忆",
  complete: "完成",
  archive_submit: "提交归档",
  archive_write: "写入归档",
  overwrite_confirm: "等待覆盖确认",
  overwrite: "覆盖写回",
  archive_refresh: "刷新项目状态",
  doctor_request: "发起诊断",
  doctor_check: "执行检查",
  doctor_render: "渲染诊断",
  trajectory_load: "加载轨迹",
  packet_generate: "生成复盘包",
  operation_done: "完成",
};
const STRUCTURE_ROLE_LABELS = {
  base_setting: "基础设定",
  character: "人物设定",
  worldview: "世界观设定",
  plot: "情节设定",
  outline: "大纲",
  chapter_summary: "已完成章节摘要",
  chapter_status: "章节完成状态",
  narrative_rules: "叙事规则",
  style_guide: "风格规范",
  chapter_body: "章节正文",
  brief: "创作简报",
  concept: "概念",
  beat_sheet: "节拍表",
  screenplay: "剧本",
  shot_list: "分镜表",
  style: "影像风格",
  inbox: "随想收集",
  ideas: "灵感池",
  draft: "草稿",
  references: "参考材料",
};

function loadBoolPref(key, fallback = false) {
  const raw = localStorage.getItem(key);
  if (raw === null) return fallback;
  return raw === "1";
}

function saveBoolPref(key, value) {
  localStorage.setItem(key, value ? "1" : "0");
}

function loadProviderPrefs() {
  try {
    const parsed = JSON.parse(localStorage.getItem(PROVIDER_PREFS_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveProviderPref(provider, checked) {
  const prefs = loadProviderPrefs();
  prefs[provider] = Boolean(checked);
  localStorage.setItem(PROVIDER_PREFS_KEY, JSON.stringify(prefs));
}

function loadModelPrefs() {
  try {
    const parsed = JSON.parse(localStorage.getItem(MODEL_PREFS_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveModelPref(role, value) {
  const prefs = loadModelPrefs();
  prefs[role] = value || "";
  localStorage.setItem(MODEL_PREFS_KEY, JSON.stringify(prefs));
}

function modelPreferences() {
  return {
    chat: chatModelSelect?.value || "",
    writing: writingModelSelect?.value || "",
    review: reviewModelSelect?.value || "",
    image: imageModelSelect?.value || "",
  };
}

function modelRoleLabel(role) {
  return { chat: "聊天", writing: "创作", review: "审查", image: "生图" }[role] || role;
}

function missingModelRoles(roles) {
  const prefs = modelPreferences();
  return (roles || []).filter((role) => !prefs[role]);
}

function isModelError(message) {
  const text = String(message || "");
  const lower = text.toLowerCase();
  return text.includes("模型") || lower.includes("model") || lower.includes("api key") || lower.includes("llm");
}

function alertModelError(message) {
  const text = String(message || "");
  if (isModelError(text)) {
    window.alert(`${text}\n\n请在顶部模型下拉框切换后重试。`);
    return true;
  }
  return false;
}

function refreshModelPayload(payload = {}) {
  const prefs = modelPreferences();
  const next = { ...payload, model_preferences: prefs };
  if (Object.prototype.hasOwnProperty.call(next, "image_model_key")) {
    next.image_model_key = prefs.image;
  }
  return next;
}

function showModelRetry(message, retryAction, options = {}) {
  const text = String(message || "模型不可用，请在顶部切换模型后继续。");
  const roles = options.roles || [];
  const retryLabel = options.label || "已切换模型，继续执行";
  if (options.popup !== false) alertModelError(text);
  const msg = addMessage("system", text, "模型不可用");
  const row = document.createElement("div");
  row.className = "confirm-row model-retry-row";
  const retryBtn = document.createElement("button");
  retryBtn.type = "button";
  retryBtn.className = "button primary";
  retryBtn.textContent = retryLabel;
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "button";
  cancelBtn.textContent = "取消";
  const status = document.createElement("span");
  status.className = "muted-line model-retry-status";
  status.textContent = roles.length
    ? `需要：${roles.map(modelRoleLabel).join("、")}`
    : "将使用顶部当前选择的模型继续。";
  row.append(retryBtn, cancelBtn, status);
  msg.item.appendChild(row);
  scrollMessagesToBottom();

  const token = Symbol("model-retry");
  pendingModelRetry = { token, retryAction };
  retryBtn.addEventListener("click", async () => {
    const missing = missingModelRoles(roles);
    if (missing.length) {
      status.textContent = `仍缺少：${missing.map(modelRoleLabel).join("、")}模型`;
      return;
    }
    if (typeof retryAction !== "function") {
      status.textContent = "没有可继续的任务，请重新提交。";
      return;
    }
    retryBtn.disabled = true;
    cancelBtn.disabled = true;
    status.textContent = "继续执行中...";
    try {
      await retryAction();
      row.classList.add("done");
      status.textContent = "已继续执行";
      if (pendingModelRetry?.token === token) pendingModelRetry = null;
    } catch (error) {
      retryBtn.disabled = false;
      cancelBtn.disabled = false;
      status.textContent = `继续失败：${error}`;
      if (isModelError(error)) {
        showModelRetry(error, retryAction, { ...options, popup: false });
      }
    }
  });
  cancelBtn.addEventListener("click", () => {
    row.classList.add("done");
    retryBtn.disabled = true;
    cancelBtn.disabled = true;
    status.textContent = "已取消继续";
    if (pendingModelRetry?.token === token) pendingModelRetry = null;
  });
}

function offerModelRetry(message, retryAction, options = {}) {
  if (!isModelError(message)) return false;
  showModelRetry(message, retryAction, options);
  return true;
}

function requireModels(roles, retrySpec = {}) {
  const missing = missingModelRoles(roles);
  if (!missing.length) return true;
  const message = `请先在顶部选择可用的${missing.map(modelRoleLabel).join("、")}模型。`;
  showModelRetry(message, retrySpec.retry, {
    roles: missing,
    label: retrySpec.label || "已选择模型，继续",
  });
  return false;
}

function workflowStages(name, fallback = "draft") {
  const presets = workflowRegistry?.presets || {};
  const stages = presets[name] || FALLBACK_STAGE_PRESETS[name] || presets[fallback] || FALLBACK_STAGE_PRESETS[fallback] || [];
  return Array.isArray(stages) ? [...stages] : [];
}

async function loadWorkflowStages() {
  try {
    const res = await fetch("/api/writing/workflow-stages");
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || "流程节点加载失败");
    workflowRegistry = {
      presets: data.presets || {},
      labels: data.labels || {},
    };
  } catch {
    workflowRegistry = { presets: {}, labels: {} };
  }
}

async function loadModels() {
  try {
    const res = await fetch("/api/writing/models");
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || "模型列表加载失败");
    modelRegistry = data;
    renderModelSelect(chatModelSelect, data.models || [], "chat", data.roles?.chat);
    renderModelSelect(writingModelSelect, data.models || [], "writing", data.roles?.writing);
    renderModelSelect(reviewModelSelect, data.models || [], "review", data.roles?.review);
    renderModelSelect(imageModelSelect, data.image_models || [], "image", data.roles?.image);
  } catch (error) {
    addMessage("system", `模型配置加载失败：${error}`, "模型");
  }
}

function renderModelSelect(select, models, role, defaultKey) {
  if (!select) return;
  const prefs = loadModelPrefs();
  const selected = prefs[role] || defaultKey || "";
  select.innerHTML = "";
  if (!models.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "无可用模型";
    select.appendChild(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.key;
    option.textContent = model.model || model.name || model.key;
    option.title = [model.key, model.name, model.base_url].filter(Boolean).join(" · ");
    select.appendChild(option);
  }
  select.value = models.some((item) => item.key === selected) ? selected : "";
}

aiToggle.checked = loadBoolPref(AI_TOGGLE_KEY, false);

function projectKindLabel(kind) {
  return {
    novel_strong: "小说",
    short_film: "电影脚本",
    generic: "随想",
  }[kind] || kind || "项目";
}

function currentChapter() {
  return null;
}

function normalizeTask(value) {
  return String(value || "").trim();
}

function taskFromRequestAnalysis(analysis = {}) {
  if (!analysis || typeof analysis !== "object") return "";
  return normalizeTask(analysis.task || analysis.normalized_task || analysis.flow_task);
}

function taskFromStructureRole(role) {
  return {
    base_setting: "setting",
    character: "character",
    worldview: "world",
    plot: "beat_sheet",
    outline: "outline",
    brief: "brief",
    concept: "logline",
    beat_sheet: "beat_sheet",
    screenplay: "screenplay",
    shot_list: "shot_list",
    style: "style",
    inbox: "materials",
    ideas: "outline",
    draft: "draft",
    references: "materials",
  }[role] || "";
}

function structureRoleLabel(role, fallback = "") {
  const key = String(role || "").trim();
  return STRUCTURE_ROLE_LABELS[key] || fallback || key || "未知文件";
}

function activeFileTask() {
  return normalizeTask(activeFile?.task || taskFromStructureRole(activeFile?.structure_role));
}

function flowTask(fallback = "generic") {
  return normalizeTask(latestFlowTask || activeFileTask() || fallback);
}

function rememberFlowTask(...sources) {
  for (const source of sources) {
    const task = normalizeTask(
      typeof source === "string"
        ? source
        : source?.normalized_task || source?.task || taskFromRequestAnalysis(source?.request_analysis),
    );
    if (task && task !== "generic") {
      latestFlowTask = task;
      renderSop();
      return task;
    }
  }
  return "";
}

function loginConfirmed() {
  const result = {};
  for (const input of providerChecks.querySelectorAll("[data-provider]")) {
    result[input.dataset.provider] = input.checked;
  }
  return result;
}

function syncAiToggleFromProviders() {
  const hasSelectedProvider = Object.values(loginConfirmed()).some(Boolean);
  aiToggle.checked = hasSelectedProvider;
  saveBoolPref(AI_TOGGLE_KEY, aiToggle.checked);
}

function modeText(mode) {
  return {
    parallel_collect_then_serial_merge: "并行征集→串行融合",
    serial_transform: "串行转化",
    serial_repair: "串行修复",
  }[mode] || mode || "未指定";
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function compactText(value, max = 28) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  const pathLike = text.includes("/") || text.includes("\\");
  if (pathLike) {
    const parts = text.split(/[\\/]+/).filter(Boolean);
    const leaf = parts.at(-1) || text.slice(-10);
    const root = parts[0] || "";
    const compactPath = `${root}/…/${leaf}`;
    if (compactPath.length <= max + 4) return compactPath;
    const tail = Math.max(8, max - root.length - 3);
    return `${root}/…/${leaf.slice(-tail)}`;
  }
  const head = Math.max(8, Math.ceil(max * 0.55));
  const tail = Math.max(6, max - head - 1);
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}

function latestInvocationId() {
  return collaborationState?.latest_invocation_id || "";
}

function normalizeWritingPath(path) {
  const value = String(path || "").replaceAll("\\", "/").replace(/^\/+/, "");
  return value.startsWith("projects/writing/")
    ? value.slice("projects/writing/".length)
    : value;
}

function switchStatusTab(name) {
  activeStatusTab = name;
  document.querySelectorAll("[data-status-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.statusTab === name);
    if (btn.dataset.statusTab === name) btn.classList.remove("has-update");
  });
  document.querySelectorAll("[data-status-pane]").forEach((pane) => {
    pane.classList.toggle("active", pane.dataset.statusPane === name);
  });
}

function markStatusTabUpdated(name, value) {
  const signature = JSON.stringify(value ?? "");
  const previous = statusTabSignatures[name];
  statusTabSignatures[name] = signature;
  if (previous === undefined || previous === signature || activeStatusTab === name) return;
  const btn = document.querySelector(`[data-status-tab="${name}"]`);
  if (btn) btn.classList.add("has-update");
}

function scrollMessagesToBottom() {
  if (!messagesEl) return;
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

function createTypewriter(target, options = {}) {
  const minInterval = options.minInterval || 8;
  const smallBatch = options.smallBatch || 1;
  const mediumBatch = options.mediumBatch || 6;
  const largeBatch = options.largeBatch || 18;
  let queue = [];
  let displayed = target?.textContent || "";
  let timer = null;
  let resolveDrain = null;

  const batchSize = () => {
    if (queue.length > 3000) return largeBatch;
    if (queue.length > 800) return mediumBatch;
    return smallBatch;
  };

  const tick = () => {
    timer = null;
    if (!target) return;
    const take = Math.min(batchSize(), queue.length);
    if (take > 0) {
      displayed += queue.splice(0, take).join("");
      target.textContent = displayed;
      scrollMessagesToBottom();
    }
    if (queue.length) {
      timer = window.setTimeout(tick, minInterval);
      return;
    }
    if (resolveDrain) {
      const done = resolveDrain;
      resolveDrain = null;
      done();
    }
  };

  const schedule = () => {
    if (!timer) timer = window.setTimeout(tick, minInterval);
  };

  return {
    append(text) {
      if (!text) return;
      queue.push(...Array.from(text));
      schedule();
    },
    setText(text) {
      displayed = "";
      queue = Array.from(text || "");
      if (target) target.textContent = "";
      schedule();
    },
    drain() {
      if (!queue.length) return Promise.resolve();
      return new Promise((resolve) => {
        resolveDrain = resolve;
        schedule();
      });
    },
    flush() {
      if (!target) return;
      if (timer) window.clearTimeout(timer);
      timer = null;
      if (queue.length) {
        displayed += queue.join("");
        queue = [];
        target.textContent = displayed;
        scrollMessagesToBottom();
      }
      if (resolveDrain) {
        const done = resolveDrain;
        resolveDrain = null;
        done();
      }
    },
  };
}

function formatElapsed(ms) {
  const seconds = Math.max(0, Math.round((ms || 0) / 1000));
  return `${seconds}s`;
}

function epochFromPerf(perfValue) {
  if (!perfValue) return "";
  const elapsed = Math.max(0, performance.now() - perfValue);
  return new Date(Date.now() - elapsed).toISOString();
}

function createStageTimer(stages, onTick) {
  const now = performance.now();
  const timer = {
    startedAt: now,
    current: stages[0] || "",
    stageStartedAt: new Map(),
    durations: new Map(),
    totalMs: null,
    interval: null,
  };
  if (timer.current) timer.stageStartedAt.set(timer.current, now);
  timer.interval = window.setInterval(() => onTick?.(), 1000);
  return timer;
}

function markStageStarted(timer, node) {
  if (!timer || !node || timer.durations.has(node)) return;
  timer.current = node;
  if (!timer.stageStartedAt.has(node)) timer.stageStartedAt.set(node, performance.now());
}

function markStageDone(timer, node) {
  if (!timer || !node || timer.durations.has(node)) return;
  const started = timer.stageStartedAt.get(node) || performance.now();
  timer.durations.set(node, performance.now() - started);
}

function finishStageTimer(timer) {
  if (!timer) return;
  if (timer.totalMs !== null) return;
  timer.totalMs = performance.now() - timer.startedAt;
  if (timer.interval) window.clearInterval(timer.interval);
  timer.interval = null;
}

function stageElapsed(timer, node, isRunning) {
  if (!timer || !node) return "";
  if (timer.durations.has(node)) return formatElapsed(timer.durations.get(node));
  if (isRunning && timer.stageStartedAt.has(node)) {
    return formatElapsed(performance.now() - timer.stageStartedAt.get(node));
  }
  return "";
}

function workflowSnapshotFromFlow(flow, meta = {}) {
  if (!flow?.timer) return null;
  const timer = flow.timer;
  const durations = {};
  for (const [node, ms] of timer.durations.entries()) {
    durations[node] = Math.max(0, Math.round(ms));
  }
  return {
    stages: flow.stages || [],
    current: timer.current || "",
    done: Array.from(flow.doneNodes || []),
    durations_ms: durations,
    stage_started_at: epochFromPerf(timer.stageStartedAt.get(timer.current)),
    total_ms: timer.totalMs === null ? null : Math.max(0, Math.round(timer.totalMs)),
    status: meta.status || "running",
    source: meta.source || "ui",
    updated_at: new Date().toISOString(),
    invocation_id: meta.invocation_id || "",
    task: meta.task || flowTask(),
    chapter: meta.chapter || null,
    track: meta.track || "create",
  };
}

function workflowSnapshotFromTimer(stages, doneNodes, timer, meta = {}) {
  return workflowSnapshotFromFlow({ stages, doneNodes, timer }, meta);
}

function schedulePendingWorkflowPersist(snapshot, immediate = false) {
  if (!snapshot?.invocation_id) return;
  activeWorkflowStatus = snapshot;
  const send = () => {
    pendingWorkflowPersistTimer = null;
    const payload = {
      novel_id: currentProject,
      track: snapshot.track || "create",
      invocation_id: snapshot.invocation_id,
      workflow_status: snapshot,
    };
    fetch("/api/writing/pending-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
  };
  if (pendingWorkflowPersistTimer) window.clearTimeout(pendingWorkflowPersistTimer);
  if (immediate) {
    send();
  } else {
    pendingWorkflowPersistTimer = window.setTimeout(send, 350);
  }
}

function createRestoredFlow(stages) {
  if (restoredWorkflowFlow?.timer?.interval) {
    window.clearInterval(restoredWorkflowFlow.timer.interval);
  }
  restoredWorkflowFlow = createOperationFlow(stages);
  return restoredWorkflowFlow;
}

function restoreFlowFromWorkflowStatus(status = {}) {
  const stages = Array.isArray(status.stages) && status.stages.length ? status.stages : workflowStages("draft");
  const current = status.current || stages[0];
  const flow = createRestoredFlow(stages);
  const done = new Set(Array.isArray(status.done) ? status.done : []);
  flow.doneNodes.clear();
  for (const node of done) {
    if (stages.includes(node)) flow.doneNodes.add(node);
  }
  flow.timer.durations.clear();
  const durations = status.durations_ms || {};
  for (const [node, ms] of Object.entries(durations)) {
    if (stages.includes(node)) flow.timer.durations.set(node, Number(ms) || 0);
  }
  flow.timer.current = current;
  const started = Date.parse(status.stage_started_at || "");
  if (Number.isFinite(started)) {
    flow.timer.stageStartedAt.set(current, performance.now() - Math.max(0, Date.now() - started));
  } else {
    markStageStarted(flow.timer, current);
  }
  if (status.total_ms !== null && status.total_ms !== undefined) {
    flow.timer.totalMs = Number(status.total_ms) || null;
  }
  renderStages(current, flow.doneNodes, stages, flow.timer);
  return flow;
}

function setBusy(busy, label = "运行中") {
  sendBtn.disabled = busy;
  if (chatBtn) chatBtn.disabled = busy;
  doctorBtn.disabled = busy;
  if (createProjectBtn) createProjectBtn.disabled = busy;
  if (deleteProjectBtn) deleteProjectBtn.disabled = busy || !currentProject;
  if (submitProjectCreateBtn) submitProjectCreateBtn.disabled = busy;
  if (saveFileBtn) saveFileBtn.disabled = busy || !canEditActiveFile();
  if (rewriteFileBtn) rewriteFileBtn.disabled = busy || !canEditActiveFile();
  if (visualPromptBtn) visualPromptBtn.disabled = busy;
  if (storyboardImagesBtn) storyboardImagesBtn.disabled = busy;
  if (storyboardBeatInput) storyboardBeatInput.disabled = busy;
  workspaceTitle.textContent = busy ? label : "创作驾驶舱";
}

async function persistMessage(payload) {
  try {
    await fetch("/api/chat/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: "create", novel_id: currentProject, ...payload }),
    });
  } catch {
    // History persistence should never block the live UI.
  }
}

function addMessage(role, text, title = "", options = {}) {
  const item = document.createElement("article");
  item.className = `message ${role}`;
  if (title) {
    const head = document.createElement("div");
    head.className = "message-title";
    head.textContent = title;
    item.appendChild(head);
  }
  const body = document.createElement("div");
  body.textContent = text || "";
  item.appendChild(body);
  messagesEl.appendChild(item);
  scrollMessagesToBottom();
  if (options.persist) {
    persistMessage({ role, kind: "text", text: text || "", meta: title || "" });
  }
  return { item, body };
}

function cleanFinalDraftText(text = "") {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{4,}/g, "\n\n\n")
    .trim();
}

function draftResultMeta(ctx = {}) {
  const original = cleanFinalDraftText(ctx.original || "");
  const archiveContent = cleanFinalDraftText(ctx.archive_content || "");
  return {
    original,
    archive_content: archiveContent,
    chapter: ctx.chapter || null,
    task: ctx.task || flowTask(),
    track: ctx.track || "create",
    novel_id: ctx.novel_id || currentProject,
    project_kind: ctx.project_kind || currentKind,
    invocation_id: ctx.invocation_id || "",
    request_analysis: ctx.request_analysis || {},
    provider_answers: Array.isArray(ctx.provider_answers) ? ctx.provider_answers : [],
    artifacts: ctx.artifacts || {},
    merge_info: ctx.merge_info || {},
  };
}

async function persistDraftResult(text, title, ctx = {}) {
  const cleanText = cleanFinalDraftText(text || "");
  await persistMessage({
    role: "assistant",
    kind: "draft_result",
    text: cleanText,
    meta: title || "生成稿",
    data: draftResultMeta({ ...ctx, original: cleanText || ctx.original || "" }),
  });
}

function clearAcceptanceControls(keepMessage = null) {
  document.querySelectorAll(".acceptance-row, .acceptance-editor").forEach((el) => {
    const msg = el.closest(".message");
    if (keepMessage && msg === keepMessage) return;
    if (msg) msg._acceptanceControls = false;
    el.remove();
  });
}

function finalizeAcceptanceControls(msgEl, row, editor, label) {
  if (editor) editor.remove();
  row.innerHTML = "";
  row.classList.add("done");
  const status = document.createElement("span");
  status.className = "muted-line";
  status.textContent = label || "已处理";
  row.appendChild(status);
  if (msgEl) msgEl._acceptanceControls = false;
}

function extractDraftText(doneData, streamed = "") {
  return (
    doneData?.data?.draft ||
    doneData?.data?.answer ||
    doneData?.answer ||
    doneData?.draft ||
    streamed ||
    ""
  );
}

async function readJsonResponse(res) {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

function assertApiOk(res, data, fallback = "请求失败") {
  if (!res.ok || data?.ok === false) {
    throw new Error(data?.detail || data?.error || data?.message || fallback);
  }
}

async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 90000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    const data = await readJsonResponse(res);
    return { res, data };
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("请求超时，请检查后端是否仍在处理，或稍后重试。");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function providerDisplayName(provider) {
  return providers.find((item) => item.id === provider)?.name || provider;
}

function clearMessages() {
  messagesEl.innerHTML = "";
  stageBar.innerHTML = "";
  delete stageBar.dataset.stageSignature;
  invocationCard.textContent = "尚未运行";
  historyLoadedFor = "";
  pendingWorkflowRecovery = null;
  latestFlowTask = "";
  closeFileEditor();
}

async function reloadProjectWorkspace(options = {}) {
  const { clear = true, flow = null } = options;
  collapsedFileDirs.clear();
  if (clear) clearMessages();
  flow?.step("workspace_status");
  await loadStatus();
  flow?.step("workspace_files");
  await loadFiles();
  flow?.step("workspace_cost");
  await loadCostBoard();
  flow?.step("workspace_mission");
  await loadMission();
  flow?.step("workspace_observe");
  await loadObservability();
  flow?.step("workspace_history");
  await loadChatHistory();
}

function showConversation() {
  fileEditor.hidden = true;
  if (wikiViewer) wikiViewer.hidden = true;
  messagesEl.hidden = false;
  workspaceTitle.textContent = "创作驾驶舱";
}

function showFileEditor() {
  fileEditor.hidden = false;
  if (wikiViewer) wikiViewer.hidden = true;
  messagesEl.hidden = true;
  workspaceTitle.textContent = "文件编辑";
}

function showWikiViewer() {
  if (!wikiViewer) return;
  fileEditor.hidden = true;
  wikiViewer.hidden = false;
  messagesEl.hidden = true;
  workspaceTitle.textContent = "项目维基";
}

function canEditActiveFile() {
  return Boolean(activeFile) && !activeFile.truncated && activeFile.editable !== false;
}

function renderFileEditor() {
  if (!activeFile) {
    fileEditorTitle.textContent = "未选择文件";
    fileEditorPath.textContent = "";
    fileEditorText.value = "";
    fileEditorStatus.textContent = "";
    saveFileBtn.disabled = true;
    if (rewriteFileBtn) rewriteFileBtn.disabled = true;
    return;
  }
  fileEditorTitle.textContent = `${activeFile.dirty ? "* " : ""}${activeFile.name}`;
  fileEditorPath.textContent = activeFile.path;
  fileEditorText.value = activeFile.content + (activeFile.truncated ? "\n\n[内容已截断]" : "");
  if (activeFile.truncated) {
    fileEditorStatus.textContent = "内容已截断，为避免误覆盖，暂不支持保存。";
  } else if (activeFile.editable === false) {
    fileEditorStatus.textContent = activeFile.message || "框架文件受保护，不能在 Web 文件编辑器中保存。";
  } else {
    fileEditorStatus.textContent = activeFile.dirty ? "有未保存修改。" : "可编辑。";
  }
  saveFileBtn.disabled = !canEditActiveFile();
  if (rewriteFileBtn) rewriteFileBtn.disabled = !canEditActiveFile();
}

function closeFileEditor() {
  activeFile = null;
  renderFileEditor();
  renderSop();
  showConversation();
}

function findFileTreeNode(path, node = fileTreeData) {
  if (!path || !node) return null;
  if (node.path === path) return node;
  for (const child of node.children || []) {
    const found = findFileTreeNode(path, child);
    if (found) return found;
  }
  return null;
}

function isProjectWikiPath(path) {
  const text = String(path || "").replaceAll("\\", "/");
  return /(^|\/)novels\/[^/]+\/(维基|wiki)(\/|$)/.test(text);
}

function wikiKindLabel(path) {
  const name = String(path || "").split(/[\\/]/).pop() || "";
  if (name === "project-structure.json" || name === "项目结构.md" || name === "project-structure-map.md") return "结构 Wiki";
  if (name === "project_wiki.json" || name.startsWith("project-")) return "项目 Wiki";
  if (name === "index.json" || name.startsWith("WK-")) return "LLM Wiki";
  if (name === "README.md") return "Wiki 说明";
  return "项目维基";
}

function stripWikiMetadata(text) {
  return String(text || "").replace(/^```json\s*[\s\S]*?\n```\s*/i, "").trim();
}

function renderMarkdownLite(text) {
  const raw = stripWikiMetadata(text);
  if (!raw.trim()) return "<p class=\"muted-line\">暂无内容。</p>";
  const lines = raw.split(/\r?\n/);
  const html = [];
  let inCode = false;
  let codeLines = [];
  let listLines = [];
  const flushList = () => {
    if (!listLines.length) return;
    html.push(`<ul>${listLines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`);
    listLines = [];
  };
  const flushCode = () => {
    if (!codeLines.length) return;
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    codeLines = [];
  };
  for (const line of lines) {
    if (/^```/.test(line.trim())) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      continue;
    }
    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushList();
      const level = heading[1].length;
      html.push(`<h${level}>${escapeHtml(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      listLines.push(bullet[1]);
      continue;
    }
    flushList();
    html.push(`<p>${escapeHtml(trimmed)}</p>`);
  }
  flushList();
  if (inCode) flushCode();
  return html.join("");
}

function renderWikiIndex(llmData, projectData) {
  if (!wikiViewerSummary || !wikiViewerList) return;
  const llmItems = llmData?.items || [];
  const projectItems = projectData?.items || [];
  wikiViewerSummary.innerHTML = `
    <div><strong>${llmData?.summary?.total || 0}</strong> 条 LLM Wiki</div>
    <div><strong>${projectData?.summary?.total || 0}</strong> 条项目 Wiki</div>
    <div>格式：条目为 Markdown，索引为 JSON，只读。</div>
  `;
  const rows = [
    ...projectItems.slice(0, 8).map((item) => ({ ...item, group: "项目" })),
    ...llmItems.slice(0, 8).map((item) => ({ ...item, group: "共识" })),
  ];
  wikiViewerList.innerHTML = rows.length
    ? rows.map((item) => `
        <div class="wiki-list-item" title="${escapeHtml(item.path || "")}">
          <strong>${escapeHtml(item.title || item.id || "未命名条目")}</strong>
          <span>${escapeHtml(item.group)} · ${escapeHtml(item.category_label || item.category || "条目")} · ${escapeHtml(item.updated_at || "")}</span>
        </div>
      `).join("")
    : "<div class=\"muted-line\">暂无维基条目。</div>";
}

async function openWikiFile(path) {
  path = normalizeWritingPath(path);
  const flow = createOperationFlow(["file_open", "file_read", "file_render"]);
  try {
    flow.step("file_read");
    const [fileRes, llmRes, projectRes] = await Promise.all([
      fetch(`/api/writing/file?path=${encodeURIComponent(path)}`),
      fetch(`/api/writing/wiki?novel_id=${encodeURIComponent(currentProject)}&limit=50`),
      fetch(`/api/writing/project-wiki?novel_id=${encodeURIComponent(currentProject)}&limit=50`),
    ]);
    const data = await readJsonResponse(fileRes);
    assertApiOk(fileRes, data, "维基文件无法预览");
    if (data.previewable === false) {
      addMessage("system", data.message || "维基文件无法预览。", path);
      flow.fail();
      return;
    }
    const llmData = await readJsonResponse(llmRes);
    const projectData = await readJsonResponse(projectRes);
    assertApiOk(llmRes, llmData, "LLM Wiki 加载失败");
    assertApiOk(projectRes, projectData, "项目 Wiki 加载失败");
    wikiViewerTitle.textContent = `${wikiKindLabel(path)}｜${data.name || path.split(/[\\/]/).pop()}`;
    wikiViewerPath.textContent = path;
    wikiViewerStatus.textContent = data.message || "项目维基为只读内容，只能由确认采纳、知识沉淀和系统 API 更新。";
    renderWikiIndex(llmData, projectData);
    if (String(data.name || path).toLowerCase().endsWith(".json")) {
      try {
        wikiViewerContent.innerHTML = `<pre><code>${escapeHtml(JSON.stringify(JSON.parse(data.content || "{}"), null, 2))}</code></pre>`;
      } catch {
        wikiViewerContent.innerHTML = `<pre><code>${escapeHtml(data.content || "")}</code></pre>`;
      }
    } else {
      wikiViewerContent.innerHTML = renderMarkdownLite(data.content || "");
    }
    flow.step("file_render");
    showWikiViewer();
    flow.done();
  } catch (error) {
    flow.fail();
    addMessage("system", `打开维基失败：${error}`, path);
  }
}

async function openFile(path) {
  path = normalizeWritingPath(path);
  if (isProjectWikiPath(path)) {
    await openWikiFile(path);
    return;
  }
  const flow = createOperationFlow(["file_open", "file_read", "file_render"]);
  try {
    flow.step("file_read");
    const res = await fetch(`/api/writing/file?path=${encodeURIComponent(path)}`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "文件无法预览");
    if (data.previewable === false) {
      addMessage("system", data.message || "文件无法预览。", path);
      flow.fail();
      return;
    }
    const fileNode = findFileTreeNode(path) || {};
    activeFile = {
      path,
      name: data.name || path.split(/[\\/]/).pop(),
      content: data.content || "",
      savedContent: data.content || "",
      truncated: Boolean(data.truncated),
      editable: data.editable !== false,
      message: data.message || "",
      structure_role: data.structure_role || fileNode.structure_role || "",
      structure_label: data.structure_label || fileNode.structure_label || "",
      task: data.task || fileNode.task || "",
      dirty: false,
    };
    flow.step("file_render");
    renderSop();
    renderFileEditor();
    showFileEditor();
    flow.done();
  } catch (error) {
    flow.fail();
    addMessage("system", `打开文件失败：${error}`, path);
  }
}

async function saveActiveFile(options = {}) {
  if (!activeFile) return;
  const runUpdateFlow = Boolean(options.runUpdateFlow);
  activeFile.content = fileEditorText.value;
  activeFile.dirty = activeFile.content !== activeFile.savedContent;
  renderFileEditor();
  if (activeFile.truncated) {
    fileEditorStatus.textContent = "内容已截断，为避免误覆盖，暂不支持保存。";
    return;
  }
  if (activeFile.editable === false) {
    fileEditorStatus.textContent = activeFile.message || "框架文件受保护，不能在 Web 文件编辑器中保存。";
    return;
  }
  saveFileBtn.disabled = true;
  if (rewriteFileBtn) rewriteFileBtn.disabled = true;
  fileEditorStatus.textContent = runUpdateFlow ? "保存并分析中..." : "保存中...";
  const flow = createOperationFlow(runUpdateFlow
    ? ["file_validate", "file_write", "file_update_analyze", "file_update_proposals", "file_reload"]
    : ["file_validate", "file_write", "file_reload"]);
  try {
    flow.step("file_write");
    if (runUpdateFlow) flow.step("file_update_analyze");
    const res = await fetch("/api/writing/file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: activeFile.path,
        content: activeFile.content,
        novel_id: currentProject,
        run_update_flow: runUpdateFlow,
      }),
    });
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "保存失败");
    activeFile.savedContent = activeFile.content;
    activeFile.dirty = false;
    renderFileEditor();
    const proposals = Array.isArray(data.update?.proposals) ? data.update.proposals.length : 0;
    if (runUpdateFlow) {
      flow.step("file_update_proposals");
      if (proposals) {
        renderFileUpdateProposals(data.update.proposals);
      } else {
        fileEditorStatus.textContent = `已保存并完成联动分析：${data.update?.message || "未发现需要联动的建议。"}`;
      }
    } else {
      fileEditorStatus.textContent = `已保存：${activeFile.name}`;
    }
    flow.step("file_reload");
    await loadFiles();
    flow.done();
  } catch (error) {
    fileEditorStatus.textContent = `保存失败：${error}`;
    flow.fail();
    saveFileBtn.disabled = !canEditActiveFile();
    if (rewriteFileBtn) rewriteFileBtn.disabled = !canEditActiveFile();
  }
}

function renderFileUpdateProposals(proposals) {
  fileEditorStatus.innerHTML = `
    <div>已保存并生成 ${proposals.length} 条待确认更新建议。</div>
    <div class="file-update-proposals">
      ${proposals.map((item) => `
        <div class="file-update-proposal" data-update-id="${escapeHtml(item.id || "")}">
          <strong>${escapeHtml(item.target_name || item.target || "关联文件")}</strong>
          <span>${escapeHtml(item.reason || "待确认联动更新")}</span>
          <div class="mini-actions">
            <button type="button" data-apply-update="${escapeHtml(item.id || "")}" title="采纳该建议，将补丁写入目标文件。">采纳</button>
            <button type="button" data-reject-update="${escapeHtml(item.id || "")}" title="拒绝该建议，不写入目标文件。">拒绝</button>
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

async function handlePendingFileUpdate(updateId, action) {
  if (!updateId) return;
  const isApply = action === "apply";
  const row = Array.from(fileEditorStatus.querySelectorAll("[data-update-id]"))
    .find((item) => item.dataset.updateId === updateId);
  const rowButtons = row ? Array.from(row.querySelectorAll("button")) : [];
  rowButtons.forEach((btn) => { btn.disabled = true; });
  setBusy(true, isApply ? "采纳更新" : "拒绝更新");
  const flow = createOperationFlow([isApply ? "file_update_apply" : "file_update_reject", "file_reload", "workspace_status", "workspace_observe"]);
  try {
    flow.step(isApply ? "file_update_apply" : "file_update_reject");
    const res = await fetch(`/api/writing/file-update/${isApply ? "apply" : "reject"}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: updateId }),
    });
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false) {
      throw new Error(data.detail || data.error || "处理待确认更新失败");
    }
    if (row) {
      row.classList.add("done");
      const state = document.createElement("span");
      state.className = "muted-line";
      state.textContent = isApply ? "已采纳写入" : "已拒绝";
      row.appendChild(state);
    }
    flow.step("file_reload");
    await refreshWorkspacePanels(flow);
    flow.done();
  } catch (error) {
    flow.fail();
    rowButtons.forEach((btn) => { btn.disabled = false; });
    if (row) {
      const state = document.createElement("span");
      state.className = "muted-line error-line";
      state.textContent = `处理失败：${error}`;
      row.appendChild(state);
    } else {
      fileEditorStatus.textContent = `待确认更新处理失败：${error}`;
    }
  } finally {
    setBusy(false);
  }
}

function messageInvocationId(msg = {}) {
  return msg.data?.invocation_id || msg.data?.context?.invocation_id || "";
}

function messageSeq(msg = {}) {
  return Number.parseInt(msg.seq || "0", 10) || 0;
}

function providerAnswersFromDraftData(data = {}) {
  if (Array.isArray(data.provider_answers) && data.provider_answers.length) return data.provider_answers;
  const artifactAnswers = data.artifacts?.provider_answers?.provider_answers;
  if (Array.isArray(artifactAnswers) && artifactAnswers.length) return artifactAnswers;
  return [];
}

function workflowRecoveryPhase(status = {}) {
  const stages = Array.isArray(status.stages) ? status.stages : [];
  const current = status.current || "";
  const state = status.status || "";
  if (state === "awaiting_archive" || stages.includes("archive_submit") || current.startsWith("archive_") || current === "overwrite_confirm" || current === "overwrite") {
    return "archive";
  }
  if (state === "awaiting_confirm" && current === "provider_confirm_gate") return "provider_confirm";
  if (state === "awaiting_confirm" && (current === "user_confirm" || stages.includes("user_confirm"))) return "user_confirm";
  return "";
}

function buildHistoryRestoreState(messages = [], workflowRecovery = null) {
  const completedProviderInvocations = new Set();
  const providerAnswersByInvocation = new Map();
  const draftByInvocation = new Map();
  const draftBySeq = new Map();
  const providerByInvocation = new Map();
  const archivedInvocations = new Set();
  const archivePendingByInvocation = new Map();
  const looseArchivePending = [];
  for (const msg of messages) {
    const invocationId = messageInvocationId(msg);
    if (msg.kind === "draft_result") {
      const seq = messageSeq(msg);
      draftBySeq.set(seq, msg);
      if (!invocationId) continue;
      draftByInvocation.set(invocationId, msg);
      completedProviderInvocations.add(invocationId);
      const answers = providerAnswersFromDraftData(msg.data || {});
      if (answers.length) providerAnswersByInvocation.set(invocationId, answers);
    } else if (msg.kind === "provider" && invocationId) {
      providerByInvocation.set(invocationId, msg);
    } else if (msg.kind === "archive_result" && invocationId) {
      archivedInvocations.add(invocationId);
      archivePendingByInvocation.delete(invocationId);
    } else if (msg.kind === "archive_pending" && msg.data?.status !== "archived") {
      if (invocationId && !archivedInvocations.has(invocationId)) {
        archivePendingByInvocation.set(invocationId, msg);
      } else if (!invocationId) {
        looseArchivePending.push(msg);
      }
    }
  }

  let pendingDraftSeq = 0;
  let pendingProviderSeq = 0;
  const pendingArchiveByDraftSeq = new Map();
  const workflowStatus = workflowRecovery?.status || {};
  const workflowInvocationId = workflowStatus.invocation_id || workflowRecovery?.pending?.invocation_id || "";
  const workflowPhase = workflowRecoveryPhase(workflowStatus);
  for (const [invocationId, pending] of archivePendingByInvocation.entries()) {
    const draft = draftByInvocation.get(invocationId);
    if (draft) {
      pendingArchiveByDraftSeq.set(messageSeq(draft), archiveContextFromMessages(draft, pending));
    }
  }
  if (workflowPhase === "archive" && workflowInvocationId && !archivedInvocations.has(workflowInvocationId)) {
    const draft = draftByInvocation.get(workflowInvocationId);
    if (draft) {
      pendingArchiveByDraftSeq.set(
        messageSeq(draft),
        archiveContextFromWorkflow(draft, workflowRecovery?.pending || {}, workflowStatus),
      );
    }
  }
  for (const pending of looseArchivePending.slice(-1)) {
    let draft = null;
    const pendingSeq = messageSeq(pending);
    for (const [seq, item] of Array.from(draftBySeq.entries()).sort((a, b) => b[0] - a[0])) {
      if (!pendingSeq || seq < pendingSeq) {
        draft = item;
        break;
      }
    }
    if (draft) pendingArchiveByDraftSeq.set(messageSeq(draft), archiveContextFromMessages(draft, pending));
  }
  if (!pendingArchiveByDraftSeq.size) {
    const legacy = legacyPendingArchive(messages, draftByInvocation, draftBySeq, archivedInvocations);
    if (legacy?.draftSeq && legacy.context) {
      pendingArchiveByDraftSeq.set(legacy.draftSeq, legacy.context);
    }
  }
  if (workflowPhase === "user_confirm" && workflowInvocationId) {
    const draft = draftByInvocation.get(workflowInvocationId);
    if (draft) pendingDraftSeq = messageSeq(draft);
  } else if (workflowPhase === "provider_confirm" && workflowInvocationId) {
    const provider = providerByInvocation.get(workflowInvocationId);
    if (provider) pendingProviderSeq = messageSeq(provider);
  } else if (!workflowPhase) {
    for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
      const msg = messages[idx] || {};
      if (msg.kind === "archive_pending" && msg.data?.status !== "archived") break;
      if (msg.kind === "archive_result") break;
      if (msg.kind === "draft_result" && (msg.text || msg.data?.original)) {
        pendingDraftSeq = messageSeq(msg);
        break;
      }
      if (msg.kind === "provider" && msg.data?.awaiting_provider_confirm) {
        const invocationId = messageInvocationId(msg);
        if (!invocationId || !completedProviderInvocations.has(invocationId)) {
          pendingProviderSeq = messageSeq(msg);
          break;
        }
      }
      if (msg.kind === "intervene" || msg.role === "user") break;
    }
  }

  return {
    completedProviderInvocations,
    providerAnswersByInvocation,
    pendingDraftSeq,
    pendingProviderSeq,
    pendingArchiveByDraftSeq,
    workflowPhase,
    workflowInvocationId,
    timingByInvocation: new Map(),
  };
}

function archiveContextFromMessages(draftMsg = {}, pendingMsg = {}) {
  const draftData = draftMsg.data || {};
  const pendingData = pendingMsg.data || {};
  const text = cleanFinalDraftText(
    pendingData.archive_content || pendingData.accepted || draftData.archive_content || draftMsg.text || draftData.original || "",
  );
  return {
    ...draftData,
    ...pendingData,
    accepted: text,
    archive_content: text,
    original: text || cleanFinalDraftText(draftMsg.text || draftData.original || ""),
    task: pendingData.task || draftData.task || flowTask(),
    chapter: pendingData.chapter || draftData.chapter || null,
    track: pendingMsg.track || draftMsg.track || "create",
    novel_id: pendingMsg.novel_id || draftMsg.novel_id || currentProject,
    project_kind: pendingData.project_kind || draftData.project_kind || currentKind,
    invocation_id: pendingData.invocation_id || messageInvocationId(draftMsg) || "",
    request_analysis: pendingData.request_analysis || draftData.request_analysis || {},
  };
}

function archiveContextFromWorkflow(draftMsg = {}, pendingIntent = {}, workflowStatus = {}) {
  const draftData = draftMsg.data || {};
  const analysis = pendingIntent.analysis || draftData.request_analysis || {};
  const text = cleanFinalDraftText(draftData.archive_content || draftMsg.text || draftData.original || "");
  return {
    ...draftData,
    accepted: text,
    archive_content: text,
    original: text,
    task: workflowStatus.task || pendingIntent.task || analysis.task || draftData.task || flowTask(),
    chapter: workflowStatus.chapter || pendingIntent.chapter || analysis.target_chapter || draftData.chapter || null,
    track: workflowStatus.track || pendingIntent.track || draftMsg.track || "create",
    novel_id: pendingIntent.novel_id || draftMsg.novel_id || currentProject,
    project_kind: pendingIntent.project_kind || draftData.project_kind || currentKind,
    invocation_id: workflowStatus.invocation_id || pendingIntent.invocation_id || messageInvocationId(draftMsg) || "",
    request_analysis: analysis || {},
    workflow_status: workflowStatus,
  };
}

function legacyPendingArchive(messages = [], draftByInvocation = new Map(), draftBySeq = new Map(), archivedInvocations = new Set()) {
  for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
    const msg = messages[idx] || {};
    if (msg.kind === "archive_result") return null;
    if (msg.kind === "intervene") {
      const decision = msg.data?.decision || "";
      if (decision === "reject") return null;
      if (!["confirm", "other"].includes(decision)) continue;
      const invocationId = messageInvocationId(msg);
      if (invocationId && archivedInvocations.has(invocationId)) return null;
      let draft = invocationId ? draftByInvocation.get(invocationId) : null;
      if (!draft) {
        for (let j = idx - 1; j >= 0; j -= 1) {
          if (messages[j]?.kind === "draft_result") {
            draft = messages[j];
            break;
          }
        }
      }
      if (!draft) return null;
      const draftSeq = messageSeq(draft);
      if (!draftBySeq.has(draftSeq)) return null;
      const context = archiveContextFromMessages(draft, {
        ...msg,
        data: {
          ...(msg.data || {}),
          accepted: (msg.data?.decision === "other" ? msg.data?.user_text : "") || draft.data?.archive_content || draft.text || draft.data?.original || "",
          archive_content: draft.data?.archive_content || "",
          project_kind: draft.data?.project_kind || currentKind,
        },
      });
      return { draftSeq, context };
    }
    if (msg.role === "user") return null;
  }
  return null;
}

function parseEventTime(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function invocationStageTimings(record = {}, stages = []) {
  const durations = new Map();
  const stageStartedAt = new Map();
  const events = (record.events || [])
    .map((event) => ({ ...event, ts: parseEventTime(event.at) }))
    .filter((event) => event.ts)
    .sort((a, b) => a.ts - b.ts);
  if (!events.length) return { durations, stageStartedAt };

  let cursor = parseEventTime(record.created_at) || events[0].ts;
  const postProviderConfirmFlow = stages[0] === "provider_confirm_gate" && !stages.includes("provider_fanout");
  let active = !postProviderConfirmFlow;
  const addDuration = (node, start, end) => {
    if (!node || !stages.includes(node) || !start || !end || end < start) return;
    const delta = end - start;
    if (delta < 500) return;
    durations.set(node, (durations.get(node) || 0) + delta);
  };

  for (const event of events) {
    const node = event.node || "";
    if (postProviderConfirmFlow && !active && event.event !== "provider_material_confirmed") {
      continue;
    }
    if (event.event === "provider_fanout_started") {
      cursor = event.ts;
      if (stages.includes("provider_fanout")) stageStartedAt.set("provider_fanout", event.ts);
      continue;
    }
    if (event.event === "provider_material_confirmed") {
      active = true;
      cursor = event.ts;
      if (stages.includes("provider_confirm_gate")) stageStartedAt.set("provider_confirm_gate", event.ts);
      continue;
    }
    if (event.event === "graph_resume") {
      cursor = event.ts;
      if (stages.includes(node)) stageStartedAt.set(node, event.ts);
      continue;
    }
    if (event.event === "provider_stage" && node && stages.includes(node)) {
      if (event.status === "running") {
        stageStartedAt.set(node, event.ts);
        cursor = event.ts;
      } else if (event.status === "done") {
        addDuration(node, stageStartedAt.get(node) || cursor, event.ts);
        cursor = event.ts;
      }
      continue;
    }
    if (event.event === "graph_node_completed" && node && stages.includes(node)) {
      addDuration(node, cursor, event.ts);
      cursor = event.ts;
      continue;
    }
    if (event.event === "invocation_finished" && event.status === "awaiting_confirm") {
      if (stages.includes("user_confirm")) stageStartedAt.set("user_confirm", event.ts);
    }
  }

  const providers = record.providers || {};
  const providerElapsed = Object.values(providers)
    .map((item) => Number(item?.elapsed_seconds || 0))
    .filter((value) => value > 0);
  if (stages.includes("provider_fanout") && providerElapsed.length) {
    durations.set("provider_fanout", Math.max(...providerElapsed) * 1000);
  }
  return { durations, stageStartedAt };
}

async function enrichHistoryRestoreTimings(restore, messages = []) {
  const ids = Array.from(new Set(
    messages.map(messageInvocationId).filter(Boolean),
  ));
  await Promise.all(ids.map(async (invocationId) => {
    try {
      const res = await fetch(`/api/writing/invocation/${encodeURIComponent(invocationId)}?novel_id=${encodeURIComponent(currentProject)}`);
      const data = await readJsonResponse(res);
      if (!res.ok || data.ok === false || !data.invocation) return;
      restore.timingByInvocation.set(invocationId, data.invocation);
    } catch {
      // Missing invocation logs should not block history restore.
    }
  }));
}

function restoreFlowAt(stages, current, timing = null) {
  const flow = createRestoredFlow(stages);
  const currentIndex = stages.indexOf(current);
  const stageTimings = timing ? invocationStageTimings(timing, stages) : null;
  for (const node of stages.slice(0, Math.max(0, currentIndex))) {
    if (stageTimings?.durations?.has(node)) {
      flow.timer.durations.set(node, stageTimings.durations.get(node));
    }
    flow.doneNodes.add(node);
  }
  if (stageTimings?.stageStartedAt?.has(current)) {
    const epochStart = stageTimings.stageStartedAt.get(current);
    const elapsedSinceStart = Math.max(0, Date.now() - epochStart);
    flow.timer.stageStartedAt.set(current, performance.now() - elapsedSinceStart);
  }
  markStageStarted(flow.timer, current);
  renderStages(current, flow.doneNodes, stages, flow.timer);
  return flow;
}

function renderStoredMessage(msg, restore = {}) {
  if (msg.kind === "archive_pending") {
    return;
  }
  if (msg.kind === "provider" && msg.data) {
    renderStoredProviderMessage(msg.data, {
      seq: messageSeq(msg),
      completed: restore.completedProviderInvocations?.has(messageInvocationId(msg)),
      pending: restore.pendingProviderSeq === messageSeq(msg),
      restoredAnswers: restore.providerAnswersByInvocation?.get(messageInvocationId(msg)) || [],
    });
    return;
  }
  let text = msg.text || "";
  let messageRef = null;
  if (msg.kind === "draft_result") {
    text = cleanFinalDraftText(text);
    if (msg.data) {
      msg.data.original = cleanFinalDraftText(msg.data.original || text);
      msg.data.archive_content = cleanFinalDraftText(msg.data.archive_content || "");
    }
  }
  if (!text && msg.data) {
    try { text = JSON.stringify(msg.data, null, 2); } catch { text = String(msg.data); }
  }
  messageRef = addMessage(msg.role || "system", text, msg.meta || "", { persist: false });
  const archiveCtx = msg.kind === "draft_result"
    ? restore.pendingArchiveByDraftSeq?.get(messageSeq(msg))
    : null;
  if (archiveCtx) {
    const note = document.createElement("div");
    note.className = "muted-line writeback-hint";
    note.textContent = "已确认采纳，等待归档写回。";
    messageRef.item.appendChild(note);
    attachArchiveControls(messageRef.item, archiveCtx);
  } else if (msg.kind === "draft_result" && restore.pendingDraftSeq === messageSeq(msg) && text) {
    const hasProvider = Boolean(messageInvocationId(msg) && restore.completedProviderInvocations?.has(messageInvocationId(msg)));
    const stages = hasProvider ? workflowStages("provider_confirm") : workflowStages("draft");
    const flow = restore.workflowPhase
      ? restoredWorkflowFlow
      : restoreFlowAt(stages, "user_confirm", restore.timingByInvocation?.get(messageInvocationId(msg)));
    attachAcceptanceControls(messageRef, {
      ...(msg.data || {}),
      original: text,
      archive_content: msg.data?.archive_content || "",
      model_preferences: modelPreferences(),
    }, flow);
  }
}

function renderStoredProviderMessage(data, options = {}) {
  const wrap = document.createElement("article");
  wrap.className = "message assistant";
  const title = document.createElement("div");
  title.className = "message-title";
  title.textContent = data.message || "网页模型协同结果";
  wrap.appendChild(title);
  const grid = document.createElement("div");
  grid.className = "provider-card-grid";
  wrap._grid = grid;
  wrap._cards = {};
  const completed = Boolean(options.completed);
  const restoredAnswers = Array.isArray(options.restoredAnswers) ? options.restoredAnswers : [];
  const results = (data.results || []).map((result) => {
    if (String(result.result || "").trim() || !restoredAnswers.length) return result;
    const restored = restoredAnswers.find((item) => (item.provider || "") === (result.provider || ""));
    return restored ? { ...result, ...restored, status: restored.status || "success" } : result;
  });
  for (const result of results) {
    const card = renderProviderCard(result, {
      editable: !completed && Boolean(data.manual_entry || data.awaiting_provider_confirm),
      completed,
    });
    grid.appendChild(card);
    if (result.provider) wrap._cards[result.provider] = card;
  }
  wrap.appendChild(grid);
  if (completed) {
    const done = document.createElement("div");
    done.className = "muted-line provider-history-done";
    done.textContent = "材料已确认，融合稿已生成。";
    wrap.appendChild(done);
  }
  messagesEl.appendChild(wrap);
  if (!completed && options.pending && data.awaiting_provider_confirm && data.context) {
    attachProviderGate(wrap, data.context);
  }
}

function renderProviderCard(result = {}, options = {}) {
  const card = document.createElement("section");
  card.className = "provider-card";
  card.dataset.provider = result.provider || "";
  const hasText = Boolean(String(result.result || "").trim());
  const status = result.status || (hasText ? "success" : "partial");
  const name = result.name || result.provider || "网页模型";
  card.innerHTML = `<header><strong></strong><span class="status-chip"></span></header>`;
  card.querySelector("strong").textContent = name;
  const chip = card.querySelector(".status-chip");
  updateProviderStatusChip(chip, status);
  card._providerResult = {
    provider: result.provider || "",
    name,
    status,
    result: result.result || "",
    files: result.files || [],
  };
  if (options.editable && (!hasText || result.manual_entry)) {
    const input = document.createElement("textarea");
    input.className = "provider-manual-input";
    input.placeholder = `粘贴${name}的完整回答`;
    input.value = result.result || "";
    input.addEventListener("input", () => {
      card._providerResult.result = input.value.trim();
      card._providerResult.status = card._providerResult.result ? "success" : "partial";
      updateProviderStatusChip(chip, card._providerResult.status);
    });
    card.appendChild(input);
  } else {
    const pre = document.createElement("pre");
    pre.textContent = result.result || (options.completed ? "材料已确认并完成融合，原始回答未保存在历史卡片。" : "等待手动粘贴 provider 回答。");
    card.appendChild(pre);
  }
  return card;
}

function updateProviderStatusChip(chip, status) {
  if (!chip) return;
  chip.className = `status-chip ${status === "failed" ? "error" : status === "partial" ? "warn" : "ok"}`;
  chip.textContent = status || "done";
}

async function loadChatHistory() {
  if (historyLoadedFor === currentProject) return;
  try {
    const res = await fetch(`/api/chat/history?novel_id=${encodeURIComponent(currentProject)}`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "历史对话加载失败");
    const messages = data.messages || [];
    const restore = buildHistoryRestoreState(messages, pendingWorkflowRecovery);
    await enrichHistoryRestoreTimings(restore, messages);
    for (const msg of messages) renderStoredMessage(msg, restore);
    historyLoadedFor = currentProject;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  } catch {
    addMessage("system", "历史对话加载失败", "History", { persist: false });
  }
}

async function loadPendingWorkflowStatus() {
  if (!currentProject) return;
  pendingWorkflowRecovery = null;
  try {
    const res = await fetch(`/api/writing/pending-status?novel_id=${encodeURIComponent(currentProject)}&track=create`);
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false || !data.found || !data.workflow_status) return;
    const status = data.workflow_status || {};
    if (!Array.isArray(status.stages) || !status.stages.length || !status.current) return;
    activeWorkflowStatus = status;
    pendingWorkflowRecovery = { status, pending: data.pending_intent || {} };
    restoreFlowFromWorkflowStatus(status);
    const pending = data.pending_intent || {};
    if (pending.invocation_id) {
      invocationCard.innerHTML = `<div class="invocation-list"><strong>${escapeHtml(pending.invocation_id)}</strong><span>pending intent 恢复</span></div>`;
    }
  } catch {
    // Pending workflow restore is best-effort; chat history can still recover.
  }
}

function renderStages(current, done = new Set(), stages = null, timer = null) {
  const visibleStages = Array.isArray(stages) && stages.length ? stages : workflowStages("draft");
  const hasTotal = Boolean(timer && timer.totalMs !== null);
  const signature = [...visibleStages, ...(hasTotal ? ["__total__"] : [])].join("|");
  if (stageBar.dataset.stageSignature !== signature) {
    stageBar.textContent = "";
    for (const node of visibleStages) {
      const chip = document.createElement("span");
      chip.className = "stage-chip";
      chip.dataset.stageNode = node;
      stageBar.appendChild(chip);
    }
    if (hasTotal) {
      const total = document.createElement("span");
      total.className = "stage-chip total";
      total.dataset.stageNode = "__total__";
      stageBar.appendChild(total);
    }
    stageBar.dataset.stageSignature = signature;
  }
  for (const node of visibleStages) {
    const chip = Array.from(stageBar.children).find((item) => item.dataset.stageNode === node);
    if (!chip) continue;
    const stateClass = done.has(node) ? "done" : node === current ? "running" : "";
    const nextClassName = `stage-chip${stateClass ? ` ${stateClass}` : ""}`;
    if (chip.className !== nextClassName) chip.className = nextClassName;
    const elapsed = stageElapsed(timer, node, node === current && !done.has(node));
    const text = `${done.has(node) ? "✓ " : ""}${stageLabel(node)}${elapsed ? ` · ${elapsed}` : ""}`;
    if (chip.textContent !== text) chip.textContent = text;
  }
  const total = stageBar.querySelector('[data-stage-node="__total__"]');
  if (hasTotal && total) {
    const text = `总耗时 · ${formatElapsed(timer.totalMs)}`;
    if (total.textContent !== text) total.textContent = text;
  }
}

function stageLabel(node) {
  return workflowRegistry.labels?.[node] || FALLBACK_NODE_LABELS[node] || UI_OPERATION_LABELS[node] || node;
}

function createOperationFlow(stages) {
  const steps = Array.isArray(stages) && stages.length ? stages : ["operation_done"];
  const doneNodes = new Set();
  const timer = createStageTimer(steps, () => renderStages(timer.current, doneNodes, steps, timer));
  renderStages(steps[0], doneNodes, steps, timer);

  return {
    stages: steps,
    doneNodes,
    timer,
    step(node) {
      const target = node || steps[0];
      if (timer.current && timer.current !== target) {
        markStageDone(timer, timer.current);
        doneNodes.add(timer.current);
      }
      markStageStarted(timer, target);
      renderStages(target, doneNodes, steps, timer);
    },
    done() {
      if (timer.current) {
        markStageDone(timer, timer.current);
        doneNodes.add(timer.current);
      }
      finishStageTimer(timer);
      renderStages(timer.current, doneNodes, steps, timer);
    },
    fail() {
      finishStageTimer(timer);
      renderStages(timer.current, doneNodes, steps, timer);
    },
  };
}

function applyFlowProgress(flow, event = {}) {
  if (!flow?.timer || !event.stage) return;
  const stages = flow.stages || workflowStages("draft");
  const doneNodes = flow.doneNodes || new Set();
  const stage = event.stage;
  if (!stages.includes(stage)) return;
  if (event.status === "done") {
    if (flow.timer.current !== stage) {
      flow.step(stage);
    }
    markStageDone(flow.timer, stage);
    doneNodes.add(stage);
    const next = nextStageAfter(stage, stages);
    if (next && next !== stage && !doneNodes.has(next)) {
      markStageStarted(flow.timer, next);
    }
    renderStages(next || stage, doneNodes, stages, flow.timer);
    return;
  }
  if (event.status === "error") {
    if (flow.timer.current !== stage) flow.step(stage);
    flow.fail();
    return;
  }
  flow.step(stage);
}

async function refreshWorkspacePanels(flow = null) {
  flow?.step("workspace_status");
  await loadStatus();
  flow?.step("workspace_files");
  await loadFiles();
  flow?.step("workspace_cost");
  await loadCostBoard();
  flow?.step("workspace_mission");
  await loadMission();
  flow?.step("workspace_observe");
  await loadObservability();
}

function stagesForPayload(payload) {
  const selectedProviders = Object.values(payload.login_confirmed || {}).some(Boolean);
  const base = workflowStages("draft");
  if (!payload.use_provider_source || !selectedProviders) return base;
  const routeIndex = base.indexOf("provider_route");
  if (routeIndex < 0) return base;
  return [
    ...base.slice(0, routeIndex + 1),
    ...workflowStages("provider"),
    ...base.slice(routeIndex + 1),
  ];
}

function nextStageAfter(node, stages) {
  const idx = stages.indexOf(node);
  return idx >= 0 && idx + 1 < stages.length ? stages[idx + 1] : node;
}

function renderTasks() {
  if (shortFilmActions) shortFilmActions.hidden = currentKind !== "short_film";
}

function renderProjectProgress(progress) {
  if (!projectProgress) return;
  const items = Array.isArray(progress?.items) ? progress.items : [];
  if (!items.length) {
    projectProgress.innerHTML = `<div class="project-progress-empty">暂无项目进度</div>`;
    return;
  }
  const chips = progress.kind === "short_film" && Array.isArray(progress.stages)
    ? `<div class="project-stage-strip">${progress.stages.map((stage) => (
        `<span class="${stage.done ? "done" : ""}" title="${escapeHtml(stage.path || stage.label || "")}">${escapeHtml(stage.label || "")}</span>`
      )).join("")}</div>`
    : "";
  projectProgress.innerHTML = `
    <div class="project-progress-grid">
      ${items.map((item) => `
        <div class="project-progress-item ${item.wide ? "wide" : ""} ${item.label === "当前进度" ? "current" : ""}">
          <span>${escapeHtml(item.label || "")}</span>
          <strong>${escapeHtml(item.value ?? "")}${escapeHtml(item.unit || "")}</strong>
        </div>
      `).join("")}
    </div>
    ${chips}
  `;
}

function renderFiveDimChart(dimensions) {
  const axes = [
    ["scenes", "场景"],
    ["psychology", "心理"],
    ["characters", "角色"],
    ["twists", "反转"],
    ["intelligence", "智性"],
  ];
  const values = axes.map(([key]) => Number(dimensions?.[key] || 0));
  const maxValue = Math.max(...values, 1);
  const cx = 58;
  const cy = 58;
  const radius = 35;
  const point = (index, ratio = 1, extra = 0) => {
    const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
    const r = radius * ratio + extra;
    return {
      x: cx + Math.cos(angle) * r,
      y: cy + Math.sin(angle) * r,
    };
  };
  const polygon = values.map((value, index) => {
    const p = point(index, value / maxValue);
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }).join(" ");
  const rings = [0.33, 0.66, 1].map((ratio) => {
    const pts = axes.map((_, index) => {
      const p = point(index, ratio);
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    }).join(" ");
    return `<polygon class="five-dim-ring" points="${pts}"></polygon>`;
  }).join("");
  const axisLines = axes.map(([, label], index) => {
    const end = point(index);
    const labelPoint = point(index, 1, 13);
    const anchor = Math.abs(labelPoint.x - cx) < 3 ? "middle" : labelPoint.x > cx ? "start" : "end";
    return `
      <line class="five-dim-axis" x1="${cx}" y1="${cy}" x2="${end.x.toFixed(1)}" y2="${end.y.toFixed(1)}"></line>
      <text class="five-dim-label" x="${labelPoint.x.toFixed(1)}" y="${labelPoint.y.toFixed(1)}" text-anchor="${anchor}">${escapeHtml(label)}</text>
    `;
  }).join("");
  const legend = axes.map(([key, label]) => `
    <span><b>${escapeHtml(label)}</b>${escapeHtml(dimensions?.[key] || 0)}</span>
  `).join("");
  return `
    <div class="five-dim-chart">
      <div class="five-dim-chart-head">
        <span>五维度图表</span>
        <strong>峰值 ${escapeHtml(maxValue)}</strong>
      </div>
      <div class="five-dim-chart-body">
        <svg viewBox="-12 -8 140 132" role="img" aria-label="五维库维度分布图">
          ${rings}
          ${axisLines}
          <polygon class="five-dim-area" points="${polygon}"></polygon>
          ${values.map((value, index) => {
            const p = point(index, value / maxValue);
            return `<circle class="five-dim-dot" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="2.4"></circle>`;
          }).join("")}
        </svg>
        <div class="five-dim-legend">${legend}</div>
      </div>
    </div>
  `;
}

function renderProjectInventory(inventory) {
  if (!projectInventory) return;
  const skills = inventory?.skills || {};
  const refs = inventory?.reference_novels || {};
  const five = inventory?.five_dim || {};
  const dimensions = five.dimensions || {};
  const dimText = Object.entries(dimensions)
    .map(([name, count]) => `${name}: ${count}`)
    .join("\n");
  const refTitle = [
    `清单：${refs.listed_count || 0}`,
    `原文：${refs.raw_count || 0}`,
    `已抽取：${refs.extracted_count || 0}`,
    refs.reference_dir ? `目录：${refs.reference_dir}` : "",
  ].join("\n");
  const fiveTitle = [
    `分析文件：${five.analysis_file_count || 0}`,
    `锚点文件：${five.anchor_file_count || 0}`,
    `锚点：${five.anchor_count || 0}`,
    `片段：${five.segment_count || 0}`,
    dimText ? `维度：\n${dimText}` : "",
  ].filter(Boolean).join("\n");
  const skillTitle = [
    `公共技能：${skills.public_count || 0}`,
    `项目沉淀技能：${skills.project_count || 0}`,
    ...(skills.files || []).map((item) => {
      if (typeof item === "string") return item;
      const label = item.source === "public_novel"
        ? "小说公共"
        : item.source === "public_short_film"
          ? "电影公共"
          : "项目";
      return `${label}：${item.name || item.path || ""}`;
    }),
  ].filter(Boolean).join("\n");
  const rows = [
    { label: "技能", value: skills.count || 0, unit: "张", title: skillTitle || "暂无技能卡" },
    { label: "参考小说", value: refs.count || 0, unit: "本", title: refTitle, action: "import_reference" },
    { label: "五维库", value: five.segment_count || five.anchor_count || 0, unit: five.segment_count ? "段" : "项", title: fiveTitle },
  ];
  const chart = Object.keys(dimensions).length ? renderFiveDimChart(dimensions) : "";
  projectInventory.innerHTML = `
    <div class="project-inventory-grid">
      ${rows.map((item) => `
        <div class="project-inventory-item" title="${escapeHtml(item.title || "")}">
          <span>${escapeHtml(item.label)}</span>
          <strong class="${item.action === "import_reference" ? "with-action" : ""}">
            <span>${escapeHtml(item.value)}${escapeHtml(item.unit)}</span>
            ${item.action === "import_reference" ? `<button class="inventory-action-btn" type="button" data-import-reference title="导入 TXT 小说到参考小说库，并执行五维抽取与索引重建。">导入</button>` : ""}
          </strong>
        </div>
      `).join("")}
    </div>
    ${chart}
  `;
}

function referenceImportInput() {
  let input = document.querySelector("#referenceNovelImportInput");
  if (input) return input;
  input = document.createElement("input");
  input.id = "referenceNovelImportInput";
  input.type = "file";
  input.accept = ".txt,text/plain";
  input.hidden = true;
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    input.value = "";
    if (file) await importReferenceNovel(file);
  });
  document.body.appendChild(input);
  return input;
}

async function importReferenceNovel(file) {
  if (!file) return;
  if (!/\.txt$/i.test(file.name || "")) {
    addMessage("system", "只支持导入 .txt 格式小说。", "参考小说");
    return;
  }
  setBusy(true, "导入参考小说");
  setProjectActionStatus(`正在导入：${file.name}`);
  const flow = createOperationFlow(workflowStages("reference_import"));
  const form = new FormData();
  form.append("file", file);
  let doneData = null;
  try {
    const res = await fetch(`/api/writing/reference-novels/import-stream?novel_id=${encodeURIComponent(currentProject)}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok || !res.body) {
      const errorData = await readJsonResponse(res);
      throw new Error(errorData.detail || errorData.error || "导入状态流不可用");
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split("\n\n");
      buf = blocks.pop() || "";
      for (const block of blocks) {
        const ev = parseSSE(block);
        if (!ev) continue;
        if (ev.event === "progress") {
          applyFlowProgress(flow, ev.data || {});
          const label = ev.data?.label || stageLabel(ev.data?.stage || "");
          const status = ev.data?.status === "done" ? "完成" : ev.data?.status === "warn" ? "有警告" : "处理中";
          if (label) setProjectActionStatus(`${label}：${status}`);
        } else if (ev.event === "done") {
          doneData = ev.data || {};
        } else if (ev.event === "error") {
          throw new Error(ev.data?.message || "导入失败");
        }
      }
    }
    flow.done();
    if (doneData?.project_status) {
      currentKind = doneData.project_status.project_kind || currentKind;
      renderProjectProgress(doneData.project_status.project_progress || {});
      renderProjectInventory(doneData.project_status.project_inventory || {});
    } else {
      await loadStatus();
    }
    await loadFiles();
    const warnings = Array.isArray(doneData?.warnings) ? doneData.warnings : [];
    setProjectActionStatus(warnings.length ? "导入完成，但部分索引步骤有警告。" : "参考小说导入完成。", warnings.length ? "warn" : "");
    addMessage(
      "system",
      `已导入《${doneData?.title || file.name}》。${warnings.length ? `警告：${warnings.map((item) => item.message).join("；")}` : "五维库与索引已刷新。"}`,
      "参考小说",
    );
  } catch (error) {
    flow.fail();
    setProjectActionStatus(`导入失败：${error}`, "error");
    addMessage("system", `参考小说导入失败：${error}`, "参考小说");
  } finally {
    setBusy(false);
  }
}

function activeShortFilmSource() {
  if (!activeFile) return {};
  const task = activeFileTask();
  if (!task) return {};
  return {
    task,
    content: activeFile.content || "",
    source_path: activeFile.path.startsWith("projects/") ? activeFile.path : `projects/writing/${activeFile.path}`,
  };
}

function renderSop() {
  const task = flowTask();
  const sop = workflowSop?.tasks?.[task];
  if (!sop) {
    sopCard.innerHTML = `
      <div><strong>LLM 自动判断任务</strong></div>
      <div class="muted-line">请求理解节点将结合项目结构和当前状态选择流程。</div>
    `;
    return;
  }
  sopCard.innerHTML = `
    <div><strong>${sop.stage || task}</strong></div>
    <div class="muted-line">角色：${sop.role_label || sop.role || "创作助手"}</div>
    <div class="muted-line">协作：${modeText(sop.mode)}</div>
    <div class="muted-line">确认门：${sop.confirmation_gate || "material_selection"}</div>
    <div class="muted-line">硬规则：${(sop.hard_rules || []).length} 条</div>
  `;
}

function renderCollaborationIdle() {
  if (!collaborationState) return;
  if (!collaborationState.latest_invocation_id) {
    invocationCard.textContent = "尚未运行";
    markStatusTabUpdated("invocation", "empty");
    return;
  }
  invocationCard.innerHTML = `
    <div><strong>${collaborationState.latest_invocation_id}</strong></div>
    <div class="muted-line">状态：${collaborationState.latest_status || "unknown"}</div>
    <div class="muted-line">轨迹：${collaborationState.trajectory_count || 0} · 门禁：${collaborationState.harness_count || 0} · 预算：${collaborationState.budget_count || 0}</div>
    <div class="mini-actions">
      <button type="button" data-load-trajectory="${collaborationState.latest_invocation_id}">轨迹</button>
      <button type="button" data-review-packet="${collaborationState.latest_invocation_id}">Packet</button>
    </div>
  `;
  markStatusTabUpdated("invocation", {
    id: collaborationState.latest_invocation_id,
    status: collaborationState.latest_status,
    trajectory: collaborationState.trajectory_count,
    harness: collaborationState.harness_count,
    budget: collaborationState.budget_count,
  });
}

function renderCostBoard(data) {
  const summary = data?.summary || {};
  const latest = (data?.items || [])[0] || {};
  const route = latest.route || {};
  costCard.innerHTML = `
    <div class="metric-grid">
      <div><strong>${summary.estimated_total_tokens || 0}</strong><span>估算总量</span></div>
      <div><strong>${summary.average_estimated_tokens || 0}</strong><span>单次均值</span></div>
      <div><strong>${summary.fanout_routes || 0}</strong><span>Fanout</span></div>
      <div><strong>${summary.single_agent_routes || 0}</strong><span>单 Agent</span></div>
    </div>
    <div class="muted-line">最近：${route.decision || "none"} · ${route.reason || "暂无任务"}</div>
  `;
  markStatusTabUpdated("cost", data);
}

function renderMission(data) {
  const stages = data?.stages || [];
  const recent = data?.recent || [];
  const latest = recent[0] || {};
  missionCard.innerHTML = `
    <div class="stage-mini">${stages.map((stage) => `<span class="${stage.status || "pending"}">${stage.label || stage.id}</span>`).join("")}</div>
    <div class="muted-line">当前：${data?.active_stage || "unknown"} · 阻塞 ${data?.blocking?.length || 0}</div>
    <div class="muted-line">最近：${escapeHtml(latest.id || "无任务")} · ${escapeHtml(latest.status || "idle")}</div>
    ${latest.id ? `
      <div class="mini-actions">
        <button type="button" data-load-trajectory="${escapeHtml(latest.id)}">查看轨迹</button>
        <button type="button" data-review-packet="${escapeHtml(latest.id)}">生成复盘</button>
      </div>
    ` : ""}
  `;
}

function renderNeedAudit(audit) {
  const risks = audit?.risks || [];
  const missing = audit?.missing || [];
  const riskRows = risks.map((item) => item?.message || String(item || "")).filter(Boolean);
  const missingRows = missing.map((item) => String(item || "")).filter(Boolean);
  const detailRows = [
    ...riskRows.map((text) => `<div class="audit-detail-line warn">风险：${escapeHtml(text)}</div>`),
    ...missingRows.map((text) => `<div class="audit-detail-line">缺失：${escapeHtml(text)}</div>`),
  ].join("");
  auditCard.innerHTML = `
    <div class="audit-head"><strong>${escapeHtml(audit?.deliverable || "未识别")}</strong> <span class="status-chip ${audit?.level || "ok"}">${audit?.level || "ok"}</span></div>
    <div class="audit-line">建议任务：${escapeHtml(audit?.suggested_task || "无")} · 网页模型：${audit?.provider_recommended ? "建议" : "可选"}</div>
    <div class="audit-line">风险 ${risks.length} · 缺失 ${missing.length}</div>
    ${detailRows ? `<div class="audit-detail-list">${detailRows}</div>` : ""}
  `;
  auditCard.title = [...riskRows, ...missingRows.map((item) => `缺失：${item}`)].join("\n");
}

function renderHarnessSuggestions(data) {
  const suggestions = data?.suggestions || [];
  latestHarnessSuggestions = suggestions;
  const first = suggestions[0] || {};
  const evidence = (first.evidence || [])[0] || {};
  const invocationId = evidence.invocation_id || latestInvocationId();
  harnessCard.innerHTML = `
    <div><strong>${suggestions.length}</strong> 条候选建议</div>
    <div class="muted-line">${escapeHtml(first.reason || "近期没有明显门禁改造建议")}</div>
    <div class="muted-line">验收：先生成复盘包核对证据，再采纳为经验/技能或调整 SOP。</div>
    ${suggestions.length ? `
      <div class="mini-actions">
        ${invocationId ? `<button type="button" data-review-packet="${escapeHtml(invocationId)}">生成复盘</button>` : ""}
        <button type="button" data-status-tab="lessons">查看经验</button>
      </div>
    ` : ""}
  `;
  markStatusTabUpdated("harness", data);
}

function renderTrajectory(data) {
  const inv = data?.invocation || {};
  const timeline = data?.timeline || [];
  const last = timeline[timeline.length - 1] || {};
  trajectoryCard.innerHTML = `
    <div><strong>${escapeHtml(inv.id || latestInvocationId() || "无任务")}</strong></div>
    <div class="muted-line">节点/事件：${timeline.length} · 当前：${escapeHtml(inv.current_node || "unknown")}</div>
    <div class="muted-line">${escapeHtml(last.label || "暂无轨迹")}</div>
    <div class="muted-line">验收：轨迹用于确认节点是否完整流转，异常节点进入复盘包。</div>
    ${inv.id ? `
      <div class="mini-actions">
        <button type="button" data-review-packet="${escapeHtml(inv.id)}">生成复盘</button>
      </div>
    ` : ""}
  `;
  markStatusTabUpdated("trajectory", data);
}

function renderReviewPacket(data) {
  const packet = data?.packet || {};
  const inv = packet.invocation || {};
  const checklist = packet.acceptance_checklist || [];
  const path = data?.path || "";
  reviewPacketCard.innerHTML = `
    <div><strong>${escapeHtml(inv.id || latestInvocationId() || "无任务")}</strong></div>
    <div class="muted-line">验收项：${(packet.acceptance_checklist || []).length} · 门禁：${(packet.harness_issues || []).length}</div>
    <div class="muted-line">首项：${escapeHtml(checklist[0] || "暂无验收项")}</div>
    <div class="observe-path" title="${escapeHtml(path || "未生成文件")}">${escapeHtml(path || "未生成文件")}</div>
    <div class="mini-actions">
      ${path ? `<button type="button" data-open-file-path="${escapeHtml(path)}">打开文件</button>` : ""}
      <button type="button" data-status-tab="lessons">沉淀经验</button>
    </div>
  `;
  markStatusTabUpdated("review", data);
}

function renderRecallEval(data) {
  const summary = data?.summary || {};
  recallCard.innerHTML = `
    <div class="metric-grid">
      <div><strong>${summary.average_proxy_score || 0}</strong><span>复用代理分</span></div>
      <div><strong>${summary.with_merge || 0}</strong><span>融合痕迹</span></div>
    </div>
    <div class="muted-line">completed ${summary.completed || 0}/${summary.invocations || 0} · 非语义真值评分</div>
  `;
  markStatusTabUpdated("recall", data);
}

function renderSkillsRegistry(data) {
  const files = data?.files || [];
  const relevant = files.filter((item) => item.relevant).length;
  skillsCard.innerHTML = `
    <div><strong>${relevant}</strong> / ${files.length} 张技能卡匹配当前任务</div>
    <div class="muted-line">缺失推荐：${escapeHtml((data?.missing_recommended || []).join(", ") || "无")}</div>
  `;
  markStatusTabUpdated("skills", data);
}

function renderLessonSuggestions(data) {
  const suggestions = data?.suggestions || [];
  latestLessonSuggestions = suggestions;
  const first = suggestions[0] || {};
  const firstKey = lessonSuggestionKey(first);
  const firstLocked = firstKey && adoptingLessonKeys.has(firstKey);
  const firstAdopted = firstKey && adoptedLessonKeys.has(firstKey);
  const adoptLabel = firstAdopted ? "已采纳" : firstLocked ? "采纳中..." : "采纳首条";
  lessonsCard.innerHTML = `
    <div><strong>${suggestions.length}</strong> 条经验草案</div>
    <div class="muted-line">${escapeHtml(first.title || "暂无需要沉淀的失败模式")}</div>
    <div class="muted-line">晋级：人工采纳后写入 lessons，并同步到对应公共/项目技能库。</div>
    ${suggestions.length ? `
      <div class="mini-actions">
        <button type="button" data-adopt-lesson="0" ${firstLocked || firstAdopted ? "disabled" : ""}>${adoptLabel}</button>
        ${first.source_invocation_id ? `<button type="button" data-review-packet="${escapeHtml(first.source_invocation_id)}">查看复盘</button>` : ""}
      </div>
    ` : ""}
  `;
  markStatusTabUpdated("lessons", data);
}

function lessonSuggestionKey(suggestion) {
  if (!suggestion) return "";
  const source = suggestion.source_invocation_id || suggestion.id || "";
  const title = suggestion.title || "";
  const markdown = suggestion.draft_markdown || "";
  return `${source}|${title}|${markdown.length}`;
}

function renderWiki(data, projectData = null) {
  const summary = data?.summary || {};
  const projectSummary = projectData?.summary || {};
  const items = data?.items || [];
  const projectItems = projectData?.items || [];
  const first = items[0] || {};
  const projectFirst = projectItems[0] || {};
  wikiCard.innerHTML = `
    <div class="metric-grid">
      <div><strong>${summary.total || 0}</strong><span>LLM Wiki</span></div>
      <div><strong>${projectSummary.total || 0}</strong><span>项目 Wiki</span></div>
    </div>
    <div class="muted-line">稳定共识：${escapeHtml(first.title || "暂无")}</div>
    <div class="muted-line">项目过程：${escapeHtml(projectFirst.title || "暂无")}</div>
  `;
  markStatusTabUpdated("wiki", { data, projectData });
}

function renderProviders() {
  providerChecks.innerHTML = "";
  const prefs = loadProviderPrefs();
  for (const p of providers) {
    const checked = prefs[p.id] === true;
    const pill = document.createElement("div");
    pill.className = "provider-pill provider-control";
    pill.title = p.reason || "";
    pill.innerHTML = `
      <label class="provider-check">
        <input type="checkbox" data-provider="${p.id}" ${checked ? "checked" : ""}>
        <span>${p.name}${p.pinned_conversation ? " 📌" : ""}</span>
      </label>
      <span class="provider-actions">
        <button type="button" data-open-provider="${p.id}">打开</button>
        <button type="button" data-pin-provider="${p.id}">固定</button>
        <button type="button" data-reset-provider="${p.id}">重置</button>
      </span>
    `;
    providerChecks.appendChild(pill);
  }
  syncAiToggleFromProviders();
}

function renderFileTree(node, root = fileTreeEl, depth = 0) {
  if (!node || !root) return;
  if (depth === 0) root.innerHTML = "";
  for (const child of node.children || []) {
    const item = document.createElement("div");
    item.className = `file-item ${child.type === "directory" ? "dir" : ""}`;
    item.classList.toggle("wiki", isProjectWikiPath(child.path));
    item.style.paddingLeft = `${6 + depth * 12}px`;
    item.title = child.path || child.name;
    if (child.type === "directory") {
      const key = child.path || child.name;
      const isOpen = collapsedFileDirs.has(key);
      item.textContent = `${isOpen ? "▾" : "▸"} ${child.name}`;
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-expanded", String(isOpen));
      const toggle = () => {
        if (isOpen) collapsedFileDirs.delete(key);
        else collapsedFileDirs.add(key);
        renderFileTree(fileTreeData || node);
      };
      item.addEventListener("click", toggle);
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
      root.appendChild(item);
      if (isOpen) renderFileTree(child, root, depth + 1);
    } else {
      item.textContent = child.name;
      item.classList.toggle("disabled", !child.previewable);
      if (child.previewable) {
        item.tabIndex = 0;
        item.setAttribute("role", "button");
        item.addEventListener("click", () => openFile(child.path));
        item.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openFile(child.path);
          }
        });
      }
      root.appendChild(item);
    }
  }
}

async function loadStatus() {
  const statusUrl = currentProject
    ? `/api/writing/status?novel_id=${encodeURIComponent(currentProject)}`
    : "/api/writing/status";
  const res = await fetch(statusUrl);
  const data = await readJsonResponse(res);
  assertApiOk(res, data, "项目状态加载失败");
  currentProject = data.novel || currentProject;
  currentKind = data.project_kind || "generic";
  workflowSop = data.workflow_sop || null;
  collaborationState = data.collaboration || null;
  localStorage.setItem("writing.ui.project", currentProject);
  renderProjectProgress(data.project_progress || {});
  renderProjectInventory(data.project_inventory || {});
  projectSelect.innerHTML = "";
  for (const item of data.novels || []) {
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = `${item.id} · ${projectKindLabel(item.kind)}`;
    opt.selected = item.id === currentProject;
    projectSelect.appendChild(opt);
  }
  if (deleteProjectBtn) deleteProjectBtn.disabled = !currentProject;
  renderTasks();
  renderSop();
  renderCollaborationIdle();
}

function setProjectActionStatus(text, tone = "") {
  if (!projectActionStatus) return;
  projectActionStatus.textContent = text || "";
  projectActionStatus.dataset.tone = tone || "";
}

function normalizeProjectId(value) {
  return String(value || "").trim();
}

function validateProjectId(projectId) {
  return /^[A-Za-z0-9]{1,40}$/.test(projectId);
}

async function createProject() {
  const projectId = normalizeProjectId(projectIdInput?.value);
  const projectType = projectTypeSelect?.value || "novel";
  if (!validateProjectId(projectId)) {
    setProjectActionStatus("项目 ID 只能包含 1-40 位字母或数字。", "error");
    projectIdInput?.focus();
    return;
  }
  setBusy(true, "创建项目");
  setProjectActionStatus("正在创建项目...");
  const flow = createOperationFlow(["project_submit", "project_create", "project_reload", "workspace_status", "workspace_files", "workspace_cost", "workspace_mission", "workspace_observe", "workspace_history"]);
  try {
    flow.step("project_create");
    const res = await fetch("/api/writing/project", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, project_type: projectType }),
    });
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false) {
      throw new Error(data.detail || data.message || "创建项目失败");
    }
    currentProject = data.novel_id || data.project_id || projectId;
    localStorage.setItem("writing.ui.project", currentProject);
    projectCreateForm.hidden = true;
    projectIdInput.value = "";
    setProjectActionStatus(`已创建并切换到项目 ${currentProject}。`, "ok");
    flow.step("project_reload");
    await reloadProjectWorkspace({ flow });
    flow.done();
  } catch (error) {
    flow.fail();
    setProjectActionStatus(String(error), "error");
  } finally {
    setBusy(false);
  }
}

async function deleteCurrentProject() {
  if (!currentProject) {
    setProjectActionStatus("当前没有可删除的项目。", "error");
    return;
  }
  const confirmed = window.confirm(`确认删除项目 ${currentProject}？项目会移动到回收目录。`);
  if (!confirmed) return;
  setBusy(true, "删除项目");
  setProjectActionStatus(`正在删除项目 ${currentProject}...`);
  const deletingProject = currentProject;
  const flow = createOperationFlow(["project_submit", "project_delete", "project_reload", "workspace_status", "workspace_files", "workspace_cost", "workspace_mission", "workspace_observe", "workspace_history"]);
  try {
    flow.step("project_delete");
    const res = await fetch(`/api/writing/project/${encodeURIComponent(deletingProject)}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false) {
      throw new Error(data.detail || data.message || "删除项目失败");
    }
    const nextProject = data.next_novel || data.novel || data.novels?.[0]?.id || "";
    currentProject = nextProject || deletingProject;
    localStorage.setItem("writing.ui.project", currentProject);
    setProjectActionStatus(`已删除项目 ${deletingProject}。`, "ok");
    flow.step("project_reload");
    await reloadProjectWorkspace({ flow });
    flow.done();
  } catch (error) {
    flow.fail();
    setProjectActionStatus(String(error), "error");
  } finally {
    setBusy(false);
  }
}

async function loadProviders() {
  const res = await fetch("/api/ai-providers/status");
  const data = await readJsonResponse(res);
  assertApiOk(res, data, "网页模型状态加载失败");
  providers = data.providers || [];
  renderProviders();
}

async function loadCostBoard() {
  try {
    const res = await fetch(`/api/writing/cost-board?novel_id=${encodeURIComponent(currentProject)}&limit=20`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "成本看板加载失败");
    renderCostBoard(data);
  } catch {
    costCard.textContent = "成本看板加载失败";
  }
}

async function loadMission() {
  try {
    const res = await fetch(`/api/writing/mission?novel_id=${encodeURIComponent(currentProject)}&limit=10`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "任务概览加载失败");
    renderMission(data);
  } catch {
    missionCard.textContent = "任务概览加载失败";
  }
}

async function loadHarnessSuggestions() {
  try {
    const res = await fetch(`/api/writing/harness-suggestions?novel_id=${encodeURIComponent(currentProject)}&limit=20`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "门禁建议加载失败");
    renderHarnessSuggestions(data);
  } catch {
    harnessCard.textContent = "门禁建议加载失败";
  }
}

async function loadTrajectory(invocationId = latestInvocationId()) {
  if (!invocationId) {
    trajectoryCard.textContent = "等待任务";
    markStatusTabUpdated("trajectory", "empty");
    return { ok: false, empty: true };
  }
  try {
    const res = await fetch(`/api/writing/trajectory/${encodeURIComponent(invocationId)}?novel_id=${encodeURIComponent(currentProject)}`);
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || "轨迹加载失败");
    renderTrajectory(data);
    return { ok: true, data };
  } catch (error) {
    trajectoryCard.textContent = "轨迹加载失败";
    markStatusTabUpdated("trajectory", { error: String(error) });
    throw error;
  }
}

async function loadReviewPacket(invocationId = latestInvocationId()) {
  if (!invocationId) {
    reviewPacketCard.textContent = "等待任务";
    markStatusTabUpdated("review", "empty");
    return { ok: false, empty: true };
  }
  try {
    const res = await fetch(`/api/writing/review-packet/${encodeURIComponent(invocationId)}?novel_id=${encodeURIComponent(currentProject)}&write_file=true`);
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || "复盘包生成失败");
    renderReviewPacket(data);
    return { ok: true, data };
  } catch (error) {
    reviewPacketCard.textContent = "复盘包生成失败";
    markStatusTabUpdated("review", { error: String(error) });
    throw error;
  }
}

async function loadRecallEval() {
  try {
    const res = await fetch(`/api/writing/recall-eval?novel_id=${encodeURIComponent(currentProject)}&limit=20`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "复用评估加载失败");
    renderRecallEval(data);
  } catch {
    recallCard.textContent = "复用评估加载失败";
  }
}

async function loadSkillsRegistry() {
  try {
    const res = await fetch(`/api/writing/skills?novel_id=${encodeURIComponent(currentProject)}&task=${encodeURIComponent(flowTask())}`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "技能卡加载失败");
    renderSkillsRegistry(data);
  } catch {
    skillsCard.textContent = "技能卡加载失败";
  }
}

async function loadLessonSuggestions() {
  try {
    const res = await fetch(`/api/writing/lessons/suggestions?novel_id=${encodeURIComponent(currentProject)}&limit=20`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "经验草案加载失败");
    renderLessonSuggestions(data);
  } catch {
    lessonsCard.textContent = "经验草案加载失败";
  }
}

async function adoptLessonSuggestion(index) {
  const suggestion = latestLessonSuggestions[Number(index)] || null;
  if (!suggestion) {
    addMessage("system", "没有找到可采纳的经验草案。", "经验");
    return;
  }
  const suggestionKey = lessonSuggestionKey(suggestion);
  if (adoptingLessonKeys.has(suggestionKey) || adoptedLessonKeys.has(suggestionKey)) {
    addMessage("system", "该经验已经在采纳流程中或已采纳，无需重复点击。", "经验晋级");
    return;
  }
  adoptingLessonKeys.add(suggestionKey);
  renderLessonSuggestions({ suggestions: latestLessonSuggestions });
  setBusy(true, "采纳经验");
  const flow = createOperationFlow(["submit", "knowledge_settle", "policy_update", "complete"]);
  try {
    flow.step("knowledge_settle");
    const res = await fetch("/api/writing/lessons/adopt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: suggestion.title || "writing lesson",
        draft_markdown: suggestion.draft_markdown || "",
        source_invocation_id: suggestion.source_invocation_id || "",
        novel_id: currentProject,
        task: suggestion.task || flowTask(),
        apply_to_skill: true,
      }),
    });
    const data = await readJsonResponse(res);
    if (!res.ok || data.ok === false) {
      throw new Error(data.detail || data.error || "采纳经验失败");
    }
    adoptedLessonKeys.add(suggestionKey);
    flow.step("policy_update");
    await Promise.all([loadLessonSuggestions(), loadSkillsRegistry(), loadWiki()]);
    flow.done();
    const skillPath = data.skill?.path || data.skill?.file || "";
    const statusText = data.already_adopted ? "该经验此前已采纳" : "经验已采纳";
    addMessage(
      "system",
      `${statusText}：${data.path || data.id || ""}${skillPath ? `\n已同步技能：${skillPath}` : ""}`,
      "经验晋级",
    );
  } catch (error) {
    flow.fail();
    addMessage("system", `采纳经验失败：${error}`, "经验");
  } finally {
    adoptingLessonKeys.delete(suggestionKey);
    renderLessonSuggestions({ suggestions: latestLessonSuggestions });
    setBusy(false);
  }
}

async function loadWiki() {
  try {
    const [wikiRes, projectRes] = await Promise.all([
      fetch(`/api/writing/wiki?novel_id=${encodeURIComponent(currentProject)}&limit=50`),
      fetch(`/api/writing/project-wiki?novel_id=${encodeURIComponent(currentProject)}&limit=50`),
    ]);
    const wikiData = await readJsonResponse(wikiRes);
    const projectData = await readJsonResponse(projectRes);
    assertApiOk(wikiRes, wikiData, "LLM Wiki 加载失败");
    assertApiOk(projectRes, projectData, "项目 Wiki 加载失败");
    renderWiki(wikiData, projectData);
  } catch {
    wikiCard.textContent = "Wiki 加载失败";
  }
}

async function loadObservability() {
  await Promise.allSettled([
    loadHarnessSuggestions(),
    loadTrajectory(),
    loadRecallEval(),
    loadSkillsRegistry(),
    loadLessonSuggestions(),
    loadWiki(),
  ]);
}

async function runNeedAuditPreview(message) {
  try {
    const res = await fetch("/api/writing/need-audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        task: flowTask(),
        chapter: currentChapter(),
        novel_id: currentProject,
        use_provider_source: aiToggle.checked,
      }),
    });
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "需求审计失败");
    renderNeedAudit(data.audit || {});
  } catch {
    auditCard.textContent = "需求审计失败";
  }
}

async function loadFiles() {
  const res = await fetch(`/api/writing/files?novel_id=${encodeURIComponent(currentProject)}`);
  const data = await readJsonResponse(res);
  assertApiOk(res, data, "文件树加载失败");
  fileTreeData = data.root;
  renderFileTree(fileTreeData);
}

function parseSSE(block) {
  let event = "message";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch { return null; }
}

async function runNodeStream(url, payload, stages, title, options = {}) {
  setBusy(true, title);
  payload = refreshModelPayload(payload);
  const doneNodes = new Set();
  const timer = createStageTimer(stages, () => renderStages(timer.current, doneNodes, stages, timer));
  renderStages(stages[0], doneNodes, stages, timer);
  const msg = addMessage("assistant", "", title);
  const lines = [];
  const appendLine = (line) => {
    if (!line) return;
    lines.push(line);
    msg.body.textContent = lines.join("\n");
    scrollMessagesToBottom();
  };
  let doneData = null;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) throw new Error("stream unavailable");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split("\n\n");
      buf = blocks.pop() || "";
      for (const block of blocks) {
        const ev = parseSSE(block);
        if (!ev) continue;
        if (ev.event === "node" || ev.event === "progress") {
          const node = ev.data.node;
          rememberFlowTask(ev.data.request_analysis);
          if (node) {
            markStageDone(timer, node);
            doneNodes.add(node);
            const next = nextStageAfter(node, stages);
            markStageStarted(timer, next);
            renderStages(next, doneNodes, stages, timer);
          }
          appendLine(ev.data.label || stageLabel(node));
        } else if (ev.event === "done") {
          doneData = ev.data;
        } else if (ev.event === "error") {
          appendLine(`失败：${ev.data.message || "未知错误"}`);
          offerModelRetry(
            ev.data.message || "",
            options.retry || (() => runNodeStream(url, refreshModelPayload(payload), stages, title, options)),
            {
              roles: options.retryRoles || [],
              label: options.retryLabel || "已切换模型，继续执行",
            },
          );
        }
      }
    }
    const data = doneData?.data || {};
    if (doneData?.ok) {
      if (data.index) appendLine(`已生成索引：${data.index}`);
      if (data.manifest) appendLine(`已生成清单：${data.manifest}`);
      if (data.queue) appendLine(`已生成生图队列：${data.queue}`);
      await loadFiles();
    }
    finishStageTimer(timer);
    renderStages(timer.current, doneNodes, stages, timer);
    return { ok: Boolean(doneData?.ok), data, messageEl: msg.item };
  } catch (error) {
    appendLine(`失败：${error}`);
    offerModelRetry(
      error,
      options.retry || (() => runNodeStream(url, refreshModelPayload(payload), stages, title, options)),
      {
        roles: options.retryRoles || [],
        label: options.retryLabel || "已切换模型，继续执行",
      },
    );
    finishStageTimer(timer);
    renderStages(timer.current, doneNodes, stages, timer);
    return { ok: false, error, messageEl: msg.item };
  } finally {
    finishStageTimer(timer);
    setBusy(false);
  }
}

async function runShortFilmVisualPrompts() {
  if (currentKind !== "short_film") {
    addMessage("system", "当前项目不是电影脚本类型。", "生词");
    return;
  }
  const source = activeShortFilmSource();
  const payload = {
    task: source.task || "screenplay",
    content: source.content || "",
    source_path: source.source_path || "",
    overwrite_script: true,
    novel_id: currentProject,
    model_preferences: modelPreferences(),
  };
  const result = await runNodeStream(
    "/api/writing/visual-prompts-stream",
    payload,
    VISUAL_PROMPT_STAGES,
    "短片生词",
  );
  if (result.ok) {
    addMessage("system", "提示词已生成，可继续执行生图。", "短片");
  }
}

async function runStoryboardImages() {
  if (currentKind !== "short_film") {
    addMessage("system", "当前项目不是电影脚本类型。", "生图");
    return;
  }
  if (!requireModels(["image"], {
    label: "已选择模型，继续生图",
    retry: () => runStoryboardImages(),
  })) return;
  const beat = Number.parseInt(storyboardBeatInput?.value || "", 10);
  const payload = {
    novel_id: currentProject,
    image_model_key: modelPreferences().image,
    model_preferences: modelPreferences(),
  };
  if (Number.isInteger(beat) && beat > 0) payload.beat = beat;
  const title = payload.beat ? `短片生图 · 第 ${payload.beat} 个分镜` : "短片生图";
  await runNodeStream("/api/writing/storyboard-images-stream", payload, IMAGE_GENERATION_STAGES, title, {
    retryRoles: ["image"],
    retryLabel: "已切换模型，继续生图",
    retry: () => runNodeStream(
      "/api/writing/storyboard-images-stream",
      refreshModelPayload(payload),
      IMAGE_GENERATION_STAGES,
      title,
      { retryRoles: ["image"], retryLabel: "已切换模型，继续生图" },
    ),
  });
}

function buildProviderGrid(order) {
  const wrap = document.createElement("article");
  wrap.className = "message assistant";
  const title = document.createElement("div");
  title.className = "message-title";
  title.textContent = "网页模型协同";
  wrap.appendChild(title);
  const grid = document.createElement("div");
  grid.className = "provider-card-grid";
  wrap.appendChild(grid);
  wrap._grid = grid;
  wrap._cards = {};
  for (const pid of order) {
    const card = document.createElement("section");
    card.className = "provider-card";
    card.dataset.provider = pid;
    card.innerHTML = `<header><strong>${providerName(pid)}</strong><span class="status-chip running">运行中</span></header><pre>等待结果…</pre>`;
    grid.appendChild(card);
    wrap._cards[pid] = card;
  }
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrap;
}

function providerName(id) {
  return (providers.find((p) => p.id === id) || { name: id }).name;
}

function updateProviderGrid(gridMsg, data) {
  const card = gridMsg?._cards?.[data.provider];
  if (!card) return;
  const status = card.querySelector(".status-chip");
  const pre = card.querySelector("pre");
  updateProviderStatusChip(status, data.status || "success");
  if (pre) pre.textContent = data.result || "无正文";
  card._providerResult = {
    provider: data.provider,
    name: data.name || providerName(data.provider),
    status: data.status || "success",
    result: data.result || "",
    files: [],
  };
  scrollMessagesToBottom();
}

function confirmedAnswers(gridMsg) {
  return Object.values(gridMsg?._cards || {})
    .map((card) => {
      const input = card.querySelector(".provider-manual-input");
      if (input && card._providerResult) {
        card._providerResult.result = input.value.trim();
        card._providerResult.status = card._providerResult.result ? "success" : "partial";
      }
      return card._providerResult;
    })
    .filter((item) => item && item.result && item.status !== "failed");
}

async function persistProviderMaterials(gridMsg, ctx = {}) {
  const answers = confirmedAnswers(gridMsg);
  if (!answers.length || gridMsg?._providerPersisted) return;
  gridMsg._providerPersisted = true;
  await persistMessage({
    role: "assistant",
    kind: "provider",
    meta: "网页模型协同",
    track: ctx.track || "create",
    data: {
      ok: true,
      awaiting_provider_confirm: true,
      message: "网页模型协同结果",
      results: answers,
      context: {
        chapter: ctx.chapter || null,
        task: ctx.task || flowTask(),
        track: ctx.track || "create",
        novel_id: ctx.novel_id || currentProject,
        project_kind: ctx.project_kind || currentKind,
        checkpoint_id: ctx.checkpoint_id || "",
        invocation_id: ctx.invocation_id || "",
        request_analysis: ctx.request_analysis || {},
        archive_content: ctx.archive_content || "",
        model_preferences: ctx.model_preferences || modelPreferences(),
      },
    },
  });
}

function attachProviderGate(gridMsg, ctx, flow = {}) {
  if (!gridMsg || gridMsg._gate) return;
  const row = document.createElement("div");
  row.className = "confirm-row";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "button primary";
  btn.textContent = "确认材料并继续融合";
  const note = document.createElement("span");
  note.className = "muted-line";
  note.textContent = "将所有成功 provider 结果作为确认材料。";
  row.append(btn, note);
  const progress = createAcceptanceProgress(row, note);
  gridMsg.appendChild(row);
  gridMsg._gate = row;
  async function confirmProviderMaterials() {
    const answers = confirmedAnswers(gridMsg);
    if (!answers.length) {
      note.textContent = "没有可确认的 provider 回答。";
      return;
    }
    if (!requireModels(["writing", "review"], {
      label: "已选择模型，继续融合",
      retry: () => confirmProviderMaterials(),
    })) return;
    btn.disabled = true;
    progress.mount();
    const activeFlow = flow?.timer ? flow : createOperationFlow(workflowStages("provider_confirm"));
    applyFlowProgress(activeFlow, { stage: "provider_confirm_gate", status: "running" });
    schedulePendingWorkflowPersist(workflowSnapshotFromFlow(activeFlow, {
      invocation_id: ctx.invocation_id || "",
      task: ctx.task,
      chapter: ctx.chapter,
      track: ctx.track || "create",
      status: "running",
      source: "provider_confirm",
    }), true);
    let data = {};
    let fusedTextStream = "";
    let fusedMsg = null;
    let fusedWriter = null;
    try {
      note.textContent = "确认材料...";
      const res = await fetch("/api/writing/provider-confirm-stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...ctx, model_preferences: modelPreferences(), answers }),
      });
      if (!res.ok || !res.body) {
        const errorData = await readJsonResponse(res);
        throw new Error(errorData.detail || errorData.error || "融合状态流不可用");
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const blocks = buf.split("\n\n");
        buf = blocks.pop() || "";
        for (const block of blocks) {
          const ev = parseSSE(block);
          if (!ev) continue;
          if (ev.event === "progress") {
            const stage = ev.data?.stage || "";
            const status = ev.data?.status || "";
            const shouldDelayDone = status === "done" && ["provider_merge", "generate", "draft_finalize"].includes(stage);
            if (!shouldDelayDone) {
              applyFlowProgress(activeFlow, ev.data || {});
            } else if (activeFlow?.timer?.current !== stage && !activeFlow.doneNodes?.has(stage)) {
              applyFlowProgress(activeFlow, { ...(ev.data || {}), status: "running" });
            }
            schedulePendingWorkflowPersist(workflowSnapshotFromFlow(activeFlow, {
              invocation_id: ctx.invocation_id || "",
              task: ctx.task,
              chapter: ctx.chapter,
              track: ctx.track || "create",
              status: ev.data?.status === "done" && ev.data?.stage === "draft_finalize" ? "awaiting_confirm" : "running",
              source: "provider_confirm",
            }));
            const label = ev.data?.label || stageLabel(ev.data?.stage || "");
            const details = ev.data?.details || {};
            if (ev.data?.stage === "provider_digest" && details.current && details.total) {
              note.textContent = `${label} ${details.current}/${details.total}...`;
            } else if (label) {
              note.textContent = ev.data?.status === "done" ? `${label}完成` : `${label}...`;
            }
          } else if (ev.event === "token") {
            const chunk = ev.data?.text || "";
            if (!chunk) continue;
            fusedTextStream += chunk;
            if (!fusedMsg) {
              fusedMsg = addMessage("assistant", "", "融合稿", { persist: false });
              fusedWriter = createTypewriter(fusedMsg.body);
            }
            fusedWriter?.append(chunk);
          } else if (ev.event === "done") {
            data = ev.data || {};
          } else if (ev.event === "error") {
            throw new Error(ev.data?.message || "融合失败");
          }
        }
      }
      if (fusedWriter) await fusedWriter.drain();
      if (!data || data.ok === false) {
        throw new Error(data.detail || data.error || "融合失败");
      }
    } catch (error) {
      note.textContent = `融合失败：${error}`;
      progress.fail(note.textContent);
      activeFlow.fail?.();
      btn.disabled = false;
      offerModelRetry(error, () => confirmProviderMaterials(), {
        roles: ["writing", "review"],
        label: "已切换模型，继续融合",
      });
      return;
    }
    note.textContent = "已融合";
    progress.complete("已融合");
    const finalText = cleanFinalDraftText(data.answer || data.data?.draft || "");
    let fusedText = finalText || fusedTextStream;
    if (!fusedText) {
      addMessage("system", "融合完成，但没有收到可供确认的正文内容。", "用户确认");
      return;
    }
    if (!fusedMsg) {
      fusedMsg = addMessage("assistant", "", "融合稿", { persist: false });
      fusedWriter = createTypewriter(fusedMsg.body);
      fusedWriter.append(fusedText);
      await fusedWriter.drain();
    } else if (!fusedTextStream && finalText) {
      fusedWriter?.setText(finalText);
      await fusedWriter?.drain();
    }
    if (finalText && finalText !== fusedTextStream && fusedMsg?.body) {
      fusedWriter?.flush?.();
      fusedMsg.body.textContent = finalText;
      fusedText = finalText;
      scrollMessagesToBottom();
    }
    finishProviderTextStages(activeFlow);
    schedulePendingWorkflowPersist(workflowSnapshotFromFlow(activeFlow, {
      invocation_id: data.data?.invocation_id || ctx.invocation_id || "",
      task: data.data?.task || ctx.task,
      chapter: data.data?.chapter || ctx.chapter,
      track: ctx.track || "create",
      status: "awaiting_confirm",
      source: "provider_confirm",
    }), true);
    const draftCtx = {
      original: fusedText,
      chapter: data.data?.chapter || ctx.chapter,
      task: data.data?.task || ctx.task,
      track: ctx.track,
      novel_id: ctx.novel_id,
      project_kind: data.data?.project_kind || ctx.project_kind || currentKind,
      invocation_id: data.data?.invocation_id || ctx.invocation_id || "",
      request_analysis: data.data?.request_analysis || ctx.request_analysis || {},
      archive_content: data.data?.archive_content || ctx.archive_content || "",
      provider_answers: data.data?.provider_answers || answers,
      artifacts: data.data?.artifacts || {},
      merge_info: data.data?.merge_info || {},
      model_preferences: modelPreferences(),
    };
    rememberFlowTask(draftCtx, draftCtx.request_analysis);
    await persistDraftResult(fusedText, "融合稿", draftCtx);
    attachAcceptanceControls(fusedMsg, {
      ...draftCtx,
      original: fusedText,
    }, activeFlow);
    invocationCard.textContent = data.data?.invocation_log || ctx.invocation_id || "已完成";
    startUserConfirmStage(activeFlow);
    scrollMessagesToBottom();
  }
  btn.addEventListener("click", confirmProviderMaterials);
}

async function runProviderPlainChat(message) {
  const login = loginConfirmed();
  const selected = Object.keys(login).filter((key) => login[key]);
  if (!selected.length) {
    addMessage("system", "已开启网页模型，但未选择 provider。请先勾选千问、DeepSeek 或豆包。", "聊天");
    return;
  }
  setBusy(true, "网页模型聊天");
  const flow = createOperationFlow(["provider_launch", "provider_wait", "provider_persist"]);
  const gridMsg = buildProviderGrid(selected);
  try {
    flow.step("provider_launch");
    const startRes = await fetch("/api/ai-providers/run-async", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        mode: "chat",
        chapter: null,
        attachments: [],
        login_confirmed: login,
        format_for_writing: false,
        novel_id: currentProject,
      }),
    });
    const started = await readJsonResponse(startRes);
    if (!startRes.ok || !started.ok || !started.job_id) {
      throw new Error(started.detail || started.message || "网页模型任务启动失败");
    }
    const seen = new Set();
    flow.step("provider_wait");
    while (true) {
      const res = await fetch(`/api/ai-providers/job/${encodeURIComponent(started.job_id)}`);
      const snapshot = await readJsonResponse(res);
      assertApiOk(res, snapshot, "网页模型任务状态读取失败");
      for (const item of snapshot.providers || []) {
        const status = item.status;
        if (["success", "partial", "failed"].includes(status) && !seen.has(item.provider)) {
          seen.add(item.provider);
          updateProviderGrid(gridMsg, {
            provider: item.provider,
            name: item.name,
            status,
            result: item.result || "",
          });
        }
      }
      if (snapshot.done) break;
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
    flow.step("provider_persist");
    await persistMessage({
      role: "assistant",
      kind: "provider",
      data: {
        ok: true,
        message: "网页模型聊天结果",
        results: confirmedAnswers(gridMsg),
      },
      track: "normal",
    });
    flow.done();
  } catch (error) {
    flow.fail();
    addMessage("system", `网页模型聊天失败：${error}`, "聊天");
  } finally {
    setBusy(false);
  }
}

async function runPlainChatMessage(message, options = {}) {
  const echoUser = options.echoUser !== false;
  if (echoUser) {
    addMessage("user", message, "聊天", { persist: false });
    persistMessage({ role: "user", kind: "text", text: message, meta: "聊天", track: "normal" });
  }
  if (options.clearInput !== false && messageInput.value.trim() === message) {
    messageInput.value = "";
  }
  if (aiToggle.checked) {
    await runProviderPlainChat(message);
    return;
  }
  setBusy(true, "聊天中");
  const flow = createOperationFlow(["chat_submit", "chat_model", "chat_persist"]);
  try {
    flow.step("chat_model");
    const res = await fetch("/api/writing/plain-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        novel_id: currentProject,
        model_key: modelPreferences().chat,
        model_preferences: modelPreferences(),
      }),
    });
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "LLM 聊天失败");
    addMessage("assistant", data.answer || "", "聊天", { persist: false });
    flow.step("chat_persist");
    await persistMessage({ role: "assistant", kind: "text", text: data.answer || "", meta: "聊天", track: "normal" });
    flow.done();
  } catch (error) {
    flow.fail();
    addMessage("system", `聊天失败：${error}`, "聊天");
    offerModelRetry(error, () => runPlainChatMessage(message, { echoUser: false, clearInput: false }), {
      roles: ["chat"],
      label: "已切换模型，继续聊天",
    });
  } finally {
    setBusy(false);
  }
}

async function runPlainChat() {
  const message = messageInput.value.trim();
  if (!message) return;
  if (!aiToggle.checked && !requireModels(["chat"], {
    label: "已选择模型，继续聊天",
    retry: () => runPlainChatMessage(message, { echoUser: true, clearInput: true }),
  })) return;
  await runPlainChatMessage(message, { echoUser: true, clearInput: true });
}

function finishUserConfirmStage(flow) {
  if (!flow?.timer) return;
  markStageDone(flow.timer, "user_confirm");
  flow.doneNodes?.add("user_confirm");
  finishStageTimer(flow.timer);
  renderStages("user_confirm", flow.doneNodes || new Set(), flow.stages || workflowStages("draft"), flow.timer);
}

function startUserConfirmStage(flow) {
  if (!flow?.timer) return;
  markStageDone(flow.timer, "draft_finalize");
  flow.doneNodes?.add("draft_finalize");
  markStageStarted(flow.timer, "user_confirm");
  renderStages("user_confirm", flow.doneNodes || new Set(), flow.stages || workflowStages("draft"), flow.timer);
}

function finishProviderTextStages(flow) {
  if (!flow?.timer) return;
  for (const stage of ["provider_merge", "generate"]) {
    if ((flow.stages || []).includes(stage)) {
      markStageDone(flow.timer, stage);
      flow.doneNodes?.add(stage);
    }
  }
  if ((flow.stages || []).includes("draft_finalize")) {
    markStageDone(flow.timer, "draft_finalize");
    flow.doneNodes?.add("draft_finalize");
  }
}

function renderImpactPlan(msgEl, data) {
  if (!msgEl || (!data?.impact_plan && !(data?.pending_updates || []).length && !data?.writeback_hint && !data?.wiki && !data?.project_wiki)) return;
  const plan = data.impact_plan || {};
  const wrap = document.createElement("div");
  wrap.className = "impact-plan";
  const title = document.createElement("strong");
  title.textContent = "文件影响与知识沉淀";
  wrap.appendChild(title);

  const knowledge = [];
  if (data.wiki) knowledge.push(`LLM Wiki：${data.wiki.ok ? "已更新" : `失败 ${data.wiki.error || ""}`}`);
  if (data.project_wiki) knowledge.push(`项目 Wiki：${data.project_wiki.ok ? "已更新" : `失败 ${data.project_wiki.error || ""}`}`);
  if (data.normalized_chapter) knowledge.push(`识别章节：第${data.normalized_chapter}章`);
  if (data.recovered_intent?.memory_source) knowledge.push(`恢复来源：${data.recovered_intent.memory_source}`);
  if (knowledge.length) {
    const note = document.createElement("div");
    note.className = "muted-line";
    note.textContent = knowledge.join("；");
    wrap.appendChild(note);
  }

  const changes = Array.isArray(plan.changes) ? plan.changes : [];
  const labelsFor = (items) => [...new Set(items.map((change) => {
    const target = change.target || "";
    return change.label || structureRoleLabel(target, target);
  }).filter(Boolean))];
  const primary = changes.filter((change) => change.role === "primary");
  const related = changes.filter((change) => change.role !== "primary" && change.target !== "chapter_body");
  const archiveTargets = changes.filter((change) => change.target === "chapter_body");
  const autoSaved = changes.filter((change) => change.auto_apply);
  const manual = changes.filter((change) => !change.auto_apply && change.target !== "chapter_body");
  const reason = changes.map((change) => String(change.reason || "").trim()).find(Boolean) || plan.summary || "";
  const rows = [
    ["本轮目标", reason || (primary.length ? labelsFor(primary).join("、") : "已确认采纳本轮内容。")],
    ["主目标文件", labelsFor(primary).join("、") || "未识别"],
    ["关联文件", labelsFor(related).join("、") || "无"],
  ];
  if (archiveTargets.length) rows.push(["需归档", labelsFor(archiveTargets).join("、")]);
  if (manual.length) rows.push(["待确认更新", labelsFor(manual).join("、")]);
  if (autoSaved.length) rows.push(["已自动保存", labelsFor(autoSaved).join("、")]);
  const summaryList = document.createElement("div");
  summaryList.className = "impact-summary-list";
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "impact-summary-row";
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    const valueEl = document.createElement("strong");
    valueEl.textContent = value;
    row.append(labelEl, valueEl);
    summaryList.appendChild(row);
  }
  wrap.appendChild(summaryList);

  const relatedFiles = data.recovered_intent?.related_files || data.request_analysis?.related_files || [];
  if (relatedFiles.length) {
    const relatedTitle = document.createElement("div");
    relatedTitle.className = "muted-line";
    relatedTitle.textContent = "关联文件定位：";
    wrap.appendChild(relatedTitle);
    const relatedList = document.createElement("ul");
    for (const ref of relatedFiles.slice(0, 8)) {
      const item = document.createElement("li");
      const lines = ref.start_line ? `:${ref.start_line}${ref.end_line && ref.end_line !== ref.start_line ? `-${ref.end_line}` : ""}` : "";
      item.textContent = `${ref.target || ref.role || "文件"}｜${ref.path || ""}${lines}`;
      relatedList.appendChild(item);
    }
    wrap.appendChild(relatedList);
  }

  const pending = data.pending_updates || [];
  if (pending.length) {
    const note = document.createElement("div");
    note.className = "muted-line";
    note.textContent = `已生成 ${pending.length} 条关联材料待确认更新，可在观察/诊断的待处理建议中继续处理。`;
    wrap.appendChild(note);
  }
  if (data.writeback_hint?.message) {
    const hint = document.createElement("div");
    hint.className = "muted-line writeback-hint";
    hint.textContent = data.writeback_hint.message;
    wrap.appendChild(hint);
  }
  msgEl.appendChild(wrap);
  scrollMessagesToBottom();
}

function createAcceptanceProgress(container, statusEl) {
  const wrap = document.createElement("div");
  wrap.className = "acceptance-progress";
  const rows = new Map();
  let currentStage = "";
  let totalStartedAt = performance.now();
  let interval = null;

  const ensureTimer = () => {
    if (!interval) {
      interval = window.setInterval(render, 500);
    }
  };

  const stopTimer = () => {
    if (interval) window.clearInterval(interval);
    interval = null;
  };

  const ensureRow = (stage, label) => {
    if (rows.has(stage)) return rows.get(stage);
    const row = document.createElement("div");
    row.className = "acceptance-progress-row";
    const name = document.createElement("span");
    name.className = "acceptance-progress-name";
    name.textContent = label || stage;
    const time = document.createElement("span");
    time.className = "acceptance-progress-time";
    row.append(name, time);
    wrap.appendChild(row);
    const item = {
      stage,
      label: label || stage,
      status: "running",
      startedAt: performance.now(),
      endedAt: null,
      row,
      name,
      time,
    };
    rows.set(stage, item);
    return item;
  };

  function render() {
    const now = performance.now();
    for (const item of rows.values()) {
      const elapsed = item.endedAt ? item.endedAt - item.startedAt : now - item.startedAt;
      item.row.className = `acceptance-progress-row ${item.status || ""}`;
      item.name.textContent = `${item.status === "done" ? "✓ " : item.status === "error" ? "× " : ""}${item.label}`;
      item.time.textContent = formatElapsed(elapsed);
    }
    scrollMessagesToBottom();
  }

  return {
    mount() {
      // Progress details are rendered in the unified top stage bar. This object
      // only keeps the small inline status text for button context.
      totalStartedAt = performance.now();
      ensureTimer();
    },
    update(event = {}) {
      const stage = event.stage || "running";
      const item = ensureRow(stage, event.label || stage);
      if (currentStage && currentStage !== stage) {
        const prev = rows.get(currentStage);
        if (prev && prev.status === "running") {
          prev.status = "done";
          prev.endedAt = performance.now();
        }
      }
      item.label = event.label || item.label;
      item.status = event.status || "running";
      if (item.status !== "running" && !item.endedAt) item.endedAt = performance.now();
      currentStage = item.status === "running" ? stage : "";
      if (statusEl) statusEl.textContent = `${item.label}...`;
    },
    complete(label = "已完成归档") {
      if (currentStage) {
        const current = rows.get(currentStage);
        if (current && current.status === "running") {
          current.status = "done";
          current.endedAt = performance.now();
        }
      }
      const total = ensureRow("total", `${label} · 总耗时`);
      total.status = "done";
      total.startedAt = totalStartedAt;
      total.endedAt = performance.now();
      if (statusEl) statusEl.textContent = label;
      stopTimer();
    },
    fail(message) {
      if (currentStage) {
        const current = rows.get(currentStage);
        if (current) {
          current.status = "error";
          current.endedAt = performance.now();
        }
      }
      const failed = ensureRow("failed", "执行失败");
      failed.status = "error";
      failed.endedAt = performance.now();
      if (statusEl) statusEl.textContent = message || "采纳失败";
      stopTimer();
    },
  };
}

async function runInterventionStream(payload, progress, operationFlow = null, workflowMeta = {}) {
  const res = await fetch("/api/writing/intervene-stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) {
    const data = await readJsonResponse(res);
    throw new Error(data.detail || data.error || "采纳提交失败");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let doneData = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const blocks = buf.split("\n\n");
    buf = blocks.pop() || "";
    for (const block of blocks) {
      const ev = parseSSE(block);
      if (!ev) continue;
      if (ev.event === "progress") {
        progress?.update(ev.data);
        if (operationFlow) {
          applyFlowProgress(operationFlow, ev.data || { stage: "submit" });
          schedulePendingWorkflowPersist(workflowSnapshotFromFlow(operationFlow, {
            invocation_id: workflowMeta.invocation_id || payload.invocation_id || "",
            task: workflowMeta.task || payload.task,
            chapter: workflowMeta.chapter || payload.chapter,
            track: workflowMeta.track || payload.track || "create",
            status: "running",
            source: "intervene",
          }));
        }
      } else if (ev.event === "done") {
        doneData = ev.data;
      } else if (ev.event === "error") {
        throw new Error(ev.data?.message || "采纳提交失败");
      }
    }
  }
  if (!doneData) throw new Error("采纳提交未返回结果");
  return doneData;
}

function attachAcceptanceControls(messageRef, ctx, flow = null) {
  const msgEl = messageRef?.item || messageRef;
  if (!msgEl || msgEl._acceptanceControls) return;
  clearAcceptanceControls(msgEl);
  msgEl._acceptanceControls = true;
  ctx.original = cleanFinalDraftText(ctx.original || "");
  ctx.archive_content = cleanFinalDraftText(ctx.archive_content || "");
  let acceptedText = ctx.original || "";

  const row = document.createElement("div");
  row.className = "confirm-row acceptance-row";
  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "button primary";
  confirmBtn.textContent = "确认采纳";
  const rejectBtn = document.createElement("button");
  rejectBtn.type = "button";
  rejectBtn.className = "button";
  rejectBtn.textContent = "拒绝";
  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "button";
  editBtn.textContent = "改写后采纳";
  const status = document.createElement("span");
  status.className = "muted-line";
  row.append(confirmBtn, rejectBtn, editBtn, status);
  const progress = createAcceptanceProgress(row, status);

  const editor = document.createElement("div");
  editor.className = "acceptance-editor";
  editor.hidden = true;
  const textarea = document.createElement("textarea");
  textarea.value = acceptedText;
  textarea.placeholder = "编辑后作为最终采纳内容";
  const submitEdit = document.createElement("button");
  submitEdit.type = "button";
  submitEdit.className = "button primary";
  submitEdit.textContent = "提交采纳";
  editor.append(textarea, submitEdit);

  async function sendDecision(decision, userText = "") {
    [confirmBtn, rejectBtn, editBtn, submitEdit].forEach((btn) => { btn.disabled = true; });
    progress.mount();
    progress.update({ stage: "submit", label: "提交中", status: "running" });
      const operationFlow = createOperationFlow(workflowStages("intervention"));
      schedulePendingWorkflowPersist(workflowSnapshotFromFlow(operationFlow, {
        invocation_id: ctx.invocation_id || "",
        task: ctx.task || flowTask(),
        chapter: ctx.chapter || null,
        track: ctx.track || "create",
        status: "running",
        source: "intervene",
      }), true);
      try {
      const payload = {
        decision,
        original: cleanFinalDraftText(ctx.original || ""),
        user_text: cleanFinalDraftText(userText || ""),
        chapter: ctx.chapter || null,
        task: ctx.task || flowTask(),
        track: ctx.track || "create",
        novel_id: ctx.novel_id || currentProject,
        invocation_id: ctx.invocation_id || "",
        request_analysis: ctx.request_analysis || {},
        model_preferences: modelPreferences(),
      };
      const data = await runInterventionStream(payload, progress, operationFlow, {
        invocation_id: ctx.invocation_id || "",
        task: ctx.task || flowTask(),
        chapter: ctx.chapter || null,
        track: ctx.track || "create",
      });
      if (data.ok === false) {
        status.textContent = data.detail || data.error || "采纳提交失败";
        progress.fail(status.textContent);
        operationFlow.fail();
        [confirmBtn, rejectBtn, editBtn, submitEdit].forEach((btn) => { btn.disabled = false; });
        offerModelRetry(status.textContent, () => sendDecision(decision, userText), {
          roles: ["review"],
          label: "已切换模型，继续确认",
        });
        return;
      }
      acceptedText = data.accepted || userText || ctx.original || "";
      editor.hidden = true;
      row.classList.add("done");
      const savedFile = data.writeback?.novel_artifact?.file || data.writeback?.artifact?.file || "";
      const extraSaved = Array.isArray(data.writeback?.impact_auto_saved) ? data.writeback.impact_auto_saved : [];
      const savedText = extraSaved.length
        ? `${savedFile || extraSaved[0]?.file || "项目文件"} 等 ${extraSaved.length + (savedFile ? 1 : 0)} 处`
        : savedFile;
      status.textContent = decision === "reject"
        ? "已拒绝"
        : savedText
          ? `已确认采纳，已保存到 ${savedText}`
          : "已确认采纳";
      const finalStatus = status.textContent;
      progress.complete(data.writeback_hint ? "确认完成，等待写回归档" : (decision === "reject" ? "已拒绝" : "已完成归档"));
      operationFlow.done();
      finalizeAcceptanceControls(msgEl, row, editor, finalStatus);
      if (decision !== "reject") renderImpactPlan(msgEl, data);
      finishUserConfirmStage(flow);
      if (decision !== "reject" && acceptedText) {
        attachArchiveControls(msgEl, {
          ...ctx,
          task: data.normalized_task || ctx.task,
          chapter: data.normalized_chapter || ctx.chapter || data.writeback_hint?.chapter,
          accepted: data.archive_content || acceptedText,
        });
        await refreshWorkspacePanels();
      }
    } catch (error) {
      status.textContent = `采纳失败：${error}`;
      progress.fail(status.textContent);
      operationFlow.fail();
      [confirmBtn, rejectBtn, editBtn, submitEdit].forEach((btn) => { btn.disabled = false; });
      offerModelRetry(error, () => sendDecision(decision, userText), {
        roles: ["review"],
        label: "已切换模型，继续确认",
      });
    }
  }

  confirmBtn.addEventListener("click", () => sendDecision("confirm"));
  rejectBtn.addEventListener("click", () => sendDecision("reject"));
  editBtn.addEventListener("click", () => {
    editor.hidden = !editor.hidden;
    if (!editor.hidden) textarea.focus();
  });
  submitEdit.addEventListener("click", () => {
    const text = textarea.value.trim();
    if (!text) {
      textarea.focus();
      return;
    }
    sendDecision("other", text);
  });

  msgEl.append(row, editor);
}

function attachArchiveControls(msgEl, ctx) {
  if (!msgEl || msgEl._archiveControls) return;
  const task = ctx.task || flowTask();
  const chapter = Number.parseInt(ctx.chapter || "", 10);
  const isStrongNovel = (ctx.project_kind || currentKind) === "novel_strong";
  let endpoint = "/api/writing/archive-artifact";
  let label = "归档";
  const payload = {
    task,
    content: ctx.accepted || "",
    overwrite: false,
    track: ctx.track || "create",
    novel_id: ctx.novel_id || currentProject,
    invocation_id: ctx.invocation_id || "",
  };
  if (isStrongNovel && task === "outline" && Number.isInteger(chapter) && chapter > 0) {
    endpoint = "/api/writing/archive-outline";
    payload.chapter = chapter;
  } else if (isStrongNovel && task === "prose" && Number.isInteger(chapter) && chapter > 0) {
    endpoint = "/api/writing/archive-chapter";
    payload.chapter = chapter;
  } else if (isStrongNovel) {
    return;
  }

  msgEl._archiveControls = true;
  const row = document.createElement("div");
  row.className = "confirm-row archive-row";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "button danger";
  btn.textContent = label;
  btn.title = isStrongNovel && task === "outline" && Number.isInteger(chapter) && chapter > 0
    ? `确认归档并写回第${chapter}章大纲`
    : "确认归档";
  const status = document.createElement("span");
  status.className = "muted-line";
  row.append(btn, status);
  const progress = createAcceptanceProgress(row, status);

  async function archive(overwrite = false, operationFlow = null) {
    const flow = operationFlow || createOperationFlow(workflowStages("archive"));
    btn.disabled = true;
    progress.mount();
    progress.update({ stage: overwrite ? "overwrite" : "archive_submit", label: overwrite ? "覆盖写回中" : "归档写入中", status: "running" });
    schedulePendingWorkflowPersist(workflowSnapshotFromFlow(flow, {
      invocation_id: payload.invocation_id || "",
      task,
      chapter: Number.isInteger(chapter) && chapter > 0 ? chapter : null,
      track: payload.track,
      status: "running",
      source: "archive",
    }), true);
    try {
      flow.step(overwrite ? "overwrite" : "archive_write");
      schedulePendingWorkflowPersist(workflowSnapshotFromFlow(flow, {
        invocation_id: payload.invocation_id || "",
        task,
        chapter: Number.isInteger(chapter) && chapter > 0 ? chapter : null,
        track: payload.track,
        status: "running",
        source: "archive",
      }));
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, overwrite }),
      });
      const data = await readJsonResponse(res);
      if (data.need_overwrite) {
        progress.update({ stage: "overwrite_confirm", label: "等待覆盖确认", status: "done" });
        flow.step("overwrite_confirm");
        schedulePendingWorkflowPersist(workflowSnapshotFromFlow(flow, {
          invocation_id: payload.invocation_id || "",
          task,
          chapter: Number.isInteger(chapter) && chapter > 0 ? chapter : null,
          track: payload.track,
          status: "awaiting_overwrite_confirm",
          source: "archive",
        }), true);
        const ok = window.confirm(data.message || "目标文件已有内容，确认覆盖？");
        if (ok) return archive(true, flow);
        status.textContent = "已取消写入";
        progress.complete("已取消写入");
        flow.done();
        btn.disabled = false;
        return;
      }
      if (!res.ok || data.ok === false) {
        status.textContent = data.detail || data.error || "写入失败";
        progress.fail(status.textContent);
        flow.fail();
        btn.disabled = false;
        return;
      }
      status.textContent = data.backup ? `已写入 ${data.file}，备份 ${data.backup}` : `已写入 ${data.file}`;
      progress.complete("已完成归档");
      flow.step("archive_refresh");
      await refreshWorkspacePanels();
      flow.done();
      schedulePendingWorkflowPersist(workflowSnapshotFromFlow(flow, {
        invocation_id: payload.invocation_id || "",
        task,
        chapter: Number.isInteger(chapter) && chapter > 0 ? chapter : null,
        track: payload.track,
        status: "completed",
        source: "archive",
      }), true);
    } catch (error) {
      status.textContent = `写入失败：${error}`;
      progress.fail(status.textContent);
      flow.fail();
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", () => archive(false));
  msgEl.appendChild(row);
}

async function runDraftStream(payload) {
  setBusy(true, "生成中");
  payload = refreshModelPayload(payload);
  const stages = stagesForPayload(payload);
  const doneNodes = new Set();
  const timer = createStageTimer(stages, () => {
    renderStages(timer.current, doneNodes, stages, timer);
  });
  renderStages(stages[0], doneNodes, stages, timer);
  let draft = "";
  let draftMsg = null;
  let draftWriter = null;
  let providerGrid = null;
  let doneData = null;
  let keepTimerRunning = false;
  let streamFailed = false;
  let invocationId = payload.invocation_id || "";

  try {
  const res = await fetch("/api/writing/draft-stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) throw new Error("stream unavailable");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const blocks = buf.split("\n\n");
    buf = blocks.pop() || "";
    for (const block of blocks) {
      const ev = parseSSE(block);
      if (!ev) continue;
      if (ev.event === "invocation") {
        invocationId = ev.data.invocation_id || invocationId;
        payload.invocation_id = invocationId;
        invocationCard.innerHTML = `<div class="invocation-list"><strong>${ev.data.invocation_id}</strong><span>${ev.data.log || ""}</span></div>`;
        markStatusTabUpdated("invocation", ev.data);
        schedulePendingWorkflowPersist(workflowSnapshotFromTimer(stages, doneNodes, timer, {
          invocation_id: invocationId,
          task: payload.task,
          chapter: payload.chapter,
          track: payload.track,
          status: "running",
          source: "draft_stream",
        }), true);
      } else if (ev.event === "provider") {
        if (ev.data.type === "provider_init") providerGrid = buildProviderGrid(ev.data.order || []);
        if (ev.data.type === "provider") updateProviderGrid(providerGrid, ev.data);
      } else if (ev.event === "node") {
        const node = ev.data.node;
        rememberFlowTask(ev.data.request_analysis);
        markStageDone(timer, node);
        doneNodes.add(node);
        const next = nextStageAfter(node, stages);
        markStageStarted(timer, next);
        renderStages(next, doneNodes, stages, timer);
        schedulePendingWorkflowPersist(workflowSnapshotFromTimer(stages, doneNodes, timer, {
          invocation_id: invocationId,
          task: payload.task,
          chapter: payload.chapter,
          track: payload.track,
          status: node === "provider_confirm_gate" ? "awaiting_confirm" : "running",
          source: "draft_stream",
        }));
      } else if (ev.event === "token") {
        const chunk = ev.data.text || "";
        draft += chunk;
        if (!draftMsg) {
          draftMsg = addMessage("assistant", "", "生成稿");
          draftWriter = createTypewriter(draftMsg.body);
        }
        draftWriter?.append(chunk);
      } else if (ev.event === "done") {
        doneData = ev.data;
      } else if (ev.event === "error") {
        streamFailed = true;
        addMessage("system", ev.data.message || "生成失败", "错误");
        offerModelRetry(ev.data.message || "", () => runDraftStream(refreshModelPayload(payload)), {
          roles: ["chat", "writing", "review"],
          label: "已切换模型，继续创作",
        });
      }
    }
  }
  if (!doneData && draft && !streamFailed) {
    doneData = {
      task: payload.task,
      chapter: payload.chapter,
      data: {
        draft,
        task: payload.task,
        chapter: payload.chapter,
        project_kind: currentKind,
      },
    };
  }
  if (doneData?.data?.awaiting_provider_confirm) {
    rememberFlowTask(doneData.data, doneData.data.request_analysis);
    keepTimerRunning = true;
    doneNodes.delete("provider_confirm_gate");
    timer.durations.delete("provider_confirm_gate");
    markStageStarted(timer, "provider_confirm_gate");
    renderStages("provider_confirm_gate", doneNodes, stages, timer);
    schedulePendingWorkflowPersist(workflowSnapshotFromTimer(stages, doneNodes, timer, {
      invocation_id: invocationId || doneData.data.invocation_id || "",
      task: doneData.data.task || payload.task,
      chapter: doneData.data.chapter || payload.chapter,
      track: payload.track,
      status: "awaiting_confirm",
      source: "draft_stream",
    }), true);
    const providerCtx = {
      chapter: doneData.data.chapter || payload.chapter,
      task: doneData.data.task || payload.task,
      track: payload.track,
      novel_id: payload.novel_id,
      project_kind: doneData.data.project_kind || currentKind,
      checkpoint_id: doneData.data.checkpoint_id || "",
      invocation_id: doneData.data.invocation_id || "",
      request_analysis: doneData.data.request_analysis || {},
      archive_content: doneData.data.archive_content || "",
      model_preferences: modelPreferences(),
    };
    await persistProviderMaterials(providerGrid, providerCtx);
    attachProviderGate(providerGrid, providerCtx, { timer, doneNodes, stages });
  } else if (doneData) {
    const text = cleanFinalDraftText(extractDraftText(doneData, draft));
    const draftCtx = {
      original: text,
      chapter: doneData.data?.chapter || doneData.chapter || payload.chapter,
      task: doneData.data?.task || doneData.task || payload.task,
      track: payload.track,
      novel_id: payload.novel_id,
      project_kind: doneData.data?.project_kind || currentKind,
      invocation_id: doneData.data?.invocation_id || "",
      request_analysis: doneData.data?.request_analysis || {},
      archive_content: doneData.data?.archive_content || "",
      provider_answers: doneData.data?.provider_answers || [],
      artifacts: doneData.data?.artifacts || {},
      merge_info: doneData.data?.merge_info || {},
      model_preferences: modelPreferences(),
    };
    rememberFlowTask(draftCtx, draftCtx.request_analysis);
    if (text && draftWriter) {
      if (text.startsWith(draft) && text.length > draft.length) {
        draftWriter.append(text.slice(draft.length));
      } else if (draft && text !== draft) {
        draftWriter.setText(text);
      }
      await draftWriter.drain();
    }
    if (text && !draftMsg) {
      draftMsg = addMessage("assistant", "", "生成稿", { persist: false });
      draftWriter = createTypewriter(draftMsg.body);
      draftWriter.append(text);
      await draftWriter.drain();
    }
    if (text) await persistDraftResult(text, "生成稿", draftCtx);
    if (text && draftMsg) {
      attachAcceptanceControls(draftMsg, {
        ...draftCtx,
        original: text,
      }, { timer, doneNodes, stages });
      keepTimerRunning = true;
      startUserConfirmStage({ timer, doneNodes, stages });
      schedulePendingWorkflowPersist(workflowSnapshotFromTimer(stages, doneNodes, timer, {
        invocation_id: draftCtx.invocation_id || invocationId,
        task: draftCtx.task,
        chapter: draftCtx.chapter,
        track: draftCtx.track,
        status: "awaiting_confirm",
        source: "draft_stream",
      }), true);
    } else {
      addMessage("system", "生成完成，但没有收到可供确认的正文内容。", "用户确认");
      markStageDone(timer, "draft_finalize");
      doneNodes.add("draft_finalize");
      finishStageTimer(timer);
      renderStages("draft_finalize", doneNodes, stages, timer);
    }
  }
  } catch (error) {
    addMessage("system", `生成失败：${error}`, "错误");
    offerModelRetry(error, () => runDraftStream(refreshModelPayload(payload)), {
      roles: ["chat", "writing", "review"],
      label: "已切换模型，继续创作",
    });
  } finally {
    if (!keepTimerRunning && timer.interval) {
      finishStageTimer(timer);
      renderStages(timer.current, doneNodes, stages, timer);
    }
    setBusy(false);
  }
  scrollMessagesToBottom();
}

async function runDoctor() {
  setBusy(true, "诊断中");
  const flow = createOperationFlow(["doctor_request", "doctor_check", "doctor_render"]);
  try {
    flow.step("doctor_check");
    const res = await fetch(`/api/writing/doctor?novel_id=${encodeURIComponent(currentProject)}`);
    const data = await readJsonResponse(res);
    assertApiOk(res, data, "诊断失败");
    flow.step("doctor_render");
    const item = document.createElement("article");
    item.className = "message assistant";
    item.innerHTML = `<div class="message-title">写作诊断 <span class="status-chip ${data.status}">${data.status}</span></div>`;
    const grid = document.createElement("div");
    grid.className = "doctor-grid";
    for (const check of groupedDoctorChecks(data.checks || [])) {
      grid.appendChild(renderDoctorCard(check));
    }
    item.appendChild(grid);
    messagesEl.appendChild(item);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    flow.done();
  } catch (error) {
    flow.fail();
    addMessage("system", `诊断失败：${error}`, "诊断");
  } finally {
    setBusy(false);
  }
}

function groupedDoctorChecks(checks) {
  const framework = [];
  const others = [];
  for (const check of checks || []) {
    const name = String(check?.name || "");
    if (name.startsWith("框架保护：")) {
      framework.push(check);
    } else {
      others.push(check);
    }
  }
  if (!framework.length) return others;
  const level = worstLevel(framework.map((item) => item.level));
  const lines = framework.map((item) => ({
    path: String(item.name || "").replace(/^框架保护：/, ""),
    level: item.level || "warn",
    message: item.message || "",
    hint: item.hint || "",
  }));
  const grouped = {
    name: "框架保护",
    level,
    message: `${framework.length} 条保护策略${level === "ok" ? "正常。" : "需检查。"}`,
    hint: "框架说明、SOP、Wiki 结构与运行时目录不应通过 Web 文件编辑器保存。",
    framework_lines: lines,
  };
  return [...others, grouped];
}

function worstLevel(levels) {
  if ((levels || []).includes("error")) return "error";
  if ((levels || []).includes("warn")) return "warn";
  return "ok";
}

function renderDoctorCard(check) {
  const card = document.createElement("section");
  card.className = `doctor-card ${check.framework_lines ? "framework-protection" : ""}`;
  const header = document.createElement("header");
  const name = document.createElement("strong");
  name.textContent = compactText(check.name || "检查项", 18);
  name.title = check.name || "";
  const level = document.createElement("span");
  level.className = `status-chip ${check.level || "warn"}`;
  level.textContent = check.level || "warn";
  header.append(name, level);

  const message = document.createElement("div");
  message.className = "doctor-message-line";
  message.textContent = check.message || "";
  message.title = check.message || "";

  card.append(header, message);
  if (Array.isArray(check.framework_lines) && check.framework_lines.length) {
    const list = document.createElement("div");
    list.className = "framework-protection-list";
    list.title = check.framework_lines.map((item) => (
      [item.path, item.level || "warn", item.message || ""].filter(Boolean).join("｜")
    )).join("\n");
    for (const item of check.framework_lines) {
      const row = document.createElement("div");
      row.className = `framework-protection-row ${item.level || "warn"}`;
      const text = `${item.path}｜${item.level || "warn"}｜${item.message || ""}`;
      row.textContent = text;
      row.title = [item.path, item.message, item.hint].filter(Boolean).join("\n");
      list.appendChild(row);
    }
    card.appendChild(list);
  }

  const hint = document.createElement("div");
  hint.className = "muted-line doctor-hint-line";
  hint.textContent = check.hint || "";
  hint.title = check.hint || "";
  card.appendChild(hint);
  return card;
}

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;
  if (!requireModels(["chat", "writing", "review"], {
    label: "已选择模型，继续创作",
    retry: () => {
      messageInput.value = message;
      composer.requestSubmit();
    },
  })) return;
  const chapter = currentChapter();
  const auditFlow = createOperationFlow(["need_audit"]);
  try {
    await runNeedAuditPreview(message);
    auditFlow.done();
  } catch {
    auditFlow.fail();
  }
  clearAcceptanceControls();
  addMessage("user", message, "创作", { persist: true });
  messageInput.value = "";
  const payload = {
    message,
    mode: "draft",
    chapter,
    task: "generic",
    track: "create",
    novel_id: currentProject,
    login_confirmed: aiToggle.checked ? loginConfirmed() : {},
    use_provider_source: aiToggle.checked,
    model_preferences: modelPreferences(),
  };
  try {
    await runDraftStream(payload);
  } catch (error) {
    addMessage("system", String(error), "请求失败");
  } finally {
    setBusy(false);
    await loadStatus();
    await loadCostBoard();
    await loadMission();
    await loadObservability();
  }
});

projectSelect.addEventListener("change", async () => {
  currentProject = projectSelect.value;
  setProjectActionStatus("");
  const flow = createOperationFlow(["project_reload", "workspace_status", "workspace_files", "workspace_cost", "workspace_mission", "workspace_observe", "workspace_history"]);
  try {
    await reloadProjectWorkspace({ flow });
    flow.done();
  } catch (error) {
    flow.fail();
    addMessage("system", `切换项目失败：${error}`, "项目");
  }
});

createProjectBtn.addEventListener("click", () => {
  projectCreateForm.hidden = !projectCreateForm.hidden;
  setProjectActionStatus(projectCreateForm.hidden ? "" : "输入项目 ID，选择类型后创建。");
  if (!projectCreateForm.hidden) projectIdInput.focus();
});
cancelProjectCreateBtn.addEventListener("click", () => {
  projectCreateForm.hidden = true;
  projectIdInput.value = "";
  setProjectActionStatus("");
});
submitProjectCreateBtn.addEventListener("click", createProject);
projectIdInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    createProject();
  }
});
deleteProjectBtn.addEventListener("click", deleteCurrentProject);
doctorBtn.addEventListener("click", runDoctor);
if (chatBtn) {
  chatBtn.addEventListener("click", runPlainChat);
}
closeFileBtn.addEventListener("click", showConversation);
if (closeWikiBtn) {
  closeWikiBtn.addEventListener("click", showConversation);
}
saveFileBtn.addEventListener("click", () => saveActiveFile({ runUpdateFlow: false }));
if (rewriteFileBtn) {
  rewriteFileBtn.addEventListener("click", () => saveActiveFile({ runUpdateFlow: true }));
}
if (visualPromptBtn) {
  visualPromptBtn.addEventListener("click", runShortFilmVisualPrompts);
}
if (storyboardImagesBtn) {
  storyboardImagesBtn.addEventListener("click", runStoryboardImages);
}
aiToggle.addEventListener("change", () => {
  saveBoolPref(AI_TOGGLE_KEY, aiToggle.checked);
});
providerChecks.addEventListener("change", (event) => {
  const input = event.target.closest("[data-provider]");
  if (!input) return;
  saveProviderPref(input.dataset.provider, input.checked);
  syncAiToggleFromProviders();
});
for (const [role, select] of [["chat", chatModelSelect], ["writing", writingModelSelect], ["review", reviewModelSelect], ["image", imageModelSelect]]) {
  select?.addEventListener("change", () => saveModelPref(role, select.value));
}
fileEditorText.addEventListener("input", () => {
  if (!activeFile) return;
  activeFile.content = fileEditorText.value;
  activeFile.dirty = activeFile.content !== activeFile.savedContent;
  fileEditorTitle.textContent = `${activeFile.dirty ? "* " : ""}${activeFile.name}`;
  if (activeFile.editable === false) {
    fileEditorStatus.textContent = activeFile.message || "框架文件受保护，不能在 Web 文件编辑器中保存。";
  } else {
    fileEditorStatus.textContent = activeFile.dirty ? "有未保存修改。" : "可编辑。";
  }
  saveFileBtn.disabled = !canEditActiveFile();
  if (rewriteFileBtn) rewriteFileBtn.disabled = !canEditActiveFile();
});

document.addEventListener("click", async (event) => {
  const importReferenceBtn = event.target.closest("[data-import-reference]");
  if (importReferenceBtn) {
    event.preventDefault();
    referenceImportInput().click();
    return;
  }

  const applyUpdateBtn = event.target.closest("[data-apply-update]");
  if (applyUpdateBtn) {
    await handlePendingFileUpdate(applyUpdateBtn.dataset.applyUpdate, "apply");
    return;
  }

  const rejectUpdateBtn = event.target.closest("[data-reject-update]");
  if (rejectUpdateBtn) {
    await handlePendingFileUpdate(rejectUpdateBtn.dataset.rejectUpdate, "reject");
    return;
  }

  const openBtn = event.target.closest("[data-open-provider]");
  if (openBtn) {
    const provider = openBtn.dataset.openProvider;
    setBusy(true, `打开 ${providerDisplayName(provider)}`);
    const flow = createOperationFlow(["provider_open", "provider_refresh"]);
    try {
      flow.step("provider_open");
      const res = await fetch(`/api/ai-providers/${provider}/open`, { method: "POST" });
      const data = await readJsonResponse(res);
      if (!res.ok || data.ok === false) {
        addMessage("system", data.detail || data.result || `打开 ${providerDisplayName(provider)} 失败`, "网页模型");
      } else {
        const pinnedNote = data.pinned ? `已续用固定会话：${data.pinned}` : "未固定会话，当前为新建对话页";
        addMessage("system", `${data.name || providerDisplayName(provider)} 已在可见浏览器窗口打开。${pinnedNote}。登录后勾选即可。`, data.profile || "网页模型");
      }
      flow.step("provider_refresh");
      await loadProviders();
      flow.done();
    } catch (error) {
      flow.fail();
      addMessage("system", `打开 ${providerDisplayName(provider)} 失败：${error}`, "网页模型");
    } finally {
      setBusy(false);
    }
    return;
  }

  const pinBtn = event.target.closest("[data-pin-provider]");
  if (pinBtn) {
    const provider = pinBtn.dataset.pinProvider;
    setBusy(true, `固定 ${providerDisplayName(provider)} 会话`);
    const flow = createOperationFlow(["provider_pin", "provider_refresh"]);
    try {
      flow.step("provider_pin");
      const res = await fetch(`/api/ai-providers/${provider}/pin`, { method: "POST" });
      const data = await readJsonResponse(res);
      if (!res.ok || data.ok === false) {
        throw new Error(data.detail || data.error || data.result || "固定会话失败");
      }
      addMessage("system", data.result || (data.ok ? "已固定会话" : "固定失败"), data.url || "网页模型");
      flow.step("provider_refresh");
      await loadProviders();
      flow.done();
    } catch (error) {
      flow.fail();
      addMessage("system", `固定 ${providerDisplayName(provider)} 会话失败：${error}`, "网页模型");
    } finally {
      setBusy(false);
    }
    return;
  }

  const resetBtn = event.target.closest("[data-reset-provider]");
  if (resetBtn) {
    const provider = resetBtn.dataset.resetProvider;
    setBusy(true, `重置 ${providerDisplayName(provider)} 会话`);
    const flow = createOperationFlow(["provider_reset", "provider_refresh"]);
    try {
      flow.step("provider_reset");
      const res = await fetch(`/api/ai-providers/${provider}/reset-conversation`, { method: "POST" });
      const data = await readJsonResponse(res);
      if (!res.ok || data.ok === false) {
        throw new Error(data.detail || data.error || data.result || "重置会话失败");
      }
      addMessage("system", data.result || "已重置会话", "网页模型");
      flow.step("provider_refresh");
      await loadProviders();
      flow.done();
    } catch (error) {
      flow.fail();
      addMessage("system", `重置 ${providerDisplayName(provider)} 会话失败：${error}`, "网页模型");
    } finally {
      setBusy(false);
    }
    return;
  }

  const trajectoryBtn = event.target.closest("[data-load-trajectory]");
  if (trajectoryBtn) {
    const flow = createOperationFlow(["trajectory_load"]);
    switchStatusTab("trajectory");
    try {
      await loadTrajectory(trajectoryBtn.dataset.loadTrajectory);
      flow.done();
    } catch (error) {
      flow.fail();
      addMessage("system", `轨迹加载失败：${error}`, "观察");
    }
    return;
  }

  const packetBtn = event.target.closest("[data-review-packet]");
  if (packetBtn) {
    const flow = createOperationFlow(["packet_generate"]);
    switchStatusTab("review");
    try {
      await loadReviewPacket(packetBtn.dataset.reviewPacket);
      flow.done();
    } catch (error) {
      flow.fail();
      addMessage("system", `复盘包生成失败：${error}`, "观察");
    }
    return;
  }

  const openFilePathBtn = event.target.closest("[data-open-file-path]");
  if (openFilePathBtn) {
    await openFile(openFilePathBtn.dataset.openFilePath || "");
    return;
  }

  const adoptLessonBtn = event.target.closest("[data-adopt-lesson]");
  if (adoptLessonBtn) {
    await adoptLessonSuggestion(adoptLessonBtn.dataset.adoptLesson);
    return;
  }

  const statusTab = event.target.closest("[data-status-tab]");
  if (statusTab) {
    switchStatusTab(statusTab.dataset.statusTab);
  }
});

window.addEventListener("beforeunload", () => {
  if (activeWorkflowStatus?.invocation_id) {
    schedulePendingWorkflowPersist({
      ...activeWorkflowStatus,
      updated_at: new Date().toISOString(),
    }, true);
  }
});

Promise.all([loadWorkflowStages(), loadModels(), loadStatus(), loadProviders()])
  .then(loadFiles)
  .then(loadCostBoard)
  .then(loadMission)
  .then(loadObservability)
  .then(loadPendingWorkflowStatus)
  .then(loadChatHistory)
  .catch((error) => {
    setProjectActionStatus(`工作台初始化失败：${error}`, "error");
    addMessage("system", `工作台初始化失败：${error}`, "启动");
  });
