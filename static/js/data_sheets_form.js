(function(){
  const err=document.getElementById('formError');
  const box=document.getElementById('fieldRows');
  let fields = (window.SHEET_INIT && window.SHEET_INIT.fields) ? window.SHEET_INIT.fields.slice() : [];

  function showError(msg){ err.textContent=msg||''; err.style.display=msg?'block':'none'; }

  function fieldRow(field, index){
    const row=document.createElement('div');
    row.className='kv-row';
    row.innerHTML='<input class="f-key" placeholder="key" value="'+esc(field.key||'')+'">'+
      '<input class="f-label" placeholder="label" value="'+esc(field.label||field.key||'')+'">'+
      '<select class="f-type"><option>string</option><option>number</option><option>boolean</option><option>object</option><option>array</option><option>null</option></select>'+
      '<input class="f-order" type="number" min="0" value="'+esc(index)+'">'+
      '<label class="inline-check"><input class="f-enabled" type="checkbox"'+(field.enabled===false?'':' checked')+'> on</label>'+
      '<button type="button" class="btn btn-xs btn-danger f-del">&times;</button>';
    row.querySelector('.f-type').value=field.json_type||'string';
    row.querySelector('.f-order').value=field.order===undefined?index:field.order;
    row.querySelector('.f-del').onclick=()=>row.remove();
    return row;
  }

  function render(){
    box.innerHTML='';
    fields.forEach((field,index)=>box.appendChild(fieldRow(field,index)));
  }

  function collect(){
    return [...box.children].map((row,index)=>({
      key: row.querySelector('.f-key').value.trim(),
      label: row.querySelector('.f-label').value.trim(),
      json_type: row.querySelector('.f-type').value,
      order: parseInt(row.querySelector('.f-order').value||index,10),
      enabled: row.querySelector('.f-enabled').checked
    })).filter(field=>field.key);
  }

  document.getElementById('addField').onclick=()=>box.appendChild(fieldRow({}, box.children.length));
  document.getElementById('discoverBtn').onclick=async()=>{
    showError('');
    const r=await apiFetch('/api/data-sheets/discover-fields','POST',{
      sample_json:document.getElementById('sampleJson').value
    });
    if(!r.ok){showError(r.error||'Discovery failed');return;}
    fields=r.fields||[];
    render();
  };
  document.getElementById('saveBtn').onclick=async()=>{
    showError('');
    const payload={
      name:document.getElementById('sheetName').value.trim(),
      description:document.getElementById('sheetDescription').value.trim(),
      fields:collect()
    };
    const id=window.SHEET_INIT&&window.SHEET_INIT.id;
    const r=await apiFetch(id?'/api/data-sheets/'+id:'/api/data-sheets',id?'PUT':'POST',payload);
    if(!r.ok){showError(r.error||'Save failed');return;}
    location.href='/panel/data-sheets';
  };
  render();
})();
