// M2.5.13 — real backend Recovery Solver monitor.
// Progress shown here comes only from /site-plan/solver/status; there is no fake timer progress.
let recoverySolverJobId = null;
let recoverySolverRunning = false;

function solverEl(id){ return document.getElementById(id); }
function solverNum(v,d=2){ const n=Number(v); return Number.isFinite(n)?n.toFixed(d):'—'; }

function resetRecoveryMonitor(){
  const panel=solverEl('solverMonitor');
  if(!panel)return;
  panel.classList.remove('running','completed-pass','completed-fail');
  solverEl('solverOverallStatus').textContent='Siap';
  solverEl('solverOverallStatus').className='solver-state';
  solverEl('solverBest').textContent='—';
  solverEl('solverTarget').textContent='70.00%';
  solverEl('solverGap').textContent='—';
  solverEl('solverProgressBar').style.width='0%';
  if(solverEl('solverCancelBtn'))solverEl('solverCancelBtn').disabled=true;
  solverEl('solverStages').innerHTML='<div class="empty-state">Solver belum dijalankan.</div>';
  solverEl('solverCurrent').innerHTML='<div class="empty-state">Belum ada candidate aktif.</div>';
  solverEl('solverHistory').innerHTML='<div class="empty-state">Search History masih kosong.</div>';
  solverEl('solverFeasibility').innerHTML='<div class="empty-state">Feasibility Analysis dijalankan hanya jika seluruh recovery stage belum mencapai 70%.</div>';
}

function solverStageIcon(status){
  if(status==='running')return '▶';
  if(status==='completed')return '✓';
  if(status==='failed')return '✕';
  if(status==='skipped')return '—';
  return '○';
}

function renderSolverStages(stages=[]){
  const root=solverEl('solverStages'); if(!root)return;
  root.innerHTML='';
  for(const stage of stages){
    const div=document.createElement('div');
    div.className=`solver-stage ${stage.status||'pending'}`;
    const tested=Number(stage.candidates_tested||0), total=Number(stage.candidate_total||0);
    const metric=stage.status==='running'||stage.status==='completed'
      ? `${tested}${total?`/${total}`:''} • best ${solverNum(stage.best_efficiency_pct,2)}%`
      : '';
    div.innerHTML=`<div class="icon">${solverStageIcon(stage.status)}</div><div><strong>${stage.name||stage.id}</strong><small>${stage.current_strategy?`${stage.current_strategy} • `:''}${stage.message||''}</small></div><div class="stage-metric">${metric}</div>`;
    root.appendChild(div);
  }
}

function renderSolverCurrent(candidate){
  const root=solverEl('solverCurrent'); if(!root)return;
  if(!candidate){root.innerHTML='<div class="empty-state">Belum ada candidate aktif.</div>';return;}
  const status=candidate.pass?'PASS':'REJECT';
  root.innerHTML=`<div class="solver-current-grid">
    <div class="wide"><span>Strategy</span><b>${candidate.strategy||candidate.name||'—'}</b></div>
    <div><span>Stage</span><b>${candidate.stage||'—'}</b></div>
    <div><span>Efficiency</span><b>${solverNum(candidate.efficiency_pct,2)}% • ${status}</b></div>
    <div><span>Road</span><b>${solverNum(candidate.road_pct,2)}%</b></div>
    <div><span>Standard</span><b>${candidate.standard_count??'—'}</b></div>
    <div><span>Adaptive</span><b>${candidate.adaptive_count??0}</b></div>
    <div><span>Residual</span><b>${solverNum(candidate.residual_pct,2)}%</b></div>
  </div>`;
}

function renderSolverHistory(history=[]){
  const root=solverEl('solverHistory'); if(!root)return;
  const rows=[...history].slice(-30).reverse();
  if(!rows.length){root.innerHTML='<div class="empty-state">Search History masih kosong.</div>';return;}
  root.innerHTML='';
  rows.forEach((h,i)=>{
    const row=document.createElement('div');
    row.className='solver-history-row';
    row.innerHTML=`<span>#${history.length-i}</span><span class="strategy">${h.strategy||h.name||'candidate'}</span><span><b>${solverNum(h.efficiency_pct,2)}%</b></span><span class="${h.pass?'pass':'fail'}">${h.pass?'PASS':'REJECT'}</span>`;
    root.appendChild(row);
  });
}

