// Development OS — Pekanbaru Housing Market Reference
// Isolated frontend module. It does not modify the site-planning solver.
// Raw source records are preserved; only derived market statistics exclude price <= Rp1,000,000.

(() => {
  'use strict';

  const PARTS = [
    '/static/data/pekanbaru_housing_market.part01.b64',
    '/static/data/pekanbaru_housing_market.part02.b64',
    '/static/data/pekanbaru_housing_market.part03.b64',
    '/static/data/pekanbaru_housing_market.part04.b64',
    '/static/data/pekanbaru_housing_market.part05.b64',
    '/static/data/pekanbaru_housing_market.part06.b64',
    '/static/data/pekanbaru_housing_market.part07.b64',
  ];
  const SOURCE_ID = 'market-housing-source';
  const R5_SOURCE = 'market-radius-primary-source';
  const R7_SOURCE = 'market-radius-extended-source';
  const POINT_LAYER = 'market-housing-points';
  const VALID_PRICE_MIN_RP = 1_000_000;

  let marketDataset = null;
  let marketReady = false;
  let lastGeometryKey = '';
  let refreshing = false;

  const el = id => document.getElementById(id);
  const esc = v => String(v ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = v => {
    const n = Number(v);
    if (!Number.isFinite(n) || n <= 0) return '—';
    return 'Rp' + Math.round(n).toLocaleString('id-ID');
  };
  const blankFC = () => ({type:'FeatureCollection',features:[]});

  function injectStyles(){
    if(el('market-reference-style')) return;
    const style=document.createElement('style');
    style.id='market-reference-style';
    style.textContent=`
      .market-reference-panel .market-intro{margin:-2px 0 10px}
      .market-reference-panel .market-switch{display:flex;align-items:center;gap:8px;margin:9px 0 10px;font-size:12px;font-weight:700}
      .market-reference-panel .market-kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}
      .market-reference-panel .market-kpi{border:1px solid #e4e7ec;border-radius:9px;padding:8px;background:#f8fafc}
      .market-reference-panel .market-kpi span{display:block;font-size:10px;color:#667085}
      .market-reference-panel .market-kpi b{display:block;margin-top:2px;font-size:13px;color:#172033}
      .market-reference-panel .market-note{font-size:10.5px;color:#667085;line-height:1.45;margin-top:7px}
      .market-reference-panel .market-nearest{max-height:190px;overflow:auto;border-top:1px solid #eaecf0;margin-top:9px;padding-top:7px}
      .market-reference-panel .market-row{display:grid;grid-template-columns:1fr auto;gap:8px;padding:6px 0;border-bottom:1px solid #f0f2f5;font-size:10.5px}
      .market-reference-panel .market-row strong{display:block;font-size:11px;color:#172033}
      .market-reference-panel .market-row small{display:block;color:#667085;margin-top:2px}
      .market-reference-panel .market-row .market-price{text-align:right;font-weight:800;white-space:nowrap}
      .market-reference-panel .market-status{padding:8px;border-radius:8px;background:#f6f8fb;font-size:11px;color:#475467}
      .market-reference-panel select,.market-reference-panel input[type=number]{width:100%}
      .market-popup{font:12px/1.45 system-ui;min-width:260px;max-width:360px}
      .market-popup h3{margin:0 0 4px;font-size:14px}
      .market-popup .sub{color:#667085;margin-bottom:7px}
      .market-popup table{border-collapse:collapse;width:100%;margin-top:7px;font-size:10.5px}
      .market-popup th,.market-popup td{border-bottom:1px solid #eaecf0;padding:4px;text-align:left}
    `;
    document.head.appendChild(style);
  }

  function injectPanel(){
    if(el('marketReferencePanel')) return;
    const aside=document.querySelector('aside');
    if(!aside) return;
    const panel=document.createElement('div');
    panel.className='panel market-reference-panel';
    panel.id='marketReferencePanel';
    panel.innerHTML=`
      <div class="panel-title-row">
        <h2>Market Reference Pekanbaru</h2>
        <span id="marketDataBadge" class="mini-badge">memuat…</span>
      </div>
      <p class="small market-intro">Spatial comparable dari data perumahan yang di-join ke Development OS. Polygon aktif otomatis dibandingkan dengan proyek dalam radius 5–7 km.</p>
      <label class="market-switch"><input id="marketLayerToggle" type="checkbox" checked /> Tampilkan comparable pada peta</label>
      <div class="grid2">
        <div><label>Radius utama (km)</label><input id="marketRadius" type="number" value="5" min="1" max="20" step="0.5" /></div>
        <div><label>Radius extended (km)</label><input id="marketRadiusExtended" type="number" value="7" min="1" max="30" step="0.5" /></div>
        <div><label>Kategori harga</label><select id="marketCategory"><option value="all">Semua tipe</option><option value="komersil">Komersil</option><option value="subsidi">Subsidi</option></select></div>
        <div><label>Basis floor</label><div class="readonly-chip">P25 comparable</div></div>
      </div>
      <div id="marketReferenceSummary" class="market-status">Pilih/gambar polygon untuk menghitung comparable.</div>
      <div id="marketReferenceKpis" class="market-kpis" style="display:none"></div>
      <div id="marketReferenceNearest" class="market-nearest" style="display:none"></div>
      <div class="market-note">Seluruh data mentah tetap dipertahankan. Harga ≤ Rp1 juta yang ditandai perlu validasi tidak dipakai dalam statistik pasar agar tidak merusak floor/median.</div>
    `;
    const firstPanel=aside.querySelector('.panel');
    if(firstPanel) firstPanel.insertAdjacentElement('afterend',panel);
    else aside.prepend(panel);

    ['marketRadius','marketRadiusExtended','marketCategory'].forEach(id=>{
      el(id)?.addEventListener('change',()=>refreshFromCurrentGeometry(true));
    });
    el('marketLayerToggle')?.addEventListener('change',applyLayerVisibility);
  }

  async function loadDataset(){
    const chunks=await Promise.all(PARTS.map(async url=>{
      const r=await fetch(url,{cache:'no-store'});
      if(!r.ok) throw new Error(`gagal memuat ${url} (${r.status})`);
      return (await r.text()).trim();
    }));
    const b64=chunks.join('').replace(/\s+/g,'');
    const raw=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
    if(typeof DecompressionStream==='undefined') throw new Error('browser tidak mendukung DecompressionStream(gzip)');
    const stream=new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'));
    return JSON.parse(await new Response(stream).text());
  }

  function collectCoordinates(g,out=[]){
    if(!g) return out;
    const walk=v=>{
      if(Array.isArray(v)&&v.length>=2&&typeof v[0]==='number'&&typeof v[1]==='number') out.push([Number(v[0]),Number(v[1])]);
      else if(Array.isArray(v)) v.forEach(walk);
    };
    walk(g.coordinates);
    return out;
  }

  function geometryCenter(g){
    const pts=collectCoordinates(g,[]);
    if(!pts.length) return null;
    const unique=[];
    for(const p of pts){
      if(!unique.length || p[0]!==unique[unique.length-1][0] || p[1]!==unique[unique.length-1][1]) unique.push(p);
    }
    const lon=unique.reduce((s,p)=>s+p[0],0)/unique.length;
    const lat=unique.reduce((s,p)=>s+p[1],0)/unique.length;
    return {lon,lat};
  }

  function haversineKm(a,b){
    const R=6371.0088, rad=x=>x*Math.PI/180;
    const p1=rad(a.lat),p2=rad(b.lat),dp=rad(b.lat-a.lat),dl=rad(b.lon-a.lon);
    const q=Math.sin(dp/2)**2+Math.cos(p1)*Math.cos(p2)*Math.sin(dl/2)**2;
    return 2*R*Math.asin(Math.sqrt(q));
  }

  function marketCategory(record){
    const cats=new Set((record.types||[]).map(t=>String(t.category||'').toLowerCase()));
    if(cats.has('subsidi')&&cats.has('komersil')) return 'mixed';
    if(cats.has('komersil')) return 'komersil';
    if(cats.has('subsidi')) return 'subsidi';
    return 'none';
  }

  function comparableTypes(record,category){
    const types=record.types||[];
    if(category==='all') return types;
    return types.filter(t=>String(t.category||'').toLowerCase()===category);
  }

  function percentile(sorted,q){
    if(!sorted.length) return null;
    if(sorted.length===1) return sorted[0];
    const pos=(sorted.length-1)*q,lo=Math.floor(pos),hi=Math.ceil(pos),f=pos-lo;
    return lo===hi?sorted[lo]:sorted[lo]*(1-f)+sorted[hi]*f;
  }

  function priceStats(projects,category){
    const prices=[];
    for(const p of projects){
      for(const t of comparableTypes(p,category)){
        const n=Number(t.price);
        if(Number.isFinite(n)&&n>VALID_PRICE_MIN_RP) prices.push(n);
      }
    }
    prices.sort((a,b)=>a-b);
    return {
      count:prices.length,
      min:prices[0]??null,
      p10:percentile(prices,.10),
      p25:percentile(prices,.25),
      median:percentile(prices,.50),
      p75:percentile(prices,.75),
      max:prices[prices.length-1]??null
    };
  }

  function circlePolygon(center,radiusKm,steps=96){
    const coords=[],latRad=center.lat*Math.PI/180;
    for(let i=0;i<=steps;i++){
      const a=2*Math.PI*i/steps;
      const dLat=(radiusKm/111.32)*Math.sin(a);
      const dLon=(radiusKm/(111.32*Math.max(Math.cos(latRad),0.05)))*Math.cos(a);
      coords.push([center.lon+dLon,center.lat+dLat]);
    }
    return {type:'FeatureCollection',features:[{type:'Feature',properties:{radius_km:radiusKm},geometry:{type:'Polygon',coordinates:[coords]}}]};
  }

  function ensureMapLayers(){
    if(typeof map==='undefined'||!map||!map.loaded()) return false;
    if(!map.getSource(R7_SOURCE)) map.addSource(R7_SOURCE,{type:'geojson',data:blankFC()});
    if(!map.getSource(R5_SOURCE)) map.addSource(R5_SOURCE,{type:'geojson',data:blankFC()});
    if(!map.getSource(SOURCE_ID)) map.addSource(SOURCE_ID,{type:'geojson',data:blankFC()});

    if(!map.getLayer('market-radius-extended-fill')) map.addLayer({id:'market-radius-extended-fill',type:'fill',source:R7_SOURCE,paint:{'fill-color':'#7c3aed','fill-opacity':0.025}});
    if(!map.getLayer('market-radius-extended-line')) map.addLayer({id:'market-radius-extended-line',type:'line',source:R7_SOURCE,paint:{'line-color':'#7c3aed','line-width':1.5,'line-dasharray':[3,2]}});
    if(!map.getLayer('market-radius-primary-fill')) map.addLayer({id:'market-radius-primary-fill',type:'fill',source:R5_SOURCE,paint:{'fill-color':'#155eef','fill-opacity':0.035}});
    if(!map.getLayer('market-radius-primary-line')) map.addLayer({id:'market-radius-primary-line',type:'line',source:R5_SOURCE,paint:{'line-color':'#155eef','line-width':2,'line-dasharray':[2,2]}});
    if(!map.getLayer(POINT_LAYER)) {
      map.addLayer({
        id:POINT_LAYER,type:'circle',source:SOURCE_ID,
        paint:{
          'circle-radius':['interpolate',['linear'],['zoom'],9,3,13,5,16,7],
          'circle-color':['match',['get','market_category'],'komersil','#f59e0b','mixed','#7c3aed','subsidi','#2563eb','#64748b'],
          'circle-opacity':0.9,'circle-stroke-color':'#fff','circle-stroke-width':1.4
        }
      });
      map.on('mouseenter',POINT_LAYER,()=>{map.getCanvas().style.cursor='pointer';});
      map.on('mouseleave',POINT_LAYER,()=>{map.getCanvas().style.cursor='';});
      map.on('click',POINT_LAYER,e=>{
        const p=e.features?.[0]?.properties||{};
        let types=[];
        try{ types=JSON.parse(p.types_json||'[]'); }catch{}
        const rows=types.length?`<table><thead><tr><th>Tipe</th><th>Kategori</th><th>Harga</th><th>LB/LT</th></tr></thead><tbody>${types.map(t=>`<tr><td>${esc(t.type)}</td><td>${esc(t.category)}</td><td>${money(t.price)}</td><td>${esc(t.building??'—')}/${esc(t.land??'—')}</td></tr>`).join('')}</tbody></table>`:'<div>Data tipe/harga tidak tersedia.</div>';
        const html=`<div class="market-popup"><h3>${esc(p.name)}</h3><div class="sub">${esc(p.village)}, ${esc(p.district)} • ${Number(p.distance_km||0).toFixed(2)} km</div><b>${esc(p.developer)}</b><div>${esc(p.association)} • ${esc(p.market_category)}</div><div style="margin-top:5px">Unit: ${esc(p.subsidized_units)} subsidi • ${esc(p.commercial_units)} komersil</div><div>Kontak: ${esc(p.phone)}${p.whatsapp&&p.whatsapp!=='-'?' • WA '+esc(p.whatsapp):''}</div><div>Email: ${esc(p.email)}</div>${rows}</div>`;
        new maplibregl.Popup({closeButton:true,closeOnClick:true}).setLngLat(e.lngLat).setHTML(html).addTo(map);
      });
    }
    applyLayerVisibility();
    return true;
  }

  function applyLayerVisibility(){
    if(typeof map==='undefined'||!map) return;
    const visible=el('marketLayerToggle')?.checked!==false?'visible':'none';
    ['market-radius-extended-fill','market-radius-extended-line','market-radius-primary-fill','market-radius-primary-line',POINT_LAYER].forEach(id=>{
      if(map.getLayer(id)) map.setLayoutProperty(id,'visibility',visible);
    });
  }

  function buildFeature(project){
    return {
      type:'Feature',
      geometry:{type:'Point',coordinates:[Number(project.lon),Number(project.lat)]},
      properties:{
        id:project.id,name:project.name,developer:project.developer,association:project.association,
        district:project.district,village:project.village,phone:project.phone,whatsapp:project.whatsapp,email:project.email,
        subsidized_units:project.subsidized_units,commercial_units:project.commercial_units,
        distance_km:project.distance_km,market_category:marketCategory(project),
        types_json:JSON.stringify(project.types||[])
      }
    };
  }

  function renderResult(primary,extended,category){
    const s5=priceStats(primary,category),s7=priceStats(extended,category);
    const k=el('marketReferenceKpis'),nearest=el('marketReferenceNearest'),summary=el('marketReferenceSummary');
    if(summary) summary.innerHTML=`Comparable aktif: <b>${primary.length}</b> proyek ≤ ${Number(el('marketRadius')?.value||5).toFixed(1)} km • <b>${extended.length}</b> proyek ≤ ${Number(el('marketRadiusExtended')?.value||7).toFixed(1)} km.`;
    if(k){
      k.style.display='grid';
      k.innerHTML=`
        <div class="market-kpi"><span>Floor P25 • radius utama</span><b>${money(s5.p25)}</b></div>
        <div class="market-kpi"><span>Median • radius utama</span><b>${money(s5.median)}</b></div>
        <div class="market-kpi"><span>Floor P25 • extended</span><b>${money(s7.p25)}</b></div>
        <div class="market-kpi"><span>Median • extended</span><b>${money(s7.median)}</b></div>`;
    }
    if(nearest){
      nearest.style.display='block';
      const rows=extended.slice(0,8);
      nearest.innerHTML=rows.length?`<div class="small" style="font-weight:800;margin-bottom:3px">Comparable terdekat</div>${rows.map(p=>{
        const valid=comparableTypes(p,category).map(t=>Number(t.price)).filter(n=>Number.isFinite(n)&&n>VALID_PRICE_MIN_RP).sort((a,b)=>a-b);
        return `<div class="market-row"><div><strong>${esc(p.name)}</strong><small>${Number(p.distance_km).toFixed(2)} km • ${esc(p.village)}, ${esc(p.district)} • ${esc(p.developer)}</small></div><div class="market-price">${money(valid[0])}</div></div>`;
      }).join('')}`:'<div class="small">Tidak ada comparable dalam radius.</div>';
    }
  }

  async function refreshFromCurrentGeometry(force=false){
    if(!marketReady||refreshing) return;
    if(typeof currentGeometry==='undefined'||!currentGeometry) return;
    const key=JSON.stringify(currentGeometry);
    if(!force&&key===lastGeometryKey) return;
    lastGeometryKey=key;
    refreshing=true;
    try{
      if(!ensureMapLayers()) return;
      const center=geometryCenter(currentGeometry);
      if(!center) return;
      let r5=Math.max(0.5,Number(el('marketRadius')?.value||5));
      let r7=Math.max(r5,Number(el('marketRadiusExtended')?.value||7));
      if(el('marketRadiusExtended')&&Number(el('marketRadiusExtended').value)<r5) el('marketRadiusExtended').value=String(r5);
      const category=el('marketCategory')?.value||'all';
      const matched=[];
      for(const raw of marketDataset.records||[]){
        if(raw.lat==null||raw.lon==null) continue;
        const d=haversineKm(center,{lat:Number(raw.lat),lon:Number(raw.lon)});
        if(d<=r7){
          if(category!=='all'&&!comparableTypes(raw,category).length) continue;
          matched.push({...raw,distance_km:d});
        }
      }
      matched.sort((a,b)=>a.distance_km-b.distance_km||String(a.name).localeCompare(String(b.name),'id'));
      const primary=matched.filter(p=>p.distance_km<=r5);
      const extended=matched;
      map.getSource(R5_SOURCE)?.setData(circlePolygon(center,r5));
      map.getSource(R7_SOURCE)?.setData(circlePolygon(center,r7));
      map.getSource(SOURCE_ID)?.setData({type:'FeatureCollection',features:extended.map(buildFeature)});
      applyLayerVisibility();
      renderResult(primary,extended,category);
    }catch(err){
      const s=el('marketReferenceSummary');
      if(s) s.textContent='Market reference gagal: '+err.message;
      console.error('Market reference error:',err);
    }finally{refreshing=false;}
  }

  async function initMarketReference(){
    injectStyles();
    injectPanel();
    try{
      marketDataset=await loadDataset();
      marketReady=true;
      const m=marketDataset.metadata||{};
      if(el('marketDataBadge')) el('marketDataBadge').textContent=`${m.record_count??(marketDataset.records||[]).length} proyek`;
      if(el('marketReferenceSummary')) el('marketReferenceSummary').innerHTML=`Dataset siap: <b>${m.record_count??(marketDataset.records||[]).length}</b> proyek • <b>${m.mapped_count??'—'}</b> bertitik koordinat. Pilih/gambar polygon untuk comparable 5–7 km.`;
      const waitForMap=setInterval(()=>{
        if(ensureMapLayers()){
          clearInterval(waitForMap);
          refreshFromCurrentGeometry(true);
        }
      },300);
      setTimeout(()=>clearInterval(waitForMap),15000);
      setInterval(()=>refreshFromCurrentGeometry(false),800);
    }catch(err){
      console.error('Market dataset load error:',err);
      if(el('marketDataBadge')) el('marketDataBadge').textContent='gagal';
      if(el('marketReferenceSummary')) el('marketReferenceSummary').textContent='Gagal memuat market dataset: '+err.message;
    }
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',initMarketReference,{once:true});
  else initMarketReference();
})();
