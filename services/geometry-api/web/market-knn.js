// Development OS — Spatial KNN House Price Recommendation
// Uses the joined Pekanbaru housing market dataset without modifying the site-planning solver.
// KNN neighbors are selected by geographic distance from the active polygon centroid.

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

  const VALID_PRICE_MIN_RP = 1_000_000;
  const DEFAULT_K = 7;
  const MAX_K = 15;
  const MIN_DISTANCE_KM = 0.25;

  let dataset = null;
  let ready = false;
  let refreshing = false;
  let lastKey = '';

  const el = id => typeof document === 'undefined' ? null : document.getElementById(id);
  const money = value => {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? `Rp${Math.round(n).toLocaleString('id-ID')}` : '—';
  };

  function toNumber(value){
    if(typeof value === 'number') return Number.isFinite(value) ? value : null;
    if(value == null) return null;
    let s = String(value).trim().replace(/[^0-9,.-]/g,'');
    if(!s) return null;
    if(/^[-+]?\d{1,3}(\.\d{3})+(,\d+)?$/.test(s)) s = s.replace(/\./g,'').replace(',','.');
    else s = s.replace(',','.');
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }

  function collectCoordinates(input,out=[]){
    const g = input?.type === 'Feature' ? input.geometry : input;
    if(!g) return out;
    const walk = value => {
      if(Array.isArray(value) && value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number'){
        out.push([Number(value[0]),Number(value[1])]);
      }else if(Array.isArray(value)) value.forEach(walk);
    };
    walk(g.coordinates);
    return out;
  }

  function ringAreaAndCentroid(ring){
    if(!Array.isArray(ring) || ring.length < 3) return null;
    let twiceArea = 0, cxNumerator = 0, cyNumerator = 0;
    for(let i=0;i<ring.length;i++){
      const a = ring[i], b = ring[(i+1)%ring.length];
      if(!Array.isArray(a) || !Array.isArray(b)) continue;
      const cross = Number(a[0]) * Number(b[1]) - Number(b[0]) * Number(a[1]);
      twiceArea += cross;
      cxNumerator += (Number(a[0]) + Number(b[0])) * cross;
      cyNumerator += (Number(a[1]) + Number(b[1])) * cross;
    }
    if(Math.abs(twiceArea) < 1e-15) return null;
    return {
      area: Math.abs(twiceArea / 2),
      lon: cxNumerator / (3 * twiceArea),
      lat: cyNumerator / (3 * twiceArea),
    };
  }

  function geometryCenter(input){
    const g = input?.type === 'Feature' ? input.geometry : input;
    if(!g) return null;
    if(g.type === 'Polygon' && Array.isArray(g.coordinates?.[0])){
      const c = ringAreaAndCentroid(g.coordinates[0]);
      if(c && Number.isFinite(c.lon) && Number.isFinite(c.lat)) return {lon:c.lon,lat:c.lat};
    }
    if(g.type === 'MultiPolygon' && Array.isArray(g.coordinates)){
      const candidates = g.coordinates
        .map(poly => ringAreaAndCentroid(poly?.[0]))
        .filter(Boolean)
        .sort((a,b)=>b.area-a.area);
      if(candidates.length) return {lon:candidates[0].lon,lat:candidates[0].lat};
    }
    const pts = collectCoordinates(g,[]);
    if(!pts.length) return null;
    const lon = pts.reduce((s,p)=>s+p[0],0)/pts.length;
    const lat = pts.reduce((s,p)=>s+p[1],0)/pts.length;
    return {lon,lat};
  }

  function haversineKm(a,b){
    const R = 6371.0088;
    const rad = x => x * Math.PI / 180;
    const p1 = rad(a.lat), p2 = rad(b.lat);
    const dp = rad(b.lat-a.lat), dl = rad(b.lon-a.lon);
    const q = Math.sin(dp/2)**2 + Math.cos(p1)*Math.cos(p2)*Math.sin(dl/2)**2;
    return 2 * R * Math.asin(Math.sqrt(q));
  }

  function comparableTypes(record,category='all'){
    const types = Array.isArray(record?.types) ? record.types : [];
    if(category === 'all') return types;
    return types.filter(t => String(t?.category || '').toLowerCase() === String(category).toLowerCase());
  }

  function percentile(sorted,q){
    if(!sorted.length) return null;
    if(sorted.length === 1) return sorted[0];
    const pos = (sorted.length-1)*q;
    const lo = Math.floor(pos), hi = Math.ceil(pos), f = pos-lo;
    return lo === hi ? sorted[lo] : sorted[lo]*(1-f)+sorted[hi]*f;
  }

  function selectRepresentativeType(record,category='all',targetLotArea=0){
    const valid = comparableTypes(record,category)
      .map(t => ({
        raw:t,
        price:toNumber(t?.price),
        landArea:toNumber(t?.land),
        buildingArea:toNumber(t?.building),
        type:String(t?.type ?? '—'),
        category:String(t?.category ?? '—'),
      }))
      .filter(t => Number.isFinite(t.price) && t.price > VALID_PRICE_MIN_RP);

    if(!valid.length) return null;
    const target = Number(targetLotArea);
    if(Number.isFinite(target) && target > 0){
      const withLand = valid.filter(t => Number.isFinite(t.landArea) && t.landArea > 0);
      if(withLand.length){
        withLand.sort((a,b)=>{
          const da = Math.abs(Math.log(a.landArea/target));
          const db = Math.abs(Math.log(b.landArea/target));
          return da-db || a.price-b.price;
        });
        return withLand[0];
      }
    }
    valid.sort((a,b)=>a.price-b.price);
    return valid[Math.floor((valid.length-1)/2)];
  }

  function spatialKnnEstimate(projects,category='all',targetLotArea=0,requestedK=DEFAULT_K){
    const kWanted = Math.max(1,Math.min(MAX_K,Math.round(Number(requestedK)||DEFAULT_K)));
    const samples = [];
    for(const project of projects || []){
      const distanceKm = Number(project?.distance_km);
      if(!Number.isFinite(distanceKm) || distanceKm < 0) continue;
      const representative = selectRepresentativeType(project,category,targetLotArea);
      if(!representative) continue;
      samples.push({
        project,
        distanceKm,
        price:representative.price,
        landArea:representative.landArea,
        buildingArea:representative.buildingArea,
        type:representative.type,
        category:representative.category,
      });
    }
    samples.sort((a,b)=>a.distanceKm-b.distanceKm || String(a.project?.name||'').localeCompare(String(b.project?.name||''),'id'));
    if(!samples.length) return null;

    const neighbors = samples.slice(0,Math.min(kWanted,samples.length));
    let weightedPrice = 0, totalWeight = 0;
    const target = Number(targetLotArea);
    for(const n of neighbors){
      const distanceWeight = 1 / Math.max(n.distanceKm,MIN_DISTANCE_KM)**2;
      let lotSimilarity = 1;
      if(Number.isFinite(target) && target > 0 && Number.isFinite(n.landArea) && n.landArea > 0){
        const mismatch = Math.abs(Math.log(n.landArea/target));
        lotSimilarity = 1 / (1 + 2*mismatch);
      }
      n.weight = distanceWeight * lotSimilarity;
      weightedPrice += n.price * n.weight;
      totalWeight += n.weight;
    }
    if(!(totalWeight > 0)) return null;

    const predictedPrice = weightedPrice / totalWeight;
    const prices = neighbors.map(n=>n.price).sort((a,b)=>a-b);
    const p25 = percentile(prices,.25);
    const median = percentile(prices,.50);
    const p75 = percentile(prices,.75);
    const maxDistanceKm = Math.max(...neighbors.map(n=>n.distanceKm));
    const meanDistanceKm = neighbors.reduce((s,n)=>s+n.distanceKm,0)/neighbors.length;
    let coverage = 'TERBATAS';
    if(neighbors.length >= 7 && maxDistanceKm <= 5) coverage = 'KUAT';
    else if(neighbors.length >= 5 && maxDistanceKm <= 7) coverage = 'CUKUP';

    return {
      requestedK:kWanted,
      k:neighbors.length,
      predictedPrice,
      conservativePrice:p25,
      medianPrice:median,
      p25,
      p75,
      maxDistanceKm,
      meanDistanceKm,
      coverage,
      neighbors,
    };
  }

  async function loadDataset(){
    const chunks = await Promise.all(PARTS.map(async url => {
      const response = await fetch(url,{cache:'force-cache'});
      if(!response.ok) throw new Error(`gagal memuat ${url} (${response.status})`);
      return (await response.text()).trim();
    }));
    const b64 = chunks.join('').replace(/\s+/g,'');
    const raw = Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
    if(typeof DecompressionStream === 'undefined') throw new Error('browser tidak mendukung DecompressionStream(gzip)');
    const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'));
    return JSON.parse(await new Response(stream).text());
  }

  function injectStyles(){
    if(el('market-knn-style')) return;
    const style = document.createElement('style');
    style.id = 'market-knn-style';
    style.textContent = `
      .stats .knn-price-card{border-color:#b7dfca;background:#f3fbf6}
      .stats .knn-price-card strong{color:#126a43}
      .stats .knn-basis-card{border-color:#d7dce4;background:#fafbfc}
      #marketKnnStatus{margin-top:8px;padding:8px;border:1px solid #dfe5ec;border-radius:8px;background:#fafbfc;font-size:10.5px;line-height:1.45;color:#475467}
    `;
    document.head.appendChild(style);
  }

  function injectStatsCards(){
    if(el('knnRecommendedPrice')) return;
    const stats = el('parcelArea')?.closest('.stats');
    if(!stats) return;
    const cards = [
      ['Rekomendasi harga rumah (Spatial KNN)','knnRecommendedPrice','knn-price-card'],
      ['Harga konservatif KNN','knnConservativePrice','knn-price-card'],
      ['Rentang neighbor KNN','knnPriceRange','knn-basis-card'],
      ['Basis KNN','knnNeighborInfo','knn-basis-card'],
    ];
    for(const [label,id,cls] of cards){
      const card = document.createElement('div');
      card.className = cls;
      card.innerHTML = `<span>${label}</span><strong id="${id}">—</strong>`;
      stats.appendChild(card);
    }
  }

  function ensureKnnControls(){
    const panel = el('marketReferencePanel');
    if(!panel || el('marketK')) return;
    const grid = panel.querySelector('.grid2');
    if(!grid) return;
    const kWrap = document.createElement('div');
    kWrap.innerHTML = '<label>K neighbor</label><input id="marketK" type="number" value="7" min="3" max="15" step="2" />';
    const modelWrap = document.createElement('div');
    modelWrap.innerHTML = '<label>Model rekomendasi</label><div class="readonly-chip">Spatial KNN + IDW</div>';
    grid.appendChild(kWrap);
    grid.appendChild(modelWrap);
    el('marketK')?.addEventListener('change',()=>refresh(true));

    if(!el('marketKnnStatus')){
      const status = document.createElement('div');
      status.id = 'marketKnnStatus';
      status.textContent = 'Spatial KNN menunggu polygon aktif.';
      panel.appendChild(status);
    }
  }

  function resetStats(message='—'){
    ['knnRecommendedPrice','knnConservativePrice','knnPriceRange','knnNeighborInfo'].forEach(id=>{
      if(el(id)) el(id).textContent = message;
    });
    if(el('marketKnnStatus')) el('marketKnnStatus').textContent = 'Spatial KNN menunggu polygon aktif.';
  }

  function renderKnn(result,targetLotArea,category,radiusKm){
    injectStatsCards();
    if(!result){
      resetStats();
      if(el('marketKnnStatus')) el('marketKnnStatus').textContent = `Tidak ada data harga valid untuk Spatial KNN dalam radius ${radiusKm.toFixed(1)} km.`;
      return;
    }
    if(el('knnRecommendedPrice')) el('knnRecommendedPrice').textContent = money(result.predictedPrice);
    if(el('knnConservativePrice')) el('knnConservativePrice').textContent = money(result.conservativePrice);
    if(el('knnPriceRange')) el('knnPriceRange').textContent = `${money(result.p25)} – ${money(result.p75)}`;
    if(el('knnNeighborInfo')) el('knnNeighborInfo').textContent = `K=${result.k} • ${result.coverage} • max ${result.maxDistanceKm.toFixed(2)} km`;

    const lotText = Number.isFinite(targetLotArea) && targetLotArea > 0 ? `${targetLotArea.toFixed(0)} m²` : 'tanpa filter LT';
    const categoryText = category === 'all' ? 'semua kategori' : category;
    const closest = result.neighbors.slice(0,3).map(n=>`${n.project?.name||'—'} (${n.distanceKm.toFixed(2)} km, ${money(n.price)})`).join(' • ');
    if(el('marketKnnStatus')){
      el('marketKnnStatus').innerHTML = `<b>Spatial KNN:</b> rekomendasi ${money(result.predictedPrice)} • floor P25 ${money(result.conservativePrice)} • ${result.k} neighbor • target LT ${lotText} • ${categoryText}.<br>${closest}`;
    }
  }

  function currentTargetLotArea(){
    const width = Number(el('lotWidth')?.value);
    const depth = Number(el('lotDepth')?.value);
    return Number.isFinite(width) && width > 0 && Number.isFinite(depth) && depth > 0 ? width*depth : 0;
  }

  async function refresh(force=false){
    if(!ready || refreshing) return;
    ensureKnnControls();
    injectStatsCards();
    if(typeof currentGeometry === 'undefined' || !currentGeometry){
      lastKey = '';
      resetStats();
      return;
    }

    const targetLotArea = currentTargetLotArea();
    const category = el('marketCategory')?.value || 'all';
    const radiusKm = Math.max(0.5,Number(el('marketRadiusExtended')?.value || 7));
    const requestedK = Math.max(1,Math.min(MAX_K,Math.round(Number(el('marketK')?.value || DEFAULT_K))));
    const key = JSON.stringify([currentGeometry,targetLotArea,category,radiusKm,requestedK]);
    if(!force && key === lastKey) return;
    lastKey = key;
    refreshing = true;
    try{
      const center = geometryCenter(currentGeometry);
      if(!center){ resetStats(); return; }
      const candidates = [];
      for(const raw of dataset?.records || []){
        if(raw?.lat == null || raw?.lon == null) continue;
        const lat = Number(raw.lat), lon = Number(raw.lon);
        if(!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
        const distance_km = haversineKm(center,{lat,lon});
        if(distance_km > radiusKm) continue;
        if(category !== 'all' && !comparableTypes(raw,category).length) continue;
        candidates.push({...raw,distance_km});
      }
      candidates.sort((a,b)=>a.distance_km-b.distance_km || String(a.name||'').localeCompare(String(b.name||''),'id'));
      const result = spatialKnnEstimate(candidates,category,targetLotArea,requestedK);
      renderKnn(result,targetLotArea,category,radiusKm);
    }catch(err){
      console.error('Spatial KNN error:',err);
      resetStats();
      if(el('marketKnnStatus')) el('marketKnnStatus').textContent = 'Spatial KNN gagal: '+err.message;
    }finally{
      refreshing = false;
    }
  }

  async function init(){
    injectStyles();
    injectStatsCards();
    try{
      dataset = await loadDataset();
      ready = true;
      ensureKnnControls();
      ['lotWidth','lotDepth','marketCategory','marketRadiusExtended'].forEach(id=>el(id)?.addEventListener('change',()=>refresh(true)));
      refresh(true);
      setInterval(()=>refresh(false),700);
    }catch(err){
      console.error('Spatial KNN dataset load error:',err);
      resetStats();
      if(el('marketKnnStatus')) el('marketKnnStatus').textContent = 'Gagal memuat Spatial KNN dataset: '+err.message;
    }
  }

  globalThis.DevOSSpatialKNN = {
    geometryCenter,
    haversineKm,
    selectRepresentativeType,
    spatialKnnEstimate,
    percentile,
  };

  if(typeof document !== 'undefined'){
    if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded',init,{once:true});
    else init();
  }
})();