function renderSolverFeasibility(f){
  const root=solverEl('solverFeasibility'); if(!root)return;
  if(!f){root.innerHTML='<div class="empty-state">Belum dijalankan.</div>';return;}
  const proven=!!f.mathematically_infeasible;
  root.innerHTML=`<div class="solver-feasibility ${proven?'proven':'open'}">
    <strong>${proven?'70% MATHEMATICALLY INFEASIBLE':'SOLVER BELUM KONVERGEN'}</strong>
    <div>Best actual: <b>${solverNum(f.best_actual_efficiency_pct,2)}%</b></div>
    <div>Theoretical upper bound (tanpa jalan/residual): <b>${solverNum(f.theoretical_upper_bound_pct,2)}%</b></div>
    <div>Observed search estimate: <b>${f.observed_search_estimate_pct==null?'—':solverNum(f.observed_search_estimate_pct,2)+'%'}</b></div>
    <div>RTH ${solverNum(f.rth_pct,2)}% • PSU ${solverNum(f.psu_pct,2)}% • min road observed ${f.minimum_observed_road_pct==null?'—':solverNum(f.minimum_observed_road_pct,2)+'%'}</div>
    <div>${f.message||''}</div>
  </div>`;
}

function renderRecoverySolverStatus(job){
  const panel=solverEl('solverMonitor'); if(!panel)return;
  panel.classList.toggle('running',job.status==='running'||job.status==='queued');
  panel.classList.toggle('completed-pass',job.status==='completed'&&Number(job.valid_count||0)>0);
  panel.classList.toggle('completed-fail',job.status==='failed'||(job.status==='completed'&&Number(job.valid_count||0)===0));

  const statusEl=solverEl('solverOverallStatus');
  if(job.status==='running'||job.status==='queued'){
    statusEl.textContent='Solver berjalan';statusEl.className='solver-state running';
  }else if(job.status==='completed'&&Number(job.valid_count||0)>0){
    statusEl.textContent=`PASS • ${job.valid_count} valid`;statusEl.className='solver-state pass';
  }else if(job.status==='completed'){
    statusEl.textContent='Belum ada PASS';statusEl.className='solver-state fail';
  }else if(job.status==='failed'){
    statusEl.textContent='Solver error';statusEl.className='solver-state fail';
  }

  const best=Number(job.best_seen?.efficiency_pct||0), target=Number(job.target_efficiency_pct||70), gap=best-target;
  solverEl('solverBest').textContent=job.best_seen?`${best.toFixed(2)}%`:'—';
  solverEl('solverTarget').textContent=`${target.toFixed(2)}%`;
  solverEl('solverGap').textContent=job.best_seen?`${gap>=0?'+':''}${gap.toFixed(2)} pt`:'—';
  solverEl('solverGapBox').classList.toggle('good',gap>=0&&!!job.best_seen);
  solverEl('solverGapBox').classList.toggle('bad',gap<0&&!!job.best_seen);
  solverEl('solverProgressBar').style.width=`${Math.max(0,Math.min(100,(best/Math.max(target,1))*100))}%`;

  renderSolverStages(job.stages||[]);
  renderSolverCurrent(job.current_candidate||null);
  renderSolverHistory(job.search_history||[]);
  renderSolverFeasibility(job.feasibility||job.result?.feasibility||null);
  if(solverEl('solverCancelBtn'))solverEl('solverCancelBtn').disabled=!(job.status==='running'||job.status==='queued');
}

function clearGeneratedLayoutForNoPass(){
  selectedAlternative=null;
  baselineByAltId=new Map();
  editHistory=[];
  ['buildable-source','roads-source','road-segments-source','road-centerlines-source','rth-source','psu-source','reserve-source','drainage-source','lots-source','residual-source','selection-source','edit-handles-source','smart-adjust-source'].forEach(id=>updateGeoSource(id,blankFC()));
  if(solverEl('saveBtn'))solverEl('saveBtn').disabled=true;
  if(solverEl('optimizeYieldBtn'))solverEl('optimizeYieldBtn').disabled=true;
  solverEl('alternativeCards').innerHTML='<div class="solver-empty-valid">0 VALID ALTERNATIVES ≥70%. Candidate di bawah 70% hanya tampil pada Search History dan tidak dapat dipilih/disimpan.</div>';
  solverEl('altSummary').textContent='0 valid';
  ['buildArea','lotCount','standardLotCount','adaptiveLotCount','efficiency','roadArea','roadLength','rthArea','psuArea','drainageLength','orientation','unusedArea','landUtilization','residualRatio','roadEfficiency','blockRegularity','roadConnectivity'].forEach(id=>{if(solverEl(id))solverEl(id).textContent='—';});
}

