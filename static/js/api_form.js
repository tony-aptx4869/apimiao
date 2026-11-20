// API 创建/编辑表单：动态键值、Body 模式、Transmission 频率与 Data Mapping 拖拽
const $ = id => document.getElementById(id);
const I18N = {
  noBody: { zh: '此请求没有 body。', en: 'No body for this request.' },
  targetName: { zh: '目标字段名:', en: 'Target field name:' },
  copied: { zh: '已复制', en: 'Copied' },
};
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
  const area = $('bodyArea');
  const hint = $('bodyHint');
  if (mode === 'none') { $('body').style.display = 'none'; hint.textContent = tr('noBody'); }
  else if (mode === 'binary') { $('body').style.display = 'none'; hint.textContent = '二进制 body 由调用方请求内容转发。'; }
  else { $('body').style.display = 'block'; hint.textContent = ''; }
  const fmt = $('formatJsonBtn');
  if (fmt) fmt.style.display = mode === 'json' ? '' : 'none';
}
$('body_mode').onchange = updateBodyMode;
$('formatJsonBtn')?.addEventListener('click', () => {
  try { $('body').value = JSON.stringify(JSON.parse($('body').value), null, 2); }
  catch (e) { alert('Invalid JSON: ' + e.message); }
});
updateBodyMode();

// ---------- 时间列表 ----------
function timeRow(v = '') {
  const div = document.createElement('div');
  div.className = 'time-item';
  div.innerHTML = '<input type="time" value="' + v + '"><button class="btn btn-xs btn-danger" type="button"><i class="bi bi-x"></i></button>';
  div.querySelector('button').onclick = () => div.remove();
  return div;
}
$('addTime')?.addEventListener('click', () => $('timeList').appendChild(timeRow()));
if ($('timeList')) {
  (INITIAL.times || ['08:00']).forEach(t => $('timeList').appendChild(timeRow(t)));
  if (!$('timeList').children.length) $('timeList').appendChild(timeRow());
}
$('frequency')?.addEventListener('change', e => {
  $('dayBox').style.display = e.target.value === 'WEEK' ? '' : 'none';
});
if ($('frequency')) $('dayBox').style.display = $('frequency').value === 'WEEK' ? '' : 'none';

// ---------- API Test 弹窗 ----------
const testModal = $('testModal');
function testParamRow(k = '', v = '') {
  const div = kvRow(k, v);
  div.className = 'kv-row test-row';
  return div;
}
function loadTestParams() {
  const box = $('testParams');
  box.innerHTML = '';
  (INITIAL.test_params || []).forEach(p => box.appendChild(testParamRow(p.key, p.value)));
  if (!box.children.length) {
    const ph = placeholdersOf($('original_url').value);
    ph.forEach(k => box.appendChild(testParamRow(k, '')));
    if (!box.children.length) box.appendChild(testParamRow());
  }
}
function placeholdersOf(s) {
  const out = [], re = /\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g;
  let m; while ((m = re.exec(s || ''))) if (!out.includes(m[1])) out.push(m[1]);
  return out;
}
$('apiTestBtn')?.addEventListener('click', async () => {
  loadTestParams();
  testModal.classList.add('show');
  $('testResult').style.display = 'none';
});
$('addTestParam').onclick = () => $('testParams').appendChild(testParamRow());
$('testCancel').onclick = () => testModal.classList.remove('show');
$('testRequest').onclick = async () => {
  const pairs = [...$('testParams').children].map(r => ({key: r.querySelector('.kv-k').value.trim(), value: r.querySelector('.kv-v').value}));
  const r = await apiPost('/api/apis/test', {url: $('original_url').value, method: $('original_method').value, pairs, api_id: API_ID});
  const pre = $('testResult');
  pre.style.display = 'block';
  if (!r.ok) { pre.textContent = r.error; return; }
  pre.textContent = 'HTTP ' + r.status + '\n\n' + r.body;
  window._testData = {body: safeJson(r.body), header: r.headers, cookie: r.cookies};
  renderSourceFields('body');
};

// ---------- Data Mapping：源字段列表 + 拖拽到目标槽位 ----------
let mapping = Object.assign({}, INITIAL.mapping || {});
let sourceData = {body: null, header: {}, cookie: {}};

function flatten(obj, prefix = '') {
  const out = [];
  if (obj == null) return out;
  if (Array.isArray(obj)) {
    obj.slice(0, 10).forEach((v, i) => out.push(...flatten(v, prefix ? prefix + '.' + i : String(i))));
  } else if (typeof obj === 'object') {
    Object.keys(obj).forEach(k => out.push(...flatten(obj[k], prefix ? prefix + '.' + k : k)));
  } else if (prefix) out.push([prefix, obj]);
  return out;
}
function safeJson(s) { try { return JSON.parse(s); } catch (e) { return s; } }

