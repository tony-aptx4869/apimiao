// PROXY API 创建/编辑表单：动态键值、Body 模式
const $ = id => document.getElementById(id);
const I18N = { noBody: { zh: '此请求没有 body。', en: 'No body for this request.' } };
const tr = key => (I18N[key] && I18N[key][LANG]) || (I18N[key] && I18N[key].en) || key;

// 复制 {{o-api-key}} 引用
$('copyOApiKey')?.addEventListener('click', async () => {
  const text = '{{o-api-key}}';
  try { await navigator.clipboard.writeText(text); }
  catch (e) { const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
  const btn = $('copyOApiKey'), old = btn.innerHTML;
  btn.innerHTML = '<i class="bi bi-check-lg"></i>';
  setTimeout(() => { btn.innerHTML = old; }, 1200);
});

// ---------- 键值对增删 ----------
function kvRow(name = '', value = '') {
  const div = document.createElement('div');
  div.className = 'kv-row';
  div.innerHTML = '<input class="kv-k" placeholder="key" value="' + esc(name) + '">' +
                  '<input class="kv-v" placeholder="value" value="' + esc(value) + '">' +
                  '<button class="btn btn-xs btn-danger kv-del" type="button"><i class="bi bi-x"></i></button>';
  div.querySelector('.kv-del').onclick = () => div.remove();
  return div;
}
function loadKV(field, data) {
  const box = $('kv-' + field);
  if (!box) return;
  box.innerHTML = '';
  (data || []).forEach(p => box.appendChild(kvRow(p.key, p.value)));
  if (!box.children.length) box.appendChild(kvRow());
}
document.querySelectorAll('[data-add]').forEach(btn => {
  btn.onclick = () => $('kv-' + btn.dataset.add).appendChild(kvRow());
});
['params', 'headers', 'cookies'].forEach(f => loadKV(f, INITIAL[f]));

// ---------- Body 模式 ----------
function updateBodyMode() {
  const mode = $('body_mode').value;
  const hint = $('bodyHint');
  const editor = $('codeEditor');
  if (mode === 'none') { editor.style.display = 'none'; if (hint) hint.textContent = tr('noBody'); }
  else if (mode === 'binary') { editor.style.display = 'none'; if (hint) hint.textContent = '二进制 body 由调用方请求内容转发。'; }
  else { editor.style.display = 'flex'; if (hint) hint.textContent = ''; updateGutter(); updateActiveLine(); }
  const fmt = $('formatJsonBtn');
  if (fmt) fmt.style.display = mode === 'json' ? '' : 'none';
}
$('body_mode').onchange = updateBodyMode;
$('formatJsonBtn')?.addEventListener('click', () => {
  try { $('body').value = JSON.stringify(JSON.parse($('body').value), null, 2); updateGutter(); updateActiveLine(); }
  catch (e) { alert('Invalid JSON: ' + e.message); }
});

// ---------- 代码编辑框：行号 + 当前行高亮 ----------
function updateGutter(){
  const ta=$('body'), g=$('codeGutter'); if(!ta||!g) return;
  const n=ta.value.split('\n').length;
  g.textContent=Array.from({length:n},(_,i)=>i+1).join('\n');
}
function updateActiveLine(){
  const ta=$('body'), line=$('codeActiveLine'); if(!ta||!line) return;
  const before=ta.value.slice(0,ta.selectionStart);
  const ln=before.split('\n').length;
  line.style.top=(10+(ln-1)*21-ta.scrollTop)+'px';
}
$('body')?.addEventListener('input', ()=>{ updateGutter(); updateActiveLine(); });
['click','keyup','scroll','select'].forEach(ev=>$('body')?.addEventListener(ev,updateActiveLine));
updateBodyMode();

// ---------- 保存 ----------
function collectKV(field) {
  return [...$('kv-' + field).children].map(r => ({key: r.querySelector('.kv-k').value.trim(), value: r.querySelector('.kv-v').value})).filter(p => p.key);
}
$('saveApiBtn').onclick = async () => {
  const payload = {
    type: API_TYPE,
    endpoint: $('endpoint').value.trim(),
    method: $('method').value,
    auth: $('auth').value,
    note: $('note').value.trim(),
    original_url: $('original_url').value.trim(),
    original_method: $('original_method').value,
    original_key: $('original_key').value.trim(),
    params: collectKV('params'), headers: collectKV('headers'), cookies: collectKV('cookies'),
    body_mode: $('body_mode').value,
    body: $('body').value,
    body_enabled: $('body_enabled') ? $('body_enabled').value === '1' : true,
    accept_encoding: $('accept_encoding').value,
    cache_unit: $('cache_unit').value,
    timeout: INITIAL.timeout || 10, retry: INITIAL.retry || 0,
    rate_limit: INITIAL.rate_limit || 0, param_conflict: INITIAL.param_conflict || 'user',
    passthrough: $('passthrough') ? $('passthrough').value === '1' : true
  };
  ['timeout','retry','rate_limit','cache_time'].forEach(k => { if ($(k)) payload[k] = $(k).value; });
  if ($('param_conflict')) payload.param_conflict = $('param_conflict').value;
  const r = API_ID ? await apiFetch('/api/apis/' + API_ID, 'PUT', payload) : await apiPost('/api/apis', payload);
  if (r.ok) location.href = '/panel/api-manager';
  else { const err = $('formError'); err.style.display = 'block'; err.textContent = r.error; }
};
