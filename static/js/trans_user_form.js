// TRANS User 表单：选数据表、字段开关、拖拽字段到 Params/Body 并改名、JOIN
const $ = id => document.getElementById(id);
const INIT = window.USER_INIT || {};
const USER_ID = window.USER_ID || null;
const ALL_SHEETS = window.ALL_SHEETS || [];
let selectedSheets = (INIT.sheet_ids || []).slice();
let sheetFields = Object.assign({}, INIT.sheet_fields || {});
let mappings = {
  params: (INIT.param_mapping || []).slice(),
  body: (INIT.body_mapping || []).slice()
};
let joins = (INIT.joins || []).slice();
let sheetMeta = {};  // sheet_id -> {name, fields}

function esc(s){ return String(s??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// ---------- 选择数据表弹窗 ----------
$('chooseSheetsBtn').onclick=async()=>{
  // 从接口拿最新（含字段）
  const r=await apiGet('/api/trans-user/sheets');
  const sheets=r.ok?r.sheets:ALL_SHEETS;
  const box=$('sheetsList');
  box.innerHTML=sheets.map(s=>{
    const checked=selectedSheets.includes(s.id)?'checked':'';
    return `<label class="sheet-opt"><input type="checkbox" class="sheet-check" value="${esc(s.id)}" ${checked}> <b>${esc(s.name)}</b> <span class="muted">(${s.fields.length} 字段)</span></label>`;
  }).join('')||'<p class="empty">没有数据表，请先创建 TRANS Origin API。</p>';
  box._sheets=sheets;
  $('sheetsModal').classList.add('show');
};
$('sheetsCancel').onclick=()=>$('sheetsModal').classList.remove('show');
$('sheetsApply').onclick=()=>{
  const box=$('sheetsList');
  selectedSheets=[...box.querySelectorAll('.sheet-check:checked')].map(c=>c.value);
  (box._sheets||[]).forEach(s=>{
    sheetMeta[s.id]={name:s.name, fields:s.fields.map(f=>f.name)};
    if(!sheetFields[s.id]){
      sheetFields[s.id]={}; s.fields.forEach(f=>sheetFields[s.id][f.name]=true);
    }
    // 保留用户已存的开关（INIT.sheet_fields 已在初始加载时保留）
  });
  renderSheetViews(); renderTargets();
  $('sheetsModal').classList.remove('show');
};

// 初始化 sheetMeta（从 ALL_SHEETS）
ALL_SHEETS.forEach(s=>sheetMeta[s.id]={name:s.name, fields:(s.fields||[]).map(f=>f.name)});

// ---------- 数据表结构视图 ----------
function renderSheetViews(){
  $('sheetViews').innerHTML=selectedSheets.map(sid=>{
    const meta=sheetMeta[sid]; if(!meta) return '';
    const fields=(meta.fields||[]).map(f=>{
      const on=(sheetFields[sid]||{})[f.name]!==false;
      return `<div class="schema-field" draggable="true" data-sheet="${esc(sid)}" data-field="${esc(f.name)}">
        <span>${esc(f.name)}</span>
        <button type="button" class="dot ${on?'on':'off'}" data-sheet="${esc(sid)}" data-field="${esc(f.name)}"></button>
      </div>`;
    }).join('');
    return `<div class="schema-card">
      <div class="schema-title">${esc(meta.name)}</div>
      ${fields||'<div class="hint">无字段</div>'}
    </div>`;
  }).join('');
  // 字段开关
  $('sheetViews').querySelectorAll('.dot').forEach(d=>d.onclick=()=>{
    const sid=d.dataset.sheet, f=d.dataset.field;
    sheetFields[sid]=sheetFields[sid]||{};
    sheetFields[sid][f]=!sheetFields[sid][f];
    d.classList.toggle('on',sheetFields[sid][f]); d.classList.toggle('off',!sheetFields[sid][f]);
  });
  // 拖拽：字段拖到另一个字段上 = JOIN；拖到 Params/Body = 映射
  $('sheetViews').querySelectorAll('.schema-field').forEach(p=>{
    p.ondragstart=e=>{ e.dataTransfer.setData('text/plain',JSON.stringify({sheet:p.dataset.sheet,field:p.dataset.field})); };
    p.ondragover=e=>e.preventDefault();
    p.ondrop=e=>{
      e.preventDefault();
      try{
        const src=JSON.parse(e.dataTransfer.getData('text/plain'));
        if(src.sheet&&src.field&&e.target.closest('.schema-field')){
          const tgt=e.target.closest('.schema-field');
          joins.push({left_sheet:src.sheet,left_field:src.field,right_sheet:tgt.dataset.sheet,right_field:tgt.dataset.field});
          alert('已建立关联：'+src.field+' = '+tgt.dataset.field);
        }
      }catch(err){}
    };
  });
}

// ---------- Params/Body 目标区 ----------
function renderTargets(){
  ['params','body'].forEach(loc=>{
    const box=$('target'+loc.charAt(0).toUpperCase()+loc.slice(1));
    box.innerHTML=mappings[loc].map((m,i)=>{
      const meta=sheetMeta[m.sheet_id];
      return `<div class="mapping-item" data-i="${i}" data-loc="${loc}">
        <span class="src-field">${esc(meta?meta.name:'?')}.${esc(m.field)}</span>
        <input class="alias-input" value="${esc(m.alias||m.field)}" placeholder="对外 key">
        <button type="button" class="btn btn-xs btn-danger map-del"><i class="bi bi-x"></i></button>
      </div>`;
    }).join('');
    box.querySelectorAll('.map-del').forEach(b=>b.onclick=()=>{
      const i=parseInt(b.closest('.mapping-item').dataset.i); mappings[loc].splice(i,1); renderTargets();
    });
    box.querySelectorAll('.alias-input').forEach(inp=>inp.onchange=()=>{
      const item=inp.closest('.mapping-item'); mappings[item.dataset.loc][parseInt(item.dataset.i)].alias=inp.value.trim();
    });
    box.ondragover=e=>e.preventDefault();
    box.ondrop=e=>{
      e.preventDefault();
      try{
        const src=JSON.parse(e.dataTransfer.getData('text/plain'));
        if(src.sheet&&src.field){
          mappings[loc].push({sheet_id:src.sheet,field:src.field,alias:src.field}); renderTargets();
        }
      }catch(err){}
    };
  });
}

// ---------- 保存 ----------
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

// 初始渲染：若有已选数据表则恢复视图
if(selectedSheets.length){ renderSheetViews(); renderTargets(); }
