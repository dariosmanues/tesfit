const DEVOS_FRONTEND_VERSION = "2.5.13";
const API = '';
let currentGeometry = null;
let sitePlan = null;
let selectedAlternative = null;
let map = null;
let drawControl = null;
let manualEditEnabled = false;
let selectedEdit = null;
let dragState = null;
let editHistory = [];
let baselineByAltId = new Map();
let editTool = 'select';
let roadDrawPoints = [];
let handleDrag = null;
let selectedItems = [];
let boxSelectState = null;
let redoHistory = [];
let suppressClickOnce = false;
let optimizerRunning = false;
let restoringOptimizationBaseline = false;

const $ = (id) => document.getElementById(id);
const msg = (text, type='') => { $('message').textContent = text; $('message').className = `message ${type}`; };
const fmtM2 = n => n == null ? '—' : `${Number(n).toLocaleString('id-ID',{maximumFractionDigits:2})} m²`;
const fmtM = n => n == null ? '—' : `${Number(n).toLocaleString('id-ID',{maximumFractionDigits:2})} m`;

function landOptimizationEnabled(){ return !!$('landOptimizationToggle')?.checked; }
function updateOptimizationModeUI(){
  const on=landOptimizationEnabled();
  $('yieldOptimizerPanel')?.classList.toggle('optimizer-off',!on);
  if($('optimizeYieldBtn')) $('optimizeYieldBtn').disabled=!on || !selectedAlternative || optimizerRunning;
  if($('yieldStatus') && !optimizerRunning){
    if(!on){ $('yieldStatus').textContent='Nonaktif • skenario awal'; $('yieldStatus').className='validation neutral'; }
    else if(selectedAlternative?.stats?.optimized){ const eff=Number(selectedAlternative.stats.lot_efficiency_pct||0); $('yieldStatus').textContent=`Aktif • Efisiensi ${eff.toFixed(2)}%`; $('yieldStatus').className=`validation ${eff>=70?'ok':'warn'}`; }
    else { $('yieldStatus').textContent='Aktif • siap optimasi'; $('yieldStatus').className='validation neutral'; }
  }
  if(!on && $('yieldResult')){ $('yieldResult').className='yield-result empty'; $('yieldResult').textContent='Optimalisasi lahan nonaktif. Layout memakai skenario awal.'; }
}
async function restoreBaselineScenario(){
  if(!selectedAlternative||!sitePlan) return;
  const base=baselineByAltId.get(selectedAlternative.id);
  if(!base) return;
  restoringOptimizationBaseline=true;
  try{
    const restored=deepClone(base);
    replaceSelectedAlternative(restored);
    renderAlternativeCards(sitePlan.alternatives);
    renderAlternative(restored);
    updateGeoSource('reserve-source',blankFC());
    updateGeoSource('residual-source',blankFC());
    msg('Optimalisasi lahan OFF. Layout dikembalikan ke skenario generate awal.','success');
  }finally{restoringOptimizationBaseline=false; updateOptimizationModeUI();}
}
async function onLandOptimizationToggle(){
  updateOptimizationModeUI();
  if(!landOptimizationEnabled()){ await restoreBaselineScenario(); return; }
  if(selectedAlternative && sitePlan && !selectedAlternative.stats?.optimized) await optimizeYield();
  else if(!selectedAlternative) msg('Optimalisasi lahan ON. Akan dijalankan setelah Generate Alternatif Layout.','success');
}

async function api(path, options={}) {
  const res = await fetch(API + path, {...options, cache:'no-store'});
  const raw = await res.text();
  let body = {};
  if (raw) {
    try { body = JSON.parse(raw); }
    catch { body = {detail: raw}; }
  }
  if (!res.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail || body || {});
    throw new Error(`${detail || `HTTP ${res.status}`} [${path}]`);
  }
  return body;
}

function blankFC(){ return {type:'FeatureCollection', features:[]}; }
function geometryFC(g, props={}){ return g ? {type:'FeatureCollection',features:[{type:'Feature',properties:props,geometry:g}]} : blankFC(); }
function lotsFC(lots,details=[]){ return {type:'FeatureCollection',features:(lots||[]).map((g,i)=>{const d=(details||[])[i]||{};return {type:'Feature',properties:{lot:i+1,featureType:'lot',index:i,parcelType:d.parcel_type||'standard',parcelId:d.id||`K-${i+1}`,area_m2:d.area_m2||0,frontage_m:d.frontage_m||0,depth_est_m:d.depth_est_m||0},geometry:g};})}; }
function residualsFC(items){ return {type:'FeatureCollection',features:(items||[]).filter(x=>x?.geometry).map(x=>({type:'Feature',properties:{id:x.id,area_m2:x.area_m2,classification:x.classification},geometry:x.geometry}))}; }
function roadSegmentsFC(segments){ return {type:'FeatureCollection',features:(segments||[]).filter(s=>s?.polygon).map((s,i)=>({type:'Feature',properties:{roadIndex:i,roadId:s.id||`R${i+1}`,kind:s.kind||'local',width_m:s.width_m||0},geometry:s.polygon}))}; }
function roadCenterlinesFC(segments){ return {type:'FeatureCollection',features:(segments||[]).filter(s=>s?.centerline).map((s,i)=>({type:'Feature',properties:{roadIndex:i,roadId:s.id||`R${i+1}`},geometry:s.centerline}))}; }
function deepClone(v){ return v == null ? v : JSON.parse(JSON.stringify(v)); }
function walkCoords(coords, fn){
  if(Array.isArray(coords) && typeof coords[0] === 'number') return fn(coords);
  return Array.isArray(coords) ? coords.map(c=>walkCoords(c,fn)) : coords;
}
function geometryCentroidApprox(g){
  const pts=[]; walkCoords(g.coordinates,p=>{pts.push(p); return p;});
  if(!pts.length) return [0,0];
  return [pts.reduce((a,p)=>a+p[0],0)/pts.length, pts.reduce((a,p)=>a+p[1],0)/pts.length];
}
function translateGeometryDegrees(g, dLon, dLat){
  const out=deepClone(g); out.coordinates=walkCoords(out.coordinates,p=>[p[0]+dLon,p[1]+dLat,...p.slice(2)]); return out;
}
function translateGeometryMeters(g, eastM, northM){
  const [,lat]=geometryCentroidApprox(g);
  const dLat=northM/111320;
  const dLon=eastM/(111320*Math.max(Math.cos(lat*Math.PI/180),0.1));
  return translateGeometryDegrees(g,dLon,dLat);
}
function rotateGeometry(g, angleDeg){
  const [lon0,lat0]=geometryCentroidApprox(g), rad=angleDeg*Math.PI/180, c=Math.cos(rad), sn=Math.sin(rad);
  const mLon=111320*Math.max(Math.cos(lat0*Math.PI/180),0.1), mLat=111320;
  const out=deepClone(g);
  out.coordinates=walkCoords(out.coordinates,p=>{
    const x=(p[0]-lon0)*mLon, y=(p[1]-lat0)*mLat;
    const xr=x*c-y*sn, yr=x*sn+y*c;
    return [lon0+xr/mLon,lat0+yr/mLat,...p.slice(2)];
  });
  return out;
}
function rotateGeometryAround(g, angleDeg, center){
  const [lon0,lat0]=center, rad=angleDeg*Math.PI/180, c=Math.cos(rad), sn=Math.sin(rad);
  const mLon=111320*Math.max(Math.cos(lat0*Math.PI/180),0.1), mLat=111320;
  const out=deepClone(g);
  out.coordinates=walkCoords(out.coordinates,p=>{
    const x=(p[0]-lon0)*mLon, y=(p[1]-lat0)*mLat;
    const xr=x*c-y*sn, yr=x*sn+y*c;
    return [lon0+xr/mLon,lat0+yr/mLat,...p.slice(2)];
  });
  return out;
}
function firstRing(g){
  if(!g) return [];
  if(g.type==='Polygon') return g.coordinates?.[0]||[];
  if(g.type==='MultiPolygon') return g.coordinates?.[0]?.[0]||[];
  return [];
}
function geometryAngleDegrees(g){
  if(!g) return 0;
  if(g.type==='LineString'||g.type==='MultiLineString'){
    const ep=lineEndpoints(g); if(ep.length<2) return 0;
    const a=ep[0],b=ep[1], lat=(a[1]+b[1])/2*Math.PI/180;
    return ((Math.atan2((b[1]-a[1])*111320,(b[0]-a[0])*111320*Math.cos(lat))*180/Math.PI)%180+180)%180;
  }
  const ring=firstRing(g); if(ring.length<2) return 0;
  let best=null;
  for(let i=0;i<ring.length-1;i++){
    const a=ring[i],b=ring[i+1],lat=(a[1]+b[1])/2*Math.PI/180;
    const dx=(b[0]-a[0])*111320*Math.cos(lat),dy=(b[1]-a[1])*111320,len=Math.hypot(dx,dy);
    if(!best||len>best.len) best={len,angle:((Math.atan2(dy,dx)*180/Math.PI)%180+180)%180};
  }
  return best?.angle||0;
}
function polygonEdgeMetrics(g){
  const ring=firstRing(g); const out=[];
  for(let i=0;i<ring.length-1;i++){
    const a=ring[i],b=ring[i+1],lat=(a[1]+b[1])/2*Math.PI/180;
    const dx=(b[0]-a[0])*111320*Math.cos(lat),dy=(b[1]-a[1])*111320;
    const len=Math.hypot(dx,dy); if(len>0.01) out.push({len,angle:normalizeAngle(Math.atan2(dy,dx)*180/Math.PI)});
  }
  return out;
}
function lotFrontageAngleDegrees(g){
  const e=polygonEdgeMetrics(g).sort((a,b)=>a.len-b.len); return e[0]?.angle ?? geometryAngleDegrees(g);
}
function lotDimensionsMeters(g){
  const e=polygonEdgeMetrics(g).map(x=>x.len).sort((a,b)=>a-b); if(!e.length)return [0,0];
  const unique=[]; for(const n of e){ if(!unique.some(x=>Math.abs(x-n)<0.05))unique.push(n); }
  return [unique[0]||e[0],unique[unique.length-1]||e[e.length-1]];
}
function lineEndpoints(g){
  if(!g) return [];
  if(g.type==='LineString'){
    const c=g.coordinates||[]; return c.length?[c[0],c[c.length-1]]:[];
  }
  if(g.type==='MultiLineString'){
    const parts=g.coordinates||[]; if(!parts.length) return [];
    const a=parts[0]?.[0], last=parts[parts.length-1]||[], b=last[last.length-1]; return a&&b?[a,b]:[];
  }
  return [];
}
function setLineEndpoint(g, endpointIndex, coord){
  const out=deepClone(g);
  if(out.type==='LineString'){
    if(endpointIndex===0) out.coordinates[0]=coord; else out.coordinates[out.coordinates.length-1]=coord;
  } else if(out.type==='MultiLineString'){
    if(endpointIndex===0) out.coordinates[0][0]=coord; else { const i=out.coordinates.length-1; out.coordinates[i][out.coordinates[i].length-1]=coord; }
  }
  return out;
}
function handlesFC(seg){
  const pts=lineEndpoints(seg?.centerline);
  return {type:'FeatureCollection',features:pts.map((c,i)=>({type:'Feature',properties:{endpoint:i},geometry:{type:'Point',coordinates:c}}))};
}
function normalizeAngle(a){ a=Number(a)||0; a=((a%180)+180)%180; return a; }
function makeRectangleAt(center,widthM,depthM,angleDeg){
  const [lon0,lat0]=center,mLon=111320*Math.max(Math.cos(lat0*Math.PI/180),0.1),mLat=111320;
  const hw=widthM/2,hd=depthM/2,rad=angleDeg*Math.PI/180,c=Math.cos(rad),sn=Math.sin(rad);
  const pts=[[-hw,-hd],[hw,-hd],[hw,hd],[-hw,hd],[-hw,-hd]].map(([x,y])=>{
    const xr=x*c-y*sn,yr=x*sn+y*c; return [lon0+xr/mLon,lat0+yr/mLat];
  });
  return {type:'Polygon',coordinates:[pts]};
}
function updateGeoSource(id, data){ if(!map) return; const s=map.getSource(id); if(s) s.setData(data); }

function bboxOfGeometry(g){
  const coords=[];
  const walk=v=>{ if(Array.isArray(v)&&typeof v[0]==='number') coords.push(v); else if(Array.isArray(v)) v.forEach(walk); };
  walk(g.coordinates);
  const xs=coords.map(c=>c[0]), ys=coords.map(c=>c[1]);
  return [[Math.min(...xs),Math.min(...ys)],[Math.max(...xs),Math.max(...ys)]];
}

function clearLayout(){
  exitManualEdit(false);
  ['buildable-source','roads-source','road-segments-source','road-centerlines-source','rth-source','psu-source','reserve-source','drainage-source','lots-source','residual-source','selection-source','edit-handles-source','road-draft-source','smart-adjust-source'].forEach(id=>updateGeoSource(id,blankFC()));
  selectedAlternative=null; sitePlan=null; $('saveBtn').disabled=true; if($('optimizeYieldBtn')) $('optimizeYieldBtn').disabled=true; if($('yieldStatus')){$('yieldStatus').textContent='Belum dioptimasi';$('yieldStatus').className='validation neutral';} if($('yieldResult')){$('yieldResult').className='yield-result empty';$('yieldResult').textContent='Generate dan pilih satu alternatif terlebih dahulu.';}
  $('alternativeCards').innerHTML='<div class="empty-state">Generate layout untuk melihat beberapa opsi otomatis.</div>';
  $('altSummary').textContent='belum ada';
  ['buildArea','lotCount','efficiency','roadArea','roadLength','rthArea','psuArea','reserveArea','drainageLength','orientation','unusedArea','landUtilization','residualRatio','roadEfficiency'].forEach(id=>$(id).textContent='—');
}