function renderSourceFields(tab) {
  document.querySelectorAll('#srcTabs .tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  const data = sourceData[tab];
  const pairs = flatten(data);
  const box = $('srcFields');
  if (!pairs.length) { box.innerHTML = '<p class="hint">Run API Test to load source fields.</p>'; return; }
  box.innerHTML = pairs.map(([path, val]) =>
    '<div class="field-pill" draggable="true" data-path="' + esc(tab + '.' + path) + '">' +
    '<span>' + esc(path) + '</span><code>' + esc(String(val).slice(0, 24)) + '</code></div>'
  ).join('');
  box.querySelectorAll('.field-pill').forEach(pill => {
    pill.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', pill.dataset.path);
      pill.classList.add('dragging');
    });
    pill.addEventListener('dragend', () => pill.classList.remove('dragging'));
  });
}
document.querySelectorAll('#srcTabs .tab').forEach(b => b.onclick = () => renderSourceFields(b.dataset.tab));

function renderTargets() {
  const box = $('targetFields');
  box.innerHTML = Object.keys(mapping).map(target =>
    '<div class="target-row"><input value="' + esc(target) + '"><span class="mapped">' + esc(mapping[target]) + '</span>' +
    '<button class="btn btn-xs btn-danger" type="button"><i class="bi bi-x"></i></button></div>'
  ).join('');
  box.querySelectorAll('.target-row').forEach((row, idx) => {
    const targetKey = Object.keys(mapping)[idx];
    row.querySelector('input').onchange = e => {
      const val = mapping[targetKey];
      delete mapping[targetKey];
      mapping[e.target.value.trim()] = val;
      renderTargets();
    };
    row.querySelector('button').onclick = () => { delete mapping[targetKey]; renderTargets(); };
  });
}
$('addTarget')?.addEventListener('click', () => {
  const name = prompt(tr('targetName'));
  if (name && name.trim()) { mapping[name.trim()] = '(unmapped)'; renderTargets(); }
});
if ($('targetFields')) {
  $('targetFields').addEventListener('dragover', e => { e.preventDefault(); $('targetFields').classList.add('over'); });
  $('targetFields').addEventListener('dragleave', () => $('targetFields').classList.remove('over'));
  $('targetFields').addEventListener('drop', e => {
    e.preventDefault();
    $('targetFields').classList.remove('over');
    const path = e.dataTransfer.getData('text/plain');
    if (!path) return;
    let name = path.split('.').pop();
    if (mapping[name] && mapping[name] !== path) name = name + '_2';
    mapping[name] = path;
    renderTargets();
  });
  renderTargets();
}
if ($('srcFields')) renderSourceFields('body');

// ---------- 历史数据 ----------
if (API_ID && $('transHistory')) {
  apiGet('/api/apis/' + API_ID + '/history').then(r => {
    if (!r.ok) return;
    $('transHistory').innerHTML = r.history.slice(0, 10).map(h =>
      '<div>' + esc(h.captured_at) + ' — <code>' + esc((h.payload_json || '').slice(0, 60)) + '</code></div>'
    ).join('') || '<div>No data yet</div>';
  });
  const fetchBtn = document.createElement('button');
  fetchBtn.className = 'btn btn-xs';
  fetchBtn.textContent = 'Fetch now';
  fetchBtn.onclick = async () => {
    const r = await apiPost('/api/apis/' + API_ID + '/fetch-now', {});
    alert(r.ok ? 'Fetched: ' + JSON.stringify(r.payload) : r.error);
    if (r.ok) { sourceData.body = safeJson(JSON.stringify(r.payload)); renderSourceFields('body'); }
  };
  $('transHistory').parentNode.insertBefore(fetchBtn, $('transHistory'));
}

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
    encoding: $('encoding').value,
    note: $('note').value.trim(),
    original_url: $('original_url').value.trim(),
    original_method: $('original_method').value,
    original_key: $('original_key').value.trim(),
    params: collectKV('params'), headers: collectKV('headers'), cookies: collectKV('cookies'),
    body_mode: $('body_mode').value,
    body: $('body').value,
    body_enabled: $('body_enabled') ? $('body_enabled').checked : true,
    encoding: $('original_encoding') ? $('original_encoding').value : 'UTF-8',
    timeout: INITIAL.timeout || 10, retry: INITIAL.retry || 0,
    rate_limit: INITIAL.rate_limit || 0, param_conflict: INITIAL.param_conflict || 'user',
    passthrough: $('passthrough') ? $('passthrough').checked : true
  };
  // 高级设置值
  ['timeout','retry','rate_limit'].forEach(k => { if ($(k)) payload[k] = $(k).value; });
  if ($('param_conflict')) payload.param_conflict = $('param_conflict').value;
  if (API_TYPE === 'TRANSMISSION') {
    payload.frequency = $('frequency').value;
    payload.days = [...document.querySelectorAll('.day-check:checked')].map(c => c.value);
    payload.times = [...$('timeList').children].map(r => r.querySelector('input').value).filter(Boolean);
    payload.mapping = mapping;
    payload.test_params = [...($('testParams')?.children || [])].map(r => ({key: r.querySelector('.kv-k').value.trim(), value: r.querySelector('.kv-v').value})).filter(p => p.key);
  }
  const r = API_ID ? await apiFetch('/api/apis/' + API_ID, 'PUT', payload) : await apiPost('/api/apis', payload);
  if (r.ok) location.href = '/panel/api-manager';
  else { const err = $('formError'); err.style.display = 'block'; err.textContent = r.error; }
};
