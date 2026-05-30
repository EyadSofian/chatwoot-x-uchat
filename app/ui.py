"""Self-contained browser upload UI (no build step, no external deps).

Served at GET / so the operator can drag-drop the CSV/XLSX exports straight
from the browser — no command line required. The page uploads each file
sequentially to POST /upload and polls GET /status.
"""

UPLOAD_PAGE = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UChat → Chatwoot — رفع الملفات</title>
<style>
  :root{
    --bg:#0f172a; --card:#ffffff; --ink:#0f172a; --muted:#64748b;
    --line:#e2e8f0; --accent:#6366f1; --accent2:#8b5cf6;
    --ok:#16a34a; --warn:#d97706; --bad:#dc2626;
  }
  *{box-sizing:border-box}
  body{
    margin:0; font-family:"Segoe UI",Tahoma,system-ui,sans-serif;
    background:linear-gradient(160deg,#0f172a,#1e1b4b 60%,#312e81);
    color:var(--ink); min-height:100vh; padding:32px 16px;
  }
  .wrap{max-width:840px;margin:0 auto}
  .head{color:#fff;text-align:center;margin-bottom:24px}
  .head h1{margin:0 0 6px;font-size:26px;font-weight:800}
  .head p{margin:0;color:#c7d2fe;font-size:14px}
  .card{
    background:var(--card);border-radius:18px;padding:22px;
    box-shadow:0 20px 50px rgba(0,0,0,.35);margin-bottom:18px;
  }
  .drop{
    border:2px dashed var(--accent);border-radius:14px;padding:34px 18px;
    text-align:center;cursor:pointer;transition:.18s;background:#f8f7ff;
  }
  .drop:hover,.drop.over{background:#eef2ff;border-color:var(--accent2);transform:translateY(-1px)}
  .drop .big{font-size:40px;margin-bottom:8px}
  .drop .t{font-weight:700;font-size:16px}
  .drop .s{color:var(--muted);font-size:13px;margin-top:4px}
  input[type=file]{display:none}
  .files{margin-top:14px;max-height:220px;overflow:auto;border:1px solid var(--line);border-radius:10px}
  .files:empty{display:none}
  .frow{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);font-size:13px}
  .frow:last-child{border-bottom:0}
  .frow .nm{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%}
  .frow .sz{color:var(--muted)}
  .btns{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
  button{
    border:0;border-radius:10px;padding:12px 18px;font-size:14px;font-weight:700;
    cursor:pointer;transition:.15s;font-family:inherit;
  }
  .primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;flex:1;min-width:160px}
  .primary:disabled{opacity:.5;cursor:not-allowed}
  .ghost{background:#f1f5f9;color:var(--ink)}
  .bar{height:8px;background:#e2e8f0;border-radius:99px;overflow:hidden;margin-top:16px;display:none}
  .bar.on{display:block}
  .bar > i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .3s}
  table{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}
  th,td{padding:8px 10px;text-align:center;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:700;background:#f8fafc}
  td.file{text-align:right;font-weight:600;max-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:700}
  .pill.ok{background:#dcfce7;color:var(--ok)}
  .pill.bad{background:#fee2e2;color:var(--bad)}
  .pill.run{background:#fef3c7;color:var(--warn)}
  h2{font-size:16px;margin:0 0 10px}
  .badges{display:flex;gap:8px;flex-wrap:wrap}
  .badge{background:#f1f5f9;border-radius:10px;padding:10px 14px;font-size:13px;min-width:110px;text-align:center}
  .badge b{display:block;font-size:20px;margin-top:2px}
  .muted{color:var(--muted);font-size:12px;margin-top:10px}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1>UChat → Chatwoot</h1>
    <p>ارفع ملفات العملاء (CSV / XLSX) — اسحبهم بالماوس، من غير أي أوامر</p>
  </div>

  <div class="card">
    <label class="drop" id="drop">
      <div class="big">📤</div>
      <div class="t">اسحب الملفات هنا أو دوس للاختيار</div>
      <div class="s">تقدر تختار كل الملفات مرة واحدة • CSV, XLSX, XLS</div>
      <input type="file" id="picker" multiple accept=".csv,.xlsx,.xls">
    </label>

    <div class="files" id="files"></div>

    <div class="btns">
      <button class="primary" id="go" disabled>ابدأ الرفع</button>
      <button class="ghost" id="clear">مسح القائمة</button>
    </div>

    <div class="bar" id="bar"><i></i></div>
    <div id="results"></div>
  </div>

  <div class="card">
    <h2>📊 حالة الترحيل (Live)</h2>
    <div class="badges" id="statBadges"><span class="muted">دوس "تحديث" لعرض التقدم…</span></div>
    <div class="btns"><button class="ghost" id="refresh">🔄 تحديث الحالة</button></div>
    <div class="muted">العميل بيتنقل في الخلفية بواسطة الـ worker — متابعة لحظية لكل الـ contacts.</div>
  </div>
</div>

<script>
  const picker=document.getElementById('picker'), drop=document.getElementById('drop'),
        filesBox=document.getElementById('files'), go=document.getElementById('go'),
        clearBtn=document.getElementById('clear'), bar=document.getElementById('bar'),
        barFill=bar.querySelector('i'), results=document.getElementById('results'),
        statBadges=document.getElementById('statBadges'), refresh=document.getElementById('refresh');
  let queue=[];

  const fmt=n=> n<1024?n+' B': n<1048576?(n/1024).toFixed(1)+' KB':(n/1048576).toFixed(1)+' MB';

  function render(){
    filesBox.innerHTML = queue.map(f=>
      `<div class="frow"><span class="nm">📄 ${f.name}</span><span class="sz">${fmt(f.size)}</span></div>`).join('');
    go.disabled = queue.length===0;
    go.textContent = queue.length ? `ابدأ الرفع (${queue.length} ملف)` : 'ابدأ الرفع';
  }
  function addFiles(list){
    for(const f of list){
      if(/\\.(csv|xlsx|xls)$/i.test(f.name) && !queue.some(q=>q.name===f.name && q.size===f.size)) queue.push(f);
    }
    render();
  }

  picker.addEventListener('change', e=> addFiles(e.target.files));
  clearBtn.addEventListener('click', ()=>{ queue=[]; results.innerHTML=''; render(); });
  ;['dragover','dragenter'].forEach(ev=> drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('over');}));
  ;['dragleave','drop'].forEach(ev=> drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('over');}));
  drop.addEventListener('drop', e=> addFiles(e.dataTransfer.files));

  go.addEventListener('click', async ()=>{
    go.disabled=true; clearBtn.disabled=true; bar.classList.add('on');
    let rows='', tot={new:0,dup:0,filt:0,ok:0,fail:0};
    results.innerHTML='<table><thead><tr><th>الملف</th><th>جديد</th><th>مكرر</th><th>اتفلتر</th><th>الحالة</th></tr></thead><tbody id="tb"></tbody></table>';
    const tb=document.getElementById('tb');
    for(let i=0;i<queue.length;i++){
      const f=queue[i];
      tb.insertAdjacentHTML('beforeend',
        `<tr id="r${i}"><td class="file" title="${f.name}">${f.name}</td><td>—</td><td>—</td><td>—</td><td><span class="pill run">جارٍ…</span></td></tr>`);
      try{
        const fd=new FormData(); fd.append('file', f);
        const res=await fetch('/upload',{method:'POST',body:fd});
        const j=await res.json();
        if(!res.ok) throw new Error(j.detail||('HTTP '+res.status));
        tot.new+=j.queued_new; tot.dup+=j.duplicates_ignored; tot.filt+=j.filtered_out_by_date; tot.ok++;
        document.getElementById('r'+i).innerHTML=
          `<td class="file" title="${f.name}">${f.name}</td><td>${j.queued_new}</td><td>${j.duplicates_ignored}</td><td>${j.filtered_out_by_date}</td><td><span class="pill ok">تم ✓</span></td>`;
      }catch(err){
        tot.fail++;
        document.getElementById('r'+i).innerHTML=
          `<td class="file" title="${f.name}">${f.name}</td><td>—</td><td>—</td><td>—</td><td><span class="pill bad" title="${err.message}">فشل ✕</span></td>`;
      }
      barFill.style.width = Math.round(((i+1)/queue.length)*100)+'%';
    }
    tb.insertAdjacentHTML('beforeend',
      `<tr style="font-weight:800;background:#f8fafc"><td class="file">الإجمالي (${tot.ok} نجح / ${tot.fail} فشل)</td><td>${tot.new}</td><td>${tot.dup}</td><td>${tot.filt}</td><td>—</td></tr>`);
    go.disabled=false; clearBtn.disabled=false;
    loadStatus();
  });

  async function loadStatus(){
    statBadges.innerHTML='<span class="muted">جاري التحميل…</span>';
    try{
      const j=await (await fetch('/status')).json();
      const s=j.by_status||{};
      const order=[['pending','قيد الانتظار'],['processing','شغّال'],['done','اتنقل ✓'],['empty','بلا رسائل'],['skipped','اتفلتر'],['failed','فشل']];
      let html=order.filter(([k])=>s[k]!=null).map(([k,lbl])=>
        `<div class="badge">${lbl}<b>${s[k]}</b></div>`).join('');
      html+=`<div class="badge" style="background:#eef2ff">رسائل مُرحّلة<b>${j.messages_injected||0}</b></div>`;
      statBadges.innerHTML=html||'<span class="muted">لسه مفيش بيانات.</span>';
    }catch(e){ statBadges.innerHTML='<span class="muted">تعذّر تحميل الحالة.</span>'; }
  }
  refresh.addEventListener('click', loadStatus);
  render();
</script>
</body>
</html>"""
