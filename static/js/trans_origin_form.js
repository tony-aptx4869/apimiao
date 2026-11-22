// TRANS Origin 表单：键值对（含列表编辑）、时间、Fetch Data、Save 预检弹窗
const $ = id => document.getElementById(id);
const INIT = window.ORIGIN_INIT || {};
const ORIGIN_ID = window.ORIGIN_ID || null;
const T = (zh, en) => window.LANG_ZH === true ? zh : en;
let fieldConfig = INIT.fields || [];
let fetchFields = [];
let sheets = [];

function esc(s){ return String(s??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// 页面内错误提示（替代浏览器 alert）
function showError(msg){
  const box=$('formError'); if(!box){ uiAlert(msg); return; }
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
    throw new Error(T("不是有效的 Python list，例如 ['北京', '天津']", "Not a valid Python list, e.g. ['Beijing', 'Tianjin']"));
  }
  return s.split(';').map(x=>x.trim()).filter(Boolean);
}

// ---------- 键值对行（列表型带橙色标签） ----------
function kvRow(key='', value='', isList=false, items=[]){
  const div=document.createElement('div');
  div.className='kv-row';
  const tag=isList?`<span class="tag tag-list">${T('列表','List')}</span>`:'';
  div.innerHTML=`<input class="kv-k" placeholder="key" value="${esc(key)}">`+
    `<input class="kv-v" placeholder="value" value="${esc(value)}" ${isList?'style="display:none"':''}>`+
    tag+
    `<button type="button" class="btn btn-xs btn-outline kv-list" title="${T('编辑列表','Edit list')}"><i class="bi bi-list-ul"></i></button>`+
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
    catch(e){ showError(T('列表解析失败：','Failed to parse list: ')+e.message); return; }
  }
  if(!items.length){ showError(T('列表不能为空','List cannot be empty')); return; }
  editingRow._items=items; editingRow._isList=true;
  const inp=editingRow.querySelector('.kv-v'); inp.style.display='none';
  if(!editingRow.querySelector('.tag-list')){
    const tag=document.createElement('span'); tag.className='tag tag-list'; tag.textContent=T('列表','List');
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
    note:$('note')?.value||'',
    original_url:$('original_url').value.trim(),
    original_method:$('original_method').value,
    original_key:$('original_key').value.trim(),
    params:collectKV('params'), headers:collectKV('headers'), cookies:collectKV('cookies'),
    body_mode:$('body_mode').value, body:$('body').value,
    timeout:parseInt($('timeout').value)||10, retry:parseInt($('retry').value)||0,
    batch_retry_rounds:parseInt($('batch_retry_rounds')?.value||2),
    request_interval:parseFloat($('request_interval').value)||0,
    frequency:$('frequency').value,
    days:[...document.querySelectorAll('.day-check:checked')].map(c=>parseInt(c.value)),
    times:collectTimes(),
    sheet_id:$('sheet_id')?.value||''
  };
}

// ---------- Fetch Data：只发第一个值，返回字段供配置 ----------
$('fetchDataBtn').onclick=async()=>{
  const btn=$('fetchDataBtn'); btn.disabled=true; btn.textContent='...';
  try{
    const r=await apiPost('/api/trans-origins/fetch-preview', collectPayload());
    const out=$('fetchResult');
    if(!r.ok){ out.innerHTML=`<div class="alert alert-error">${esc(r.error)}</div>`; return; }
    if(!r.success){ out.innerHTML=`<div class="alert alert-warning">HTTP ${r.status}：${esc(r.biz_error||T('业务错误','Business error'))}</div>`; }
    const drift=r.drift||[];
    if(drift.length){
      out.insertAdjacentHTML('afterbegin',`<div class="alert alert-warning">Upstream JSON drift: ${esc(drift.map(d=>d.path).join(', '))}</div>`);
    }
    fetchFields=r.fields||[];
    renderFields(r.data,drift);
  } finally { btn.disabled=false; btn.textContent='Fetch Data'; }
};
function renderFields(data,drift){
  const box=$('sheetFields')||$('fetchResult');
  if(!box) return;
  const driftPaths=new Set((drift||[]).map(d=>d.path));
  const rows=(fieldConfig||[]).slice().sort((a,b)=>(a.order||0)-(b.order||0));
  box.innerHTML=rows.length?rows.map(f=>{
    const val=data?.[f.name];
    const state=driftPaths.has(f.name)?'drift':(f.enabled?'enabled':'disabled');
    return `<div class="field-pill" data-field="${esc(f.name)}">
      <div class="field-pill-text"><b>${esc(f.name)}</b><span>${esc(state)}${val===undefined?'':' · '+String(val).slice(0,40)}</span></div>
    </div>`;
  }).join(''):`<div class="hint">${T('选择一张包含字段的数据表。','Choose a Data Sheet with fields.')}</div>`;
}

