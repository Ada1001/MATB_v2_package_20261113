(() => {
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
      status.textContent=d.error||`${names[d.status]||d.status} · S3 ${d.record?.task||''} · ${(d.elapsedSeconds||0).toFixed(1)}秒 · 难度1–2：低负荷 / 3–5：高负荷 · 数据集回放`;
    }catch{status.textContent='请使用 start_matb.bat 启动脑电回放服务';}
    finally{pending=false;}
  }
  refresh();setInterval(refresh,1000);
})();