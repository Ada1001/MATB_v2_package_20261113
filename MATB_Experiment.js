/* Experiment controller. The existing demo is kept unchanged. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const test = new URLSearchParams(location.search).get('test') === '1';
  const cfg = Object.freeze({blocks:6, countdownSec:3, blockSec:test?3:300, restSec:test?1:60, trainingSec:test?3:60,
    sampleHz:10, baseDifficulty:3, radarDifficulty:3, eventRates:MATBTiming.profiles,rateSource:MATBTiming.source,eventSchedule:'one event per equal slot with +/-20% slot jitter; count fixed by duration and rate',trainingTasks:['tracking','monitor','throttle','comms','radar'], sequence:['low','high','low','high','low','high'], thirdTask:'throttle',
    disturbance:{low:null,high:{x:'34*sin(2.3*t)+15*sin(5.7*t) px/s',y:'30*cos(1.9*t)+13*sin(4.1*t) px/s'}},test});
  const codes = Object.freeze({experiment_start:100,experiment_end:101,experiment_abort:102,
    training_start:110,training_end:111,kss_pre_start:120,kss_pre_submit:121,kss_pre_end:122,
    rest_pre_eyes_open_start:130,rest_pre_eyes_open_end:131,rest_pre_eyes_closed_start:132,rest_pre_eyes_closed_end:133,
    block_low_start:200,block_low_end:201,block_high_start:210,block_high_end:211,
    rsme_start:220,rsme_submit:221,rsme_end:222,break_start:230,break_end:231,
    rest_post_eyes_open_start:300,rest_post_eyes_open_end:301,rest_post_eyes_closed_start:302,rest_post_eyes_closed_end:303,
    kss_post_start:310,kss_post_submit:311,kss_post_end:312,task_start:400,task_end:401,
    task_event:410,manual_marker:420,visibility_change:430,window_blur:431,window_focus:432,
    stage_start:440,stage_end:441,device_status:450,fullscreen_change:460,stage_skip:470,countdown_start:480,countdown_end:481});
  const guides={
    tracking:{title:'追踪 Tracking',instruction:'使用 W / A / S / D 控制蓝色飞行器，尽量保持在绿色目标圆圈内。',tips:['W 向上 · S 向下 · A 向左 · D 向右','观察目标运动，持续小幅修正位置。','本项训练开启追踪扰动，体验高负荷时的操作。']},
    monitor:{title:'系统监控 Sysmon',instruction:'监视四个系统通道。出现红色 FAULT 时，确认对应故障。',tips:['数字键 3 / 4 / 5 / 6 分别对应四个通道。','也可以点击故障通道的 ACK 按钮。','没有故障时不要确认；误报和超时都会被记录。']},
    throttle:{title:'油门 Throttle',instruction:'移动油门杆，把当前数值保持在彩色标注的最佳区间。',tips:['加速目标：65–85%；减速目标：15–35%。','任务会自动切换加速 / 减速，请跟随提示调整。','请在首页完成控制器识别和油门轴校准。']},
    comms:{title:'视觉通信 Communication',instruction:'看到频道指令后，尽快选择对应编号。',tips:['频道 7 / 8 / 9 / 10 对应键盘 7 / 8 / 9 / 0。','也可以直接点击频道按钮。','每条指令响应窗口为 6.5 秒。']},
    radar:{title:'雷达 Radar',instruction:'观察红色目标从左前方或右前方快速接近，及时确认方向。',tips:['左前方目标按 ←；右前方目标按 →。','目标出现后约 2 秒内作答。','高、低负荷中的雷达速度、出现间隔和响应窗口一致。']},
  };
  const labels=['极度清醒','非常清醒','清醒','比较清醒','既不清醒也不困倦','有一些困意','困倦，但保持清醒不费力','困倦，保持清醒需要努力','非常困倦，需很大努力保持清醒，与睡意抗争'];
  let data=null, active=null, timer=null, sampleTimer=null, busy=false, audio=null, db=null, saving=false, nextStage=null;
  const taskApi=()=> $('engine').contentWindow.matbExperiment;
  let seq=0, onset=0;
  const elapsed=()=>data?performance.now()-onset:0;
  const loadLabel=x=>x==='low'?'低负荷':'高负荷';
  function context(){return {stage:active?.name||'setup',block:active?.block||null,load:active?.load||null,phase:active?.phase||null,trainingTask:active?.trainingTask||null,trainingStep:active?.trainingStep||null};}
  const bus=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('matb-experiment-markers'):null;
  function mark(label,details={}) {
    if(!data)return;
    const event={index:++seq,experimentId:data.id,code:codes[label]??499,label,tMs:+elapsed().toFixed(3),...MATBTiming.stamp(),...context(),...details};
    data.events.push(event); if(bus)bus.postMessage({type:'marker',payload:event});
    window.dispatchEvent(new CustomEvent('matb-experiment-marker',{detail:event}));
    return event;
  }
  function download(value,name,type){const url=URL.createObjectURL(new Blob([value],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function fileStem(){return (data?.participant||'participant').replace(/[^\w\u4e00-\u9fff-]/g,'_')+'_'+(data?.id||'matb');}
  function exportJson(){if(data)download(JSON.stringify(data,null,2),fileStem()+'.json','application/json');}
  function exportCsv(){if(!data)return;const keys=['index','code','label','timestampMs','wallTime','localTime','timezone','tMs','stage','phase','block','load','trainingTask','trainingStep','task','type','rtMs','expected','actual','reason','details'];const quote=v=>'"'+String(v??'').replace(/"/g,'""')+'"';download('\ufeff'+[keys.join(','),...data.events.map(e=>keys.map(k=>quote(k==='details'?JSON.stringify(e):e[k])).join(','))].join('\r\n'),fileStem()+'_markers.csv','text/csv;charset=utf-8');}
  const dbReady=new Promise(resolve=>{try{const request=indexedDB.open('matb-experiment-v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('sessions',{keyPath:'id'});request.onsuccess=()=>{db=request.result;resolve(db);};request.onerror=()=>resolve(null);}catch{resolve(null);}});
  async function save(){await dbReady;if(!db||!data||saving)return;saving=true;try{await new Promise((resolve,reject)=>{const tx=db.transaction('sessions','readwrite');tx.objectStore('sessions').put(data);tx.oncomplete=resolve;tx.onerror=reject;tx.onabort=reject;});$('notice').textContent='本地自动备份：'+new Date().toLocaleTimeString()+' · 请在结束后导出 JSON 和标签 CSV';}catch{$('notice').textContent='本地备份失败，请及时手动导出数据';}finally{saving=false;}}
  function stopTimers(){clearInterval(timer);clearInterval(sampleTimer);timer=sampleTimer=null;}
  function beep(){if(!audio)return;audio.resume().catch(()=>{});for(let i=0;i<3;i++){const o=audio.createOscillator(),g=audio.createGain();o.frequency.value=660;g.gain.value=.16;o.connect(g);g.connect(audio.destination);o.start(audio.currentTime+i*.32);o.stop(audio.currentTime+i*.32+.18);}}
  function card(title,body,actions=''){$('stage').innerHTML=`<div class="card"><h1>${title}</h1>${body}<div class="actions">${actions}</div></div>`;}
  function button(id,text,primary=true){return `<button id="${id}" class="${primary?'primary':''}">${text}</button>`;}
  function bind(id,fn){$(id).onclick=async()=>{if(busy)return;busy=true;try{await fn();}finally{busy=false;}};}
  function enter(name,extra={}) {
    stopTimers();nextStage=null;active={name,...extra,startMs:elapsed()};$('skip').disabled=false;
    $('stage').classList.remove('setup-stage');$('stage').hidden=false;$('taskArea').hidden=true;$('taskCountdown').hidden=true;
    $('status').textContent=({rest:'静息采集',kss:'KSS 量表',block:'任务采集',rsme:'RSME 量表',break:'自主休息',block_ready:'准备任务',training:'单项任务训练',training_complete:'训练完成',post_ready:'准备实验后静息',complete:'范式结束'})[name]||name;
    $('progress').textContent=(test?'【快速流程验证，不用于正式采集】 · ':'')+'KSS → 前静息 → '+cfg.sequence.map((l,i)=>`${i+1}:${loadLabel(l)}${extra.block===i+1?' ◀':''}`).join(' → ')+' → 后静息 → KSS';
    mark('stage_start');save();
  }
  function leave(reason='completed'){if(!active)return;mark('stage_end',{durationMs:+(elapsed()-active.startMs).toFixed(3),reason});stopTimers();}
  function rest(phase,eyes){
    enter('rest',{phase,eyes});const prefix=`rest_${phase}_eyes_${eyes}`;mark(prefix+'_start',{eyes,durationSec:cfg.restSec});
    card(eyes==='open'?'睁眼静息':'闭眼静息',`<div class="rest"><p>${phase==='pre'?'实验前':'实验后'}静息 · ${cfg.restSec} 秒</p><p>${eyes==='open'?'请保持安静，注视中央十字，尽量减少身体和眼部运动。':'请闭上眼睛，保持安静。听到三声提示音后睁眼。'}</p><div class="fixation">${eyes==='open'?'+':'闭眼'}</div><div id="clock" class="timer"></div></div>`);
    const deadline=performance.now()+cfg.restSec*1000;
    nextStage=(reason='completed')=>{mark(prefix+'_end',{eyes,reason,actualDurationMs:elapsed()-active.startMs});leave(reason);beep();if(eyes==='open')rest(phase,'closed');else if(phase==='pre')readyBlock(1);else kss('post');};
    const tick=()=>{const remain=deadline-performance.now();$('clock').textContent=Math.max(0,Math.ceil(remain/1000));if(remain<=0)nextStage();};
    timer=setInterval(tick,50);tick();
  }
  function kss(phase){
    enter('kss',{phase});mark(`kss_${phase}_start`);
    card('KSS 困倦量表',`<p>请选择最符合您<strong>此刻</strong>清醒 / 困倦状态的一项。</p><div class="options">${labels.map((s,i)=>`<label><input type="radio" name="kss" value="${i+1}"> ${i+1} · ${s}</label>`).join('')}</div>`,button('submit','提交并继续'));
    $('submit').disabled=true;document.querySelectorAll('[name=kss]').forEach(x=>x.onchange=()=>{$('submit').disabled=false;});
    nextStage=(reason='completed')=>{const skipped=reason==='skipped';const v=document.querySelector('[name=kss]:checked');if(!skipped&&!v)return;data.scales.push({scale:'KSS',phase,value:skipped?null:Number(v.value),skipped,tMs:elapsed(),...MATBTiming.stamp(),responseMs:elapsed()-active.startMs});if(!skipped)mark(`kss_${phase}_submit`,{value:Number(v.value)});mark(`kss_${phase}_end`,{reason});leave(reason);if(phase==='pre')rest('pre','open');else finish();};
    bind('submit',()=>nextStage());
  }
  function readyBlock(n){
    if(n===1){enter('block_ready',{block:1,load:'low'});card('准备进入 Block 1 / 6',`<p>低负荷：追踪＋系统监控＋雷达。任务持续 ${cfg.blockSec} 秒，结束后填写 RSME。</p><p>W/A/S/D 追踪 · 3/4/5/6 确认系统故障 · ←/→ 雷达方向</p>`,button('next','开始 Block 1'));}
    else {enter('break',{block:n-1,load:cfg.sequence[n-2]});mark('break_start',{nextBlock:n});card('自主休息',`<p>Block ${n-1} 已完成，您可以按需休息，时长不限。</p><p>下一阶段：Block ${n} / 6 · ${loadLabel(cfg.sequence[n-1])} · ${cfg.blockSec} 秒</p>`,button('next',`准备好了，开始 Block ${n}`));}
    nextStage=(reason='completed')=>{if(n>1)mark('break_end',{nextBlock:n,reason});leave(reason);runTasks('block',n);};
    bind('next',()=>nextStage());
  }
  function enabled(load){return load==='low'?['tracking','monitor','radar']:['tracking','monitor','throttle','comms','radar'];}
  function runTasks(kind,n=0){
    const api=taskApi();if(!api)return alert('任务页面尚未就绪，请稍后重试。');
    const trainingTask=kind==='training'?cfg.trainingTasks[n]:null;
    const load=kind==='training'?(trainingTask==='tracking'?'high':'low'):cfg.sequence[n-1];
    const tasks=kind==='training'?[trainingTask]:enabled(load);
    const seconds=kind==='training'?cfg.trainingSec:cfg.blockSec;
    api.configure(load,seconds,`${data.participant}-${kind}-${n}`,kind==='training'?tasks:null);
    const taskContext={block:kind==='block'?n:null,load,tasks,trainingTask,trainingStep:kind==='training'?n+1:null};
    enter('countdown',{...taskContext,targetKind:kind});$('status').textContent='任务准备 · 倒计时';$('stage').hidden=true;$('taskArea').hidden=false;
    $('taskWorkspace').classList.toggle('training',kind==='training');$('trainingGuide').hidden=kind!=='training';
    const title=kind==='training'?`训练 ${n+1} / 5 · ${guides[trainingTask].title}`:`Block ${n} / 6 · ${loadLabel(load)}`;
    if(trainingTask){const guide=guides[trainingTask];$('trainingGuide').innerHTML=`<span class="guide-eyebrow">操作指南 · ${n+1} / 5</span><h2>${guide.title}</h2><p>${guide.instruction}</p><ul>${guide.tips.map(t=>`<li>${t}</li>`).join('')}</ul><p>本项训练 ${seconds} 秒，结束后自动进入下一项；可点击右上角 Skip 跳过。</p>`;}
    $('taskBanner').textContent=`${title} · 请先熟悉界面 · ${cfg.countdownSec} 秒后开始`;
    $('taskCountdown').hidden=false;$('countdownNumber').textContent=cfg.countdownSec;
    mark('countdown_start',{targetKind:kind,durationSec:cfg.countdownSec});
    const startTask=(reason='completed')=>{
      mark('countdown_end',{targetKind:kind,reason,actualDurationMs:elapsed()-active.startMs});leave(reason);
      enter(kind,taskContext);$('stage').hidden=true;$('taskArea').hidden=false;
      active.durationSec=seconds;
      $('taskBanner').textContent=`${title} · 剩余 ${seconds} 秒${load==='high'?' · 追踪扰动开启':''}`;
      nextStage=(reason='completed')=>{api.stop();tasksEnded(reason);};
      api.start();$('engine').contentWindow.focus();
      sampleTimer=setInterval(()=>{
      if(!active||!['block','training'].includes(active.name))return;
      const snap=api.snapshot();data.samples.push({tMs:+elapsed().toFixed(3),...MATBTiming.stamp(),...context(),...snap});
      $('taskBanner').textContent=`${title} · 剩余 ${Math.max(0,Math.ceil(seconds-snap.elapsedMs/1000))} 秒${load==='high'?' · 追踪扰动开启':''}`;
      const device=snap.throttle.padId+':'+snap.throttle.axisIndex;
      if(device!==active.device){active.device=device;mark('device_status',{device:snap.throttle.padId,axisIndex:snap.throttle.axisIndex});}
      },1000/cfg.sampleHz);
    };
    nextStage=startTask;
    const deadline=performance.now()+cfg.countdownSec*1000;
    timer=setInterval(()=>{const remaining=deadline-performance.now();$('countdownNumber').textContent=Math.max(1,Math.ceil(remaining/1000));if(remaining<=0)startTask();},40);
  }
  function tasksEnded(reason='completed'){
    if(!active||!['block','training'].includes(active.name))return;
    const {name,block,load,tasks,trainingStep}=active;
    tasks.forEach(task=>mark('task_end',{task,reason}));
    mark(name==='training'?'training_end':`block_${load}_end`,{reason,actualDurationMs:elapsed()-active.startMs,summary:taskApi().snapshot()});
    leave(reason);
    if(name==='training'&&trainingStep<cfg.trainingTasks.length){runTasks('training',trainingStep);}
    else if(name==='training'){enter('training_complete');card('训练完成','<p>正式流程开始后依次填写 KSS，进行睁眼和闭眼静息，然后进入 6 个任务 block。</p>',button('begin','开始实验'));nextStage=startFormal;bind('begin',()=>nextStage());}
    else rsme(block,load);
  }
  function rsme(n,load){
    enter('rsme',{block:n,load});mark('rsme_start');
    const anchors=[[2,'几乎不费力'],[13,'有一点费力'],[26,'稍微费力'],[38,'有些费力'],[57,'比较费力'],[71,'相当费力'],[85,'非常费力'],[112,'极其费力'],[150,'量尺上限']];
    card(`Block ${n} · RSME 心理努力量表`,`<p>请评价<strong>刚刚这个 block</strong>中投入的心理努力程度。拖动竖直量尺选择 0–150 分，数字越大表示越费力。</p><div class="scale"><input id="rsme" class="vertical" type="range" min="0" max="150" step="1" value="0" aria-label="心理努力评分"><div class="ruler">${anchors.map(([v,t])=>`<span class="anchor" style="bottom:${v/1.5}%">${v} · ${t}</span>`).join('')}</div></div><p>当前评分：<strong id="score">尚未选择</strong></p>`,button('submit','提交 RSME'));
    $('score').insertAdjacentHTML('afterend',' <button id="zero" type="button">选择 0 分（完全不费力）</button>');
    $('submit').disabled=true;$('rsme').oninput=e=>{$('score').textContent=e.target.value;$('submit').disabled=false;};
    bind('zero',()=>{$('rsme').value=0;$('score').textContent='0';$('submit').disabled=false;});
    nextStage=(reason='completed')=>{const skipped=reason==='skipped';const value=skipped?null:Number($('rsme').value);data.scales.push({scale:'RSME',block:n,load,value,skipped,tMs:elapsed(),...MATBTiming.stamp(),responseMs:elapsed()-active.startMs});if(!skipped)mark('rsme_submit',{value});mark('rsme_end',{reason});leave(reason);if(n<6)readyBlock(n+1);else {enter('post_ready');card('任务部分完成','<p>接下来进行睁眼静息 1 分钟、闭眼静息 1 分钟，最后填写 KSS。</p>',button('post','开始实验后静息'));nextStage=(reason='completed')=>{leave(reason);rest('post','open');};bind('post',()=>nextStage());}};
    bind('submit',()=>nextStage());
  }
  function startFormal(reason='completed'){leave(reason);data.formalStartedAt=new Date().toISOString();mark('experiment_start',{config:cfg});kss('pre');}
  function finish(){enter('complete');data.status='completed';data.endedAt=new Date().toISOString();mark('experiment_end',{totalMs:elapsed(),hasSkippedStages:data.hasSkippedStages});leave();active=null;nextStage=null;$('skip').disabled=true;$('abort').disabled=true;save();card('范式结束',`<p class="done">实验流程已结束。${data.hasSkippedStages?'本次包含调试跳过阶段，不能作为完整正式采集。':'6 个 block、前后静息与所有量表均已完成。'}</p><p>已记录 ${data.events.length} 条标签、${data.samples.length} 条行为采样。请保存采集文件。</p>`,button('final','下载完整数据')+button('finalcsv','下载标签 CSV',false));bind('final',exportJson);bind('finalcsv',exportCsv);exportJson();}
  function abort(){if(!data||!active||!confirm('终止本次实验？已采集的数据会保留，可以导出。'))return;
    const wasTask=['block','training'].includes(active.name);
    if(wasTask){active.tasks.forEach(task=>mark('task_end',{task,reason:'aborted'}));mark(active.name==='training'?'training_end':`block_${active.load}_end`,{reason:'aborted'});}
    else if(active.name==='rest')mark(`rest_${active.phase}_eyes_${active.eyes}_end`,{reason:'aborted',eyes:active.eyes});
    else if(active.name==='kss')mark(`kss_${active.phase}_end`,{reason:'aborted'});
    else if(active.name==='rsme')mark('rsme_end',{reason:'aborted'});
    else if(active.name==='break')mark('break_end',{reason:'aborted'});
    else if(active.name==='countdown')mark('countdown_end',{reason:'aborted',targetKind:active.targetKind,actualDurationMs:elapsed()-active.startMs});
    leave('aborted');mark('experiment_abort',{reason:'operator'});active=null;nextStage=null;$('skip').disabled=true;if(wasTask)taskApi().stop();data.status='aborted';data.endedAt=new Date().toISOString();$('taskArea').hidden=true;$('stage').hidden=false;$('abort').disabled=true;card('实验已终止','<p>已采集的数据保留在本地，请导出。</p>',button('final','导出已采集数据'));bind('final',exportJson);save();exportJson();}
  async function createSession(withTraining=true){
    const participant=$('participant').value.trim();if(!participant)return alert('请填写被试编号。');
    if(!taskApi())return alert('任务页面加载中，请稍后重试。');
    if(!taskApi().ready())return alert('油门正在校准，请等待校准完成。');
    let eegRecording=null;try{eegRecording=await window.EEGBridge?.beforeSession(participant);if(eegRecording)window.EEGBridge.show();}catch(error){alert('脑电尚未就绪：'+error.message);return;}
    const Audio=window.AudioContext||window.webkitAudioContext;try{if(Audio){audio=new Audio();audio.resume().catch(()=>{});}}catch{}
    onset=performance.now();seq=0;data={schema:'matb-experiment-v1',id:new Date().toISOString().replace(/[:.]/g,'-'),participant,createdAt:new Date().toISOString(),formalStartedAt:null,endedAt:null,status:'running',trainingRequested:withTraining,hasSkippedStages:false,config:cfg,clock:{originMs:performance.timeOrigin,sessionOnsetPerformanceMs:onset,sessionOnsetTimestampMs:Date.now(),eventTime:'timestampMs: system Date.now() Unix milliseconds; wallTime: same value ISO UTC; localTime/timezone: system local time; tMs and RT: monotonic performance clock',markers:'software only; no hardware/EEG trigger connection'},markerCodes:codes,scaleVersion:'KSS 1–9; RSME 0–150; Chinese labels are working translations',events:[],scales:[],samples:[]};
    data.eegRecording=eegRecording;$('abort').disabled=false;if(withTraining)runTasks('training');else startFormal();
  }
  function setup(){
    $('stage').classList.add('setup-stage');
    card('MATB 实验采集',`<div class="fields"><label>被试编号<input id="participant" value="P01" maxlength="60"></label></div><div class="device-tools">${button('calibrate','校准油门',false)+button('axisMinus','轴 −',false)+button('axisPlus','轴 ＋',false)+button('sound','试听结束提示',false)}</div><p id="device" class="warning">等待任务引擎加载…</p>${test?'<p class="warning">当前为快速验证模式：任务 3 秒，静息 1 秒。</p>':''}`,
      button('train','开启任务训练')+button('beginDirect','开始实验')+'<div class="recovery-action">'+button('recover','导出最近本地备份',false)+'</div>');
    bind('train',()=>createSession(true));bind('beginDirect',()=>createSession(false));bind('calibrate',()=>taskApi()?.calibrate());bind('axisMinus',()=>taskApi()?.axis(-1));bind('axisPlus',()=>taskApi()?.axis(1));bind('sound',()=>{const Audio=window.AudioContext||window.webkitAudioContext;if(Audio){audio=audio||new Audio();beep();}});
    bind('recover',async()=>{await dbReady;if(!db)return alert('本地数据库不可用。');const req=db.transaction('sessions').objectStore('sessions').getAll();req.onsuccess=()=>{const last=req.result.sort((a,b)=>b.id.localeCompare(a.id))[0];if(last)download(JSON.stringify(last,null,2),last.participant+'_recovered_'+last.id+'.json','application/json');else alert('暂无备份。');};});
  }
  $('engine').addEventListener('load',()=>{
    const win=$('engine').contentWindow;
    win.addEventListener('matb-task-event',e=>{if(!active||!['training','block'].includes(active.name))return;const ev=e.detail;
      const sourceClock={timestampMs:ev.timestampMs,performanceEpochMs:ev.performanceEpochMs,wallTime:ev.wallTime,localTime:ev.localTime,timezone:ev.timezone};
      if(ev.task==='session'&&ev.type==='start'){
        mark(active.name==='training'?'training_start':`block_${active.load}_start`,{...sourceClock,durationSec:active.durationSec,tasks:active.tasks,trackingDisturbance:active.load==='high',eventPlan:ev.eventPlan,rates:ev.rates});
        active.tasks.forEach(task=>mark('task_start',{...sourceClock,task,trackingDisturbance:task==='tracking'&&active.load==='high'}));
      }
      mark('task_event',{...sourceClock,task:ev.task,type:ev.type,taskElapsedMs:ev.tMs,rtMs:ev.rtMs,expected:ev.expected,actual:ev.actual,details:ev});
      if(ev.task==='session'&&ev.type==='timeout')tasksEnded();
    });
    win.addEventListener('keydown',e=>{if(e.key.toLowerCase()==='m'&&!e.repeat)mark('manual_marker',{source:'keyboard'});});
    win.addEventListener('blur',()=>{if(data&&active)mark('window_blur',{surface:'task'});});
  });
  $('monitor').onclick=()=>window.open('MATB_Monitor_v2.html','matb-monitor');$('export').onclick=exportJson;$('csv').onclick=exportCsv;$('abort').onclick=abort;
  bind('skip',async()=>{if(!data){await createSession(false);if(data){data.hasSkippedStages=true;mark('stage_skip',{fromStage:'setup',reason:'debug'});}return;}if(!active||!nextStage)return;data.hasSkippedStages=true;mark('stage_skip',{reason:'debug',actualDurationMs:elapsed()-active.startMs});nextStage('skipped');});
  document.addEventListener('keydown',e=>{if(e.key.toLowerCase()==='m'&&!e.repeat&&!['INPUT','TEXTAREA'].includes(e.target.tagName))mark('manual_marker',{source:'keyboard'});});
  document.addEventListener('visibilitychange',()=>{if(data&&active){mark('visibility_change',{visibility:document.visibilityState});save();}});
  window.addEventListener('blur',()=>{if(data&&active)mark('window_blur',{surface:'controller'});});
  window.addEventListener('focus',()=>{if(data&&active)mark('window_focus',{surface:'controller'});});
  document.addEventListener('fullscreenchange',()=>mark('fullscreen_change',{fullscreen:!!document.fullscreenElement}));
  window.addEventListener('beforeunload',e=>{if(data?.status==='running'){e.preventDefault();e.returnValue='';}});
  setInterval(save,5000);
  setInterval(()=>{if(!active&&$('device')&&taskApi()){const d=taskApi().snapshot().throttle;$('device').textContent=d.padId?`设备：${d.padId} · 油门轴：${d.axisIndex??'未校准'}`:'未检测到 Gamepad：请连接控制器并操作任一按键，随后校准油门。';}},500);
  setup();
  window.EEGBridge?.mount();
})();