function setCurrentGeometry(g, stats=null){
  currentGeometry = g;
  $('generateBtn').disabled = false;
  updateGeoSource('parcel-source', geometryFC(g));
  clearLayout();
  try{ if(map) map.fitBounds(bboxOfGeometry(g), {padding:70,maxZoom:18}); }catch{}
  $('parcelArea').textContent = stats ? fmtM2(stats.area_m2) : '—';
  $('utm').textContent = stats?.utm_epsg ? `EPSG:${stats.utm_epsg}` : '—';
  msg('Polygon siap. Atur parameter lalu generate beberapa alternatif layout.');
}

async function statsFor(g){
  const r=await api('/geometry/stats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry:g})});
  setCurrentGeometry(r.geometry,r.stats);
}

function initMap(){
  map = new maplibregl.Map({
    container:'map',
    style:{version:8,sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},layers:[{id:'osm-basemap',type:'raster',source:'osm'}]},
    center:[101.447,0.507], zoom:12, maxPitch:70
  });
  map.addControl(new maplibregl.NavigationControl(), 'top-right');
  map.on('load',()=>{
    const add=(id)=>map.addSource(id,{type:'geojson',data:blankFC()});
    add('parcel-source'); add('buildable-source'); add('roads-source'); add('road-segments-source'); add('road-centerlines-source'); add('rth-source'); add('psu-source'); add('reserve-source'); add('drainage-source'); add('lots-source'); add('residual-source'); add('selection-source'); add('edit-handles-source'); add('road-draft-source'); add('smart-adjust-source');
    map.addLayer({id:'buildable-fill',type:'fill',source:'buildable-source',paint:{'fill-color':'#f4a261','fill-opacity':0.08}});
    map.addLayer({id:'reserve-fill',type:'fill',source:'reserve-source',paint:{'fill-color':'#84cc16','fill-opacity':0.26}});
    map.addLayer({id:'reserve-line',type:'line',source:'reserve-source',paint:{'line-color':'#65a30d','line-width':1.4,'line-dasharray':[2,2]}});
    map.addLayer({id:'residual-fill',type:'fill',source:'residual-source',paint:{'fill-color':['case',['==',['get','classification'],'large_residual'],'#f59e0b','#fde68a'],'fill-opacity':0.20}});
    map.addLayer({id:'residual-line',type:'line',source:'residual-source',paint:{'line-color':'#d97706','line-width':1.5,'line-dasharray':[3,2]}});
    map.addLayer({id:'rth-fill',type:'fill',source:'rth-source',paint:{'fill-color':'#55a630','fill-opacity':0.58}});
    map.addLayer({id:'psu-fill',type:'fill',source:'psu-source',paint:{'fill-color':'#8e63ce','fill-opacity':0.56}});
    map.addLayer({id:'roads-fill',type:'fill',source:'roads-source',paint:{'fill-color':'#4b5563','fill-opacity':0.25}});
    map.addLayer({id:'road-segments-fill',type:'fill',source:'road-segments-source',paint:{'fill-color':['case',['==',['get','kind'],'main'],'#374151','#5b6470'],'fill-opacity':0.86}});
    map.addLayer({id:'road-centerlines-line',type:'line',source:'road-centerlines-source',paint:{'line-color':'#f3f4f6','line-width':1,'line-dasharray':[2,2]}});
    map.addLayer({id:'lots-fill',type:'fill',source:'lots-source',paint:{'fill-color':['case',['==',['get','parcelType'],'residual'],'#f59e0b','#2a9d8f'],'fill-opacity':['case',['==',['get','parcelType'],'residual'],0.52,0.38]}});
    map.addLayer({id:'lots-line',type:'line',source:'lots-source',paint:{'line-color':['case',['==',['get','parcelType'],'residual'],'#b45309','#176b61'],'line-width':['case',['==',['get','parcelType'],'residual'],1.8,1]}});
    map.addLayer({id:'drainage-line',type:'line',source:'drainage-source',paint:{'line-color':'#1976d2','line-width':2}});
    map.addLayer({id:'parcel-fill',type:'fill',source:'parcel-source',paint:{'fill-color':'#0f4c5c','fill-opacity':0.03}});
    map.addLayer({id:'parcel-line',type:'line',source:'parcel-source',paint:{'line-color':'#0f4c5c','line-width':3}});
    map.addLayer({id:'buildable-line',type:'line',source:'buildable-source',paint:{'line-color':'#e76f51','line-width':1.5,'line-dasharray':[2,1]}});
    map.addLayer({id:'selection-fill',type:'fill',source:'selection-source',paint:{'fill-color':'#ffd166','fill-opacity':0.35}});
    map.addLayer({id:'selection-line',type:'line',source:'selection-source',paint:{'line-color':'#ff8c00','line-width':4}});
    map.addLayer({id:'smart-adjust-fill',type:'fill',source:'smart-adjust-source',paint:{'fill-color':'#ffd166','fill-opacity':0.58}});
    map.addLayer({id:'smart-adjust-line',type:'line',source:'smart-adjust-source',paint:{'line-color':'#f59e0b','line-width':3}});
    map.addLayer({id:'road-draft-line',type:'line',source:'road-draft-source',paint:{'line-color':'#ef4444','line-width':3,'line-dasharray':[2,2]}});
    map.addLayer({id:'edit-handles-circle',type:'circle',source:'edit-handles-source',paint:{'circle-radius':7,'circle-color':'#ffd166','circle-stroke-color':'#7c4a00','circle-stroke-width':2}});

    map.on('click','lots-fill',(e)=>{
      const f=e.features?.[0]; if(!f) return;
      const p=f.properties||{};
      const isResidual=p.parcelType==='residual';
      const html=`<div style="font:12px/1.45 system-ui;min-width:180px"><b>${isResidual?'Kavling Adaptive (Lahan Sisa)':'Kavling Standar'} ${p.parcelId||''}</b><br>Luas: <b>${Number(p.area_m2||0).toLocaleString('id-ID',{maximumFractionDigits:2})} m²</b><br>Frontage: <b>${Number(p.frontage_m||0).toFixed(2)} m</b><br>Depth estimasi: <b>${Number(p.depth_est_m||0).toFixed(2)} m</b></div>`;
      new maplibregl.Popup({closeButton:true,closeOnClick:true}).setLngLat(e.lngLat).setHTML(html).addTo(map);
    });
    map.on('mouseenter','lots-fill',()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave','lots-fill',()=>{if(!manualEditEnabled)map.getCanvas().style.cursor='';});

    drawControl = new MaplibreTerradrawControl.MaplibreTerradrawControl({modes:['polygon','select','delete-selection','delete'], open:true});
    map.addControl(drawControl,'top-left');
    installManualEditHandlers();
  });
}

async function useDrawn(){
  try{
    const fc=drawControl?.getFeatures();
    const polys=(fc?.features||[]).filter(f=>['Polygon','MultiPolygon'].includes(f.geometry?.type));
    if(!polys.length) throw new Error('Belum ada polygon yang digambar.');
    await statsFor(polys[polys.length-1].geometry);
  }catch(e){msg(e.message,'error');}
}

async function useManual(){
  try{
    msg('Memproses koordinat…');
    const r=await api('/geometry/from-coordinates',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('manualText').value,epsg:Number($('manualEpsg').value||4326),order:$('manualOrder').value})});
    setCurrentGeometry(r.geometry,r.stats);
  }catch(e){msg(e.message,'error');}
}

async function uploadFile(){
  const file=$('fileInput').files[0];
  if(!file){msg('Pilih file terlebih dahulu.','error');return;}
  const fd=new FormData(); fd.append('file',file);
  if($('fileEpsg').value) fd.append('epsg',$('fileEpsg').value);
  try{
    msg(`Import ${file.name}…`);
    const r=await api('/geometry/import',{method:'POST',body:fd});
    setCurrentGeometry(r.geometry,r.stats);
    msg(`${file.name} berhasil diimport.`,'success');
  }catch(e){msg(e.message,'error');}
}

function renderAlternative(alt){
  exitManualEdit(false);
  selectedAlternative=alt;
  document.querySelectorAll('.alt-card').forEach(x=>x.classList.toggle('selected',x.dataset.id===alt.id));
  updateGeoSource('parcel-source',geometryFC(sitePlan.parcel));
  updateGeoSource('buildable-source',geometryFC(alt.buildable));
  updateGeoSource('roads-source',geometryFC(alt.roads));
  updateGeoSource('road-segments-source',roadSegmentsFC(alt.road_segments));
  updateGeoSource('road-centerlines-source',roadCenterlinesFC(alt.road_segments));
  updateGeoSource('rth-source',geometryFC(alt.rth));
  updateGeoSource('psu-source',geometryFC(alt.psu));
  updateGeoSource('reserve-source',landOptimizationEnabled()?geometryFC(alt.reserve):blankFC());
  updateGeoSource('drainage-source',geometryFC(alt.drainage));
  updateGeoSource('lots-source',lotsFC(alt.lots,alt.lot_details||[]));
  updateGeoSource('residual-source',landOptimizationEnabled()?residualsFC(alt.residuals||[]):blankFC());

  const s=alt.stats, p=sitePlan.parcel_stats;
  $('parcelArea').textContent=fmtM2(p.parcel_area_m2);
  $('buildArea').textContent=fmtM2(p.buildable_area_m2);
  $('lotCount').textContent=`${s.lot_count} unit`;
  if($('standardLotCount')) $('standardLotCount').textContent=`${s.standard_lot_count??s.lot_count} unit`;
  if($('adaptiveLotCount')) $('adaptiveLotCount').textContent=`${s.adaptive_lot_count??0} unit`;
  const eff=Number(s.lot_efficiency_pct||0);
  const effPass=eff>=70.0;
  $('efficiency').innerHTML=`<b>${eff.toFixed(2)}%</b> <span class="mini-status ${effPass?'pass':'warn'}">${effPass?'PASS':'FAIL'} (min 70%)</span>`;
  $('roadArea').textContent=`${fmtM2(s.road_area_m2)} (${s.road_pct}%)`;
  $('roadLength').textContent=fmtM(s.road_length_m);
  $('rthArea').textContent=`${fmtM2(s.rth_area_m2)} (${s.rth_pct}%)`;
  $('psuArea').textContent=`${fmtM2(s.psu_area_m2)} (${s.psu_pct}%)`;
  if($('reserveArea')) $('reserveArea').textContent=`${fmtM2(s.reserve_area_m2||0)} (${Number(s.reserve_pct||0).toFixed(2)}%)`;
  $('drainageLength').textContent=fmtM(s.drainage_length_m);
  $('orientation').textContent=`${Number(alt.angle_deg||0).toFixed(2)}° • ${alt.pattern}`;
  if($('layoutAngleEdit')) $('layoutAngleEdit').value=Number(alt.angle_deg||0).toFixed(2);
  $('utm').textContent=`EPSG:${p.utm_epsg}`;
  $('unusedArea').textContent=fmtM2(s.unused_area_m2);
  if($('landUtilization')) $('landUtilization').textContent=s.land_utilization_pct!=null?`${Number(s.land_utilization_pct).toFixed(2)}%`:'—';
  if($('residualRatio')) {
    const rr=s.residual_pct_total_land!=null?s.residual_pct_total_land:((Number(s.unused_area_m2||0)/Math.max(Number(p.parcel_area_m2||0),1e-9))*100);
    $('residualRatio').textContent=rr!=null?`${Number(rr).toFixed(2)}%`:'—';
  }
  if($('roadEfficiency')) $('roadEfficiency').textContent=s.road_efficiency!=null?Number(s.road_efficiency).toFixed(3):'—';
  if($('blockRegularity')) $('blockRegularity').textContent=s.average_block_regularity!=null?Number(s.average_block_regularity).toFixed(3):'—';
  if($('roadConnectivity')) $('roadConnectivity').textContent=s.road_connectivity_score!=null?Number(s.road_connectivity_score).toFixed(3):'—';
  updateOptimizationModeUI();
  const optOn=landOptimizationEnabled();
  $('saveBtn').disabled=optOn ? !(s.optimized===true && effPass && s.validation_passed===true) : false;
  updateManualValidation(s);
  msg(`${alt.name} dipilih.`, 'success');
}

function renderAlternativeCards(alts){
  $('alternativeCards').innerHTML='';
  $('altSummary').textContent=`${alts.length} opsi`;
  alts.forEach(alt=>{
    const c=document.createElement('button');
    c.className='alt-card'; c.dataset.id=alt.id;
    const eff=Number(alt.stats.lot_efficiency_pct||0);
    const effTag=eff>=70.0?'<span class="eff-tag-pass">≥70% PASS</span>':'<span class="eff-tag-fail">&lt;70% FAIL</span>';
    c.innerHTML=`<div class="alt-head"><span class="rank">#${alt.rank}</span><strong>${alt.name}</strong>${alt.recommended?'<span class="recommended">Rekomendasi</span>':''}${alt.stats?.manual_adjusted?'<span class="manual-tag">Manual</span>':''}${alt.stats?.optimized?'<span class="yield-tag">Best Yield</span>':''} ${effTag}</div>
      <div class="alt-metrics"><span><b>${alt.stats.standard_lot_count??alt.stats.lot_count}</b> Standard</span><span><b>${alt.stats.adaptive_lot_count??0}</b> Adaptive</span><span><b>${eff.toFixed(1)}%</b> efisiensi</span><span><b>${alt.stats.road_pct}%</b> jalan</span><span><b>${Number(alt.stats.residual_pct_total_land||0).toFixed(1)}%</b> sisa</span></div>`;
    c.onclick=async()=>{renderAlternative(alt);if(landOptimizationEnabled()&&!alt.stats?.optimized)await optimizeYield();};
    $('alternativeCards').appendChild(c);
  });
}

