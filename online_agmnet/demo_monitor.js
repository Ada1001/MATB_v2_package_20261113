(() => {
  if(new URLSearchParams(location.search).get('agmnet')!=='1')return;
  const placeholder=document.getElementById('eegCanvas').closest('section');
  placeholder.hidden=true;
  document.getElementById('load').parentElement.previousElementSibling.textContent='行为表现估计（与 EEG 模型输出独立）';
  document.querySelector('.topbar .sub').textContent='副屏 / 行为指标 · EEG 回放 · 认知负荷实时解码';
  const style=document.createElement('style');
  style.textContent=`.agm-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.agm-grid .body{font-size:12px}.agm-muted{color:#7f9bb5;font-size:11px;line-height:1.6}.agm-grid select{background:#102335;color:#d8efff;border:1px solid #395a70;padding:4px;border-radius:6px}.agm-grid canvas{display:block;width:100%;background:#07111e}.agm-grid .head{min-height:40px}#modelResult{font-size:12px;line-height:1.6;color:#abc8db}@media(max-width:900px){.agm-grid{grid-template-columns:1fr}}`;
  document.head.append(style);
  const grid=document.createElement('section');grid.className='agm-grid';grid.id='agmGrid';
  grid.innerHTML=`<section class="panel"><div class="head"><b>EEG · 实时波形</b><label>通道 <select id="waveGroup"><option value="0">1–8</option><option value="8">9–16</option><option value="16">17–24</option><option value="24">25–32</option><option value="32">33–40</option><option value="40">41–48</option><option value="48">49–56</option><option value="56">57–62</option></select></label></div><div class="body"><div id="modelStatus" class="agm-muted">等待开始实验</div><canvas id="modelEEG" width="720" height="240" style="height:240px;margin:8px 0"></canvas><div id="waveInfo" class="agm-muted">S3 数据集回放 · 62 路 · 原始 500 Hz</div></div></section><section class="panel"><div class="head"><b>认知负荷 · 连续解码</b><span class="hint">AGMNet / 每 0.5 秒更新</span></div><div class="body"><div id="modelResult">等待开始实验</div><canvas id="loadCurve" width="720" height="240" style="height:240px;margin:8px 0" role="img" aria-label="认知负荷连续解码折线图，横轴时间，纵轴分数0至100"></canvas><div class="agm-muted">最近 60 秒 · 分数 = 平滑后高负荷概率 ×100 · 历史 EEG 回放</div></div></section>`;
  placeholder.after(grid);
  const names={idle:'等待开始实验',loading:'提取数据 / 加载模型',running:'回放中',paused:'已暂停',stopped:'已停止',completed:'片段播放完毕',error:'发生错误'};
  const phases={warmup:'滤波预热中',calibration_overlap_guard:'等待完整测试窗口',rejected:'信号质量不足，暂不解码'};
  let latest=null,pending=false;
  function draw(eeg){
    const c=document.getElementById('modelEEG'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);
    if(!eeg)return;
    const first=Number(document.getElementById('waveGroup').value),count=Math.min(8,eeg.channels.length-first);
    const gain=c.height/count/220;
    for(let ch=0;ch<count;ch++){
      const samples=eeg.samples[first+ch],base=(ch+.5)*c.height/count,center=samples.reduce((a,b)=>a+b,0)/(samples.length||1);
      ctx.fillStyle='#abc8db';ctx.font='11px sans-serif';ctx.fillText(eeg.channels[first+ch],3,base+3);
      ctx.strokeStyle='#163247';ctx.beginPath();ctx.moveTo(45,base);ctx.lineTo(c.width,base);ctx.stroke();
      ctx.strokeStyle='#54dfbf';ctx.beginPath();
      samples.forEach((v,i)=>{const x=45+i/624*(c.width-50),y=base-Math.max(-100,Math.min(100,v-center))*gain;if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);});ctx.stroke();
    }
    document.getElementById('waveInfo').textContent=`${eeg.startSeconds.toFixed(1)}–${eeg.endSeconds.toFixed(1)}秒 · 显示125Hz / 去直流 / ±100μV限幅 · 仅显示处理`;
  }
  document.getElementById('waveGroup').onchange=()=>draw(latest);
  function drawCurve(history,elapsed){
    const c=document.getElementById('loadCurve'),ctx=c.getContext('2d');
    const left=48,right=c.width-18,top=24,bottom=c.height-38;
    const end=Math.max(60,elapsed),start=end-60;
    const x=t=>left+(t-start)/60*(right-left),y=v=>bottom-v/100*(bottom-top);
    const timeLabel=t=>`${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}`;
    ctx.clearRect(0,0,c.width,c.height);ctx.font='11px sans-serif';
    ctx.fillStyle='#abc8db';ctx.textAlign='left';ctx.fillText('认知负荷分数',left,13);
    ctx.textAlign='right';
    for(const value of [0,25,50,75,100]){
      ctx.strokeStyle='#163247';ctx.beginPath();ctx.moveTo(left,y(value));ctx.lineTo(right,y(value));ctx.stroke();
      ctx.fillStyle='#abc8db';ctx.fillText(String(value),left-9,y(value)+4);
    }
    ctx.textAlign='center';
    for(let t=Math.ceil(start/10)*10;t<=end;t+=10){
      ctx.strokeStyle='#163247';ctx.beginPath();ctx.moveTo(x(t),top);ctx.lineTo(x(t),bottom);ctx.stroke();
      ctx.fillStyle='#abc8db';ctx.fillText(timeLabel(t),x(t),bottom+17);
    }
    ctx.fillText('时间（分:秒）',(left+right)/2,c.height-3);
    ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
    ctx.strokeStyle='#39d5ff';ctx.lineWidth=2;ctx.beginPath();
    let previous=null,last=null;
    for(const point of history){
      if(point.score==null){previous=null;continue;}
      if(point.time<start-1||point.time>end)continue;
      if(!previous||point.time-previous.time>.75)ctx.moveTo(x(point.time),y(point.score));
      else ctx.lineTo(x(point.time),y(point.score));
      previous=point;last=point;
    }
    ctx.stroke();
    if(last){ctx.fillStyle='#39d5ff';ctx.beginPath();ctx.arc(x(last.time),y(last.score),3,0,Math.PI*2);ctx.fill();}
    ctx.restore();
    c.dataset.points=String(history.filter(p=>p.score!=null&&p.time>=start&&p.time<=end).length);
    c.dataset.endSeconds=String(end);
  }
  async function refresh(){
    if(pending)return;pending=true;
    try{
      const r=await fetch('/api/agmnet/state',{cache:'no-store'});if(!r.ok)throw Error('请使用 start_matb.bat 启动回放服务');
      const d=await r.json(),p=d.prediction;
      document.getElementById('modelStatus').textContent=d.error||`${names[d.status]||d.status} · S3 · ${(d.elapsedSeconds||0).toFixed(1)} / ${(d.totalSeconds||0).toFixed(1)}秒 · 条件：${d.record?.label===0?'低负荷':d.record?.label===1?'高负荷':'—'}`;
      const valid=p?.phase==='test'&&p.loadScore!=null;
      document.getElementById('modelResult').textContent=valid?`当前分数 ${p.loadScore.toFixed(1)} / 100 · ${p.end_seconds.toFixed(1)}秒 · 处理 ${p.processing_ms.toFixed(1)} ms`:(p?.phase==='calibration'?`无标签校准 ${p.end_seconds.toFixed(1)}秒`:phases[p?.phase]||names[d.status]||'等待窗口');
      drawCurve(d.scoreHistory||[],d.elapsedSeconds||0);
      latest=d.eeg;draw(latest);
    }catch(e){document.getElementById('modelStatus').textContent=e.message;}
    finally{pending=false;}
  }
  refresh();setInterval(refresh,250);
})();