// ---------- Save：任务化预检弹窗 ----------
let precheckTaskId = null;
let precheckTimer = null;
function precheckTaskUrl(){ return ORIGIN_ID?`/api/trans-origins/${ORIGIN_ID}/precheck-tasks`:'/api/trans-origins/precheck-tasks'; }
function stopPrecheckPolling(){ if(precheckTimer){ clearInterval(precheckTimer); precheckTimer=null; } }
function hideDecisionButtons(){
  ['retryPrecheckBtn','cancelPrecheckBtn','forceSaveBtn'].forEach(id=>$(id).style.display='none');
}
function renderPrecheckTask(task){
  const total=task.total||0, done=task.done||0, failed=task.failed||0;
  $('precheckProgress').textContent=T(`${done} / ${total}，失败 ${failed}，第 ${task.round||1} 轮`, `${done} / ${total}, ${failed} failed, round ${task.round||1}`);
  const detail=[];
  if(task.current) detail.push(T('当前请求 ','Current request ')+task.current);
  if(task.next_retry_at){
    const left=Math.max(0,Math.ceil((new Date(task.next_retry_at).getTime()-Date.now())/1000));
    detail.push(T('下次重试 ','Retry in ')+left+'s');
  }
  $('precheckDetail').textContent=detail.join(' · ');
  const log=$('precheckLog');
  const errors=(task.errors||[]).map(e=>`<div class="log-err">${esc(JSON.stringify(e.params||{}))} → ${esc(e.error)}</div>`).join('');
  const warnings=(task.warnings||[]).map(w=>`<div class="log-warn">${esc(JSON.stringify(w.params||{}))} → ${esc(w.error)}</div>`).join('');
  log.innerHTML=errors+warnings+(task.error&&!errors?`<div class="log-err">${esc(task.error)}</div>`:'');
  hideDecisionButtons();
  if(task.status==='AWAITING_USER'){
    if(task.can_retry) $('retryPrecheckBtn').style.display='inline-block';
    $('cancelPrecheckBtn').style.display='inline-block';
    $('forceSaveBtn').style.display='inline-block';
  } else if(task.status==='SUCCEEDED'||task.status==='SAVED_UNVERIFIED'){
    stopPrecheckPolling();
    setTimeout(()=>location.href='/panel/trans-origin',600);
  } else if(task.status==='CANCELLED'){
    stopPrecheckPolling();
    $('precheckProgress').textContent=T('预检已取消，未保存配置，未写入数据','Precheck cancelled. Configuration was not saved and no data was written.');
  }
}
async function pollPrecheckTask(){
  if(!precheckTaskId) return;
  const r=await apiFetch('/api/trans-origins/precheck-tasks/'+precheckTaskId);
  if(!r.ok){ showError(r.error||T('预检状态读取失败','Failed to read precheck status')); stopPrecheckPolling(); return; }
  renderPrecheckTask(r.task||{});
}
async function decidePrecheck(decision){
  if(!precheckTaskId) return;
  const r=await apiPost('/api/trans-origins/precheck-tasks/'+precheckTaskId+'/decision',{decision});
  if(!r.ok){ showError(r.error||T('预检决策失败','Precheck decision failed')); return; }
  await pollPrecheckTask();
}
$('saveBtn').onclick=async()=>{
  const btn=$('saveBtn'); if(btn.disabled) return; btn.disabled=true;
  stopPrecheckPolling();
  const modal=$('precheckModal'); modal.classList.add('show');
  $('precheckProgress').textContent=T('正在创建预检任务...','Creating precheck task...'); $('precheckDetail').textContent=''; $('precheckLog').innerHTML='';
  hideDecisionButtons();
  try{
    const r=await apiPost(precheckTaskUrl(), collectPayload());
    if(!r.ok){ showError(r.error||T('预检启动失败','Failed to start precheck')); btn.disabled=false; return; }
    precheckTaskId=r.task_id;
    await pollPrecheckTask();
    precheckTimer=setInterval(pollPrecheckTask,700);
  }catch(e){ showError(e.message||e); btn.disabled=false; }
  finally { if(precheckTaskId) btn.disabled=false; }
};
$('precheckClose').onclick=()=>{ stopPrecheckPolling(); $('precheckModal').classList.remove('show'); };
$('retryPrecheckBtn').onclick=()=>decidePrecheck('retry');
$('cancelPrecheckBtn').onclick=()=>decidePrecheck('cancel');
$('forceSaveBtn').onclick=()=>decidePrecheck('save_anyway');