async function generateSitePlan(){
  if(!currentGeometry)return;
  try{
    $('generateBtn').disabled=true;
    msg('Generating jalan, RTH, PSU, drainase dan alternatif kavling…');
    const payload={
      geometry:currentGeometry,
      setback_m:Number($('setback').value),
      lot_width_m:Number($('lotWidth').value),
      lot_depth_m:Number($('lotDepth').value),
      main_road_width_m:Number($('mainRoad').value),
      local_road_width_m:Number($('localRoad').value),
      rth_pct:Number($('rthPct').value),
      psu_pct:Number($('psuPct').value),
      alternative_count:Number($('altCount').value),
      lot_efficiency_target_pct:70,
      land_optimization_enabled:landOptimizationEnabled()
    };
    sitePlan=await api('/site-plan/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    baselineByAltId=new Map(sitePlan.alternatives.map(a=>[a.id,deepClone(a)]));
    editHistory=[];
    renderAlternativeCards(sitePlan.alternatives);
    renderAlternative(sitePlan.alternatives[0]);
    msg(`${sitePlan.alternatives.length} alternatif selesai dibuat. Opsi #1 dipilih otomatis.`, 'success');
    if(landOptimizationEnabled()) await optimizeYield();
  }catch(e){msg(e.message,'error');}
  finally{$('generateBtn').disabled=false;}
}

async function saveProject(){
  if(!currentGeometry||!selectedAlternative)return;
  const effPct=Number(selectedAlternative.stats?.lot_efficiency_pct ?? 0);
  if(landOptimizationEnabled() && effPct<70.0){msg(`Mode optimalisasi aktif: layout belum bisa disimpan karena Efisiensi Kavling ${effPct.toFixed(2)}% < 70%.`,'error');return;}
  if(landOptimizationEnabled() && selectedAlternative.stats?.manual_adjusted===true){
    msg('Layout optimal sudah diedit manual. Jalankan Optimasi Ulang agar final validation M2.5.12 dihitung ulang sebelum Save.','error');return;
  }
  if(landOptimizationEnabled() && (selectedAlternative.stats?.validation_passed!==true || (selectedAlternative.validation && !selectedAlternative.validation.valid))){
    msg('Mode optimalisasi aktif: layout belum bisa disimpan karena final validation M2.5.12 belum PASS.','error');return;
  }
  try{
    const r=await api('/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      name:$('projectName').value,
      parcel:currentGeometry,
      buildable:selectedAlternative.buildable,
      lots:selectedAlternative.lots||[],
      layout:selectedAlternative,
      settings:{setback_m:Number($('setback').value),lot_width_m:Number($('lotWidth').value),lot_depth_m:Number($('lotDepth').value),main_road_width_m:Number($('mainRoad').value),local_road_width_m:Number($('localRoad').value),rth_pct:Number($('rthPct').value),psu_pct:Number($('psuPct').value),lot_efficiency_target_pct:70,enforce_lot_efficiency_target:landOptimizationEnabled(),land_optimization_enabled:landOptimizationEnabled()},
      stats:{...selectedAlternative.stats,validation:selectedAlternative.validation||null,alternative_name:selectedAlternative.name,alternative_rank:selectedAlternative.rank}
    })});
    msg(`Project + layout tersimpan. ID #${r.id}`,'success');
  }catch(e){msg(e.message,'error');}
}

function updateManualValidation(stats={}){
  if(!$('manualValidation')) return;
  const outside=Number(stats.lots_outside_buildable||0), overlaps=Number(stats.lot_overlap_pairs||0), eff=Number(stats.lot_efficiency_pct||0);
  if(!landOptimizationEnabled()){
    if(outside===0 && overlaps===0){ $('manualValidation').textContent='Skenario awal • geometri OK'; $('manualValidation').className='validation ok'; }
    else { $('manualValidation').textContent=`Perlu cek: ${outside} di luar • ${overlaps} overlap`; $('manualValidation').className='validation warn'; }
    return;
  }
  if(outside===0 && overlaps===0 && eff>=70.0){ $('manualValidation').textContent='Optimalisasi aktif • geometri + efisiensi OK'; $('manualValidation').className='validation ok'; }
  else { $('manualValidation').textContent=`Optimalisasi aktif: ${outside} di luar • ${overlaps} overlap • efisiensi ${eff.toFixed(2)}%`; $('manualValidation').className='validation warn'; }
}

function setObjectEditorState(type){
  const road=type==='road', lot=type==='lot';
  $('roadEditor')?.classList.toggle('disabled-section',!road);
  $('lotEditor')?.classList.toggle('disabled-section',!lot);
  ['applyRoadBtn','duplicateRoadBtn','deleteRoadBtn'].forEach(id=>{ if($(id)) $(id).disabled=!road; });
  ['applyLotSizeBtn','duplicateBtn','deleteBtn'].forEach(id=>{ if($(id)) $(id).disabled=!lot; });
}

function setSelectedEdit(sel){
  selectedEdit=sel;
  updateGeoSource('edit-handles-source',blankFC());
  if(!sel){
    updateGeoSource('selection-source',blankFC());
    $('editSelection').textContent='Belum ada objek dipilih';
    ['rotateLeftBtn','rotateRightBtn','applyAngleBtn'].forEach(id=>$(id).disabled=true);
    setObjectEditorState(null); return;
  }
  let g=null,label='';
  if(sel.type==='lot'){
    g=selectedAlternative?.lots?.[sel.index];
    const detail=selectedAlternative?.lot_details?.[sel.index]||{};
    label=detail.parcel_type==='residual'
      ? `${detail.id||`R-${sel.index+1}`} • Kavling Adaptive • ${Number(detail.area_m2||0).toLocaleString('id-ID',{maximumFractionDigits:2})} m² • frontage ${Number(detail.frontage_m||0).toFixed(2)} m • depth est. ${Number(detail.depth_est_m||0).toFixed(2)} m`
      : `Kavling #${sel.index+1}${detail.area_m2?` • ${Number(detail.area_m2).toLocaleString('id-ID',{maximumFractionDigits:2})} m²`:''}`;
    const dims=lotDimensionsMeters(g); $('lotWidthEdit').value=(dims[0]||Number($('lotWidth').value||8)).toFixed(2); $('lotDepthEdit').value=(dims[1]||Number($('lotDepth').value||15)).toFixed(2);
  } else if(sel.type==='road'){
    const seg=selectedAlternative?.road_segments?.[sel.index]; g=seg?.polygon; label=`Jalan ${seg?.id||`#${sel.index+1}`} • ${seg?.kind==='main'?'utama':'lingkungan'}`;
    if(seg){ $('roadWidthEdit').value=seg.width_m||6; $('roadKindEdit').value=seg.kind||'local'; updateGeoSource('edit-handles-source',handlesFC(seg)); }
  } else {
    g=selectedAlternative?.[sel.type]; label=sel.type.toUpperCase();
  }
  if(g) updateGeoSource('selection-source',geometryFC(g,{selected:true}));
  $('editSelection').textContent=label;
  const angle=sel.type==='road' ? Number(selectedAlternative?.road_segments?.[sel.index]?.angle_deg ?? geometryAngleDegrees(selectedAlternative?.road_segments?.[sel.index]?.centerline)) : (sel.type==='lot'?lotFrontageAngleDegrees(g):geometryAngleDegrees(g));
  $('objectAngle').value=Number(angle||0).toFixed(2);
  ['rotateLeftBtn','rotateRightBtn','applyAngleBtn'].forEach(id=>$(id).disabled=!g);
  setObjectEditorState(sel.type);
}

function selectedGeometry(){
  if(!selectedEdit||!selectedAlternative) return null;
  if(selectedEdit.type==='lot') return selectedAlternative.lots?.[selectedEdit.index];
  if(selectedEdit.type==='road') return selectedAlternative.road_segments?.[selectedEdit.index]?.polygon;
  return selectedAlternative[selectedEdit.type];
}
function selectedRoadSegment(){ return selectedEdit?.type==='road' ? selectedAlternative?.road_segments?.[selectedEdit.index] : null; }

