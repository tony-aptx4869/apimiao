// TRANS User form: select Data Sheets, project fields, drag mappings and joins.
const $ = id => document.getElementById(id);
const INIT = window.USER_INIT || {};
const USER_ID = window.USER_ID || null;
const ZH = window.LANG_ZH === true;
let availableSheets = (window.ALL_SHEETS || []).slice();
let selectedSheets = (INIT.sheet_ids || []).slice();
let sheetFields = Object.assign({}, INIT.sheet_fields || {});
let mappings = {
  params: (INIT.param_mapping || []).slice(),
  body: (INIT.body_mapping || []).slice()
};
let joins = (INIT.joins || []).slice();
let sheetMeta = {};

const OUTPUT_NODE_TYPES = ['object', 'array', 'field'];
function genNodeId(){ return 'node-' + Math.random().toString(36).slice(2,10) + Date.now().toString(36).slice(-4); }
function defaultOutputTree(){ return { id: genNodeId(), type: 'object', key: '', field_id: '', children: [] }; }
function normalizeOutputNode(raw){
  if(!raw || typeof raw !== 'object') return null;
  const type = OUTPUT_NODE_TYPES.includes(raw.type) ? raw.type : 'field';
  const savedKey = typeof raw.key === 'string' ? raw.key : '';
  return {
    id: typeof raw.id === 'string' && raw.id ? raw.id : genNodeId(),
    type: type,
    key: savedKey,
    field_id: typeof raw.field_id === 'string' ? raw.field_id : '',
    children: type === 'field' ? [] : (Array.isArray(raw.children) ? raw.children.map(normalizeOutputNode).filter(Boolean) : [])
  };
}
function normalizeOutputTree(tree){
  if(!tree || typeof tree !== 'object' || tree.type !== 'object') return defaultOutputTree();
  const root = normalizeOutputNode(tree);
  root.type = 'object';
  root.field_id = '';
  return root;
}
let outputTree = normalizeOutputTree(INIT.output_tree);

