/*
模块职责：驱动 v0.3 工作台的任务轮询、审阅命令和发布操作。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/test_v03_api.py
*/

const state = {
  documentId: null, documentPageCount: 0, activeJobId: null, pollTimer: null,
  currentRun: null, runs: [], pageIndex: [], pageNo: null, page: null,
  artifacts: [], contentMode: 'readable', zoom: 1, fitWidth: true,
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
document.querySelectorAll('.tab').forEach((tab) => tab.onclick = () => { document.querySelectorAll('.tab,.view').forEach((item) => item.classList.remove('active')); tab.classList.add('active'); byId(`${tab.dataset.view}-view`).classList.add('active'); });
refreshDocuments().catch((error) => notice(error.message, true));