function replaceSelectedAlternative(alt){
  selectedAlternative=alt;
  const idx=sitePlan?.alternatives?.findIndex(a=>a.id===alt.id) ?? -1;
  if(idx>=0) sitePlan.alternatives[idx]=alt;
}
function pushHistory(){
  if(!selectedAlternative) return;
  editHistory.push(deepClone(selectedAlternative)); if(editHistory.length>40) editHistory.shift(); $('undoBtn').disabled=editHistory.length===0;
}
function refreshSelectedSources(){
  if(!selectedAlternative) return;
  updateGeoSource('roads-source',geometryFC(selectedAlternative.roads));
  updateGeoSource('road-segments-source',roadSegmentsFC(selectedAlternative.road_segments));
  updateGeoSource('road-centerlines-source',roadCenterlinesFC(selectedAlternative.road_segments));
  updateGeoSource('rth-source',geometryFC(selectedAlternative.rth)); updateGeoSource('psu-source',geometryFC(selectedAlternative.psu)); updateGeoSource('reserve-source',geometryFC(selectedAlternative.reserve));
  updateGeoSource('drainage-source',geometryFC(selectedAlternative.drainage)); updateGeoSource('lots-source',lotsFC(selectedAlternative.lots,selectedAlternative.lot_details||[]));
  if(selectedEdit) setSelectedEdit(selectedEdit);
}
async function recalculateManual(showMessage=false){
  if(!selectedAlternative||!sitePlan) return;
  try{
    const r=await api('/site-plan/recalculate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      parcel:sitePlan.parcel,buildable:selectedAlternative.buildable,roads:selectedAlternative.roads,rth:selectedAlternative.rth,psu:selectedAlternative.psu,reserve:landOptimizationEnabled()?selectedAlternative.reserve:null,
      drainage:selectedAlternative.drainage,lots:selectedAlternative.lots||[],previous_stats:selectedAlternative.stats||{},land_optimization_enabled:landOptimizationEnabled()
    })});
    selectedAlternative.stats={...(selectedAlternative.stats||{}),...r.stats};
    if('reserve' in r) selectedAlternative.reserve=r.reserve;
    updateGeoSource('reserve-source',geometryFC(selectedAlternative.reserve));
    const idx=sitePlan.alternatives.findIndex(a=>a.id===selectedAlternative.id); if(idx>=0) sitePlan.alternatives[idx]=selectedAlternative;
    renderAlternativeCards(sitePlan.alternatives); document.querySelectorAll('.alt-card').forEach(x=>x.classList.toggle('selected',x.dataset.id===selectedAlternative.id));
    const s=selectedAlternative.stats,p=sitePlan.parcel_stats;
    $('lotCount').textContent=`${s.lot_count} unit`;
  if($('standardLotCount')) $('standardLotCount').textContent=`${s.standard_lot_count??s.lot_count} unit`;
  if($('adaptiveLotCount')) $('adaptiveLotCount').textContent=`${s.adaptive_lot_count??0} unit`; $('efficiency').textContent=`${s.lot_efficiency_pct}%`;
    $('roadArea').textContent=`${fmtM2(s.road_area_m2)} (${s.road_pct}%)`; $('roadLength').textContent=fmtM(s.road_length_m);
    $('rthArea').textContent=`${fmtM2(s.rth_area_m2)} (${s.rth_pct}%)`; $('psuArea').textContent=`${fmtM2(s.psu_area_m2)} (${s.psu_pct}%)`; if($('reserveArea')) $('reserveArea').textContent=`${fmtM2(s.reserve_area_m2||0)} (${Number(s.reserve_pct||0).toFixed(2)}%)`;
    $('drainageLength').textContent=fmtM(s.drainage_length_m); $('unusedArea').textContent=fmtM2(s.unused_area_m2); $('buildArea').textContent=fmtM2(p.buildable_area_m2);
    $('orientation').textContent=`${Number(selectedAlternative.angle_deg||0).toFixed(2)}° • ${selectedAlternative.pattern}`;
    if($('residualRatio')) $('residualRatio').textContent=`${Number(s.residual_pct_total_land||0).toFixed(2)}%`;
    updateManualValidation(s); const effOk=Number(s.lot_efficiency_pct||0)>=70.0; $('saveBtn').disabled=landOptimizationEnabled()?(!effOk || s.manual_adjusted===true || s.validation_passed!==true):false; if(showMessage) msg('Statistik layout manual dihitung ulang.','success');
  }catch(e){ msg(`Recalculate gagal: ${e.message}`,'error'); }
}
async function rebuildRoadNetwork(doRecalc=true){
  if(!selectedAlternative) return;
  try{
    const segments=(selectedAlternative.road_segments||[]).filter(x=>x?.centerline).map((x,i)=>({id:x.id||`R${i+1}`,kind:x.kind||'local',width_m:Number(x.width_m||6),centerline:x.centerline}));
    const r=await api('/site-plan/roads/rebuild',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({buildable:selectedAlternative.buildable,segments})});
    selectedAlternative.road_segments=r.road_segments||[]; selectedAlternative.roads=r.roads; selectedAlternative.drainage=r.drainage;
    refreshSelectedSources(); if(doRecalc) await recalculateManual(false);
  }catch(e){ msg(`Road rebuild gagal: ${e.message}`,'error'); }
}
function setGeometryForSelection(sel, geometry){
  if(sel.type==='lot') selectedAlternative.lots[sel.index]=geometry;
  else if(sel.type!=='road') selectedAlternative[sel.type]=geometry;
  refreshSelectedSources();
}
function pickEditableFeature(point){
  if(!map||!manualEditEnabled||editTool!=='select') return null;
  const fs=map.queryRenderedFeatures(point,{layers:['edit-handles-circle','lots-fill','road-segments-fill','rth-fill','psu-fill']});
  if(!fs.length) return null; const f=fs[0];
  if(f.layer.id==='edit-handles-circle') return {type:'handle',endpoint:Number(f.properties?.endpoint||0)};
  if(f.layer.id==='lots-fill') return {type:'lot',index:Number(f.properties?.lot||1)-1};
  if(f.layer.id==='road-segments-fill') return {type:'road',index:Number(f.properties?.roadIndex||0)};
  if(f.layer.id==='rth-fill') return {type:'rth'}; if(f.layer.id==='psu-fill') return {type:'psu'}; return null;
}
function setEditTool(tool){
  editTool=tool; roadDrawPoints=[]; updateGeoSource('road-draft-source',blankFC());
  ['toolSelect','toolAddRoad','toolAddLot'].forEach(id=>$(id)?.classList.remove('active'));
  const id=tool==='select'?'toolSelect':tool==='add-road'?'toolAddRoad':'toolAddLot'; $(id)?.classList.add('active');
  if(map) map.getCanvas().style.cursor=tool==='select'?'crosshair':'copy';
  if(tool==='add-road') msg('Tool + Jalan aktif: klik titik awal lalu titik akhir ruas jalan.','success');
  if(tool==='add-lot') msg('Tool + Kavling aktif: klik lokasi pusat kavling.','success');
}
function addLotAt(lngLat){
  if(!selectedAlternative) return; pushHistory();
  const angle=Number($('objectAngle').value||$('layoutAngleEdit').value||0), w=Number($('lotWidthEdit').value||$('lotWidth').value||8), d=Number($('lotDepthEdit').value||$('lotDepth').value||15);
  selectedAlternative.lots=selectedAlternative.lots||[]; selectedAlternative.lots.push(makeRectangleAt([lngLat.lng,lngLat.lat],w,d,angle));
  refreshSelectedSources(); setSelectedEdit({type:'lot',index:selectedAlternative.lots.length-1}); recalculateManual(false); setEditTool('select');
}
async function addRoadPoint(lngLat){
  roadDrawPoints.push([lngLat.lng,lngLat.lat]);
  if(roadDrawPoints.length===1){ msg('Titik awal jalan dipilih. Klik titik akhir.','success'); return; }
  pushHistory();
  const idx=(selectedAlternative.road_segments||[]).length;
  const seg={id:`R${Date.now().toString().slice(-6)}`,kind:$('roadKindEdit').value||'local',width_m:Number($('roadWidthEdit').value||$('localRoad').value||6),centerline:{type:'LineString',coordinates:[roadDrawPoints[0],roadDrawPoints[1]]}};
  selectedAlternative.road_segments=selectedAlternative.road_segments||[]; selectedAlternative.road_segments.push(seg); roadDrawPoints=[]; updateGeoSource('road-draft-source',blankFC());
  await rebuildRoadNetwork(false); setSelectedEdit({type:'road',index:Math.min(idx,(selectedAlternative.road_segments||[]).length-1)}); await recalculateManual(false); setEditTool('select');
}
function installManualEditHandlers(){
  map.on('mousedown',e=>{
    if(!manualEditEnabled||!selectedAlternative||editTool!=='select') return;
    const sel=pickEditableFeature(e.point);
    if(sel?.type==='handle'&&selectedEdit?.type==='road'){
      const seg=selectedRoadSegment(); if(!seg?.centerline) return; pushHistory(); handleDrag={endpoint:sel.endpoint,original:deepClone(seg.centerline)}; map.dragPan.disable(); return;
    }
    if(!sel||sel.type==='handle'){ if(!sel) setSelectedEdit(null); return; }
    setSelectedEdit(sel); pushHistory();
    if(sel.type==='road'){
      const seg=selectedRoadSegment(); dragState={sel,start:[e.lngLat.lng,e.lngLat.lat],centerline:deepClone(seg.centerline),polygon:deepClone(seg.polygon),drainage:deepClone(seg.drainage),moved:false};
    } else dragState={sel,start:[e.lngLat.lng,e.lngLat.lat],geometry:deepClone(selectedGeometry()),moved:false};
    map.dragPan.disable(); map.getCanvas().style.cursor='grabbing'; e.preventDefault?.();
  });
  map.on('mousemove',e=>{
    if(editTool==='add-road'&&roadDrawPoints.length===1){ updateGeoSource('road-draft-source',{type:'FeatureCollection',features:[{type:'Feature',properties:{},geometry:{type:'LineString',coordinates:[roadDrawPoints[0],[e.lngLat.lng,e.lngLat.lat]]}}]}); }
    if(handleDrag&&selectedEdit?.type==='road'){
      const seg=selectedRoadSegment(); seg.centerline=setLineEndpoint(handleDrag.original,handleDrag.endpoint,[e.lngLat.lng,e.lngLat.lat]); updateGeoSource('road-centerlines-source',roadCenterlinesFC(selectedAlternative.road_segments)); updateGeoSource('edit-handles-source',handlesFC(seg)); return;
    }
    if(!dragState) return;
    const dLon=e.lngLat.lng-dragState.start[0],dLat=e.lngLat.lat-dragState.start[1];
    if(dragState.sel.type==='road'){
      const seg=selectedRoadSegment(); seg.centerline=translateGeometryDegrees(dragState.centerline,dLon,dLat); seg.polygon=dragState.polygon?translateGeometryDegrees(dragState.polygon,dLon,dLat):seg.polygon; seg.drainage=dragState.drainage?translateGeometryDegrees(dragState.drainage,dLon,dLat):seg.drainage; refreshSelectedSources();
    } else setGeometryForSelection(dragState.sel,translateGeometryDegrees(dragState.geometry,dLon,dLat));
    dragState.moved=true;
  });
  map.on('mouseup',async()=>{
    if(handleDrag){ handleDrag=null; map.dragPan.enable(); await rebuildRoadNetwork(true); return; }
    if(!dragState) return; const wasRoad=dragState.sel.type==='road',moved=dragState.moved; dragState=null; map.dragPan.enable(); map.getCanvas().style.cursor=manualEditEnabled?'crosshair':'';
    if(moved){ if(wasRoad) await rebuildRoadNetwork(true); else await recalculateManual(false); } else if(editHistory.length){editHistory.pop();$('undoBtn').disabled=editHistory.length===0;}
  });
  map.on('click',async e=>{
    if(!manualEditEnabled||dragState||handleDrag) return;
    if(editTool==='add-road'){ await addRoadPoint(e.lngLat); return; }
    if(editTool==='add-lot'){ addLotAt(e.lngLat); return; }
    const sel=pickEditableFeature(e.point); if(sel&&sel.type!=='handle') setSelectedEdit(sel);
  });
}
function enterManualEdit(){
  if(!selectedAlternative){ msg('Pilih/generate alternatif layout terlebih dahulu.','error'); return; }
  manualEditEnabled=true; $('manualToggle').textContent='Selesai Manual Editor'; $('manualToggle').classList.add('active'); $('manualControls').classList.remove('disabled'); $('resetManualBtn').disabled=false; $('recalcBtn').disabled=false;
  setEditTool('select'); msg('Manual editor aktif. Jalan sekarang dapat dipilih per ruas; angle dapat diketik numerik.','success');
}
function exitManualEdit(clearSelection=true){
  manualEditEnabled=false; dragState=null; handleDrag=null; roadDrawPoints=[]; updateGeoSource('road-draft-source',blankFC());
  if($('manualToggle')){$('manualToggle').textContent='Aktifkan Manual Editor';$('manualToggle').classList.remove('active');} if($('manualControls'))$('manualControls').classList.add('disabled');
  if(map){map.dragPan.enable();map.getCanvas().style.cursor='';} if(clearSelection)setSelectedEdit(null); else updateGeoSource('selection-source',blankFC());
}
function toggleManualEdit(){manualEditEnabled?exitManualEdit(true):enterManualEdit();}
function nudgeSelected(east,north){
  if(!selectedEdit) return; pushHistory(); const step=Number($('nudgeStep').value||0.5);
  if(selectedEdit.type==='road'){
    const seg=selectedRoadSegment(); seg.centerline=translateGeometryMeters(seg.centerline,east*step,north*step); rebuildRoadNetwork(true);
  } else { const g=selectedGeometry(); if(!g)return; setGeometryForSelection(selectedEdit,translateGeometryMeters(g,east*step,north*step)); recalculateManual(false); }
}
function rotateSelected(delta){
  if(!selectedEdit)return; pushHistory();
  if(selectedEdit.type==='road'){
    const seg=selectedRoadSegment(); seg.centerline=rotateGeometry(seg.centerline,delta); rebuildRoadNetwork(true);
  } else { const g=selectedGeometry(); if(!g)return; setGeometryForSelection(selectedEdit,rotateGeometry(g,delta)); $('objectAngle').value=normalizeAngle(selectedEdit.type==='lot'?lotFrontageAngleDegrees(selectedGeometry()):geometryAngleDegrees(selectedGeometry())).toFixed(2); recalculateManual(false); }
}
function applyObjectAngle(){
  if(!selectedEdit)return; const target=normalizeAngle(Number($('objectAngle').value||0)); pushHistory();
  if(selectedEdit.type==='road'){
    const seg=selectedRoadSegment(),current=normalizeAngle(geometryAngleDegrees(seg.centerline)); seg.centerline=rotateGeometry(seg.centerline,target-current); rebuildRoadNetwork(true);
  } else { const g=selectedGeometry(); if(!g)return; const current=normalizeAngle(selectedEdit.type==='lot'?lotFrontageAngleDegrees(g):geometryAngleDegrees(g)); setGeometryForSelection(selectedEdit,rotateGeometry(g,target-current)); recalculateManual(false); }
  $('objectAngle').value=target.toFixed(2);
}
async function applyRoadChanges(){
  const seg=selectedRoadSegment(); if(!seg)return; pushHistory(); seg.width_m=Number($('roadWidthEdit').value||6); seg.kind=$('roadKindEdit').value||'local';
  const target=normalizeAngle(Number($('objectAngle').value||geometryAngleDegrees(seg.centerline))),current=normalizeAngle(geometryAngleDegrees(seg.centerline)); seg.centerline=rotateGeometry(seg.centerline,target-current); await rebuildRoadNetwork(true); setSelectedEdit(selectedEdit);
}
function applyLotSize(){
  if(selectedEdit?.type!=='lot')return; const g=selectedGeometry(); if(!g)return; pushHistory(); const center=geometryCentroidApprox(g),angle=normalizeAngle(Number($('objectAngle').value||geometryAngleDegrees(g)));
  selectedAlternative.lots[selectedEdit.index]=makeRectangleAt(center,Number($('lotWidthEdit').value||8),Number($('lotDepthEdit').value||15),angle); refreshSelectedSources(); recalculateManual(false);
}
function duplicateSelected(){
  if(selectedEdit?.type!=='lot')return; const g=selectedGeometry(); if(!g)return; pushHistory(); selectedAlternative.lots.push(translateGeometryMeters(g,2,2)); refreshSelectedSources(); setSelectedEdit({type:'lot',index:selectedAlternative.lots.length-1}); recalculateManual(false);
}
async function duplicateRoad(){
  const seg=selectedRoadSegment(); if(!seg)return; pushHistory(); const dup=deepClone(seg); dup.id=`R${Date.now().toString().slice(-6)}`; dup.centerline=translateGeometryMeters(dup.centerline,2,2); selectedAlternative.road_segments.push(dup); await rebuildRoadNetwork(false); setSelectedEdit({type:'road',index:selectedAlternative.road_segments.length-1}); await recalculateManual(false);
}
function deleteSelected(){
  if(selectedEdit?.type!=='lot')return; pushHistory(); selectedAlternative.lots.splice(selectedEdit.index,1); setSelectedEdit(null); refreshSelectedSources(); recalculateManual(false);
}
async function deleteRoad(){
  if(selectedEdit?.type!=='road')return; pushHistory(); selectedAlternative.road_segments.splice(selectedEdit.index,1); setSelectedEdit(null); await rebuildRoadNetwork(true);
}
async function applyLayoutAngle(){
  if(!selectedAlternative)return; const target=normalizeAngle(Number($('layoutAngleEdit').value||0)), current=normalizeAngle(Number(selectedAlternative.angle_deg||0)),delta=target-current; if(Math.abs(delta)<1e-9)return; pushHistory();
  const center=geometryCentroidApprox(selectedAlternative.buildable);
  for(const seg of selectedAlternative.road_segments||[]) if(seg.centerline) seg.centerline=rotateGeometryAround(seg.centerline,delta,center);
  selectedAlternative.lots=(selectedAlternative.lots||[]).map(g=>rotateGeometryAround(g,delta,center));
  if(selectedAlternative.rth)selectedAlternative.rth=rotateGeometryAround(selectedAlternative.rth,delta,center); if(selectedAlternative.psu)selectedAlternative.psu=rotateGeometryAround(selectedAlternative.psu,delta,center);
  selectedAlternative.angle_deg=target; await rebuildRoadNetwork(false); refreshSelectedSources(); await recalculateManual(false); $('layoutAngleEdit').value=target.toFixed(2); msg(`Orientasi siteplan diubah menjadi ${target.toFixed(2)}°.`, 'success');
}
function undoManual(){
  if(!editHistory.length||!selectedAlternative)return; const prev=editHistory.pop(); replaceSelectedAlternative(prev); refreshSelectedSources(); setSelectedEdit(null); $('undoBtn').disabled=editHistory.length===0; recalculateManual(false); msg('Perubahan terakhir dibatalkan.','success');
}
function resetManual(){
  if(!selectedAlternative)return; const base=baselineByAltId.get(selectedAlternative.id); if(!base)return; pushHistory(); replaceSelectedAlternative(deepClone(base)); refreshSelectedSources(); setSelectedEdit(null); recalculateManual(false); $('layoutAngleEdit').value=Number(selectedAlternative.angle_deg||0).toFixed(2); msg('Layout dikembalikan ke hasil generate awal.','success');
}

function loadSample(){
  const g={type:'Polygon',coordinates:[[[101.43870,0.51055],[101.44010,0.51057],[101.44013,0.50920],[101.43963,0.50874],[101.43868,0.50916],[101.43870,0.51055]]]};
  statsFor(g).catch(e=>msg(e.message,'error'));
}