function esc(s){ return String(s??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function feedback(msg){
  const box=$('dataFeedback');
  if(!box) return;
  box.textContent=msg||'';
  box.style.display=msg?'block':'none';
}

function normalizeSheets(sheets){
  sheetMeta={};
  (sheets||[]).forEach(s=>{
    sheetMeta[s.id]={name:s.name, fields:(s.fields||[]).map(f=>({
      id:f.id, key:f.key||f.name, enabled:f.enabled!==false
    }))};
  });
  const normalized={};
  selectedSheets.forEach(sid=>{
    const meta=sheetMeta[sid];
    if(!meta) return;
    const legacy=sheetFields[sid]||{};
    normalized[sid]={};
    meta.fields.forEach(f=>{
      const value=legacy[f.id]!==undefined?legacy[f.id]:legacy[f.key];
      normalized[sid][f.id]=value!==false;
    });
  });
  sheetFields=normalized;
  ['params','body'].forEach(loc=>{
    mappings[loc]=mappings[loc].map(m=>{
      const field=findField(m.sheet_id,m.field_id||m.field);
      return Object.assign({},m,{field_id:field?field.id:'',field:field?field.key:(m.field||'')});
    }).filter(m=>m.field_id);
  });
  joins=joins.map(j=>{
    const left=findField(j.left_sheet,j.left_field_id||j.left_field);
    const right=findField(j.right_sheet,j.right_field_id||j.right_field);
    return Object.assign({},j,{
      left_field_id:left?left.id:'',left_field:left?left.key:(j.left_field||''),
      right_field_id:right?right.id:'',right_field:right?right.key:(j.right_field||'')
    });
  }).filter(j=>j.left_field_id&&j.right_field_id);
  renderSheetViews();
  renderTargets();
  renderOutputTree();
}

function findField(sheetId, idOrKey){
  const fields=(sheetMeta[sheetId]||{}).fields||[];
  return fields.find(f=>f.id===idOrKey||f.key===idOrKey)||null;
}

$('chooseSheetsBtn').onclick=async()=>{
  const r=await apiFetch('/api/trans-user/sheets');
  if(r.ok) availableSheets=r.sheets||[];
  const box=$('sheetsList');
  box.innerHTML=availableSheets.map(s=>{
    const checked=selectedSheets.includes(s.id)?'checked':'';
    const count=(s.fields||[]).length;
    return `<label class="sheet-opt"><input type="checkbox" class="sheet-check" value="${esc(s.id)}" ${checked}> <b>${esc(s.name)}</b> <span class="muted">(${count} ${ZH?'个字段':'fields'})</span></label>`;
  }).join('')||`<p class="empty">${ZH?'暂无数据表，请先创建数据表。':'No Data Sheets yet. Create one first.'}</p>`;
  $('sheetsModal').classList.add('show');
};
$('sheetsCancel').onclick=()=>$('sheetsModal').classList.remove('show');
$('sheetsApply').onclick=()=>{
  selectedSheets=[...$('sheetsList').querySelectorAll('.sheet-check:checked')].map(c=>c.value);
  normalizeSheets(availableSheets);
  $('sheetsModal').classList.remove('show');
};

function renderSheetViews(){
  const box=$('sheetViews');
  box.innerHTML=selectedSheets.map(sid=>{
    const meta=sheetMeta[sid];
    if(!meta) return '';
    const fields=meta.fields.map(f=>{
      const on=(sheetFields[sid]||{})[f.id]!==false;
      return `<div class="schema-field" draggable="true" data-sheet="${esc(sid)}" data-field="${esc(f.key)}" data-field-id="${esc(f.id)}">
        <span>${esc(f.key)}</span>
        <button type="button" class="dot ${on?'on':'off'}" data-sheet="${esc(sid)}" data-field-id="${esc(f.id)}" title="${ZH?'切换字段是否输出':'toggle projection'}"></button>
      </div>`;
    }).join('');
    return `<div class="schema-card"><div class="schema-title">${esc(meta.name)}</div>${fields||`<div class="hint">${ZH?'暂无字段':'No fields'}</div>`}</div>`;
  }).join('');
  box.querySelectorAll('.dot').forEach(d=>d.onclick=()=>{
    const sid=d.dataset.sheet, fieldId=d.dataset.fieldId;
    sheetFields[sid]=sheetFields[sid]||{};
    const enabled=sheetFields[sid][fieldId]!==false;
    sheetFields[sid][fieldId]=!enabled;
    d.classList.toggle('on',!enabled); d.classList.toggle('off',enabled);
  });
  box.querySelectorAll('.schema-field').forEach(p=>{
    p.ondragstart=e=>{
      e.dataTransfer.setData('text/plain',JSON.stringify({
        sheet:p.dataset.sheet, field:p.dataset.field, field_id:p.dataset.fieldId
      }));
    };
    p.ondragover=e=>e.preventDefault();
    p.ondrop=e=>{
      e.preventDefault();
      let src;
      try { src=JSON.parse(e.dataTransfer.getData('text/plain')||'{}'); } catch(_) { return; }
      const target=e.target.closest('.schema-field');
      if(!src.field_id||!target||!target.dataset.fieldId) return;
      if(selectedSheets.length < 2){
        feedback(ZH?'单张数据表无需建立 Join，字段只需拖到 Params 或返回数据树。':'A single sheet does not need a Join. Drag fields to Params or the output tree.');
        return;
      }
      if(src.sheet===target.dataset.sheet || src.field_id===target.dataset.fieldId){
        feedback(ZH?'Join 只能连接不同数据表的字段。':'Joins require fields from different data sheets.');
        return;
      }
      joins.push({
        left_sheet:src.sheet, left_field:src.field, left_field_id:src.field_id,
        right_sheet:target.dataset.sheet, right_field:target.dataset.field, right_field_id:target.dataset.fieldId
      });
      feedback((ZH?'已添加 Join：':'Join added: ')+src.field+' = '+target.dataset.field);
      renderTargets();
    };
  });
}

function renderTargets(){
  ['params'].forEach(loc=>{
    const box=$('target'+loc.charAt(0).toUpperCase()+loc.slice(1));
    box.innerHTML=mappings[loc].map((m,i)=>{
      const field=findField(m.sheet_id,m.field_id);
      const sheetName=(sheetMeta[m.sheet_id]||{}).name||'?';
      return `<div class="mapping-item" data-i="${i}" data-loc="${loc}">
        <span class="src-field">${esc(sheetName)}.${esc(field?field.key:m.field)}</span>
        <input class="alias-input" value="${esc(m.alias||m.field)}" placeholder="public key">
        <button type="button" class="btn btn-xs btn-danger map-del">&times;</button>
      </div>`;
    }).join('');
    box.querySelectorAll('.map-del').forEach(b=>b.onclick=()=>{
      const i=parseInt(b.closest('.mapping-item').dataset.i,10);
      mappings[loc].splice(i,1); renderTargets();
    });
    box.querySelectorAll('.alias-input').forEach(inp=>inp.onchange=()=>{
      const item=inp.closest('.mapping-item');
      mappings[loc][parseInt(item.dataset.i,10)].alias=inp.value.trim();
    });
    box.ondragover=e=>e.preventDefault();
    box.ondrop=e=>{
      e.preventDefault();
      let src;
      try { src=JSON.parse(e.dataTransfer.getData('text/plain')||'{}'); } catch(_) { return; }
      const f=resolveFieldIdentity(src);
      if(!f.field_id) return;
      mappings[loc].push({
        sheet_id:f.sheet, field_id:f.field_id, field:f.field, alias:f.field
      });
      renderTargets();
    };
  });
}

// ---- 返回数据树（output_tree）----
const OUTPUT_NODE_MIME = 'application/x-trans-output-node';

function findFieldKeyById(fieldId){
  if(!fieldId) return '';
  for(const sid in sheetMeta){
    const f=((sheetMeta[sid]||{}).fields||[]).find(f=>f.id===fieldId);
    if(f) return f.key;
  }
  return '';
}

// Backend fields now carry stable ids. Fall back to the key when an id is absent,
// resolving through sheet fields (USER_INIT.sheet_fields / ALL_SHEETS) first, then
// saved mappings, so create/edit drags always produce a field node with an id.
function findFieldIdByKey(key, sheetId){
  if(!key) return '';
  const scopes=[];
  if(sheetId && sheetMeta[sheetId]) scopes.push(sheetMeta[sheetId].fields||[]);
  for(const sid in sheetMeta) if(sid!==sheetId) scopes.push((sheetMeta[sid]||{}).fields||[]);
  for(const fields of scopes){
    const f=fields.find(x=>x.key===key||x.id===key);
    if(f && f.id) return f.id;
  }
  const saved=INIT.sheet_fields||{};
  for(const sid in saved){
    if(sheetId && sid!==sheetId) continue;
    if(Object.prototype.hasOwnProperty.call(saved[sid]||{}, key)){
      const f=((sheetMeta[sid]||{}).fields||[]).find(x=>x.key===key||x.id===key);
      if(f && f.id) return f.id;
    }
  }
  return '';
}

function findFieldIdFromSavedMapping(key){
  if(!key) return '';
  for(const loc of ['params','body']){
    const m=(mappings[loc]||[]).find(x=>(x.field===key||x.field_id===key)&&x.field_id);
    if(m) return m.field_id;
  }
  let found='';
  (function walk(n){
    if(!n || found) return;
    if((n.key===key||n.field_id===key) && n.field_id){ found=n.field_id; return; }
    (n.children||[]).forEach(walk);
  })(outputTree);
  return found;
}

function resolveFieldIdentity(src){
  let fieldId = src.field_id || '';
  let field = src.field || '';
  if(!fieldId) fieldId = findFieldIdByKey(field, src.sheet);
  if(!fieldId) fieldId = findFieldIdFromSavedMapping(field);
  if(fieldId && !field) field = findFieldKeyById(fieldId);
  return { sheet: src.sheet || '', field: field, field_id: fieldId };
}

function findOutputNode(id, node=outputTree){
  if(!node) return null;
  if(node.id===id) return node;
  for(const child of (node.children||[])){
    const found=findOutputNode(id,child);
    if(found) return found;
  }
  return null;
}

function findOutputParentList(id, node=outputTree){
  if(!node || !Array.isArray(node.children)) return null;
  if(node.children.some(c=>c.id===id)) return node.children;
  for(const child of node.children){
    const found=findOutputParentList(id,child);
    if(found) return found;
  }
  return null;
}

function isOutputDescendant(ancestorId, maybeDescendantId){
  const ancestor=findOutputNode(ancestorId);
  if(!ancestor) return false;
  const stack=(ancestor.children||[]).slice();
  while(stack.length){
    const n=stack.pop();
    if(n.id===maybeDescendantId) return true;
    stack.push(...(n.children||[]));
  }
  return false;
}

function detachOutputNode(id){
  const list=findOutputParentList(id);
  if(!list) return null;
  const idx=list.findIndex(n=>n.id===id);
  return idx<0 ? null : list.splice(idx,1)[0];
}

function insertOutputNode(node, targetId, mode){
  if(mode==='append'){
    const target=findOutputNode(targetId);
    if(!target || target.type==='field') outputTree.children.push(node);
    else target.children.push(node);
    return;
  }
  const list=findOutputParentList(targetId);
  if(!list){ outputTree.children.push(node); return; }
  const idx=list.findIndex(n=>n.id===targetId);
  list.splice(idx<0?list.length:idx,0,node);
}

function renderOutputNode(node){
  const isContainer = node.type!=='field';
  const labels = ZH
    ? {object:'对象', array:'数组', field:'字段', source:'来源', unbound:'未绑定', del:'删除节点', key:'Key 名称'}
    : {object:'Object', array:'Array', field:'Field', source:'Source', unbound:'Unbound', del:'Delete node', key:'Key Name'};
  const typeLabel = labels[node.type] || labels.field;
  const sourceKey = findFieldKeyById(node.field_id)||node.field_id||labels.unbound;
  const source = node.type==='field'
    ? `<span class="output-node-source" draggable="true">${labels.source} ${esc(sourceKey)}</span>` : '';
  const children = isContainer
    ? `<div class="output-node-children">${(node.children||[]).map(renderOutputNode).join('')}</div>` : '';
  const isRoot = node.id===outputTree.id;
  const delBtn = isRoot ? '' : `<button type="button" class="btn btn-xs btn-danger output-node-del" title="${labels.del}">&times;</button>`;
  const keyDisabled = isRoot ? 'disabled' : '';
  return `<div class="output-node" data-id="${esc(node.id)}" draggable="true">
    <div class="output-node-row" draggable="true">
      <span class="output-node-type type-${node.type}" draggable="true">${typeLabel}</span>
      <input class="output-node-key" value="${esc(node.key)}" placeholder="${labels.key}" draggable="false" ${keyDisabled}>
      ${source}
      ${delBtn}
    </div>
    ${children}
  </div>`;
}

function handleOutputDrop(e, targetId, mode){
  const nodeId=e.dataTransfer.getData(OUTPUT_NODE_MIME);
  if(nodeId){
    if(nodeId===targetId) return;
    if(mode==='append' && isOutputDescendant(targetId,nodeId)) return;
    if(mode==='before' && isOutputDescendant(nodeId,targetId)) return;
    const moving=detachOutputNode(nodeId);
    if(!moving) return;
    insertOutputNode(moving,targetId,mode);
    renderOutputTree();
    return;
  }
  let src;
  try { src=JSON.parse(e.dataTransfer.getData('text/plain')||'{}'); } catch(_) { return; }
  const f=resolveFieldIdentity(src);
  if(!f.field_id) return;
  const sourceKey=findFieldKeyById(f.field_id)||f.field||'';
  const fieldNode={ id:genNodeId(), type:'field', key:sourceKey.split('.').pop()||sourceKey, field_id:f.field_id, children:[] };
  insertOutputNode(fieldNode,targetId,mode);
  renderOutputTree();
}

function blockOutputNodeDrag(){
  document.querySelectorAll(
    '#outputTree .output-node, #outputTree .output-node-row, #outputTree .output-node-type, #outputTree .output-node-source'
  ).forEach(el=>{
    el.draggable=false;
    el.dataset.dragBlocked='1';
  });
}

function restoreOutputNodeDraggable(){
  document.querySelectorAll('#outputTree [data-drag-blocked="1"]').forEach(el=>{
    el.draggable=true;
    delete el.dataset.dragBlocked;
  });
}

function bindOutputTreeEvents(box){
  box.querySelectorAll('.output-node').forEach(el=>{
    const id=el.dataset.id;
    el.ondragstart=e=>{
      if(e.target && e.target.closest && e.target.closest('input')){
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      e.stopPropagation();
      e.dataTransfer.setData(OUTPUT_NODE_MIME,id);
      e.dataTransfer.setData('text/plain','');
      e.dataTransfer.effectAllowed='move';
      if(e.dataTransfer.setDragImage) e.dataTransfer.setDragImage(el, e.offsetX||10, e.offsetY||10);
      el.classList.add('dragging');
    };
    el.ondragend=()=>el.classList.remove('dragging');
    el.ondragover=e=>{ e.preventDefault(); e.stopPropagation(); el.classList.add('drop-target'); };
    el.ondragleave=()=>el.classList.remove('drop-target');
    el.ondrop=e=>{
      e.preventDefault(); e.stopPropagation(); el.classList.remove('drop-target');
      handleOutputDrop(e,id,'before');
    };
  });
  box.querySelectorAll('.output-node input').forEach(inp=>{
    inp.onmousedown=e=>{
      e.stopPropagation();
      blockOutputNodeDrag();
    };
    inp.onmouseup=restoreOutputNodeDraggable;
    inp.onblur=restoreOutputNodeDraggable;
  });
  if(!window.OUTPUT_DRAG_RESTORE_BOUND){
    document.addEventListener('mouseup',restoreOutputNodeDraggable);
    document.addEventListener('dragend',restoreOutputNodeDraggable);
    window.OUTPUT_DRAG_RESTORE_BOUND=true;
  }
  box.querySelectorAll('.output-node-children').forEach(el=>{
    if(el.parentElement) el.dataset.parent=el.parentElement.dataset.id;
    el.ondragover=e=>{ e.preventDefault(); e.stopPropagation(); el.classList.add('over'); };
    el.ondragleave=()=>el.classList.remove('over');
    el.ondrop=e=>{
      e.preventDefault(); e.stopPropagation(); el.classList.remove('over');
      handleOutputDrop(e,el.dataset.parent,'append');
    };
  });
  box.querySelectorAll('.output-node-del').forEach(b=>b.onclick=()=>{
    detachOutputNode(b.closest('.output-node').dataset.id);
    renderOutputTree();
  });
  box.querySelectorAll('.output-node-key').forEach(inp=>inp.onchange=()=>{
    const n=findOutputNode(inp.closest('.output-node').dataset.id);
    if(n) n.key=inp.value.trim();
  });
}

function renderOutputTree(){
  const box=$('outputTree');
  if(!box) return;
  box.innerHTML=renderOutputNode(outputTree);
  box.ondragover=e=>e.preventDefault();
  box.ondrop=e=>{ e.preventDefault(); handleOutputDrop(e,outputTree.id,'append'); };
  bindOutputTreeEvents(box);
}

$('outputAddObject').onclick=()=>{ outputTree.children.push({id:genNodeId(),type:'object',key:'',children:[]}); renderOutputTree(); };
$('outputAddArray').onclick=()=>{ outputTree.children.push({id:genNodeId(),type:'array',key:'',children:[]}); renderOutputTree(); };
$('outputReset').onclick=()=>{ outputTree=defaultOutputTree(); renderOutputTree(); };

$('saveBtn').onclick=async()=>{
  const payload={
    id:USER_ID||undefined,
    endpoint:$('endpoint').value.trim(), method:$('method').value,
    encoding:'UTF-8', auth:$('auth').value, note:$('note').value.trim(),
    sheet_ids:selectedSheets, joins:joins, sheet_fields:sheetFields,
    param_mapping:mappings.params, body_mapping:[],
    output_tree:outputTree,
    rate_limit:parseInt($('rate_limit').value)||0
  };
  const r=USER_ID?await apiFetch('/api/trans-apis/'+USER_ID,'PUT',payload):await apiPost('/api/trans-apis',payload);
  if(r.ok) location.href='/panel/trans-user';
  else { const e=$('formError'); e.style.display='block'; e.textContent=r.error; }
};

normalizeSheets(availableSheets);
renderOutputTree();
