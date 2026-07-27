/*
模块职责：驱动 v0.2 工作台查询、审阅命令和发布操作。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/test_v02_pipeline.py
*/

const state = { documentId: null };
const byId = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `请求失败 (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function notice(message, error = false) {
  const target = byId('notice'); target.textContent = message; target.className = error ? 'notice error' : 'notice';
}

function selected() { if (!state.documentId) { throw new Error('请先在文档库选择一个文档'); } return state.documentId; }

async function refreshDocuments() {
  const documents = await api('/documents'); const list = byId('document-list'); list.innerHTML = '';
  documents.forEach((document) => {
    const button = document.createElement('button'); button.className = `document-item ${document.id === state.documentId ? 'active' : ''}`;
    button.innerHTML = `${escapeHtml(document.filename)}<small>${document.page_count} 页 · ${escapeHtml(document.status)}</small>`;
    button.onclick = async () => { state.documentId = document.id; byId('document-status').textContent = `${document.filename} · ${document.status}`; await refreshDocuments(); await refreshAll(); };
    list.appendChild(button);
  });
  if (!state.documentId && documents[0]) { state.documentId = documents[0].id; byId('document-status').textContent = `${documents[0].filename} · ${documents[0].status}`; await refreshDocuments(); await refreshAll(); }
}

const escapeHtml = (value) => String(value || '').replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char]));
function evidenceText(evidence) { return evidence.map((item) => `p.${item.page_no}: ${item.quote || ''}`).join('\n'); }
function reviewControls(target, endpoint, includeReparse = false) {
  const wrap = document.createElement('div'); wrap.className = 'review-actions';
  const reason = document.createElement('input'); reason.placeholder = '审阅原因（可选）'; wrap.appendChild(reason);
  ['accepted', 'rejected'].concat(includeReparse ? ['reparse_requested'] : []).forEach((status) => {
    const button = document.createElement('button'); button.textContent = status === 'accepted' ? '接受' : status === 'rejected' ? '拒绝' : '请求重解析'; if (status === 'rejected') button.className = 'reject';
    button.onclick = async () => { try { await api(endpoint, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status, reason:reason.value}) }); notice('审阅结论已保存'); await refreshAll(); } catch (error) { notice(error.message, true); } };
    wrap.appendChild(button);
  }); return wrap;
}

async function refreshPages() {
  if (!state.documentId) return; const pages = await api(`/documents/${selected()}/pages`); const host = byId('pages'); host.innerHTML = '';
  pages.forEach((page) => { const item = document.createElement('article'); item.className = 'page'; const image = document.createElement('img'); image.src = page.image_url; image.alt = `第 ${page.page_no} 页`; item.appendChild(image);
    const content = document.createElement('div'); content.className = 'page-content'; content.innerHTML = `<h2>第 ${page.page_no} 页 · ${escapeHtml(page.page_kind)} · ${escapeHtml(page.review_status)}</h2><div class="issues">${escapeHtml((page.quality.issues || []).join('；'))}</div><pre>${escapeHtml(page.markdown)}</pre>`; content.appendChild(reviewControls(page, `/pages/${page.id}/review`, true)); item.appendChild(content); host.appendChild(item); });
}

async function refreshKnowledge() {
  if (!state.documentId) return; const [nodes, edges] = await Promise.all([api(`/documents/${selected()}/candidates`), api(`/documents/${selected()}/edges`)]); const candidates = byId('candidates'); candidates.innerHTML = '';
  nodes.forEach((node) => { const item = document.createElement('article'); item.className='candidate'; item.innerHTML=`<h2>${escapeHtml(node.title)} <span class="meta">${escapeHtml(node.kind)} · ${escapeHtml(node.review_status)}</span></h2><p>${escapeHtml(node.content)}</p><p class="meta">${escapeHtml(evidenceText(node.evidence))}</p>`; item.appendChild(reviewControls(node, `/candidates/${node.id}/review`)); candidates.appendChild(item); });
  const edgeHost = byId('edges'); edgeHost.innerHTML = ''; const titles = Object.fromEntries(nodes.map((node) => [node.id, node.title]));
  edges.forEach((edge) => { const item = document.createElement('article'); item.className='edge'; item.innerHTML=`<strong>${escapeHtml(titles[edge.source_id] || edge.source_id)}</strong> → ${escapeHtml(edge.relation)} → <strong>${escapeHtml(titles[edge.target_id] || edge.target_id)}</strong> <span class="meta">${escapeHtml(edge.review_status)}</span><p class="meta">${escapeHtml(evidenceText(edge.evidence))}</p>`; item.appendChild(reviewControls(edge, `/edges/${edge.id}/review`)); edgeHost.appendChild(item); });
}

async function refreshWorkbook() { if (!state.documentId) return; byId('download-workbook').href = `/api/documents/${selected()}/workbook/download`; }
async function refreshGraph() { if (!state.documentId) return; const graph = byId('graph'); try { const data = await api(`/documents/${selected()}/graph`); const titles = Object.fromEntries(data.nodes.map((node) => [node.id, node.title])); graph.innerHTML = data.nodes.map((node) => `<span class="graph-node">${escapeHtml(node.title)}</span>`).join('') + data.edges.map((edge) => `<span class="graph-edge">${escapeHtml(titles[edge.source_id])} → ${escapeHtml(edge.relation)} → ${escapeHtml(titles[edge.target_id])}</span>`).join(''); } catch (error) { graph.textContent = error.message; } }
async function refreshAll() { await Promise.all([refreshPages(), refreshKnowledge(), refreshWorkbook(), refreshGraph()]); }

byId('upload-form').onsubmit = async (event) => { event.preventDefault(); try { const data = new FormData(); data.append('file', byId('pdf-file').files[0]); const document = await api('/documents', {method:'POST', body:data}); state.documentId=document.id; byId('document-status').textContent=`${document.filename} · ${document.status}`; notice('文档已导入'); await refreshDocuments(); } catch (error) { notice(error.message, true); } };
byId('parse-button').onclick = async () => { try { notice('正在解析页面...'); await api(`/documents/${selected()}/parse`, {method:'POST'}); notice('解析完成，等待页面审阅'); await refreshAll(); await refreshDocuments(); } catch (error) { notice(error.message, true); } };
byId('extract-button').onclick = async () => { try { await api(`/documents/${selected()}/candidates`, {method:'POST'}); notice('知识候选已生成，等待逐项审阅'); await refreshAll(); } catch (error) { notice(error.message, true); } };
byId('export-button').onclick = async () => { try { const revision=await api(`/documents/${selected()}/workbook/export`, {method:'POST'}); byId('workbook-detail').textContent=`草稿版本：${revision.id}\n已导出可下载工作簿。`; byId('download-workbook').hidden=false; } catch (error) { notice(error.message, true); } };
byId('workbook-form').onsubmit = async (event) => { event.preventDefault(); try { const data=new FormData(); data.append('file', byId('workbook-file').files[0]); const revision=await api(`/documents/${selected()}/workbook/import`, {method:'POST',body:data}); byId('workbook-detail').textContent=`已导入并校验草稿：${revision.id}`; notice('工作簿校验通过，现可显式发布'); } catch (error) { notice(error.message, true); } };
byId('publish-button').onclick = async () => { try { const release=await api(`/documents/${selected()}/publish`, {method:'POST'}); byId('workbook-detail').textContent=`已发布版本：${release.id}`; notice('知识快照已发布'); await refreshAll(); await refreshDocuments(); } catch (error) { notice(error.message, true); } };
byId('graph-button').onclick = refreshGraph;
document.querySelectorAll('.tab').forEach((tab) => tab.onclick = () => { document.querySelectorAll('.tab,.view').forEach((item) => item.classList.remove('active')); tab.classList.add('active'); byId(`${tab.dataset.view}-view`).classList.add('active'); });
refreshDocuments().catch((error) => notice(error.message, true));
