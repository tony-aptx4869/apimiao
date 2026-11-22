// TRANS User form: select Data Sheets, project fields, drag mappings and joins.
const $ = id => document.getElementById(id);
const INIT = window.USER_INIT || {};
const USER_ID = window.USER_ID || null;
let availableSheets = (window.ALL_SHEETS || []).slice();
let selectedSheets = (INIT.sheet_ids || []).slice();
let sheetFields = Object.assign({}, INIT.sheet_fields || {});
let mappings = {
  params: (INIT.param_mapping || []).slice(),
  body: (INIT.body_mapping || []).slice()
};
let joins = (INIT.joins || []).slice();
let sheetMeta = {};

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
    return `<label class="sheet-opt"><input type="checkbox" class="sheet-check" value="${esc(s.id)}" ${checked}> <b>${esc(s.name)}</b> <span class="muted">(${count} fields)</span></label>`;
  }).join('')||'<p class="empty">No Data Sheets yet. Create one first.</p>';
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
        <button type="button" class="dot ${on?'on':'off'}" data-sheet="${esc(sid)}" data-field-id="${esc(f.id)}" title="toggle projection"></button>
      </div>`;
    }).join('');
    return `<div class="schema-card"><div class="schema-title">${esc(meta.name)}</div>${fields||'<div class="hint">No fields</div>'}</div>`;
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
      const src=JSON.parse(e.dataTransfer.getData('text/plain')||'{}');
      const target=e.target.closest('.schema-field');
      if(!src.field_id||!target||!target.dataset.fieldId) return;
      joins.push({
        left_sheet:src.sheet, left_field:src.field, left_field_id:src.field_id,
        right_sheet:target.dataset.sheet, right_field:target.dataset.field, right_field_id:target.dataset.fieldId
      });
      feedback('Join added: '+src.field+' = '+target.dataset.field);
      renderTargets();
    };
  });
}

function renderTargets(){
  ['params','body'].forEach(loc=>{
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
      const src=JSON.parse(e.dataTransfer.getData('text/plain')||'{}');
      if(!src.field_id) return;
      mappings[loc].push({
        sheet_id:src.sheet, field_id:src.field_id, field:src.field, alias:src.field
      });
      renderTargets();
    };
  });
}

$('saveBtn').onclick=async()=>{
  const payload={
    id:USER_ID||undefined,
    endpoint:$('endpoint').value.trim(), method:$('method').value,
    encoding:'UTF-8', auth:$('auth').value, note:$('note').value.trim(),
    sheet_ids:selectedSheets, joins:joins, sheet_fields:sheetFields,
    param_mapping:mappings.params, body_mapping:mappings.body,
    timeout:parseInt($('timeout').value)||10, retry:parseInt($('retry').value)||0,
    rate_limit:parseInt($('rate_limit').value)||0
  };
  const r=USER_ID?await apiFetch('/api/trans-apis/'+USER_ID,'PUT',payload):await apiPost('/api/trans-apis',payload);
  if(r.ok) location.href='/panel/trans-user';
  else { const e=$('formError'); e.style.display='block'; e.textContent=r.error; }
};

normalizeSheets(availableSheets);
