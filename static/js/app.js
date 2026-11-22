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
document.addEventListener('DOMContentLoaded', () => formatDates());

document.getElementById('langBtn')?.addEventListener('click', e => {
  e.stopPropagation();
  document.getElementById('langMenu').classList.toggle('show');
});
document.addEventListener('click', () => document.getElementById('langMenu')?.classList.remove('show'));
