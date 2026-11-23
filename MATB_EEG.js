/* Same-origin marker transport with ACK/retry and browser/bridge monotonic clock mapping. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let session=null,experimentId=null,pending=[],sending=false,offsets=[],stopping=false,recording=false,lslRequested=false,flushError='',lastStatus=null;
  const offset=()=>offsets.length?offsets.reduce((best,x)=>x.rtt<best.rtt?x:best):null;
  async function api(path,body){
    const response=await fetch('/api/eeg/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,cache:'no-store',signal:AbortSignal.timeout(path==='start'?12000:8000)});
    const result=await response.json();if(!response.ok)throw new Error(result.error||response.statusText);return result;
  }
  function report(text,error=false){const box=$('eegLive');if(box){box.textContent=text;box.classList.toggle('warning',error);}}
  async function synchronize(){
    const t0=performance.now();const result=await api('clock');const t1=performance.now();
    const sample={offset:result.clockS-(t0+t1)/2000,rtt:t1-t0,at:t1,serverSystemMs:result.systemMs};
    offsets.push(sample);offsets=offsets.filter(x=>t1-x.at<60000).slice(-30);return sample;
  }
  async function flush(){
    if(sending||!pending.length||!session)return;
    sending=true;try{
      const batch=pending.slice(0,100);const result=await api('markers',{session,experimentId,events:batch});
      const ack=new Set(result.ack);pending=pending.filter(e=>!ack.has(e.index));flushError='';
    }catch(error){flushError=error.message;report('标签尚未确认，正在重试：'+flushError,true);}finally{sending=false;}
  }
  function setControls(disabled){$('eegOptions')?.querySelectorAll('input').forEach(input=>input.disabled=disabled);}
  async function beforeSession(participant){
    if(!$('eegEnabled')?.checked)return null;
    if(recording)throw new Error('已有脑电采集运行，请先结束并保存。');
    const cfg={participant,host:$('eegHost').value.trim(),port:Number($('eegPort').value),sampleRate:Number($('eegRate').value),channels:Number($('eegChannels').value),triggerIndex:$('eegTrigger').value,montage:'EEGCap64-V3.0',latencyMs:Number($('eegLatency').value),lsl:$('eegLSL').checked,protocolConfirmed:$('eegConfirmed').checked,channelNames:$('eegNames').value.split(',').map(x=>x.trim()).filter(Boolean)};
    if(!cfg.protocolConfirmed)throw new Error('请先在脑电设置中核对采样率、总通道数和转发格式，并勾选确认。');
    report(`正在连接 ${cfg.host}:${cfg.port} 并等待脑电样本…`);
    setControls(true);
    try{
      lastStatus=await api('start',cfg);session=lastStatus.session;recording=true;lslRequested=cfg.lsl;stopping=false;pending=[];offsets=[];
      for(let i=0;i<6;i++)await synchronize();
      const lslWarning=cfg.lsl&&!lastStatus.lslEnabled?' · LSL 发布失败，已继续本地采集':'';
      report('脑电已接入 · 原始数据保存到 '+lastStatus.output+lslWarning,!!lslWarning);
      return {...lastStatus,config:cfg,initialClockFit:offset(),browserTimeOriginMs:performance.timeOrigin};
    }catch(error){
      if(recording&&session){try{await api('stop',{session});}catch{}}
      recording=false;setControls(false);report(error.message,true);
      $('eegSetupStatus').textContent=error.message+' · 诊断日志：logs/eeg_bridge.log';
      throw error;
    }
  }
  async function stop(){
    if(!recording||stopping)return;
    stopping=true;
    try{
      const deadline=performance.now()+15000;
      while((pending.length||sending)&&performance.now()<deadline){await flush();await new Promise(resolve=>setTimeout(resolve,60));}
      if(pending.length||sending)throw new Error('仍有未确认标签，采集继续保留；请保持页面打开，连接恢复后再次点“停止脑电并保存”。');
      lastStatus=await api('stop',{session});recording=false;setControls(false);
      report('脑电与标签已保存：'+lastStatus.output,!!lastStatus.error);
      if(lastStatus.error)report('采集曾中断：'+lastStatus.error+' · 文件保存在 '+lastStatus.output,true);
    }catch(error){report(error.message,true);}finally{stopping=false;}
  }
  window.addEventListener('matb-experiment-marker',event=>{
    if(!recording)return;
    const raw=event.detail;experimentId=raw.experimentId;
    const fit=offset();
    const perfMs=raw.performanceEpochMs-performance.timeOrigin;
    const record={...raw,bridgeClockS:fit&&Number.isFinite(perfMs)?perfMs/1000+fit.offset:undefined,
                  clockRttMs:fit?.rtt,clockFitAgeMs:fit?performance.now()-fit.at:undefined};
    Object.assign(raw,{bridgeClockS:record.bridgeClockS,clockRttMs:record.clockRttMs,clockFitAgeMs:record.clockFitAgeMs});
    // The event is also retained by the experiment JSON/IndexedDB backup if transport fails.
    pending.push(record);flush();
    if(raw.label==='stage_end'&&raw.stage==='complete'||raw.label==='experiment_abort')setTimeout(stop,0);
  });
  function mount(){
    $('stage').querySelector('.fields').insertAdjacentHTML('afterend',`<details id="eegOptions" class="eeg-options" open><summary>脑电采集 · 博睿康 8712 / LSL</summary>
      <label><input id="eegEnabled" type="checkbox"> 本次实验同时录制脑电和同步标签</label>
      <div class="eeg-fields"><label>EEGRecorder 转发主机<input id="eegHost" value="127.0.0.1"></label><label>TCP 端口<input id="eegPort" type="number" value="8712"></label><label>采样率 Hz（核对软件）<input id="eegRate" type="number" value="1000"></label><label>总转发通道（含 Trigger）<input id="eegChannels" type="number" value="65"></label><label>Trigger 索引（0 起始，可留空）<input id="eegTrigger" type="number" value="64"></label><label>已实测固定转发延迟 ms<input id="eegLatency" type="number" value="0" min="0"></label></div>
      <p>本机运行 EEGRecorder 时填 127.0.0.1。跨电脑接收时填写运行 DataSending 的电脑地址。</p>
      <p>Montage：EEGCap64-V3.0。参考 MATLAB 接收配置为 1000 Hz、64 路 EEG 加末路 Trigger。请核对 EEGRecorder 的实际转发设置。延迟填 0 表示未做补偿。</p>
      <label>实际转发通道顺序（逗号分隔，可留空）<input id="eegNames" placeholder="按软件导出填写，须含全部转发通道"></label>
      <p>留空时仅使用 CH001…，不会根据帽型猜测电极顺序或空间坐标。保留原始单位，不滤波、不缩放。</p>
      <label><input id="eegLSL" type="checkbox"> 同时发布 EEG / Markers 两条 LSL 流（需安装 pylsl）</label><br>
      <label><input id="eegConfirmed" type="checkbox"> 已核对采样率、通道数，并确认 TCP 为无包头、小端 float32、按采样点交织格式</label>
      <p id="eegSetupStatus">正在检查本地桥接…</p></details>`);
    const panel=document.createElement('div');panel.id='eegBar';panel.hidden=true;panel.innerHTML='<span id="eegLive"></span><button id="eegStop">停止脑电并保存</button>';$('progress').after(panel);
    $('eegStop').onclick=()=>{if(confirm('停止脑电录制并保存文件？实验页面将继续运行，后续标签不再写入脑电采集文件。'))stop();};
    api('status').then(status=>{lastStatus=status;$('eegSetupStatus').textContent=status.lslAvailable?'本地桥接可用，LSL 已就绪。':'本地桥接可用；LSL 未安装，仍可分别保存原始脑电与标签。';if(status.active){$('eegSetupStatus').textContent='另一个采集会话正在运行，请在原页面停止后再开始。';}}).catch(()=>{$('eegSetupStatus').textContent='桥接不可用：请关闭旧静态服务器，重新运行 start_experiment.bat。';});
  }
  setInterval(()=>{if(recording){flush();synchronize().catch(()=>{});}},5000);
  setInterval(async()=>{
    if(!recording||stopping)return;
    const currentSession=session;
    try{
      const status=await api('status');
      if(!recording||stopping||session!==currentSession)return;
      lastStatus=status;
      if(!status.active||status.error)report('脑电连接异常：'+(status.error||'已停止')+' · 未确认标签 '+pending.length,true);
      else if(!flushError){
        const lslWarning=lslRequested&&!status.lslEnabled?' · LSL 发布失败，已继续本地采集':'';
        report(`脑电采集中 · ${status.samples} 样本 · ${status.deviceTriggers} 设备 Trigger · 已确认 ${status.markers} 网页标签 · 待确认 ${pending.length} · 同步 RTT ${offset()?.rtt.toFixed(2)??'—'} ms${lslWarning}`,!!lslWarning);
      }
    }catch{
      if(recording&&!stopping&&session===currentSession)report('脑电桥接连接中断，标签等待重试，请勿关闭页面。',true);
    }
  },1000);
  window.addEventListener('beforeunload',event=>{if(recording||pending.length){event.preventDefault();event.returnValue='';}});
  window.EEGBridge={mount,beforeSession,stop,status:()=>({recording,pending:pending.length,session,lastStatus}),show:()=>{$('eegBar').hidden=false;}};
})();
