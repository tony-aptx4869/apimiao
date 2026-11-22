// 通用前端工具：API 请求、转义、UTC→本地时间显示、语言菜单
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

async function apiFetch(url, method = 'GET', body) {
  const opts = {method, headers: {'Content-Type': 'application/json'}};
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(url, opts);
  if (resp.status === 401 || (resp.redirected && resp.url.includes('/login'))) {
    location.href = '/login';
    return {ok: false, error: 'Unauthorized'};
  }
  let data;
  try { data = await resp.json(); }
  catch (e) { return {ok: false, error: 'Invalid response'}; }
  if (!resp.ok && data && data.ok !== false) data.ok = false;
  return data;
}
function apiPost(url, body) { return apiFetch(url, 'POST', body); }
function apiDelete(url, body) { return apiFetch(url, 'DELETE', body); }
function apiGet(url) { return apiFetch(url, 'GET'); }

// 库里统一 UTC（...Z），这里用浏览器本地时区渲染
function formatDates(root) {
  (root || document).querySelectorAll('.date-cell[data-utc]').forEach(el => {
    const iso = el.dataset.utc;
    if (!iso) return;
    const d = new Date(iso.endsWith('Z') ? iso : iso.replace(' ', 'T') + 'Z');
    if (isNaN(d)) { el.textContent = iso; return; }
    const p = n => String(n).padStart(2, '0');
    el.textContent = d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  });
}
function observeDates() {
  const observer = new MutationObserver(mutations => {
    const hasDateCell = mutations.some(mutation =>
      Array.from(mutation.addedNodes).some(node =>
        node.nodeType === 1 &&
        (node.matches('.date-cell[data-utc]') || node.querySelector('.date-cell[data-utc]'))
      )
    );
    if (hasDateCell) formatDates(document);
  });
  observer.observe(document.body, {childList: true, subtree: true});
}
document.addEventListener('DOMContentLoaded', () => { formatDates(); observeDates(); normalizeAuthCells(); });

// ---------- 居中弹窗：uiAlert / uiConfirm / uiPrompt ----------
// 单一可复用遮罩，DOM 动态构建。Escape / 点击遮罩 = 取消；Promise 只结算一次。
const AUTH_LABELS = {
  'x-api-key,bearer': 'X-API-Key + Bearer',
  'x-api-key': 'X-API-Key',
  'bearer': 'Bearer'
};
function authLabel(value) {
  return AUTH_LABELS[String(value == null ? '' : value).toLowerCase()] || '—';
}
function normalizeAuthCells(root) {
  (root || document).querySelectorAll('.auth-cell[data-auth]').forEach(el => {
    el.textContent = authLabel(el.dataset.auth);
  });
}

let _uiModalState = null;
function _uiModal(mode, message, defaultValue = '', title) {
  const zh = window.LANG_ZH === true;
  const isPrompt = mode === 'prompt';
  const isAlert = mode === 'alert';
  return new Promise(resolve => {
    if (_uiModalState) _uiModalState.close();
    const prevFocus = document.activeElement;
    const overlay = document.createElement('div');
    overlay.className = 'ui-modal-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    const box = document.createElement('div');
    box.className = 'ui-modal-box';
    const heading = document.createElement('h3');
    heading.className = 'ui-modal-title';
    heading.textContent = title || (zh
      ? (isPrompt ? '输入' : isAlert ? '提示' : '确认')
      : (isPrompt ? 'Input' : isAlert ? 'Notice' : 'Confirm'));
    const text = document.createElement('p');
    text.className = 'ui-modal-message';
    text.textContent = message == null ? '' : String(message);
    const field = isPrompt ? document.createElement('input') : null;
    if (field) {
      field.type = 'text';
      field.className = 'ui-modal-input';
      field.value = defaultValue == null ? '' : String(defaultValue);
    }
    const actions = document.createElement('div');
    actions.className = 'ui-modal-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-outline';
    cancel.textContent = zh ? '取消' : 'Cancel';
    const ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'btn btn-primary';
    ok.textContent = zh ? '确定' : 'OK';
    if (isAlert) actions.appendChild(ok);
    else actions.append(cancel, ok);
    box.append(heading, text);
    if (field) box.appendChild(field);
    box.appendChild(actions);
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    let settled = false;
    const cancelValue = isPrompt ? null : (isAlert ? undefined : false);
    const cleanup = () => {
      document.removeEventListener('keydown', onKey);
      overlay.removeEventListener('mousedown', onBackdrop);
      cancel.removeEventListener('click', onCancel);
      ok.removeEventListener('click', onOk);
      if (field) field.removeEventListener('keydown', onFieldKey);
      overlay.remove();
      _uiModalState = null;
      if (prevFocus && document.contains(prevFocus)) prevFocus.focus();
    };
    const settle = value => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(value);
    };
    const onCancel = () => settle(cancelValue);
    const onOk = () => settle(isPrompt ? field.value : (isAlert ? undefined : true));
    const onKey = e => { if (e.key === 'Escape') { e.preventDefault(); settle(cancelValue); } };
    const onBackdrop = e => { if (e.target === overlay) settle(cancelValue); };
    const onFieldKey = e => { if (e.key === 'Enter') { e.preventDefault(); onOk(); } };
    cancel.addEventListener('click', onCancel);
    ok.addEventListener('click', onOk);
    document.addEventListener('keydown', onKey);
    overlay.addEventListener('mousedown', onBackdrop);
    if (field) { field.addEventListener('keydown', onFieldKey); field.focus(); field.select(); }
    else ok.focus();
    _uiModalState = { close: () => settle(cancelValue) };
  });
}
function uiAlert(message, title) { return _uiModal('alert', message, '', title); }
function uiConfirm(message, title) { return _uiModal('confirm', message, '', title); }
function uiPrompt(message, defaultValue, title) { return _uiModal('prompt', message, defaultValue, title); }

document.getElementById('langBtn')?.addEventListener('click', e => {
  e.stopPropagation();
  document.getElementById('langMenu').classList.toggle('show');
});
document.addEventListener('click', () => document.getElementById('langMenu')?.classList.remove('show'));