function tabs(){
  document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.tabbody').forEach(x=>x.classList.remove('active'));
    b.classList.add('active'); $('tab-'+b.dataset.tab).classList.add('active');
  }));
}

async function health(){
  try{const r=await api('/health'); $('apiBadge').textContent=`API online • ${r.database}`;$('apiBadge').classList.add('ok');}
  catch{$('apiBadge').textContent='API offline';$('apiBadge').classList.add('bad');}
}

function bindActions(){
  $('useDrawn').onclick=useDrawn;
  $('useManual').onclick=useManual;
  $('uploadBtn').onclick=uploadFile;
  $('generateBtn').onclick=generateSitePlan;
  $('saveBtn').onclick=saveProject;
  $('loadSample').onclick=loadSample;
  $('manualToggle').onclick=toggleManualEdit; $('toolSelect').onclick=()=>setEditTool('select'); $('toolAddRoad').onclick=()=>setEditTool('add-road'); $('toolAddLot').onclick=()=>setEditTool('add-lot');
  $('nudgeLeft').onclick=()=>nudgeSelected(-1,0); $('nudgeRight').onclick=()=>nudgeSelected(1,0); $('nudgeUp').onclick=()=>nudgeSelected(0,1); $('nudgeDown').onclick=()=>nudgeSelected(0,-1);
  $('rotateLeftBtn').onclick=()=>rotateSelected(-5); $('rotateRightBtn').onclick=()=>rotateSelected(5); $('applyAngleBtn').onclick=applyObjectAngle;
  $('applyRoadBtn').onclick=applyRoadChanges; $('duplicateRoadBtn').onclick=duplicateRoad; $('deleteRoadBtn').onclick=deleteRoad;
  $('applyLotSizeBtn').onclick=applyLotSize; $('duplicateBtn').onclick=duplicateSelected; $('deleteBtn').onclick=deleteSelected; $('applyLayoutAngleBtn').onclick=applyLayoutAngle;
  $('undoBtn').onclick=undoManual; $('resetManualBtn').onclick=resetManual; $('recalcBtn').onclick=()=>recalculateManual(true); if($('optimizeYieldBtn')) $('optimizeYieldBtn').onclick=optimizeYield;
}