function recoveryPayload(){
  return {
    geometry:currentGeometry,
    setback_m:Number(solverEl('setback').value),
    lot_width_m:Number(solverEl('lotWidth').value),
    lot_depth_m:Number(solverEl('lotDepth').value),
    main_road_width_m:Number(solverEl('mainRoad').value),
    local_road_width_m:Number(solverEl('localRoad').value),
    rth_pct:Number(solverEl('rthPct').value),
    psu_pct:Number(solverEl('psuPct').value),
    alternative_count:Number(solverEl('altCount').value),
    land_optimization_enabled:landOptimizationEnabled(),
  };
}

async function pollRecoverySolver(jobId){
  while(true){
    const job=await api(`/site-plan/solver/status/${encodeURIComponent(jobId)}`);
    renderRecoverySolverStatus(job);
    if(job.status==='failed') throw new Error(job.error||job.message||'Recovery Solver gagal');
    if(job.status==='completed'||job.status==='cancelled') return job;
    await new Promise(resolve=>setTimeout(resolve,450));
  }
}


async function cancelRecoverySolver(){
  if(!recoverySolverJobId||!recoverySolverRunning)return;
  const btn=solverEl('solverCancelBtn');
  try{
    if(btn){btn.disabled=true;btn.textContent='Menghentikan…';}
    await api(`/site-plan/solver/cancel/${encodeURIComponent(recoverySolverJobId)}`,{method:'POST'});
    msg('Permintaan stop dikirim. Solver berhenti setelah candidate aktif selesai.');
  }catch(e){msg(`Gagal menghentikan solver: ${e.message}`,'error');}
  finally{if(btn)btn.textContent='Stop Recovery Solver';}
}

async function generateSitePlanRecovery(){
  if(!currentGeometry||recoverySolverRunning)return;
  recoverySolverRunning=true;
  const btn=solverEl('generateBtn');
  try{
    btn.disabled=true;
    resetRecoveryMonitor();
    solverEl('solverMonitor').classList.add('running');
    solverEl('solverOverallStatus').textContent='Memulai solver…';
    solverEl('solverOverallStatus').className='solver-state running';
    msg('M2.5.13 Recovery Solver: mencari layout VALID dengan Efisiensi Kavling ≥70%…');

    const started=await api('/site-plan/solver/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(recoveryPayload())});
    recoverySolverJobId=started.job_id;
    const job=await pollRecoverySolver(recoverySolverJobId);
    if(job.status==='cancelled'){msg('Recovery Solver dihentikan. Layout sebelumnya dipertahankan.');return;}
    sitePlan=job.result;
    if(!sitePlan)throw new Error('Solver selesai tanpa result payload');

    const valid=(sitePlan.alternatives||[]).filter(a=>Number(a.stats?.lot_efficiency_pct||0)>=70&&a.validation?.valid===true);
    sitePlan.alternatives=valid;
    if(!valid.length){
      clearGeneratedLayoutForNoPass();
      const f=job.feasibility||sitePlan.feasibility;
      const text=f?.mathematically_infeasible
        ? `Target 70% terbukti tidak feasible untuk fixed constraints ini. Upper bound ${solverNum(f.theoretical_upper_bound_pct,2)}%.`
        : `Target 70% belum ditemukan. Best solver ${solverNum(job.best_seen?.efficiency_pct,2)}%; lihat Recovery Solver Monitor untuk bottleneck.`;
      msg(text,'error');
      return;
    }

    baselineByAltId=new Map(valid.map(a=>[a.id,deepClone(a)]));
    editHistory=[];
    renderAlternativeCards(valid);
    renderAlternative(valid[0]);
    msg(`${valid.length} alternatif VALID ≥70% ditemukan. Opsi #1 dipilih otomatis.`,'success');
  }catch(e){
    msg(`Recovery Solver gagal: ${e.message}`,'error');
  }finally{
    recoverySolverRunning=false;
    btn.disabled=!currentGeometry;
  }
}

window.addEventListener('load',()=>{
  resetRecoveryMonitor();
  const btn=solverEl('generateBtn');
  if(btn)btn.onclick=generateSitePlanRecovery;
  const cancelBtn=solverEl('solverCancelBtn');
  if(cancelBtn)cancelBtn.onclick=cancelRecoverySolver;
});
