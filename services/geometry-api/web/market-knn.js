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
  const MAX_NEIGHBOR_WEIGHT_SHARE = 0.35;
  const PRICE_OUTLIER_WEIGHT_FACTOR = 0.35;

  let dataset = null;
  let ready = false;
  let refreshing = false;
  let lastKey = '';

  const el = id => typeof document === 'undefined' ? null : document.getElementById(id);
  const esc = value => String(value ?? '—').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
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

  function capWeightShares(weights,maxShare=MAX_NEIGHBOR_WEIGHT_SHARE){
    if(!Array.isArray(weights) || !weights.length) return [];
    const positive = weights.map(w => Number.isFinite(Number(w)) && Number(w) > 0 ? Number(w) : 0);
    const total = positive.reduce((a,b)=>a+b,0);
    if(!(total > 0)) return positive.map(()=>1/positive.length);
    let shares = positive.map(w=>w/total);
    const cap = Math.max(Number(maxShare)||MAX_NEIGHBOR_WEIGHT_SHARE,1/shares.length);
    for(let iteration=0; iteration<12; iteration++){
      const over = shares.map((v,i)=>v>cap+1e-12?i:-1).filter(i=>i>=0);
      if(!over.length) break;
      let excess = 0;
      const overSet = new Set(over);
      for(const i of over){ excess += shares[i]-cap; shares[i]=cap; }
      const under = shares.map((v,i)=>!overSet.has(i)&&v<cap-1e-12?i:-1).filter(i=>i>=0);
      if(!under.length) break;
      const underTotal = under.reduce((sum,i)=>sum+shares[i],0);
      for(const i of under){
        const room = cap-shares[i];
        const add = underTotal>0 ? excess*(shares[i]/underTotal) : excess/under.length;
        shares[i] += Math.min(room,add);
      }
      const sum = shares.reduce((a,b)=>a+b,0);
      if(sum>0) shares = shares.map(v=>v/sum);
    }
    const sum = shares.reduce((a,b)=>a+b,0);
    return sum>0 ? shares.map(v=>v/sum) : shares;
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
    const target = Number(targetLotArea);
    for(const n of neighbors){
      const distanceWeight = 1 / Math.max(n.distanceKm,MIN_DISTANCE_KM)**2;
      let lotSimilarity = 1;
      if(Number.isFinite(target) && target > 0 && Number.isFinite(n.landArea) && n.landArea > 0){
        const mismatch = Math.abs(Math.log(n.landArea/target));
        lotSimilarity = 1 / (1 + 2*mismatch);
      }
      n.distanceWeight = distanceWeight;
      n.lotSimilarity = lotSimilarity;
      n.rawWeight = distanceWeight * lotSimilarity;
    }

    const prices = neighbors.map(n=>n.price).sort((a,b)=>a-b);
    const minPrice = prices[0] ?? null;
    const maxPrice = prices[prices.length-1] ?? null;
    const p25 = percentile(prices,.25);
    const median = percentile(prices,.50);
    const p75 = percentile(prices,.75);
    const iqr = Number.isFinite(p75) && Number.isFinite(p25) ? p75-p25 : 0;
    const lowerFence = Number.isFinite(p25) ? p25-1.5*iqr : -Infinity;
    const upperFence = Number.isFinite(p75) ? p75+1.5*iqr : Infinity;

    const rawTotalWeight = neighbors.reduce((sum,n)=>sum+n.rawWeight,0);
    if(!(rawTotalWeight > 0)) return null;
    const rawPredictedPrice = neighbors.reduce((sum,n)=>sum+n.price*n.rawWeight,0)/rawTotalWeight;

    const robustWeights = neighbors.map(n=>{
      n.isPriceOutlier = iqr>0 && (n.price<lowerFence || n.price>upperFence);
      n.outlierFactor = n.isPriceOutlier ? PRICE_OUTLIER_WEIGHT_FACTOR : 1;
      n.robustWeight = n.rawWeight*n.outlierFactor;
      return n.robustWeight;
    });
    const finalShares = capWeightShares(robustWeights,MAX_NEIGHBOR_WEIGHT_SHARE);
    for(let i=0;i<neighbors.length;i++){
      neighbors[i].rawWeightShare = neighbors[i].rawWeight/rawTotalWeight;
      neighbors[i].finalWeightShare = finalShares[i] ?? 0;
    }

    const predictedPrice = neighbors.reduce((sum,n)=>sum+n.price*n.finalWeightShare,0);
    const maxDistanceKm = Math.max(...neighbors.map(n=>n.distanceKm));
    const meanDistanceKm = neighbors.reduce((sum,n)=>sum+n.distanceKm,0)/neighbors.length;
    const maxWeightShare = Math.max(...neighbors.map(n=>n.finalWeightShare));
    const outlierCount = neighbors.filter(n=>n.isPriceOutlier).length;
    let coverage = 'TERBATAS';
    if(neighbors.length >= 7 && maxDistanceKm <= 5) coverage = 'KUAT';
    else if(neighbors.length >= 5 && maxDistanceKm <= 7) coverage = 'CUKUP';

    return {
      requestedK:kWanted,
      k:neighbors.length,
      predictedPrice,
      rawPredictedPrice,
      conservativePrice:p25,
      medianPrice:median,
      minPrice,
      maxPrice,
      p25,
      p75,
      iqr,
      lowerFence,
      upperFence,
      maxDistanceKm,
      meanDistanceKm,
      maxWeightShare,
      outlierCount,
      robustnessApplied:outlierCount>0 || Math.abs(predictedPrice-rawPredictedPrice)>1,
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
      #marketKnnAudit{margin-top:8px;border:1px solid #dfe5ec;border-radius:8px;background:#fff;padding:7px 9px;font-size:10.5px}
      #marketKnnAudit summary{cursor:pointer;font-weight:800;color:#344054}
      #marketKnnAudit table{width:100%;border-collapse:collapse;margin-top:7px;font-size:9.8px}
      #marketKnnAudit th,#marketKnnAudit td{padding:4px;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}
      #marketKnnAudit td.num{text-align:right;white-space:nowrap}
      #marketKnnAudit .outlier{color:#b42318;font-weight:800}
    `;
    document.head.appendChild(style);
  }

  function injectStatsCards(){
    if(el('knnRecommendedPrice')) return;
    const stats = el('parcelArea')?.closest('.stats');
    if(!stats) return;
    const cards = [
      ['Rekomendasi harga rumah (Robust Spatial KNN)','knnRecommendedPrice','knn-price-card'],
      ['Harga konservatif KNN (P25)','knnConservativePrice','knn-price-card'],
      ['Min–Max neighbor KNN','knnMinMaxRange','knn-basis-card'],
      ['IQR neighbor KNN (P25–P75)','knnIqrRange','knn-basis-card'],
      ['Raw weighted KNN','knnRawWeightedPrice','knn-basis-card'],
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
    if(!panel) return;
    if(!el('marketK')){
      const grid = panel.querySelector('.grid2');
      if(grid){
        const kWrap = document.createElement('div');
        kWrap.innerHTML = '<label>K neighbor</label><input id="marketK" type="number" value="7" min="3" max="15" step="2" />';
        const modelWrap = document.createElement('div');
        modelWrap.innerHTML = '<label>Model rekomendasi</label><div class="readonly-chip">Robust Spatial KNN</div>';
        grid.appendChild(kWrap);
        grid.appendChild(modelWrap);
        el('marketK')?.addEventListener('change',()=>refresh(true));
      }
    }
    if(!el('marketKnnStatus')){
      const status = document.createElement('div');
      status.id = 'marketKnnStatus';
      status.textContent = 'Robust Spatial KNN menunggu polygon aktif.';
      panel.appendChild(status);
    }
    if(!el('marketKnnAudit')){
      const details = document.createElement('details');
      details.id = 'marketKnnAudit';
      details.innerHTML = '<summary>Audit neighbor KNN — harga, jarak & bobot</summary><div id="marketKnnAuditBody">Pilih polygon untuk melihat neighbor.</div>';
      panel.appendChild(details);
    }
  }

  function resetStats(message='—'){
    ['knnRecommendedPrice','knnConservativePrice','knnMinMaxRange','knnIqrRange','knnRawWeightedPrice','knnNeighborInfo'].forEach(id=>{
      if(el(id)) el(id).textContent = message;
    });
    if(el('marketKnnStatus')) el('marketKnnStatus').textContent = 'Robust Spatial KNN menunggu polygon aktif.';
    if(el('marketKnnAuditBody')) el('marketKnnAuditBody').textContent = 'Pilih polygon untuk melihat neighbor.';
  }

  function renderKnn(result,targetLotArea,category,radiusKm){
    injectStatsCards();
    ensureKnnControls();
    if(!result){
      resetStats();
      if(el('marketKnnStatus')) el('marketKnnStatus').textContent = `Tidak ada data harga valid untuk Robust Spatial KNN dalam radius ${radiusKm.toFixed(1)} km.`;
      return;
    }
    if(el('knnRecommendedPrice')) el('knnRecommendedPrice').textContent = money(result.predictedPrice);
    if(el('knnConservativePrice')) el('knnConservativePrice').textContent = money(result.conservativePrice);
    if(el('knnMinMaxRange')) el('knnMinMaxRange').textContent = `${money(result.minPrice)} – ${money(result.maxPrice)}`;
    if(el('knnIqrRange')) el('knnIqrRange').textContent = `${money(result.p25)} – ${money(result.p75)}`;
    if(el('knnRawWeightedPrice')) el('knnRawWeightedPrice').textContent = money(result.rawPredictedPrice);
    if(el('knnNeighborInfo')) el('knnNeighborInfo').textContent = `K=${result.k} • ${result.coverage} • max ${result.maxDistanceKm.toFixed(2)} km • bobot max ${(result.maxWeightShare*100).toFixed(1)}%`;

    const lotText = Number.isFinite(targetLotArea) && targetLotArea > 0 ? `${targetLotArea.toFixed(0)} m²` : 'tanpa filter LT';
    const categoryText = category === 'all' ? 'semua kategori' : category;
    const robustNote = result.robustnessApplied
      ? `Guard aktif: ${result.outlierCount} outlier harga diturunkan bobotnya; bobot tiap neighbor dibatasi ${(MAX_NEIGHBOR_WEIGHT_SHARE*100).toFixed(0)}%.`
      : `Tidak ada outlier dominan; bobot tiap neighbor tetap dibatasi ${(MAX_NEIGHBOR_WEIGHT_SHARE*100).toFixed(0)}%.`;
    if(el('marketKnnStatus')){
      el('marketKnnStatus').innerHTML = `<b>Robust Spatial KNN:</b> rekomendasi ${money(result.predictedPrice)} • raw weighted ${money(result.rawPredictedPrice)} • P25 ${money(result.conservativePrice)} • K=${result.k} • target LT ${lotText} • ${categoryText}.<br>${robustNote}`;
    }

    const audit = el('marketKnnAuditBody');
    if(audit){
      const rows = result.neighbors.map((n,i)=>{
        const lt = Number.isFinite(n.landArea) ? `${Math.round(n.landArea)} m²` : '—';
        const weight = `${((n.finalWeightShare||0)*100).toFixed(1)}%`;
        return `<tr><td>${i+1}</td><td>${esc(n.project?.name||'—')}<br><small>${esc(n.type)} • LT ${lt}</small></td><td class="num">${n.distanceKm.toFixed(2)} km</td><td class="num">${money(n.price)}</td><td class="num">${weight}</td><td>${n.isPriceOutlier?'<span class="outlier">OUTLIER</span>':'normal'}</td></tr>`;
      }).join('');
      audit.innerHTML = `<table><thead><tr><th>#</th><th>Neighbor / tipe</th><th>Jarak</th><th>Harga</th><th>Bobot final</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
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
    capWeightShares,
    percentile,
  };

  if(typeof document !== 'undefined'){
    if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded',init,{once:true});
    else init();
  }
})();