// =============================
// Milestone 2.3 Smart Reflow Editor
// =============================
function selectionKey(item){ return item ? `${item.type}:${item.index ?? ''}` : ''; }
function geometryForItem(item){
  if(!item||!selectedAlternative) return null;
  if(item.type==='lot') return selectedAlternative.lots?.[item.index]||null;
  if(item.type==='road') return selectedAlternative.road_segments?.[item.index]?.polygon||null;
  if(item.type==='rth'||item.type==='psu') return selectedAlternative[item.type]||null;
  return null;
}
function centerlineForItem(item){ return item?.type==='road' ? selectedAlternative?.road_segments?.[item.index]?.centerline : null; }
function validSelectionItem(item){
  if(!item||!selectedAlternative) return false;
  if(item.type==='lot') return !!selectedAlternative.lots?.[item.index];
  if(item.type==='road') return !!selectedAlternative.road_segments?.[item.index];
  if(item.type==='rth'||item.type==='psu') return !!selectedAlternative[item.type];
  return false;
}
function selectionFeatureCollection(items=selectedItems){
  const features=[];
  for(const item of items){
    const g=geometryForItem(item); if(!g) continue;
    features.push({type:'Feature',properties:{selected:true,key:selectionKey(item),featureType:item.type},geometry:g});
  }
  return {type:'FeatureCollection',features};
}
function setSelection(items=[],primary=null){
  const mapByKey=new Map();
  for(const item of items||[]) if(validSelectionItem(item)) mapByKey.set(selectionKey(item),item);
  selectedItems=[...mapByKey.values()];
  selectedEdit=primary&&mapByKey.has(selectionKey(primary))?mapByKey.get(selectionKey(primary)):(selectedItems[selectedItems.length-1]||null);
  updateGeoSource('selection-source',selectionFeatureCollection());
  updateGeoSource('edit-handles-source',blankFC());
  const n=selectedItems.length;
  if(!n){
    $('editSelection').textContent='Belum ada objek dipilih';
    ['rotateLeftBtn','rotateRightBtn','applyAngleBtn','reflowLocalBtn','repackBlockBtn','validateSmartBtn','selectLinkedBtn','duplicateSelectionBtn','deleteSelectionBtn'].forEach(id=>{if($(id))$(id).disabled=true;});
    setObjectEditorState(null); return;
  }
  const counts={lot:0,road:0,rth:0,psu:0}; selectedItems.forEach(x=>counts[x.type]=(counts[x.type]||0)+1);
  const bits=[]; if(counts.lot)bits.push(`${counts.lot} kavling`);if(counts.road)bits.push(`${counts.road} jalan`);if(counts.rth)bits.push('RTH');if(counts.psu)bits.push('PSU');
  $('editSelection').textContent=n===1?bits.join(' • '):`${n} objek • ${bits.join(' • ')}`;
  ['rotateLeftBtn','rotateRightBtn','applyAngleBtn','validateSmartBtn','duplicateSelectionBtn','deleteSelectionBtn'].forEach(id=>{if($(id))$(id).disabled=false;});
  const hasSmart=selectedItems.some(x=>['road','lot','rth','psu'].includes(x.type));
  const hasPackable=selectedItems.some(x=>x.type==='road'||x.type==='lot');
  if($('reflowLocalBtn'))$('reflowLocalBtn').disabled=!hasSmart;if($('repackBlockBtn'))$('repackBlockBtn').disabled=!hasPackable;
  if($('selectLinkedBtn'))$('selectLinkedBtn').disabled=!(n===1&&selectedEdit?.type==='road');
  if(n===1){
    const item=selectedEdit, g=geometryForItem(item); let angle=0;
    if(item.type==='road'){
      const seg=selectedAlternative.road_segments[item.index]; angle=Number(seg?.angle_deg ?? geometryAngleDegrees(seg?.centerline));
      if(seg){$('roadWidthEdit').value=seg.width_m||6;$('roadKindEdit').value=seg.kind||'local';updateGeoSource('edit-handles-source',handlesFC(seg));}
    }else if(item.type==='lot'){
      angle=lotFrontageAngleDegrees(g); const dims=lotDimensionsMeters(g);$('lotWidthEdit').value=(dims[0]||Number($('lotWidth').value||8)).toFixed(2);$('lotDepthEdit').value=(dims[1]||Number($('lotDepth').value||15)).toFixed(2);
    }else angle=geometryAngleDegrees(g);
    $('objectAngle').value=Number(angle||0).toFixed(2); setObjectEditorState(item.type);
  }else{
    const g=geometryForItem(selectedEdit); const angle=selectedEdit?.type==='road'?geometryAngleDegrees(centerlineForItem(selectedEdit)):(selectedEdit?.type==='lot'?lotFrontageAngleDegrees(g):geometryAngleDegrees(g));
    $('objectAngle').value=Number(angle||0).toFixed(2); setObjectEditorState(null);
  }
}
function setSelectedEdit(sel){ setSelection(sel?[sel]:[],sel); }
function selectedGeometry(){ return geometryForItem(selectedEdit); }
function selectedRoadSegment(){ return selectedEdit?.type==='road' ? selectedAlternative?.road_segments?.[selectedEdit.index] : null; }
function refreshSelectedSources(){
  if(!selectedAlternative)return;
  updateGeoSource('roads-source',geometryFC(selectedAlternative.roads));updateGeoSource('road-segments-source',roadSegmentsFC(selectedAlternative.road_segments));updateGeoSource('road-centerlines-source',roadCenterlinesFC(selectedAlternative.road_segments));
  updateGeoSource('rth-source',geometryFC(selectedAlternative.rth));updateGeoSource('psu-source',geometryFC(selectedAlternative.psu));updateGeoSource('drainage-source',geometryFC(selectedAlternative.drainage));updateGeoSource('lots-source',lotsFC(selectedAlternative.lots,selectedAlternative.lot_details||[]));
  selectedItems=selectedItems.filter(validSelectionItem); setSelection(selectedItems,selectedEdit);
}
function setGeometryForSelection(sel,geometry){
  if(sel.type==='lot') selectedAlternative.lots[sel.index]=geometry; else if(sel.type!=='road') selectedAlternative[sel.type]=geometry;
}
function snapAngleValue(v){
  let a=normalizeAngle(v); if($('angleSnapToggle')?.checked){const step=Math.max(1,Number($('angleSnapStep')?.value||5));a=Math.round(a/step)*step;} return normalizeAngle(a);
}
function selectionCenter(items=selectedItems){
  const pts=[];for(const item of items){const g=item.type==='road'?centerlineForItem(item):geometryForItem(item);if(!g)continue;walkCoords(g.coordinates,p=>{pts.push(p);return p;});}
  if(!pts.length)return [0,0];return [pts.reduce((a,p)=>a+p[0],0)/pts.length,pts.reduce((a,p)=>a+p[1],0)/pts.length];
}
function applyGeometryToItem(item,g){
  if(item.type==='lot')selectedAlternative.lots[item.index]=g;
  else if(item.type==='road')selectedAlternative.road_segments[item.index].centerline=g;
  else if(item.type==='rth'||item.type==='psu')selectedAlternative[item.type]=g;
}
function snapshotSelection(items=selectedItems){return items.map(item=>{const road=item.type==='road'?selectedAlternative?.road_segments?.[item.index]:null;return {item:deepClone(item),geometry:deepClone(item.type==='road'?road?.centerline:geometryForItem(item)),polygon:deepClone(road?.polygon),drainage:deepClone(road?.drainage)};}).filter(x=>x.geometry);}
function translateSnapshotDegrees(snapshot,dLon,dLat){
  for(const x of snapshot){const g=translateGeometryDegrees(x.geometry,dLon,dLat);applyGeometryToItem(x.item,g);if(x.item.type==='road'){const seg=selectedAlternative.road_segments[x.item.index];if(x.polygon)seg.polygon=translateGeometryDegrees(x.polygon,dLon,dLat);if(x.drainage)seg.drainage=translateGeometryDegrees(x.drainage,dLon,dLat);}}
  refreshSelectedSources();
}
function rotateSelectionBy(delta){
  if(!selectedItems.length)return;pushHistory();const center=selectionCenter();
  for(const item of selectedItems){const g=item.type==='road'?centerlineForItem(item):geometryForItem(item);if(g)applyGeometryToItem(item,rotateGeometryAround(g,delta,center));}
  const roads=selectedItems.filter(x=>x.type==='road');
  if(roads.length)rebuildRoadNetwork(false).then(()=>runAutoReflowAfterEdit(selectedItems)); else runAutoReflowAfterEdit(selectedItems);
}
function nudgeSelected(east,north){
  if(!selectedItems.length)return;pushHistory();const step=Number($('nudgeStep').value||0.5);const snap=snapshotSelection();
  for(const x of snap){applyGeometryToItem(x.item,translateGeometryMeters(x.geometry,east*step,north*step));}
  refreshSelectedSources();const roads=selectedItems.some(x=>x.type==='road');if(roads)rebuildRoadNetwork(false).then(()=>runAutoReflowAfterEdit(selectedItems));else runAutoReflowAfterEdit(selectedItems);
}
function rotateSelected(delta){
  if(!selectedItems.length)return;let actual=delta;if($('angleSnapToggle')?.checked&&selectedEdit){const g=selectedEdit.type==='road'?centerlineForItem(selectedEdit):geometryForItem(selectedEdit);const cur=selectedEdit.type==='lot'?lotFrontageAngleDegrees(g):geometryAngleDegrees(g);actual=snapAngleValue(cur+delta)-cur;}rotateSelectionBy(actual);
}
function applyObjectAngle(){
  if(!selectedItems.length)return;const target=snapAngleValue(Number($('objectAngle').value||0));const g=selectedEdit?.type==='road'?centerlineForItem(selectedEdit):geometryForItem(selectedEdit);if(!g)return;const cur=normalizeAngle(selectedEdit.type==='lot'?lotFrontageAngleDegrees(g):geometryAngleDegrees(g));$('objectAngle').value=target.toFixed(2);rotateSelectionBy(target-cur);
}
function pushHistory(){if(!selectedAlternative)return;editHistory.push(deepClone(selectedAlternative));if(editHistory.length>50)editHistory.shift();redoHistory=[];$('undoBtn').disabled=editHistory.length===0;if($('redoBtn'))$('redoBtn').disabled=true;}
function undoManual(){if(!editHistory.length||!selectedAlternative)return;redoHistory.push(deepClone(selectedAlternative));const prev=editHistory.pop();replaceSelectedAlternative(prev);refreshSelectedSources();setSelection([]);$('undoBtn').disabled=editHistory.length===0;if($('redoBtn'))$('redoBtn').disabled=redoHistory.length===0;recalculateManual(false);msg('Perubahan terakhir dibatalkan.','success');}
function redoManual(){if(!redoHistory.length||!selectedAlternative)return;editHistory.push(deepClone(selectedAlternative));const next=redoHistory.pop();replaceSelectedAlternative(next);refreshSelectedSources();setSelection([]);$('undoBtn').disabled=false;$('redoBtn').disabled=redoHistory.length===0;recalculateManual(false);msg('Perubahan dikembalikan (redo).','success');}
function updateManualValidation(stats={}){
  if(!$('manualValidation'))return;const outside=Number(stats.lots_outside_buildable||0),overlaps=Number(stats.lot_overlap_pairs||0),front=Number(stats.lots_missing_frontage||0),obstacle=Number(stats.lot_obstacle_overlaps||0),special=Number(stats.rth_psu_overlap||0);
  if(!stats.manual_adjusted){$('manualValidation').textContent='Belum diedit';$('manualValidation').className='validation neutral';return;}
  if(outside===0&&overlaps===0&&front===0&&obstacle===0&&special===0){$('manualValidation').textContent='Validasi geometri: OK';$('manualValidation').className='validation ok';}
  else{$('manualValidation').textContent=`Cek: ${outside} di luar • ${overlaps} overlap${front?` • ${front} tanpa frontage`:''}${obstacle?` • ${obstacle} tabrak RTH/PSU`:''}${special?' • RTH/PSU overlap':''}`;$('manualValidation').className='validation warn';}
}
function flashAdjusted(indices=[]){
  const feats=indices.map(i=>selectedAlternative?.lots?.[i]).filter(Boolean).map((g,i)=>({type:'Feature',properties:{},geometry:g}));updateGeoSource('smart-adjust-source',{type:'FeatureCollection',features:feats});setTimeout(()=>updateGeoSource('smart-adjust-source',blankFC()),800);
}
function smartStatus(text,kind=''){const el=$('smartStatus');if(!el)return;el.textContent=text;el.className=`small editor-note ${kind}`;}
function smartPayload(items=selectedItems){
  const roadIds=items.filter(x=>x.type==='road').map(x=>selectedAlternative.road_segments?.[x.index]?.id).filter(Boolean);const lotIdx=items.filter(x=>x.type==='lot').map(x=>x.index);const special=[...new Set(items.filter(x=>x.type==='rth'||x.type==='psu').map(x=>x.type))];
  return {parcel:sitePlan.parcel,buildable:selectedAlternative.buildable,road_segments:(selectedAlternative.road_segments||[]).filter(x=>x?.centerline).map((x,i)=>({id:x.id||`R${i+1}`,kind:x.kind||'local',width_m:Number(x.width_m||6),centerline:x.centerline})),lots:selectedAlternative.lots||[],rth:selectedAlternative.rth||null,psu:selectedAlternative.psu||null,edited_road_ids:roadIds,edited_lot_indices:lotIdx,edited_special_types:special,lot_width_m:Number($('lotWidthEdit')?.value||$('lotWidth').value||8),lot_depth_m:Number($('lotDepthEdit')?.value||$('lotDepth').value||15),frontage_tolerance_m:Number($('frontageTolerance')?.value||1.5),reflow_radius_m:Math.max(10,Number($('lotDepth').value||15)*2)};
}
async function smartReflow(endpoint='/editor/reflow',items=selectedItems,announce=true){
  if(!selectedAlternative||!sitePlan)return;const relevant=items.filter(x=>['road','lot','rth','psu'].includes(x.type));if(!relevant.length){if(announce)smartStatus('Pilih jalan, kavling, RTH, atau PSU untuk reflow.','warn');return;}
  try{smartStatus('Smart solver sedang menyesuaikan objek terkait…');const r=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(smartPayload(relevant))});selectedAlternative.lots=r.lots||selectedAlternative.lots;if((r.removed_lot_indices||[]).length){const keep=selectedItems.filter(x=>x.type==='road'||x.type==='rth'||x.type==='psu');selectedItems=keep;selectedEdit=keep[keep.length-1]||null;}refreshSelectedSources();if(!(r.removed_lot_indices||[]).length)flashAdjusted(r.adjusted_lot_indices||[]);selectedAlternative.stats={...(selectedAlternative.stats||{}),...r.validation,manual_adjusted:true};updateManualValidation(selectedAlternative.stats);await recalculateManual(false);const v=r.validation||{};const warning=(r.warnings||[]).join(' • ');smartStatus(v.valid?`Reflow selesai • ${r.adjusted_lot_indices?.length||0} kavling menyesuaikan${(r.removed_lot_indices||[]).length?` • ${(r.removed_lot_indices||[]).length} kavling dilepas`:''}`:`Reflow selesai dengan catatan: ${warning||'periksa validasi'}${v.lot_obstacle_overlaps?` • ${v.lot_obstacle_overlaps} tabrak RTH/PSU`:''}`,v.valid?'ok':'warn');if(announce)msg(`Smart Reflow: ${r.adjusted_lot_indices?.length||0} kavling otomatis disesuaikan.`,v.valid?'success':'');return r;}catch(e){smartStatus(`Reflow gagal: ${e.message}`,'bad');msg(`Smart Reflow gagal: ${e.message}`,'error');}
}
async function validateSmart(){
  if(!selectedAlternative||!sitePlan)return;try{const payload=smartPayload([]);const r=await api('/editor/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({parcel:payload.parcel,buildable:payload.buildable,road_segments:payload.road_segments,lots:payload.lots,rth:payload.rth,psu:payload.psu,frontage_tolerance_m:payload.frontage_tolerance_m})});const v=r.validation;selectedAlternative.stats={...(selectedAlternative.stats||{}),...v,manual_adjusted:true};updateManualValidation(selectedAlternative.stats);smartStatus(v.valid?'Semua hard constraint M2.3.1 lolos.':`Validasi: ${v.lot_overlap_pairs} overlap • ${v.lots_outside_buildable} di luar • ${v.lots_missing_frontage} tanpa frontage${v.lot_obstacle_overlaps?` • ${v.lot_obstacle_overlaps} tabrak RTH/PSU`:''}`,v.valid?'ok':'warn');return v;}catch(e){smartStatus(`Validasi gagal: ${e.message}`,'bad');}
}
async function runAutoReflowAfterEdit(items=selectedItems){if($('autoReflowToggle')?.checked&&items.some(x=>['road','lot','rth','psu'].includes(x.type)))await smartReflow('/editor/reflow',items,false);else await recalculateManual(false);}
async function repackBlock(){pushHistory();await smartReflow('/editor/repack-block',selectedItems,true);}
function pointLineDistanceMeters(point,line){
  const ep=lineEndpoints(line);if(ep.length<2)return Infinity;const [p0,p1]=ep;const lat=point[1]*Math.PI/180,mx=111320*Math.cos(lat),my=111320;const ax=(p0[0]-point[0])*mx,ay=(p0[1]-point[1])*my,bx=(p1[0]-point[0])*mx,by=(p1[1]-point[1])*my;const dx=bx-ax,dy=by-ay,l2=dx*dx+dy*dy;if(!l2)return Math.hypot(ax,ay);let t=-(ax*dx+ay*dy)/l2;t=Math.max(0,Math.min(1,t));return Math.hypot(ax+t*dx,ay+t*dy);
}
function selectLinkedLots(){
  if(selectedItems.length!==1||selectedEdit?.type!=='road')return;const seg=selectedRoadSegment();if(!seg?.centerline)return;const threshold=Number($('lotDepth').value||15)+Number(seg.width_m||6)/2+3;const linked=[];(selectedAlternative.lots||[]).forEach((g,i)=>{if(pointLineDistanceMeters(geometryCentroidApprox(g),seg.centerline)<=threshold)linked.push({type:'lot',index:i});});setSelection([{type:'road',index:selectedEdit.index},...linked],{type:'road',index:selectedEdit.index});smartStatus(`${linked.length} kavling yang terkait dengan ${seg.id} dipilih.`,'ok');
}
async function duplicateSelection(){
  if(!selectedItems.length)return;pushHistory();const newItems=[];const old=[...selectedItems];for(const item of old){if(item.type==='lot'){const g=geometryForItem(item);selectedAlternative.lots.push(translateGeometryMeters(g,2,2));newItems.push({type:'lot',index:selectedAlternative.lots.length-1});}else if(item.type==='road'){const seg=selectedAlternative.road_segments[item.index];const dup=deepClone(seg);dup.id=`R${Date.now().toString().slice(-6)}${newItems.length}`;dup.centerline=translateGeometryMeters(seg.centerline,2,2);selectedAlternative.road_segments.push(dup);newItems.push({type:'road',index:selectedAlternative.road_segments.length-1});}}
  if(newItems.some(x=>x.type==='road'))await rebuildRoadNetwork(false);refreshSelectedSources();setSelection(newItems,newItems[newItems.length-1]);await runAutoReflowAfterEdit(newItems);
}
async function deleteSelection(){
  if(!selectedItems.length)return;pushHistory();const lots=selectedItems.filter(x=>x.type==='lot').map(x=>x.index).sort((a,b)=>b-a),roads=selectedItems.filter(x=>x.type==='road').map(x=>x.index).sort((a,b)=>b-a);lots.forEach(i=>selectedAlternative.lots.splice(i,1));roads.forEach(i=>selectedAlternative.road_segments.splice(i,1));setSelection([]);if(roads.length)await rebuildRoadNetwork(false);await recalculateManual(false);if(selectedItems.some(x=>x.type==='rth'||x.type==='psu'))smartStatus('RTH/PSU tidak dihapus karena merupakan constraint area.','warn');
}
function deleteSelected(){return deleteSelection();}function deleteRoad(){return deleteSelection();}
function duplicateSelected(){return duplicateSelection();}function duplicateRoad(){return duplicateSelection();}
function pickEditableFeature(point){
  if(!map||!manualEditEnabled||!['select','box-select'].includes(editTool))return null;const fs=map.queryRenderedFeatures(point,{layers:['edit-handles-circle','lots-fill','road-segments-fill','rth-fill','psu-fill']});if(!fs.length)return null;const f=fs[0];if(f.layer.id==='edit-handles-circle')return{type:'handle',endpoint:Number(f.properties?.endpoint||0)};if(f.layer.id==='lots-fill')return{type:'lot',index:Number(f.properties?.lot||1)-1};if(f.layer.id==='road-segments-fill')return{type:'road',index:Number(f.properties?.roadIndex||0)};if(f.layer.id==='rth-fill')return{type:'rth'};if(f.layer.id==='psu-fill')return{type:'psu'};return null;
}
function ensureSelectionBox(){let el=document.getElementById('m23SelectionBox');if(!el){el=document.createElement('div');el.id='m23SelectionBox';el.className='selection-box';map.getContainer().appendChild(el);}return el;}
function showSelectionBox(a,b){const el=ensureSelectionBox(),x=Math.min(a.x,b.x),y=Math.min(a.y,b.y),w=Math.abs(a.x-b.x),h=Math.abs(a.y-b.y);Object.assign(el.style,{display:'block',left:`${x}px`,top:`${y}px`,width:`${w}px`,height:`${h}px`});}
function hideSelectionBox(){const el=document.getElementById('m23SelectionBox');if(el)el.style.display='none';}
function boxSelectionItems(a,b){
  const fs=map.queryRenderedFeatures([[Math.min(a.x,b.x),Math.min(a.y,b.y)],[Math.max(a.x,b.x),Math.max(a.y,b.y)]],{layers:['lots-fill','road-segments-fill','rth-fill','psu-fill']});const out=new Map();for(const f of fs){let item=null;if(f.layer.id==='lots-fill')item={type:'lot',index:Number(f.properties?.lot||1)-1};else if(f.layer.id==='road-segments-fill')item={type:'road',index:Number(f.properties?.roadIndex||0)};else if(f.layer.id==='rth-fill')item={type:'rth'};else if(f.layer.id==='psu-fill')item={type:'psu'};if(item)out.set(selectionKey(item),item);}return [...out.values()];
}
function liveLinkedLotsForSelection(items=selectedItems){
  const roadItems=items.filter(x=>x.type==='road');if(!roadItems.length)return [];const selectedKeys=new Set(items.map(selectionKey));const linked=[];for(const rItem of roadItems){const seg=selectedAlternative?.road_segments?.[rItem.index];if(!seg?.centerline)continue;const threshold=Number($('lotDepth').value||15)+Number(seg.width_m||6)/2+3;(selectedAlternative.lots||[]).forEach((g,i)=>{const item={type:'lot',index:i};if(!selectedKeys.has(selectionKey(item))&&pointLineDistanceMeters(geometryCentroidApprox(g),seg.centerline)<=threshold)linked.push(item);});}const uniq=new Map(linked.map(x=>[selectionKey(x),x]));return [...uniq.values()];
}
function setEditTool(tool){
  editTool=tool;roadDrawPoints=[];updateGeoSource('road-draft-source',blankFC());['toolSelect','toolBoxSelect','toolAddRoad','toolAddLot'].forEach(id=>$(id)?.classList.remove('active'));const id=tool==='select'?'toolSelect':tool==='box-select'?'toolBoxSelect':tool==='add-road'?'toolAddRoad':'toolAddLot';$(id)?.classList.add('active');if(map)map.getCanvas().style.cursor=tool==='box-select'?'crosshair':tool==='select'?'default':'copy';if(tool==='box-select')msg('Box Select aktif: drag kotak di peta untuk memilih banyak objek.','success');if(tool==='add-road')msg('Tool + Jalan aktif: klik titik awal lalu titik akhir ruas jalan.','success');if(tool==='add-lot')msg('Tool + Kavling aktif: klik lokasi pusat kavling.','success');
}
function installManualEditHandlers(){
  map.on('mousedown',e=>{
    if(!manualEditEnabled||!selectedAlternative)return;
    if(editTool==='box-select'){boxSelectState={start:e.point,current:e.point};map.dragPan.disable();showSelectionBox(e.point,e.point);return;}
    if(editTool!=='select')return;const sel=pickEditableFeature(e.point);
    if(sel?.type==='handle'&&selectedItems.length===1&&selectedEdit?.type==='road'){const seg=selectedRoadSegment();if(!seg?.centerline)return;pushHistory();handleDrag={endpoint:sel.endpoint,original:deepClone(seg.centerline),roadItem:deepClone(selectedEdit)};map.dragPan.disable();return;}
    if(!sel||sel.type==='handle'){if(!sel&&!e.originalEvent?.shiftKey)setSelection([]);return;}
    const key=selectionKey(sel),exists=selectedItems.some(x=>selectionKey(x)===key),shift=!!e.originalEvent?.shiftKey;
    if(shift){suppressClickOnce=true;if(exists){setSelection(selectedItems.filter(x=>selectionKey(x)!==key));return;}else setSelection([...selectedItems,sel],sel);}else if(!exists||selectedItems.length===0){setSelection([sel],sel);suppressClickOnce=true;}else {selectedEdit=sel;suppressClickOnce=true;}
    pushHistory();const liveLinked=liveLinkedLotsForSelection(selectedItems);dragState={start:[e.lngLat.lng,e.lngLat.lat],snapshot:snapshotSelection([...selectedItems,...liveLinked]),items:deepClone(selectedItems),moved:false,liveLinked};map.dragPan.disable();map.getCanvas().style.cursor='grabbing';e.preventDefault?.();
  });
  map.on('mousemove',e=>{
    if(editTool==='add-road'&&roadDrawPoints.length===1)updateGeoSource('road-draft-source',{type:'FeatureCollection',features:[{type:'Feature',properties:{},geometry:{type:'LineString',coordinates:[roadDrawPoints[0],[e.lngLat.lng,e.lngLat.lat]]}}]});
    if(boxSelectState){boxSelectState.current=e.point;showSelectionBox(boxSelectState.start,e.point);return;}
    if(handleDrag&&selectedEdit?.type==='road'){const seg=selectedRoadSegment();seg.centerline=setLineEndpoint(handleDrag.original,handleDrag.endpoint,[e.lngLat.lng,e.lngLat.lat]);updateGeoSource('road-centerlines-source',roadCenterlinesFC(selectedAlternative.road_segments));updateGeoSource('edit-handles-source',handlesFC(seg));return;}
    if(!dragState)return;const dLon=e.lngLat.lng-dragState.start[0],dLat=e.lngLat.lat-dragState.start[1];translateSnapshotDegrees(dragState.snapshot,dLon,dLat);dragState.moved=true;
  });
  map.on('mouseup',async e=>{
    if(boxSelectState){const items=boxSelectionItems(boxSelectState.start,boxSelectState.current||e.point);hideSelectionBox();boxSelectState=null;map.dragPan.enable();setSelection(items,items[items.length-1]);smartStatus(`${items.length} objek dipilih dengan Box Select.`,items.length?'ok':'warn');return;}
    if(handleDrag){const item=handleDrag.roadItem;handleDrag=null;map.dragPan.enable();await rebuildRoadNetwork(false);setSelection([item],item);await runAutoReflowAfterEdit([item]);return;}
    if(!dragState)return;const moved=dragState.moved,items=dragState.items;dragState=null;map.dragPan.enable();map.getCanvas().style.cursor='default';if(moved){suppressClickOnce=true;const hasRoad=items.some(x=>x.type==='road');if(hasRoad)await rebuildRoadNetwork(false);await runAutoReflowAfterEdit(items);}else if(editHistory.length){editHistory.pop();$('undoBtn').disabled=editHistory.length===0;}
  });
  map.on('click',async e=>{
    if(!manualEditEnabled||dragState||handleDrag||boxSelectState)return;if(suppressClickOnce){suppressClickOnce=false;return;}if(editTool==='add-road'){await addRoadPoint(e.lngLat);return;}if(editTool==='add-lot'){addLotAt(e.lngLat);return;}if(editTool!=='select')return;const sel=pickEditableFeature(e.point);if(!sel||sel.type==='handle')return;if(e.originalEvent?.shiftKey){const key=selectionKey(sel),exists=selectedItems.some(x=>selectionKey(x)===key);setSelection(exists?selectedItems.filter(x=>selectionKey(x)!==key):[...selectedItems,sel],sel);}else if(!selectedItems.some(x=>selectionKey(x)===selectionKey(sel)))setSelection([sel],sel);
  });
}
function enterManualEdit(){if(!selectedAlternative){msg('Pilih/generate alternatif layout terlebih dahulu.','error');return;}manualEditEnabled=true;$('manualToggle').textContent='Selesai Smart Editor';$('manualToggle').classList.add('active');$('manualControls').classList.remove('disabled');$('resetManualBtn').disabled=false;$('recalcBtn').disabled=false;setEditTool('select');smartStatus('Smart editor aktif. Shift+click/Box Select untuk multi-selection; Auto-Reflow ON.','ok');msg('M2.3.1 Smart Reflow Editor aktif.','success');}
function exitManualEdit(clearSelection=true){manualEditEnabled=false;dragState=null;handleDrag=null;boxSelectState=null;roadDrawPoints=[];hideSelectionBox();updateGeoSource('road-draft-source',blankFC());updateGeoSource('smart-adjust-source',blankFC());if($('manualToggle')){$('manualToggle').textContent='Aktifkan Smart Editor';$('manualToggle').classList.remove('active');}if($('manualControls'))$('manualControls').classList.add('disabled');if(map){map.dragPan.enable();map.getCanvas().style.cursor='';}if(clearSelection)setSelection([]);else updateGeoSource('selection-source',blankFC());}
function bindActions(){
  $('useDrawn').onclick=useDrawn;$('useManual').onclick=useManual;$('uploadBtn').onclick=uploadFile;$('generateBtn').onclick=generateSitePlan;$('saveBtn').onclick=saveProject;$('loadSample').onclick=loadSample;$('manualToggle').onclick=toggleManualEdit;
  $('toolSelect').onclick=()=>setEditTool('select');$('toolBoxSelect').onclick=()=>setEditTool('box-select');$('toolAddRoad').onclick=()=>setEditTool('add-road');$('toolAddLot').onclick=()=>setEditTool('add-lot');
  $('nudgeLeft').onclick=()=>nudgeSelected(-1,0);$('nudgeRight').onclick=()=>nudgeSelected(1,0);$('nudgeUp').onclick=()=>nudgeSelected(0,1);$('nudgeDown').onclick=()=>nudgeSelected(0,-1);$('rotateLeftBtn').onclick=()=>rotateSelected(-5);$('rotateRightBtn').onclick=()=>rotateSelected(5);$('applyAngleBtn').onclick=applyObjectAngle;
  $('applyRoadBtn').onclick=applyRoadChanges;$('duplicateRoadBtn').onclick=duplicateSelection;$('deleteRoadBtn').onclick=deleteSelection;$('applyLotSizeBtn').onclick=applyLotSize;$('duplicateBtn').onclick=duplicateSelection;$('deleteBtn').onclick=deleteSelection;$('applyLayoutAngleBtn').onclick=applyLayoutAngle;
  $('reflowLocalBtn').onclick=()=>{pushHistory();smartReflow('/editor/reflow',selectedItems,true);};$('repackBlockBtn').onclick=repackBlock;$('validateSmartBtn').onclick=validateSmart;$('selectLinkedBtn').onclick=selectLinkedLots;$('duplicateSelectionBtn').onclick=duplicateSelection;$('deleteSelectionBtn').onclick=deleteSelection;
  $('undoBtn').onclick=undoManual;$('redoBtn').onclick=redoManual;$('resetManualBtn').onclick=resetManual;$('recalcBtn').onclick=()=>recalculateManual(true);if($('optimizeYieldBtn'))$('optimizeYieldBtn').onclick=optimizeYield;if($('landOptimizationToggle'))$('landOptimizationToggle').onchange=onLandOptimizationToggle;updateOptimizationModeUI();
}

// =============================
// Milestone 2.5.4 Land Utilization Optimizer — optional master toggle
// =============================
function yieldResultHtml(opt){
  const b=opt?.before||{}, a=opt?.after||{}, d=opt?.delta||{};
  const bEff=Number(b.lot_efficiency_pct||0);
  const aEff=Number(a.lot_efficiency_pct||0);
  const pass=aEff>=70.0;
  return `<div class="yield-compare">
    <div><span>Sebelum</span><b>${b.standard_lot_count??b.lot_count??'—'} Standard</b><small>Adaptive ${b.adaptive_lot_count??0} • Efisiensi ${bEff.toFixed(2)}% • Sisa ${fmtM2(b.residual_area_m2)}</small></div>
    <div class="yield-arrow">→</div>
    <div><span>Sesudah</span><b>${a.standard_lot_count??'—'} Standard</b><small>Adaptive ${a.adaptive_lot_count??0} • Efisiensi ${aEff.toFixed(2)}% • Sisa ${fmtM2(a.residual_area_m2)}</small></div>
  </div>
  <div class="yield-target-result ${pass?'pass':'fail'}"><b>${pass?'LULUS':'BELUM LULUS'}</b> • EFISIENSI KAVLING ${aEff.toFixed(2)}% • TARGET ≥ 70%</div>
  <div class="yield-detail">Kavling Adaptive amber hanya berasal dari lahan sisa dan dihitung jika saleable: ≥60 m², frontage nyata ≥4 m, tidak overlap jalan/RTH/PSU/kavling lain, dan bukan sliver. Sisa lahan (residual) bersifat informational.</div>`;
}

async function optimizeYield(){
  if(!landOptimizationEnabled()){msg('Centang Optimalisasi Lahan (M2.5) terlebih dahulu.','error');return;}
  if(optimizerRunning)return;
  if(!selectedAlternative||!sitePlan){msg('Generate dan pilih alternatif dahulu.','error');return;}
  optimizerRunning=true;
  if(manualEditEnabled) exitManualEdit(true);
  const btn=$('optimizeYieldBtn'); const old=btn.textContent;
  try{
    btn.disabled=true; btn.textContent='Mengoptimalkan efisiensi kavling (target ≥ 70%)…';
    $('yieldStatus').textContent='Optimizer berjalan…'; $('yieldStatus').className='validation neutral';
    $('yieldResult').className='yield-result'; $('yieldResult').textContent='Sistem hanya membentuk Kavling Adaptive dari TRUE residual. STANDARD, jalan, RTH dan PSU tidak digeser atau dihapus.';
    msg('M2.5.12 Residual Optimizer: STANDARD/jalan/RTH/PSU dikunci; lahan sisa diolah untuk mencapai efisiensi ≥ 70%.');
    const baseW=Math.max(4,Number($('lotWidth').value||8));
    const baseD=Math.max(8,Number($('lotDepth').value||15));
    const payload={
      parcel:sitePlan.parcel, buildable:selectedAlternative.buildable,
      road_segments:(selectedAlternative.road_segments||[]).map((r,i)=>({id:r.id||`R${i+1}`,kind:r.kind||'local',width_m:Number(r.width_m||6),centerline:r.centerline})).filter(r=>r.centerline),
      lots:selectedAlternative.lots||[], lot_details:selectedAlternative.lot_details||[], rth:selectedAlternative.rth||null, psu:selectedAlternative.psu||null,
      target_lot_width_m:baseW, target_lot_depth_m:baseD,
      min_lot_width_m:Math.max(2.5,baseW*0.70), max_lot_width_m:baseW*1.35,
      min_lot_depth_m:Math.max(4,baseD*0.60), max_lot_depth_m:baseD*1.40,
      rth_pct:Number($('rthPct').value||10), psu_pct:Number($('psuPct').value||5), local_road_width_m:Number($('localRoad').value||6),
      road_shift_m:Math.min(6,Math.max(2,Number($('localRoad').value||6)*0.75)),
      allow_road_shift:false, allow_rth_psu_relocation:false, allow_selective_extension:false, max_extensions:0,
      lot_efficiency_target_pct:70, allow_residual_rth_absorption:false, max_optimize_seconds:20
    };
    const r=await api('/site-plan/optimize-yield',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!landOptimizationEnabled()){ msg('Hasil optimizer diabaikan karena Optimalisasi Lahan sudah dimatikan.','success'); return; }
    // Keep generated baseline available for Reset Generate; replace only the active alternative.
    const alt={...deepClone(selectedAlternative),buildable:r.buildable,roads:r.roads,road_segments:r.road_segments||[],rth:r.rth,psu:r.psu,reserve:r.reserve||null,drainage:r.drainage,lots:r.lots||[],lot_details:r.lot_details||[],residuals:r.residuals||[],stats:{...(selectedAlternative.stats||{}),...(r.stats||{})},optimization:r.optimization||{},validation:r.validation||null,name:selectedAlternative.name.includes('Best Yield')?selectedAlternative.name:`${selectedAlternative.name} • Best Yield`};
    alt.stats={...(alt.stats||{}),optimized:true,land_optimization_enabled:true};
    alt.validation=r.validation||null;
    replaceSelectedAlternative(alt);
    renderAlternativeCards(sitePlan.alternatives); renderAlternative(alt);
    const effMet=!!r.stats?.lot_efficiency_met, effVal=Number(r.stats?.lot_efficiency_pct||0);
    $('yieldResult').className=`yield-result ${effMet?'ok':'bad'}`; $('yieldResult').innerHTML=yieldResultHtml(r.optimization);
    $('yieldStatus').textContent=effMet?`Aktif • Efisiensi ${effVal.toFixed(2)}% (LULUS)`:`Aktif • Efisiensi ${effVal.toFixed(2)}% (target ≥ 70% belum tercapai)`; $('yieldStatus').className=`validation ${effMet?'ok':'warn'}`;
    msg(`M2.5.12 selesai: ${r.optimization?.before?.lot_count||0} → ${r.optimization?.after?.lot_count||0} kavling; ${r.stats?.residual_lot_count||0} kavling Adaptive dari lahan sisa; Efisiensi Kavling ${effVal.toFixed(2)}%.`,effMet?'success':'error');
  }catch(e){
    $('yieldStatus').textContent='Optimizer gagal'; $('yieldStatus').className='validation warn'; $('yieldResult').className='yield-result bad'; $('yieldResult').textContent=e.message; msg(`M2.5.12 Residual Optimizer: ${e.message}`,'error');
  }finally{optimizerRunning=false;btn.disabled=!landOptimizationEnabled()||!selectedAlternative;btn.textContent=old;updateOptimizationModeUI();}
}

window.addEventListener('keydown',e=>{if(!manualEditEnabled||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName))return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='y'){e.preventDefault();redoManual();}});

