// TRANS Origin 表单：键值对（含列表编辑）、时间、Fetch Data、Save 预检弹窗
const $ = id => document.getElementById(id);
const INIT = window.ORIGIN_INIT || {};
const ORIGIN_ID = window.ORIGIN_ID || null;
let fieldConfig = INIT.fields || [];
let fetchFields = [];
let sheets = [];

function esc(s){ return String(s??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// 页面内错误提示（替代浏览器 alert）
function showError(msg){
  const box=$('formError'); if(!box){ alert(msg); return; }
  box.textContent=msg; box.style.display='block';
  box.scrollIntoView({behavior:'smooth', block:'center'});
}
function clearError(){ const box=$('formError'); if(box){ box.style.display='none'; box.textContent=''; } }

async function initSheets(){
  const select=$('sheet_id');
  if(!select) return;
  const r=await apiFetch('/api/data-sheets');
  if(!r.ok){ showError(r.error||'Failed to load data sheets'); return; }
  sheets=r.sheets||[];
  select.innerHTML=sheets.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
  const selected=sheets.find(s=>s.id===INIT.sheet_id)||sheets[0];
  if(selected){
    select.value=selected.id;
    fieldConfig=(selected.fields||[]).map(f=>({name:f.key,enabled:f.enabled,order:f.order}));
  }
  select.onchange=()=>{
    const current=sheets.find(s=>s.id===select.value);
    fieldConfig=current?(current.fields||[]).map(f=>({name:f.key,enabled:f.enabled,order:f.order})):[];
    renderFields();
  };
  renderFields();
}
initSheets();

// 解析列表输入：Python list（单引号）/ JSON 数组（双引号）/ 分号分隔文本
function parseList(raw){
  const s=(raw||'').trim();
  if(s.startsWith('[')){
    // 1) 标准 JSON 数组
    try{ const a=JSON.parse(s); if(Array.isArray(a)) return a.map(String); }catch(e){}
    // 2) Python list：把单引号字符串转成双引号字符串后再 parse（保留串内逗号/转义）
    const out=[]; const re=/'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|([^,'"\[\]\s][^,'"\[\]]*)/g;
    let m, matched=false;
    while((m=re.exec(s))!==null){
      matched=true;
      const v=(m[1]??m[2]??m[3]??'').trim();
      if(v) out.push(v.replace(/\\(['"\\])/g,'$1'));
    }
    if(matched && out.length) return out;
    throw new Error('不是有效的 Python list，例如 [\'北京\', \'天津\']');
  }
  return s.split(';').map(x=>x.trim()).filter(Boolean);
}

// ---------- 键值对行（列表型带橙色标签） ----------
function kvRow(key='', value='', isList=false, items=[]){
  const div=document.createElement('div');
  div.className='kv-row';
  const tag=isList?'<span class="tag tag-list">列表</span>':'';
  div.innerHTML=`<input class="kv-k" placeholder="key" value="${esc(key)}">`+
    `<input class="kv-v" placeholder="value" value="${esc(value)}" ${isList?'style="display:none"':''}>`+
    tag+
    `<button type="button" class="btn btn-xs btn-outline kv-list" title="编辑列表"><i class="bi bi-list-ul"></i></button>`+
    `<button type="button" class="btn btn-xs btn-danger kv-del"><i class="bi bi-x"></i></button>`;
  div._items=items.slice();
  div._isList=isList;
  div.querySelector('.kv-del').onclick=()=>div.remove();
  div.querySelector('.kv-list').onclick=()=>openListModal(div);
  return div;
}
function loadKV(field, data){
  const box=$('kv-'+field); box.innerHTML='';
  (data||[]).forEach(p=>box.appendChild(kvRow(p.key, p.value, !!p.is_list, p.items||[])));
  if(!box.children.length) box.appendChild(kvRow());
}
['params','headers','cookies'].forEach(f=>loadKV(f, INIT[f]));
document.querySelectorAll('[data-add]').forEach(b=>{
  b.onclick=()=>$('kv-'+b.dataset.add).appendChild(kvRow());
});
function collectKV(field){
  return [...$('kv-'+field).children].map(r=>({
    key:r.querySelector('.kv-k').value.trim(),
    value:r.querySelector('.kv-v').value,
    is_list:!!r._isList,
    items:r._items||[],
    list_raw:(r._items||[]).join(';')
  })).filter(p=>p.key);
}

// ---------- 列表编辑弹窗 ----------
let editingRow=null;
function openListModal(row){
  editingRow=row;
  const box=$('listItems'); box.innerHTML='';
  (row._items||[]).forEach((it,i)=>box.appendChild(listItemRow(it)));
  if(!box.children.length) box.appendChild(listItemRow(''));
  $('listRaw').value=(row._items||[]).join(';');
  $('listModal').classList.add('show');
}
function listItemRow(val=''){
  const d=document.createElement('div'); d.className='kv-row';
  d.innerHTML=`<input class="li-v" value="${esc(val)}" placeholder="item">`+
    `<button type="button" class="btn btn-xs btn-danger li-del"><i class="bi bi-x"></i></button>`;
  d.querySelector('.li-del').onclick=()=>d.remove();
  return d;
}
$('addListItem')?.addEventListener('click',()=>$('listItems').appendChild(listItemRow()));
$('listCancel')?.addEventListener('click',()=>$('listModal').classList.remove('show'));
$('listApply')?.addEventListener('click',()=>{
  let items=[];
  clearError();
  const raw=$('listRaw').value.trim();
  items=[...$('listItems').children].map(r=>r.querySelector('.li-v').value.trim()).filter(Boolean);
  if(!items.length && raw){
    try{ items=parseList(raw); }
    catch(e){ showError('列表解析失败：'+e.message); return; }
  }
  if(!items.length){ showError('列表不能为空'); return; }
  editingRow._items=items; editingRow._isList=true;
  const inp=editingRow.querySelector('.kv-v'); inp.style.display='none';
  if(!editingRow.querySelector('.tag-list')){
    const tag=document.createElement('span'); tag.className='tag tag-list'; tag.textContent='列表';
    inp.after(tag);
  }
  $('listModal').classList.remove('show');
});

// ---------- 时间行 ----------
function timeRow(val='07:00'){
  const d=document.createElement('div'); d.className='kv-row';
  d.innerHTML=`<input class="time-v" type="time" value="${esc(val)}">`+
    `<button type="button" class="btn btn-xs btn-danger time-del"><i class="bi bi-x"></i></button>`;
  d.querySelector('.time-del').onclick=()=>d.remove();
  return d;
}
function loadTimes(){
  const box=$('timeList'); box.innerHTML='';
  (INIT.times||['09:00']).forEach(t=>box.appendChild(timeRow(t)));
  if(!box.children.length) box.appendChild(timeRow());
}
loadTimes();
$('addTime').onclick=()=>$('timeList').appendChild(timeRow());
function collectTimes(){ return [...$('timeList').children].map(r=>r.querySelector('.time-v').value).filter(Boolean); }

// ---------- 天数勾选 ----------
document.querySelectorAll('.day-check').forEach(c=>{
  c.checked=(INIT.days||[]).includes(parseInt(c.value));
});
$('frequency').value=INIT.frequency||'DAY';
function toggleDays(){ $('dayBox').style.display=$('frequency').value==='WEEK'?'flex':'none'; }
$('frequency').onchange=toggleDays; toggleDays();

// ---------- Body 模式 ----------
// ---------- Body 代码编辑框：模式切换 + 行号 + 当前行高亮 ----------
function updateBodyMode(){
  const mode=$('body_mode').value, editor=$('codeEditor'), hint=$('bodyHint');
  if(mode==='none'||mode==='binary'){ editor.style.display='none'; }
  else { editor.style.display='flex'; updateGutter(); updateActiveLine(); }
  const fmt=$('formatJsonBtn'); if(fmt) fmt.style.display=mode==='json'?'':'none';
}
function updateGutter(){
  const ta=$('body'), g=$('codeGutter'); if(!ta||!g) return;
  const n=ta.value.split('\n').length;
  g.textContent=Array.from({length:n},(_,i)=>i+1).join('\n');
}
function updateActiveLine(){
  const ta=$('body'), line=$('codeActiveLine'); if(!ta||!line) return;
  const ln=ta.value.slice(0,ta.selectionStart).split('\n').length;
  line.style.top=(10+(ln-1)*21-ta.scrollTop)+'px';
}
$('body_mode').onchange=updateBodyMode;
$('formatJsonBtn')?.addEventListener('click',()=>{
  try{ $('body').value=JSON.stringify(JSON.parse($('body').value),null,2); updateGutter(); updateActiveLine(); }
  catch(e){ showError('Invalid JSON: '+e.message); }
});
$('body')?.addEventListener('input',()=>{ updateGutter(); updateActiveLine(); });
['click','keyup','scroll','select'].forEach(ev=>$('body')?.addEventListener(ev,updateActiveLine));
updateBodyMode();

// ---------- 收集配置 ----------
function collectPayload(){
  return {
    id: ORIGIN_ID||undefined,
    note:$('note')?.value||'',
    original_url:$('original_url').value.trim(),
    original_method:$('original_method').value,
    original_key:$('original_key').value.trim(),
    params:collectKV('params'), headers:collectKV('headers'), cookies:collectKV('cookies'),
    body_mode:$('body_mode').value, body:$('body').value,
    timeout:parseInt($('timeout').value)||10, retry:parseInt($('retry').value)||0,
    request_interval:parseFloat($('request_interval').value)||0,
    frequency:$('frequency').value,
    days:[...document.querySelectorAll('.day-check:checked')].map(c=>parseInt(c.value)),
    times:collectTimes(),
    sheet_id:$('sheet_id')?.value||'',
    result_path:$('result_path')?.value.trim()||'result'
  };
}

// ---------- Fetch Data：只发第一个值，返回字段供配置 ----------
$('fetchDataBtn').onclick=async()=>{
  const btn=$('fetchDataBtn'); btn.disabled=true; btn.textContent='...';
  try{
    const r=await apiPost('/api/trans-origins/fetch-preview', collectPayload());
    const out=$('fetchResult');
    if(!r.ok){ out.innerHTML=`<div class="alert alert-error">${esc(r.error)}</div>`; return; }
    if(!r.success){ out.innerHTML=`<div class="alert alert-warning">HTTP ${r.status}：${esc(r.biz_error||'业务错误')}</div>`; }
    fetchFields=r.fields||[];
    renderFields(r.data);
  } finally { btn.disabled=false; btn.textContent='Fetch Data'; }
};
function renderFields(data){
  const box=$('sheetFields')||$('fetchResult');
  if(!box) return;
  const rows=(fieldConfig||[]).slice().sort((a,b)=>(a.order||0)-(b.order||0));
  box.innerHTML=rows.length?rows.map(f=>{
    const val=data?.[f.name];
    return `<div class="field-pill" data-field="${esc(f.name)}">
      <div class="field-pill-text"><b>${esc(f.name)}</b><span>${esc(f.enabled?'enabled':'disabled')}${val===undefined?'':' · '+String(val).slice(0,40)}</span></div>
    </div>`;
  }).join(''):'<div class="hint">Choose a Data Sheet with fields.</div>';
}

// ---------- Save：预检弹窗 ----------
$('saveBtn').onclick=async()=>{
  const payload=collectPayload();
  const modal=$('precheckModal'); modal.classList.add('show');
  const prog=$('precheckProgress'), log=$('precheckLog');
  prog.textContent='正在预检...'; log.innerHTML=''; $('forceSaveBtn').style.display='none';
  try{
    const r=await apiPost('/api/trans-origins/precheck', payload);
    prog.textContent=`${r.total||0} 项，成功 ${(r.total||0)-(r.failed||0)}，失败 ${r.failed||0}`;
    if(r.errors&&r.errors.length){
      log.innerHTML=r.errors.map(e=>`<div class="log-err">${esc(JSON.stringify(e.params))} → ${esc(e.error)}</div>`).join('');
    }
    if(r.saved){
      $('forceSaveBtn').style.display='none';
      setTimeout(()=>location.href='/panel/trans-origin',800);
    } else {
      $('forceSaveBtn').style.display='inline-block';
    }
  }catch(e){ prog.textContent='预检失败'; log.innerHTML=`<div class="log-err">${esc(e.message||e)}</div>`; }
};
$('precheckClose').onclick=()=>$('precheckModal').classList.remove('show');
$('forceSaveBtn').onclick=async()=>{
  const p=collectPayload(); p.force_save=true;
  const r=await apiPost('/api/trans-origins/precheck', p);
  if(r.saved) location.href='/panel/trans-origin'; else showError(r.error||'保存失败');
};
