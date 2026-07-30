/*
模块职责：驱动 v0.3 工作台的任务轮询、审阅命令和发布操作。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/integration/test_api.py、tests/integration/test_evaluation_api.py
*/

const state = {
  documentId: null, documentPageCount: 0, activeJobId: null, pollTimer: null,
  currentRun: null, runs: [], pageIndex: [], pageNo: null, page: null,
  artifacts: [], contentMode: 'readable', zoom: 1, fitWidth: true,
  evaluationDocuments: [], evaluationCaseId: null, evaluationCase: null,
  evaluationMode: 'assessment',
  evaluationAssessment: null, evaluationAssessmentPage: null,
  evaluationAssessmentPageNo: null, evaluationAssessmentZoom: 1,
  evaluationAssessmentFitWidth: true, evaluationAssessmentPane: 'source',
  evaluationComparison: null, evaluationComparisonPage: null,
  evaluationComparisonPageNo: null, evaluationComparisonZoom: 1,
  evaluationComparisonFitWidth: true, evaluationComparisonPane: 'source',
};
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value || '').replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char]));

async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error?.message || payload.detail || `请求失败 (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function notice(message, error = false) {
  const target = byId('notice');
  target.textContent = message;
  target.className = error ? 'notice error' : 'notice';
}

function selected() {
  if (!state.documentId) throw new Error('请先在文档库选择一个文档');
  return state.documentId;
}

async function refreshDocuments() {
  const documents = await api('/documents');
  const list = byId('document-list'); list.innerHTML = '';
  documents.forEach((item) => {
    const button = document.createElement('button');
    button.className = `document-item ${item.id === state.documentId ? 'active' : ''}`;
    button.innerHTML = `${escapeHtml(item.filename)}<small>${item.page_count} 页 · ${escapeHtml(item.status)}</small>`;
    button.onclick = async () => {
      state.documentId = item.id;
      state.documentPageCount = item.page_count;
      byId('document-status').textContent = `${item.filename} · ${item.status}`;
      await refreshDocuments(); await refreshAll(); await restoreActiveJob();
    };
    list.appendChild(button);
  });
  if (!state.documentId && documents[0]) {
    state.documentId = documents[0].id;
    state.documentPageCount = documents[0].page_count;
    byId('document-status').textContent = `${documents[0].filename} · ${documents[0].status}`;
    await refreshDocuments(); await refreshAll(); await restoreActiveJob();
  }
}

function evidenceText(evidence) {
  return evidence.map((item) => `p.${item.page_no}: ${item.quote || ''}`).join('\n');
}

function reviewControls(endpoint, includeReparse = false) {
  const wrap = document.createElement('div'); wrap.className = 'review-actions';
  const reason = document.createElement('input'); reason.placeholder = '审阅原因（可选）'; wrap.appendChild(reason);
  ['accepted', 'rejected'].concat(includeReparse ? ['reparse_requested'] : []).forEach((status) => {
    const button = document.createElement('button');
    button.textContent = status === 'accepted' ? '接受' : status === 'rejected' ? '拒绝' : '请求重解析';
    if (status === 'rejected') button.className = 'reject';
    button.onclick = async () => {
      try {
        await api(endpoint, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status, reason:reason.value}) });
        notice('审阅结论已保存'); await refreshAll();
      } catch (error) { notice(error.message, true); }
    };
    wrap.appendChild(button);
  });
  return wrap;
}

function runLabel(run) {
  const range = run.page_count ? `${run.page_start}-${run.page_end} 页` : '无页面';
  return `${range} · ${run.model_calls || 0} 次调用`;
}

function renderRunHistory() {
  const host = byId('run-history'); host.innerHTML = '';
  state.runs.forEach((run) => {
    const item = document.createElement('article'); item.className = `run-item ${run.is_current ? 'current' : ''}`;
    const contract = run.provider_summary?.contract_version || run.provider_summary?.vision_model || '未知契约';
    item.innerHTML = `<div><strong>${run.is_current ? '当前结果' : escapeHtml(run.status)}</strong><span class="artifact-state">${escapeHtml(run.artifact_state || 'available')}</span></div>
      <p>${escapeHtml(runLabel(run))}</p><p class="meta">${escapeHtml(contract)} · ${Number(run.size_bytes || 0).toLocaleString()} B</p>
      <code>${escapeHtml(run.id)}</code>`;
    if (!run.is_current && run.status === 'parsed' && (run.artifact_state || 'available') === 'available') {
      const select = document.createElement('button'); select.type = 'button'; select.textContent = '设为当前';
      select.onclick = async () => {
        const reason = window.prompt('切换原因', '人工选择审阅基线');
        if (!reason) return;
        try {
          await api(`/documents/${selected()}/current-parse-run`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({run_id:run.id, reason})});
          notice('当前解析结果已切换'); await refreshPages();
        } catch (error) { notice(error.message, true); }
      };
      item.appendChild(select);
    }
    host.appendChild(item);
  });
}

function renderRunToolbar() {
  const run = state.currentRun;
  const toolbar = byId('run-toolbar');
  if (!run) { toolbar.hidden = true; return; }
  const contract = run.provider_summary?.contract_version || run.provider_summary?.vision_model || '未知契约';
  const manifest = run.manifest_hash ? run.manifest_hash.slice(0, 12) : '无清单';
  const reviews = run.review_counts || {};
  toolbar.innerHTML = `<strong>${escapeHtml(contract)}</strong><span>${escapeHtml(runLabel(run))}</span><span>Manifest ${escapeHtml(manifest)}</span><span>接受 ${reviews.accepted || 0} · 拒绝 ${reviews.rejected || 0}</span>`;
  toolbar.hidden = false;
}

function filteredPageIndex() {
  const query = byId('page-filter').value.trim().toLowerCase();
  const issuesOnly = byId('issues-only').checked;
  return state.pageIndex.filter((item) => {
    const searchable = `${item.page_no} ${item.page_kind} ${item.review_status} ${item.quality_status}`.toLowerCase();
    return (!query || searchable.includes(query)) && (!issuesOnly || item.issue_count > 0);
  });
}

function renderPageIndex() {
  const host = byId('page-index'); host.innerHTML = '';
  filteredPageIndex().forEach((entry) => {
    const button = document.createElement('button'); button.type = 'button';
    button.className = `page-index-item ${entry.page_no === state.pageNo ? 'active' : ''} ${entry.issue_count ? 'has-issues' : ''}`;
    button.innerHTML = `<span>第 ${entry.page_no} 页</span><small>${escapeHtml(entry.review_status)}${entry.issue_count ? ` · ${entry.issue_count} 问题` : ''}</small>`;
    button.onclick = () => loadPage(entry.page_no);
    host.appendChild(button);
  });
}

function renderOcrContent() {
  if (!state.page) return;
  const host = byId('ocr-content'); host.className = `ocr-content ${state.contentMode}`;
  if (state.contentMode === 'blocks') host.textContent = JSON.stringify(state.page.blocks, null, 2);
  else host.textContent = state.page.markdown;
  document.querySelectorAll('.content-tab').forEach((item) => item.classList.toggle('active', item.dataset.content === state.contentMode));
}

function applyZoom() {
  const image = byId('page-image');
  image.classList.toggle('fit', state.fitWidth);
  image.style.width = state.fitWidth ? '100%' : `${Math.round(state.zoom * 100)}%`;
  byId('zoom-level').textContent = state.fitWidth ? '适宽' : `${Math.round(state.zoom * 100)}%`;
}

function renderArtifactDownloads() {
  const host = byId('artifact-downloads'); host.innerHTML = '';
  const wanted = [
    ['document_markdown', '整篇 Markdown'], ['parse_manifest', 'Manifest'],
    ['page_json', '当前页 JSON'], ['provider_response', '供应商响应'],
  ];
  wanted.forEach(([kind, label]) => {
    const artifact = state.artifacts.find((item) => item.kind === kind &&
      (!['page_json', 'provider_response'].includes(kind) || Number(item.metadata?.page_no) === state.pageNo));
    if (!artifact) return;
    const link = document.createElement('a'); link.href = artifact.download_url; link.textContent = label; host.appendChild(link);
  });
}

async function loadPage(pageNo) {
  if (!state.currentRun) return;
  const available = state.pageIndex.map((item) => item.page_no);
  if (!available.includes(Number(pageNo))) return;
  state.pageNo = Number(pageNo);
  state.page = await api(`/parse-runs/${state.currentRun.id}/pages/${state.pageNo}`);
  byId('page-number').value = state.pageNo;
  byId('page-image').src = state.page.image_url;
  byId('page-image').alt = `PDF 第 ${state.pageNo} 页`;
  byId('page-state').textContent = `${state.page.page_kind} · ${state.page.review_status}`;
  byId('page-issues').textContent = (state.page.quality.issues || []).join('；');
  const review = byId('page-review'); review.innerHTML = ''; review.appendChild(reviewControls(`/pages/${state.page.id}/reviews`, true));
  renderPageIndex(); renderOcrContent(); renderArtifactDownloads(); applyZoom();
}

async function refreshPages() {
  if (!state.documentId) return;
  state.runs = await api(`/documents/${selected()}/parse-runs`);
  state.currentRun = state.runs.find((run) => run.is_current) || null;
  renderRunHistory(); renderRunToolbar();
  const available = Boolean(state.currentRun && (state.currentRun.artifact_state || 'available') === 'available');
  byId('review-workspace').hidden = !available; byId('empty-pages').hidden = available;
  if (!available) { state.pageIndex = []; state.page = null; return; }
  [state.pageIndex, state.artifacts] = await Promise.all([
    api(`/parse-runs/${state.currentRun.id}/page-index`), api(`/parse-runs/${state.currentRun.id}/artifacts`),
  ]);
  const desired = state.pageIndex.some((item) => item.page_no === state.pageNo) ? state.pageNo : state.pageIndex[0]?.page_no;
  if (desired) await loadPage(desired);
}

async function refreshKnowledge() {
  if (!state.documentId) return;
  const [nodes, edges] = await Promise.all([api(`/documents/${selected()}/knowledge-nodes`), api(`/documents/${selected()}/knowledge-edges`)]);
  const candidates = byId('candidates'); candidates.innerHTML = '';
  nodes.forEach((node) => {
    const item = document.createElement('article'); item.className = 'candidate';
    item.innerHTML = `<h2>${escapeHtml(node.title)} <span class="meta">${escapeHtml(node.kind)} · ${escapeHtml(node.review_status)}</span></h2><p>${escapeHtml(node.content)}</p><p class="meta">${escapeHtml(evidenceText(node.evidence))}</p>`;
    item.appendChild(reviewControls(`/knowledge-nodes/${node.id}/reviews`)); candidates.appendChild(item);
  });
  const edgeHost = byId('edges'); edgeHost.innerHTML = '';
  const titles = Object.fromEntries(nodes.map((node) => [node.id, node.title]));
  edges.forEach((edge) => {
    const item = document.createElement('article'); item.className = 'edge';
    item.innerHTML = `<strong>${escapeHtml(titles[edge.source_id] || edge.source_id)}</strong> → ${escapeHtml(edge.relation)} → <strong>${escapeHtml(titles[edge.target_id] || edge.target_id)}</strong> <span class="meta">${escapeHtml(edge.review_status)}</span><p class="meta">${escapeHtml(evidenceText(edge.evidence))}</p>`;
    item.appendChild(reviewControls(`/knowledge-edges/${edge.id}/reviews`)); edgeHost.appendChild(item);
  });
}

async function refreshWorkbook() {
  if (state.documentId) byId('download-workbook').href = `/api/v1/documents/${selected()}/workbook`;
}

async function refreshGraph() {
  if (!state.documentId) return;
  const graph = byId('graph');
  try {
    const data = await api(`/documents/${selected()}/graph`);
    const titles = Object.fromEntries(data.nodes.map((node) => [node.id, node.title]));
    graph.innerHTML = data.nodes.map((node) => `<span class="graph-node">${escapeHtml(node.title)}</span>`).join('') + data.edges.map((edge) => `<span class="graph-edge">${escapeHtml(titles[edge.source_id])} → ${escapeHtml(edge.relation)} → ${escapeHtml(titles[edge.target_id])}</span>`).join('');
  } catch (error) { graph.textContent = error.message; }
}

function fillSelect(target, items, value, label) {
  target.innerHTML = '';
  items.forEach((item) => {
    const option = document.createElement('option');
    option.value = item.id; option.textContent = label(item); target.appendChild(option);
  });
  if (items.some((item) => item.id === value)) target.value = value;
}

async function refreshEvaluationDocuments() {
  state.evaluationDocuments = await api('/evaluations/documents');
  if (!state.evaluationDocuments.some((item) => item.case_id === state.evaluationCaseId)) {
    state.evaluationCaseId = state.evaluationDocuments[0]?.case_id || null;
  }
  fillSelect(
    byId('evaluation-document'),
    state.evaluationDocuments.map((item) => ({...item, id:item.case_id})),
    state.evaluationCaseId,
    (item) => item.title,
  );
  if (state.evaluationCaseId) await loadEvaluationCase(state.evaluationCaseId);
}

async function loadEvaluationCase(caseId) {
  state.evaluationCaseId = caseId;
  state.evaluationCase = await api(`/evaluations/documents/${encodeURIComponent(caseId)}`);
  const snapshots = state.evaluationCase.snapshots || [];
  const assessments = state.evaluationCase.assessments || [];
  const baseline = snapshots.filter((item) => item.baseline_eligible);
  const assessmentSelect = byId('evaluation-assessment');
  const baselineSelect = byId('evaluation-baseline');
  const candidateSelect = byId('evaluation-candidate');
  const oldAssessment = assessmentSelect.value;
  const oldBaseline = baselineSelect.value;
  const oldCandidate = candidateSelect.value;
  fillSelect(
    assessmentSelect,
    assessments.map((item) => ({...item, id:item.assessment_id})),
    oldAssessment,
    (item) => `${item.experiment_id || item.assessment_id} · ${item.profile}`,
  );
  fillSelect(
    baselineSelect,
    baseline.map((item) => ({...item, id:item.snapshot_id})),
    oldBaseline,
    snapshotLabel,
  );
  fillSelect(
    candidateSelect,
    snapshots.map((item) => ({...item, id:item.snapshot_id})),
    oldCandidate,
    snapshotLabel,
  );
  if (candidateSelect.value === baselineSelect.value) {
    candidateSelect.value = snapshots.find((item) => item.snapshot_id !== baselineSelect.value)?.snapshot_id || '';
  }
  byId('evaluation-open-assessment').disabled = !assessments.length;
  byId('evaluation-compare').disabled = !baseline.length || snapshots.length < 2;
  updateEvaluationEmptyState();
}

function snapshotLabel(snapshot) {
  const branch = snapshot.revision?.branch ? ` · ${snapshot.revision.branch}` : '';
  return `${snapshot.label || snapshot.snapshot_id}${branch}`;
}

function latestReviews(resource) {
  const latest = {};
  (resource?.reviews || []).forEach((item) => { latest[item.page_no] = item; });
  return latest;
}

function renderAssessmentPageIndex() {
  const host = byId('evaluation-assessment-page-index'); host.innerHTML = '';
  const assessment = state.evaluationAssessment;
  if (!assessment) return;
  const reviews = latestReviews(assessment);
  const reviewPages = new Set(assessment.review_page_numbers || []);
  assessment.page_numbers.forEach((pageNo) => {
    const reviewed = Boolean(reviews[pageNo]);
    const required = reviewPages.has(pageNo);
    const button = document.createElement('button'); button.type = 'button';
    button.className = `evaluation-page-item ${pageNo === state.evaluationAssessmentPageNo ? 'active' : ''} ${required && !reviewed ? 'changed' : ''} ${reviewed ? 'reviewed' : ''}`;
    button.innerHTML = `<span>第 ${pageNo} 页</span><small>${reviewed ? escapeHtml(reviews[pageNo].verdict) : (required ? '待审阅' : '自动检查')}</small>`;
    button.onclick = () => loadAssessmentPage(pageNo);
    host.appendChild(button);
  });
}

function renderComparisonPageIndex() {
  const host = byId('evaluation-comparison-page-index'); host.innerHTML = '';
  const comparison = state.evaluationComparison;
  if (!comparison) return;
  const reviews = latestReviews(comparison);
  comparison.page_numbers.forEach((pageNo) => {
    const changed = comparison.changed_pages.includes(pageNo), reviewed = Boolean(reviews[pageNo]);
    const button = document.createElement('button'); button.type = 'button';
    button.className = `evaluation-page-item ${pageNo === state.evaluationComparisonPageNo ? 'active' : ''} ${changed ? 'changed' : ''} ${reviewed ? 'reviewed' : ''}`;
    button.innerHTML = `<span>第 ${pageNo} 页</span><small>${reviewed ? escapeHtml(reviews[pageNo].verdict) : (changed ? '待审阅' : '无变化')}</small>`;
    button.onclick = () => loadComparisonPage(pageNo);
    host.appendChild(button);
  });
}

async function loadEvaluationAssessment(assessmentId) {
  state.evaluationAssessment = await api(`/evaluations/assessments/${encodeURIComponent(assessmentId)}`);
  const pages = state.evaluationAssessment.page_numbers;
  const desired = pages.includes(state.evaluationAssessmentPageNo)
    ? state.evaluationAssessmentPageNo
    : (state.evaluationAssessment.pending_review_pages?.[0] || pages[0]);
  byId('evaluation-assessment-workspace').hidden = false;
  renderAssessmentSummary();
  renderAssessmentPageIndex();
  updateEvaluationEmptyState();
  if (desired) await loadAssessmentPage(desired);
}

async function loadAssessmentPage(pageNo) {
  if (!state.evaluationAssessment) return;
  const number = Number(pageNo);
  if (!state.evaluationAssessment.page_numbers.includes(number)) return;
  state.evaluationAssessmentPageNo = number;
  state.evaluationAssessmentPage = await api(`/evaluations/assessments/${encodeURIComponent(state.evaluationAssessment.assessment_id)}/pages/${number}`);
  byId('evaluation-assessment-page-number').value = number;
  byId('evaluation-assessment-source-image').src = state.evaluationAssessmentPage.source_image_url;
  byId('evaluation-assessment-source-image').alt = `来源第 ${number} 页`;
  byId('evaluation-assessment-content').textContent = state.evaluationAssessmentPage.snapshot?.markdown || '';
  byId('evaluation-assessment-checks').textContent = JSON.stringify(state.evaluationAssessmentPage.checks, null, 2);
  byId('evaluation-assessment-page-status').textContent = state.evaluationAssessmentPage.automatic_status === 'passed' ? '自动检查通过' : '自动检查失败';
  const review = latestReviews(state.evaluationAssessment)[number];
  const reviewRequired = state.evaluationAssessment.review_page_numbers.includes(number);
  byId('evaluation-assessment-verdict').value = review?.verdict || 'needs_review';
  byId('evaluation-assessment-reason').value = review?.reason || '';
  renderAssessmentScores(review, reviewRequired);
  byId('evaluation-assessment-verdict').disabled = !reviewRequired;
  byId('evaluation-assessment-reason').disabled = !reviewRequired;
  byId('evaluation-assessment-review-form').querySelector('button[type="submit"]').disabled = !reviewRequired;
  renderAssessmentPageIndex();
  applyAssessmentZoom();
}

function renderAssessmentScores(review, reviewRequired) {
  const formal = state.evaluationAssessment.profile === 'formal_scorecard';
  const host = byId('evaluation-assessment-scores');
  const critical = byId('evaluation-assessment-critical-errors');
  host.hidden = !formal;
  critical.hidden = !formal;
  host.innerHTML = '';
  if (!formal) return;
  const contract = state.evaluationAssessment.page_contracts?.[String(state.evaluationAssessmentPageNo)];
  (contract?.dimensions || []).forEach((dimension) => {
    const label = document.createElement('label'); label.className = 'assessment-score';
    const select = document.createElement('select'); select.dataset.scoreDimension = dimension;
    [0, 1, 2].forEach((score) => {
      const option = document.createElement('option'); option.value = score; option.textContent = score; select.appendChild(option);
    });
    select.value = String(review?.scores?.[dimension] ?? 1);
    select.disabled = !reviewRequired;
    label.append(document.createTextNode(dimension), select); host.appendChild(label);
  });
  critical.value = (review?.critical_errors || []).join('；');
  critical.disabled = !reviewRequired;
}

function assessmentReviewPayload() {
  const payload = {
    page_no:state.evaluationAssessmentPageNo,
    verdict:byId('evaluation-assessment-verdict').value,
    reason:byId('evaluation-assessment-reason').value,
    reviewer:'web-local-reviewer',
  };
  if (state.evaluationAssessment.profile === 'formal_scorecard') {
    payload.scores = Object.fromEntries(
      Array.from(document.querySelectorAll('[data-score-dimension]')).map((item) => [item.dataset.scoreDimension, Number(item.value)]),
    );
    payload.critical_errors = byId('evaluation-assessment-critical-errors').value
      .split(/[;；]/).map((item) => item.trim()).filter(Boolean);
  }
  return payload;
}

function renderAssessmentSummary() {
  const item = state.evaluationAssessment;
  if (!item) return;
  byId('evaluation-assessment-summary').innerHTML = [
    `<span><strong>Profile</strong>${escapeHtml(item.profile)}</span>`,
    `<span><strong>执行</strong>${escapeHtml(item.execution_status)}</span>`,
    `<span><strong>质量</strong>${escapeHtml(item.quality_status)}</span>`,
    `<span><strong>待审</strong>${item.pending_review_pages?.length || 0} 页</span>`,
  ].join('');
}

async function loadEvaluationComparison(comparisonId) {
  state.evaluationComparison = await api(`/evaluations/comparisons/${encodeURIComponent(comparisonId)}`);
  const pages = state.evaluationComparison.page_numbers;
  const desired = pages.includes(state.evaluationComparisonPageNo) ? state.evaluationComparisonPageNo : (state.evaluationComparison.changed_pages[0] || pages[0]);
  byId('evaluation-comparison-workspace').hidden = false;
  renderComparisonSummary();
  renderComparisonPageIndex();
  updateEvaluationEmptyState();
  if (desired) await loadComparisonPage(desired);
}

async function loadComparisonPage(pageNo) {
  if (!state.evaluationComparison) return;
  const number = Number(pageNo);
  if (!state.evaluationComparison.page_numbers.includes(number)) return;
  state.evaluationComparisonPageNo = number;
  state.evaluationComparisonPage = await api(`/evaluations/comparisons/${encodeURIComponent(state.evaluationComparison.comparison_id)}/pages/${number}`);
  const page = state.evaluationComparisonPage;
  byId('evaluation-comparison-page-number').value = number;
  byId('evaluation-comparison-source-image').src = page.source_image_url;
  byId('evaluation-comparison-baseline-image').src = page.baseline_image_url;
  byId('evaluation-comparison-candidate-image').src = page.candidate_image_url;
  byId('evaluation-comparison-source-image').alt = `来源第 ${number} 页`;
  byId('evaluation-comparison-baseline-image').alt = `基线第 ${number} 页`;
  byId('evaluation-comparison-candidate-image').alt = `候选第 ${number} 页`;
  byId('evaluation-comparison-baseline-content').textContent = page.baseline?.markdown || '';
  byId('evaluation-comparison-candidate-content').textContent = page.candidate?.markdown || '';
  byId('evaluation-comparison-diff-content').textContent = JSON.stringify(page.dimensions, null, 2);
  byId('evaluation-comparison-page-status').textContent = page.changed ? `变化：${page.changed_dimensions.join(' / ')}` : '未检测到变化';
  const review = latestReviews(state.evaluationComparison)[number];
  byId('evaluation-comparison-verdict').value = review?.verdict || 'needs_review';
  byId('evaluation-comparison-reason').value = review?.reason || '';
  renderComparisonPageIndex(); applyComparisonZoom();
}

function renderComparisonSummary() {
  const item = state.evaluationComparison;
  if (!item) return;
  byId('evaluation-comparison-summary').innerHTML = [
    `<span><strong>结论</strong>${escapeHtml(item.conclusion)}</span>`,
    `<span><strong>变化</strong>${item.changed_pages.length} 页</span>`,
    `<span><strong>待审</strong>${item.pending_review_pages.length} 页</span>`,
  ].join('');
}

function applyAssessmentZoom() {
  const image = byId('evaluation-assessment-source-image');
  image.classList.toggle('fit', state.evaluationAssessmentFitWidth);
  image.style.width = state.evaluationAssessmentFitWidth ? '100%' : `${Math.round(state.evaluationAssessmentZoom * 100)}%`;
  byId('evaluation-assessment-zoom-level').textContent = state.evaluationAssessmentFitWidth ? '适宽' : `${Math.round(state.evaluationAssessmentZoom * 100)}%`;
}

function applyComparisonZoom() {
  document.querySelectorAll('#evaluation-comparison-workspace .evaluation-media img').forEach((image) => {
    image.classList.toggle('fit', state.evaluationComparisonFitWidth);
    image.style.width = state.evaluationComparisonFitWidth ? '100%' : `${Math.round(state.evaluationComparisonZoom * 100)}%`;
  });
  byId('evaluation-comparison-zoom-level').textContent = state.evaluationComparisonFitWidth ? '适宽' : `${Math.round(state.evaluationComparisonZoom * 100)}%`;
}

function showEvaluationPane(mode, pane) {
  const dataName = mode === 'assessment' ? 'assessmentPane' : 'comparisonPane';
  state[mode === 'assessment' ? 'evaluationAssessmentPane' : 'evaluationComparisonPane'] = pane;
  document.querySelectorAll(`[data-${mode}-pane]`).forEach((item) => item.classList.toggle('active', item.dataset[dataName] === pane));
}

function setEvaluationMode(mode) {
  state.evaluationMode = mode;
  document.querySelectorAll('.evaluation-mode').forEach((button) => button.classList.toggle('active', button.dataset.evaluationMode === mode));
  byId('evaluation-assessment-selectors').hidden = mode !== 'assessment';
  byId('evaluation-comparison-selectors').hidden = mode !== 'comparison';
  byId('evaluation-assessment-workspace').hidden = mode !== 'assessment' || !state.evaluationAssessment;
  byId('evaluation-comparison-workspace').hidden = mode !== 'comparison' || !state.evaluationComparison;
  updateEvaluationEmptyState();
}

function updateEvaluationEmptyState() {
  const active = state.evaluationMode === 'assessment' ? state.evaluationAssessment : state.evaluationComparison;
  const available = state.evaluationMode === 'assessment'
    ? (state.evaluationCase?.assessments?.length || 0)
    : (state.evaluationCase?.snapshots?.length || 0);
  byId('evaluation-empty').hidden = Boolean(active);
  byId('evaluation-empty').textContent = state.evaluationMode === 'assessment'
    ? (available ? '请选择并打开一个单运行评估' : '当前文档尚无单运行评估')
    : (available >= 2 ? '请选择基线与候选运行' : '当前文档尚无可比较的快照');
}

async function refreshAll() {
  await Promise.all([refreshPages(), refreshKnowledge(), refreshWorkbook()]);
}

function showJob(job) {
  state.activeJobId = job.id;
  byId('job-panel').hidden = false;
  byId('job-kind').textContent = job.kind === 'parse_document' ? '文档解析' : '知识抽取';
  byId('job-state').textContent = `${job.status} · 尝试 ${job.attempt}/${job.max_attempts}`;
  byId('job-progress').max = Math.max(job.progress_total, 1);
  byId('job-progress').value = job.progress_current;
  byId('cancel-job-button').hidden = !['queued', 'running', 'cancel_requested'].includes(job.status);
}

async function pollJob() {
  if (!state.activeJobId) return;
  try {
    const job = await api(`/jobs/${state.activeJobId}`); showJob(job);
    if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
      clearInterval(state.pollTimer); state.pollTimer = null;
      notice(job.status === 'succeeded' ? '后台任务已完成' : (job.error?.message || `任务状态：${job.status}`), job.status !== 'succeeded');
      await refreshAll(); await refreshDocuments();
    }
  } catch (error) { notice(error.message, true); }
}

function beginPolling(job) {
  showJob(job);
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(pollJob, 1000);
  pollJob();
}

async function restoreActiveJob() {
  if (!state.documentId) return;
  const jobs = await api(`/jobs?document_id=${selected()}`);
  const active = jobs.find((job) => ['queued', 'running', 'cancel_requested'].includes(job.status));
  if (active) beginPolling(active);
}

byId('upload-form').onsubmit = async (event) => {
  event.preventDefault();
  try {
    const data = new FormData(); data.append('file', byId('pdf-file').files[0]);
    const item = await api('/documents', {method:'POST', body:data}); state.documentId = item.id; state.documentPageCount = item.page_count;
    byId('document-status').textContent = `${item.filename} · ${item.status}`; notice('文档已导入'); await refreshDocuments();
  } catch (error) { notice(error.message, true); }
};
byId('parse-button').onclick = () => {
  try {
    selected(); byId('parse-start').value = state.currentRun?.page_start || 1;
    byId('parse-end').value = state.currentRun?.page_end || Math.min(state.documentPageCount, 20);
    byId('parse-budget').textContent = ''; byId('parse-dialog').showModal();
  } catch (error) { notice(error.message, true); }
};
byId('parse-form').onsubmit = async (event) => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  const pageStart = Number(byId('parse-start').value), pageEnd = Number(byId('parse-end').value);
  try {
    const command = await api(`/documents/${selected()}/parse-jobs`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({page_start:pageStart, page_end:pageEnd})});
    byId('parse-dialog').close(); notice(command.created ? '解析任务已入队' : '已有相同解析任务'); beginPolling(command.job);
  } catch (error) { notice(error.message, true); }
};
byId('extract-button').onclick = async () => { try { const command = await api(`/documents/${selected()}/extraction-jobs`, {method:'POST'}); notice(command.created ? '知识抽取任务已入队' : '已有相同抽取任务'); beginPolling(command.job); } catch (error) { notice(error.message, true); } };
byId('cancel-job-button').onclick = async () => { try { showJob(await api(`/jobs/${state.activeJobId}/cancel`, {method:'POST'})); notice('已提交取消请求'); } catch (error) { notice(error.message, true); } };
byId('export-button').onclick = async () => { try { const revision = await api(`/documents/${selected()}/workbook-exports`, {method:'POST'}); byId('workbook-detail').textContent = `草稿版本：${revision.id}`; byId('download-workbook').hidden = false; } catch (error) { notice(error.message, true); } };
byId('workbook-form').onsubmit = async (event) => { event.preventDefault(); try { const data = new FormData(); data.append('file', byId('workbook-file').files[0]); const revision = await api(`/documents/${selected()}/workbook-imports`, {method:'POST', body:data}); byId('workbook-detail').textContent = `已导入并校验草稿：${revision.id}`; notice('工作簿校验通过'); } catch (error) { notice(error.message, true); } };
byId('publish-button').onclick = async () => { try { const release = await api(`/documents/${selected()}/releases`, {method:'POST'}); byId('workbook-detail').textContent = `已发布版本：${release.id}`; notice('知识快照已发布'); await refreshAll(); await refreshDocuments(); } catch (error) { notice(error.message, true); } };
byId('graph-button').onclick = refreshGraph;
byId('evaluation-document').onchange = async () => {
  state.evaluationAssessment = null;
  state.evaluationAssessmentPage = null;
  state.evaluationAssessmentPageNo = null;
  state.evaluationComparison = null;
  state.evaluationComparisonPage = null;
  state.evaluationComparisonPageNo = null;
  byId('evaluation-assessment-workspace').hidden = true;
  byId('evaluation-comparison-workspace').hidden = true;
  try { await loadEvaluationCase(byId('evaluation-document').value); } catch (error) { notice(error.message, true); }
};
document.querySelectorAll('.evaluation-mode').forEach((button) => {
  button.onclick = () => setEvaluationMode(button.dataset.evaluationMode);
});
byId('evaluation-open-assessment').onclick = async () => {
  try {
    await loadEvaluationAssessment(byId('evaluation-assessment').value);
    notice('单运行评估已打开');
  } catch (error) { notice(error.message, true); }
};
byId('evaluation-baseline').onchange = () => {
  if (byId('evaluation-candidate').value === byId('evaluation-baseline').value) {
    const replacement = (state.evaluationCase?.snapshots || []).find((item) => item.snapshot_id !== byId('evaluation-baseline').value);
    byId('evaluation-candidate').value = replacement?.snapshot_id || '';
  }
};
byId('evaluation-compare').onclick = async () => {
  try {
    const baselineId = byId('evaluation-baseline').value;
    const candidateId = byId('evaluation-candidate').value;
    if (baselineId === candidateId) throw new Error('基线与候选必须选择不同快照');
    const existing = (state.evaluationCase.comparisons || []).find((item) => (
      item.baseline_snapshot_id === baselineId && item.candidate_snapshot_id === candidateId
    ));
    const comparison = existing || await api(`/evaluations/documents/${encodeURIComponent(state.evaluationCaseId)}/comparisons`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({baseline_snapshot_id:baselineId, candidate_snapshot_id:candidateId}),
    });
    await loadEvaluationComparison(comparison.comparison_id); notice('评测对照已生成');
  } catch (error) { notice(error.message, true); }
};
byId('evaluation-assessment-review-form').onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api(`/evaluations/assessments/${encodeURIComponent(state.evaluationAssessment.assessment_id)}/reviews`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(assessmentReviewPayload()),
    });
    await loadEvaluationAssessment(state.evaluationAssessment.assessment_id); notice('绝对质量结论已保存');
  } catch (error) { notice(error.message, true); }
};
byId('evaluation-assessment-report').onclick = async () => {
  try {
    const report = await api(`/evaluations/assessments/${encodeURIComponent(state.evaluationAssessment.assessment_id)}/reports`, {method:'POST'});
    notice(`单运行报告已生成：${report.quality_status}`);
  } catch (error) { notice(error.message, true); }
};
byId('evaluation-comparison-review-form').onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api(`/evaluations/comparisons/${encodeURIComponent(state.evaluationComparison.comparison_id)}/reviews`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        page_no:state.evaluationComparisonPageNo, verdict:byId('evaluation-comparison-verdict').value,
        reason:byId('evaluation-comparison-reason').value, reviewer:'web-local-reviewer',
      }),
    });
    await loadEvaluationComparison(state.evaluationComparison.comparison_id); notice('相对质量结论已保存');
  } catch (error) { notice(error.message, true); }
};
byId('evaluation-comparison-report').onclick = async () => {
  try {
    const report = await api(`/evaluations/comparisons/${encodeURIComponent(state.evaluationComparison.comparison_id)}/reports`, {method:'POST'});
    notice(`版本对比报告已生成：${report.conclusion}`);
  } catch (error) { notice(error.message, true); }
};
function moveEvaluationPage(mode, offset) {
  const resource = mode === 'assessment' ? state.evaluationAssessment : state.evaluationComparison;
  const current = mode === 'assessment' ? state.evaluationAssessmentPageNo : state.evaluationComparisonPageNo;
  const pages = resource?.page_numbers || [];
  const index = pages.indexOf(current);
  const target = pages[index + offset];
  if (target) (mode === 'assessment' ? loadAssessmentPage : loadComparisonPage)(target);
}
byId('evaluation-assessment-page-number').onchange = () => loadAssessmentPage(Number(byId('evaluation-assessment-page-number').value));
byId('evaluation-assessment-previous').onclick = () => moveEvaluationPage('assessment', -1);
byId('evaluation-assessment-next').onclick = () => moveEvaluationPage('assessment', 1);
byId('evaluation-assessment-fit').onclick = () => { state.evaluationAssessmentFitWidth = true; applyAssessmentZoom(); };
byId('evaluation-assessment-zoom-out').onclick = () => { state.evaluationAssessmentFitWidth = false; state.evaluationAssessmentZoom = Math.max(0.5, state.evaluationAssessmentZoom - 0.25); applyAssessmentZoom(); };
byId('evaluation-assessment-zoom-in').onclick = () => { state.evaluationAssessmentFitWidth = false; state.evaluationAssessmentZoom = Math.min(3, state.evaluationAssessmentZoom + 0.25); applyAssessmentZoom(); };
byId('evaluation-comparison-page-number').onchange = () => loadComparisonPage(Number(byId('evaluation-comparison-page-number').value));
byId('evaluation-comparison-previous').onclick = () => moveEvaluationPage('comparison', -1);
byId('evaluation-comparison-next').onclick = () => moveEvaluationPage('comparison', 1);
byId('evaluation-comparison-fit').onclick = () => { state.evaluationComparisonFitWidth = true; applyComparisonZoom(); };
byId('evaluation-comparison-zoom-out').onclick = () => { state.evaluationComparisonFitWidth = false; state.evaluationComparisonZoom = Math.max(0.5, state.evaluationComparisonZoom - 0.25); applyComparisonZoom(); };
byId('evaluation-comparison-zoom-in').onclick = () => { state.evaluationComparisonFitWidth = false; state.evaluationComparisonZoom = Math.min(3, state.evaluationComparisonZoom + 0.25); applyComparisonZoom(); };
document.querySelectorAll('.evaluation-assessment-segment').forEach((button) => { button.onclick = () => showEvaluationPane('assessment', button.dataset.assessmentPane); });
document.querySelectorAll('.evaluation-comparison-segment').forEach((button) => { button.onclick = () => showEvaluationPane('comparison', button.dataset.comparisonPane); });
byId('history-button').onclick = () => { byId('history-drawer').hidden = false; };
byId('close-history').onclick = () => { byId('history-drawer').hidden = true; };
byId('page-filter').oninput = renderPageIndex;
byId('issues-only').onchange = renderPageIndex;
byId('page-number').onchange = () => loadPage(Number(byId('page-number').value));
byId('previous-page').onclick = () => {
  const index = state.pageIndex.findIndex((item) => item.page_no === state.pageNo);
  if (index > 0) loadPage(state.pageIndex[index - 1].page_no);
};
byId('next-page').onclick = () => {
  const index = state.pageIndex.findIndex((item) => item.page_no === state.pageNo);
  if (index >= 0 && index < state.pageIndex.length - 1) loadPage(state.pageIndex[index + 1].page_no);
};
byId('fit-width').onclick = () => { state.fitWidth = true; applyZoom(); };
byId('zoom-out').onclick = () => { state.fitWidth = false; state.zoom = Math.max(0.5, state.zoom - 0.25); applyZoom(); };
byId('zoom-in').onclick = () => { state.fitWidth = false; state.zoom = Math.min(3, state.zoom + 0.25); applyZoom(); };
document.querySelectorAll('.content-tab').forEach((tab) => {
  tab.onclick = () => { state.contentMode = tab.dataset.content; renderOcrContent(); };
});
[byId('parse-start'), byId('parse-end')].forEach((input) => {
  input.oninput = () => {
    const count = Math.max(0, Number(byId('parse-end').value) - Number(byId('parse-start').value) + 1);
    byId('parse-budget').textContent = `本次最多 ${count * 3} 次模型调用`;
  };
});
document.addEventListener('keydown', (event) => {
  if (!byId('pages-view').classList.contains('active') || ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
  if (event.key === 'ArrowLeft') byId('previous-page').click();
  if (event.key === 'ArrowRight') byId('next-page').click();
});
document.querySelectorAll('.tab').forEach((tab) => tab.onclick = async () => {
  document.querySelectorAll('.tab,.view').forEach((item) => item.classList.remove('active'));
  tab.classList.add('active'); byId(`${tab.dataset.view}-view`).classList.add('active');
  if (tab.dataset.view === 'evaluation') {
    try { await refreshEvaluationDocuments(); } catch (error) { notice(error.message, true); }
  }
});
refreshDocuments().catch((error) => notice(error.message, true));