window.addEventListener('keydown',e=>{
  if(!manualEditEnabled||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) return;
  if(e.key==='ArrowLeft'){e.preventDefault();nudgeSelected(-1,0);} if(e.key==='ArrowRight'){e.preventDefault();nudgeSelected(1,0);}
  if(e.key==='ArrowUp'){e.preventDefault();nudgeSelected(0,1);} if(e.key==='ArrowDown'){e.preventDefault();nudgeSelected(0,-1);}
  if((e.key==='Delete'||e.key==='Backspace')&&selectedEdit?.type==='lot'){e.preventDefault();deleteSelected();} else if((e.key==='Delete'||e.key==='Backspace')&&selectedEdit?.type==='road'){e.preventDefault();deleteRoad();}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();undoManual();} if(e.key==='Escape'){setEditTool('select');}
});

window.addEventListener('error',e=>console.error('Frontend error:',e.error||e.message));
window.addEventListener('DOMContentLoaded',()=>{
  tabs(); bindActions(); health();
  try{
    if(typeof maplibregl==='undefined') throw new Error('MapLibre gagal dimuat. Periksa koneksi CDN/internet.');
    initMap();
  }catch(e){console.error(e);msg(`Peta gagal dimuat: ${e.message}. Tab Koordinat dan Import tetap dapat digunakan.`,'error');}
});

