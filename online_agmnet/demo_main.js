(() => {
  const demoMode = new URLSearchParams(location.search).get('demo') === '1';
  if (demoMode) {
    const status = document.createElement('div');
    status.id = 'localDemoMainStatus';
    status.style.cssText = 'margin:8px 2px;color:#7f9bb5;font-size:12px';
    document.querySelector('.topbar').after(status);
    document.getElementById('durationInput')?.dispatchEvent(new Event('change'));
    const names = { idle:'等待本地演示', loading:'读取本地 EEG', running:'本地数据播放中', paused:'本地数据已暂停', stopped:'本地数据已停止', completed:'本地片段播放完毕', error:'本地演示错误' };
    let queue = Promise.resolve();
    window.addEventListener('matb-session', ({ detail }) => {
      const actions = { start:'start', pause:'pause', resume:'resume', end:'stop', timeout:'stop' };
      const action = actions[detail.type];
      if (!action) return;
      if (detail.type === 'start') document.getElementById('difficultyInput').disabled = true;
      if (['end', 'timeout'].includes(detail.type)) document.getElementById('difficultyInput').disabled = false;
      queue = queue.then(async () => {
        try {
          const r = await fetch('/api/demo/' + action, { method:'POST', headers:{'Content-Type':'application/json'}, body:'{}' });
          const d = await r.json(); if (!r.ok) throw Error(d.error || '接口错误');
        } catch (e) { status.textContent = '本地演示接口连接失败：' + e.message; }
      });
    });
    let pending = false;
    async function refreshDemo() {
      if (pending) return; pending = true;
      try {
        const r = await fetch('/api/demo/state', { cache:'no-store' }); if (!r.ok) throw Error('服务不可用');
        const d = await r.json();
        const score = d.prediction && Number.isFinite(Number(d.prediction.score)) ? Number(d.prediction.score) : null;
        const prediction = score == null ? null : { phase: 'test', loadScore: score, source: 'demo-replay' };
        if (window.matbAutomation?.handleModelPrediction) window.matbAutomation.handleModelPrediction(prediction);
        const auto = window.matbAutomation?.snapshot?.();
        const source = d.eegSource === 'live' ? `${d.eegStatus} · 预测表回放` : `${d.record?.label || 'S3'} · 本地数据回放`;
        const playback = d.status === 'paused' ? names.paused : d.message || names[d.status];
        status.textContent = d.error || `${playback} · ${(d.elapsedSeconds || 0).toFixed(1)}秒 · ${source}`;
        if (auto?.active) status.textContent += ` · 自动计控：${auto.task}`;
      } catch { status.textContent = '请使用 start_matb_demo.bat 启动本地演示服务'; }
      finally { pending = false; }
    }
    refreshDemo(); setInterval(refreshDemo, 250);
    return;
  }

  const status=document.createElement('div');
  status.id='agmStatus';
  status.style.cssText='margin:8px 2px;color:#7f9bb5;font-size:12px';
  document.querySelector('.topbar').after(status);
  document.getElementById('durationInput').dispatchEvent(new Event('change'));
  const names={idle:'等待开始实验',loading:'加载 S3 脑电与模型',running:'脑电回放中',paused:'脑电已暂停',stopped:'脑电已停止',completed:'本段脑电已播放完毕',error:'回放错误'};
  let queue=Promise.resolve();
  window.addEventListener('matb-session',({detail})=>{
    const actions={start:'session',pause:'pause',resume:'resume',end:'stop',timeout:'stop'};
    const action=actions[detail.type];
    if(!action)return;
    if(detail.type==='start')document.getElementById('difficultyInput').disabled=true;
    if(['end','timeout'].includes(detail.type))document.getElementById('difficultyInput').disabled=false;
    queue=queue.then(async()=>{
      try{
        const r=await fetch('/api/agmnet/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({difficulty:detail.difficulty})});
        const d=await r.json();if(!r.ok)throw Error(d.error);
      }catch(e){status.textContent='脑电回放连接失败，请使用 start_matb.bat 启动：'+e.message;}
    });
  });
  let pending=false;
  async function refresh(){
    if(pending)return;pending=true;
    try{
      const r=await fetch('/api/agmnet/state',{cache:'no-store'});if(!r.ok)throw Error('服务不可用');
      const d=await r.json();
      const prediction=d.prediction&&d.prediction.phase==='test'&&Number.isFinite(Number(d.prediction.loadScore))?d.prediction:null;
      if(window.matbAutomation?.handleModelPrediction)window.matbAutomation.handleModelPrediction(prediction);
      const modelText=prediction?` · 模型负荷 ${Number(prediction.loadScore).toFixed(0)}%`:'';
      status.textContent=d.error||`${names[d.status]||d.status} · S3 ${d.record?.task||''} · ${(d.elapsedSeconds||0).toFixed(1)}秒${modelText} · 难度1–2：低负荷 / 3–5：高负荷 · 数据集回放`;
    }catch{status.textContent='请使用 start_matb.bat 启动脑电回放服务';}
    finally{pending=false;}
  }
  refresh();setInterval(refresh,1000);
})();