// M2.3 overrides for dimension/road-property edits so dependent objects also reflow.
async function applyRoadChanges(){
  const seg=selectedRoadSegment();if(!seg||selectedItems.length!==1)return;pushHistory();seg.width_m=Number($('roadWidthEdit').value||6);seg.kind=$('roadKindEdit').value||'local';const target=snapAngleValue(Number($('objectAngle').value||geometryAngleDegrees(seg.centerline))),current=normalizeAngle(geometryAngleDegrees(seg.centerline));seg.centerline=rotateGeometry(seg.centerline,target-current);await rebuildRoadNetwork(false);setSelection([{type:'road',index:selectedEdit.index}],{type:'road',index:selectedEdit.index});await runAutoReflowAfterEdit(selectedItems);
}
async function applyLotSize(){
  if(selectedItems.length!==1||selectedEdit?.type!=='lot')return;const g=selectedGeometry();if(!g)return;pushHistory();const center=geometryCentroidApprox(g),angle=snapAngleValue(Number($('objectAngle').value||lotFrontageAngleDegrees(g)));selectedAlternative.lots[selectedEdit.index]=makeRectangleAt(center,Number($('lotWidthEdit').value||8),Number($('lotDepthEdit').value||15),angle);refreshSelectedSources();await runAutoReflowAfterEdit(selectedItems);
}

// =============================
// Milestone 2.4 Parametric Constraint Editor
// True road -> block -> lot dependency reflow.
// =============================
let parametricEditorModel = null;
let parametricModelAltId = null;

function m24ModelPayload(){
  return {
    parcel:sitePlan?.parcel,
    buildable:selectedAlternative?.buildable,
    road_segments:(selectedAlternative?.road_segments||[]).filter(x=>x?.centerline).map((x,i)=>({
      id:x.id||`R${i+1}`,kind:x.kind||'local',width_m:Number(x.width_m||6),centerline:x.centerline
    })),
    lots:selectedAlternative?.lots||[],
    rth:selectedAlternative?.rth||null,
    psu:selectedAlternative?.psu||null,
    lot_width_m:Number($('lotWidthEdit')?.value||$('lotWidth')?.value||8),
    lot_depth_m:Number($('lotDepthEdit')?.value||$('lotDepth')?.value||15)
  };
}

async function buildParametricEditorModel(force=false){
  if(!selectedAlternative||!sitePlan) return null;
  if(!force && parametricEditorModel && parametricModelAltId===selectedAlternative.id) return parametricEditorModel;
  smartStatus('Membangun dependency graph jalan → block → kavling…');
  const r=await api('/editor/parametric-model',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(m24ModelPayload())
  });
  parametricEditorModel=r;
  parametricModelAltId=selectedAlternative.id;
  selectedAlternative.parametric_model=deepClone(r);
  const s=r.summary||{};
  smartStatus(`Parametric graph siap • ${s.road_count||0} jalan • ${s.block_count||0} block • ${s.lot_count||0} kavling`,'ok');
  return r;
}

function m24LotMetaByIndex(idx){
  return (parametricEditorModel?.lots||[]).find(x=>Number(x.index)===Number(idx))||null;
}

function m24BlockLotIndices(blockId){
  return parametricEditorModel?.blocks?.[blockId]?.lot_indices||[];
}

function liveLinkedLotsForSelection(items=selectedItems){
  if(!parametricEditorModel) return [];
  const selectedKeys=new Set((items||[]).map(selectionKey));
  const indices=new Set();
  for(const item of items||[]){
    if(item.type==='road'){
      const rid=selectedAlternative?.road_segments?.[item.index]?.id;
      if(!rid) continue;
      Object.values(parametricEditorModel.blocks||{}).forEach(b=>{
        if(b.road_id===rid) (b.lot_indices||[]).forEach(i=>indices.add(Number(i)));
      });
    }
  }
  return [...indices].map(i=>({type:'lot',index:i})).filter(x=>!selectedKeys.has(selectionKey(x))&&validSelectionItem(x));
}

function selectLinkedLots(){
  if(selectedItems.length!==1||selectedEdit?.type!=='road') return;
  const seg=selectedRoadSegment(); if(!seg) return;
  if(!parametricEditorModel){ smartStatus('Dependency graph belum siap. Aktifkan ulang Smart Editor.','warn'); return; }
  const linked=[];
  Object.values(parametricEditorModel.blocks||{}).forEach(b=>{
    if(b.road_id===seg.id) (b.lot_indices||[]).forEach(i=>{ if(validSelectionItem({type:'lot',index:Number(i)})) linked.push({type:'lot',index:Number(i)}); });
  });
  setSelection([{type:'road',index:selectedEdit.index},...linked],{type:'road',index:selectedEdit.index});
  smartStatus(`${linked.length} kavling terikat secara parametrik ke ${seg.id}.`,'ok');
}

function smartPayload(items=selectedItems){
  const roadIds=(items||[]).filter(x=>x.type==='road').map(x=>selectedAlternative?.road_segments?.[x.index]?.id).filter(Boolean);
  const lotIdx=(items||[]).filter(x=>x.type==='lot').map(x=>Number(x.index));
  const special=[...new Set((items||[]).filter(x=>x.type==='rth'||x.type==='psu').map(x=>x.type))];
  return {
    ...m24ModelPayload(),
    editor_model:parametricEditorModel,
    edited_road_ids:roadIds,
    edited_lot_indices:lotIdx,
    edited_special_types:special,
    frontage_tolerance_m:Number($('frontageTolerance')?.value||1.5),
    preserve_count:true
  };
}

async function smartReflow(endpoint='/editor/parametric-reflow',items=selectedItems,announce=true){
  if(!selectedAlternative||!sitePlan) return;
  const relevant=(items||[]).filter(x=>['road','lot','rth','psu'].includes(x.type));
  if(!relevant.length){ if(announce) smartStatus('Pilih jalan, kavling, RTH, atau PSU untuk Parametric Reflow.','warn'); return; }
  try{
    const modelLotCount=parametricEditorModel?.lots?.length ?? -1;
    const modelRoadCount=Object.keys(parametricEditorModel?.roads||{}).length;
    const currentRoadCount=(selectedAlternative.road_segments||[]).length;
    const missingEditedLot=relevant.some(x=>x.type==='lot'&&!m24LotMetaByIndex(x.index));
    const missingEditedRoad=relevant.some(x=>x.type==='road'&&!parametricEditorModel?.roads?.[selectedAlternative?.road_segments?.[x.index]?.id]);
    if(!parametricEditorModel || parametricModelAltId!==selectedAlternative.id || modelLotCount!==(selectedAlternative.lots||[]).length || modelRoadCount!==currentRoadCount || missingEditedLot || missingEditedRoad) await buildParametricEditorModel(true);
    const beforeCount=(selectedAlternative.lots||[]).length;
    smartStatus('Parametric solver: propagasi dependency & repack block…');
    const r=await api('/editor/parametric-reflow',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(smartPayload(relevant))
    });
    selectedAlternative.lots=r.lots||[];
    parametricEditorModel=r.editor_model||null;
    parametricModelAltId=selectedAlternative.id;
    selectedAlternative.parametric_model=deepClone(parametricEditorModel);

    // M2.5.11 preserves STANDARD lot count. Relocated lots keep their index; only legacy/non-preserve
    // responses may still report dropped indices.
    if((r.dropped_lot_indices||[]).length){
      const keep=selectedItems.filter(x=>x.type==='road'||x.type==='rth'||x.type==='psu');
      selectedItems=keep; selectedEdit=keep[keep.length-1]||null;
    }
    refreshSelectedSources();
    flashAdjusted(r.adjusted_lot_indices||[]);
    selectedAlternative.stats={...(selectedAlternative.stats||{}),...(r.validation||{}),manual_adjusted:true};
    updateManualValidation(selectedAlternative.stats);
    await recalculateManual(false);
    const v=r.validation||{};
    const dropped=(r.dropped_lot_indices||[]).length;
    const relocated=(r.relocated_lot_indices||[]).length;
    const adjusted=(r.adjusted_lot_indices||[]).length;
    const blocks=(r.affected_block_ids||[]).length;
    let text=`Parametric Reflow • ${blocks} block • ${adjusted} kavling menyesuaikan`;
    if(relocated) text+=` • ${relocated} kavling dipindahkan ke area kosong`;
    if(dropped) text+=` • ${dropped} kavling belum tertampung (${beforeCount} → ${(selectedAlternative.lots||[]).length})`;
    if(!v.valid) text+=` • perlu validasi`;
    smartStatus(text,v.valid?'ok':'warn');
    if(announce) msg(text,v.valid?'success':'');
    return r;
  }catch(e){
    // M2.5.11 safety: a failed dependency reflow must never leave the map in a
    // half-edited state with missing/invalid lots. Restore the snapshot that
    // was pushed before the edit and make the failure visually atomic.
    if(editHistory.length){
      const prev=editHistory.pop();
      replaceSelectedAlternative(prev);
      refreshSelectedSources();
      setSelection([]);
      parametricEditorModel=null; parametricModelAltId=null;
      $('undoBtn').disabled=editHistory.length===0;
      if($('redoBtn')) $('redoBtn').disabled=redoHistory.length===0;
      await recalculateManual(false);
    }
    smartStatus(`Parametric Reflow gagal — perubahan dibatalkan, layout dipulihkan. ${e.message}`,'bad');
    msg(`Parametric Reflow gagal. Perubahan dibatalkan agar kavling tidak hilang/berubah ukuran. ${e.message}`,'error');
  }
}

async function runAutoReflowAfterEdit(items=selectedItems){
  if($('autoReflowToggle')?.checked && (items||[]).some(x=>['road','lot','rth','psu'].includes(x.type))){
    return await smartReflow('/editor/parametric-reflow',items,false);
  }
  await recalculateManual(false);
  await buildParametricEditorModel(true);
}

async function repackBlock(){
  if(!selectedItems.length) return;
  pushHistory();
  return await smartReflow('/editor/parametric-reflow',selectedItems,true);
}

async function validateSmart(){
  if(!selectedAlternative||!sitePlan) return;
  try{
    const p=smartPayload([]);
    const r=await api('/editor/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      parcel:p.parcel,buildable:p.buildable,road_segments:p.road_segments,lots:p.lots,rth:p.rth,psu:p.psu,frontage_tolerance_m:p.frontage_tolerance_m
    })});
    const v=r.validation||{};
    selectedAlternative.stats={...(selectedAlternative.stats||{}),...v,manual_adjusted:true};
    updateManualValidation(selectedAlternative.stats);
    smartStatus(v.valid?'M2.4 hard constraints lolos.':`M2.4 validasi: ${v.lot_overlap_pairs||0} overlap • ${v.lots_outside_buildable||0} di luar • ${v.lots_missing_frontage||0} tanpa frontage`,v.valid?'ok':'warn');
    return v;
  }catch(e){smartStatus(`Validasi gagal: ${e.message}`,'bad');}
}

async function enterManualEdit(){
  if(!selectedAlternative){msg('Pilih/generate alternatif layout terlebih dahulu.','error');return;}
  manualEditEnabled=true;
  $('manualToggle').textContent='Selesai Parametric Editor';
  $('manualToggle').classList.add('active');
  $('manualControls').classList.remove('disabled');
  $('resetManualBtn').disabled=false; $('recalcBtn').disabled=false;
  setEditTool('select');
  parametricEditorModel=null; parametricModelAltId=null;
  try{
    await buildParametricEditorModel(true);
    msg('M2.4 Parametric Constraint Editor aktif. Jalan, block, dan kavling sekarang punya dependency graph.','success');
  }catch(e){
    smartStatus(`Dependency graph gagal: ${e.message}`,'bad');
    msg(`Parametric editor aktif, tetapi model dependency gagal: ${e.message}`,'error');
  }
}

function exitManualEdit(clearSelection=true){
  manualEditEnabled=false; dragState=null; handleDrag=null; boxSelectState=null; roadDrawPoints=[];
  hideSelectionBox(); updateGeoSource('road-draft-source',blankFC()); updateGeoSource('smart-adjust-source',blankFC());
  parametricEditorModel=null; parametricModelAltId=null;
  if($('manualToggle')){$('manualToggle').textContent='Aktifkan Parametric Editor';$('manualToggle').classList.remove('active');}
  if($('manualControls'))$('manualControls').classList.add('disabled');
  if(map){map.dragPan.enable();map.getCanvas().style.cursor='';}
  if(clearSelection)setSelection([]); else updateGeoSource('selection-source',blankFC());
}

function toggleManualEdit(){ manualEditEnabled?exitManualEdit(true):enterManualEdit(); }

