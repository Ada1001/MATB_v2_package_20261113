(() => {
  // Local EEG/prediction playback monitor.  The page only needs a JSON state
  // endpoint; no model or Python inference is required by this UI.
  const params = new URLSearchParams(location.search);
  if (params.get('demo') !== '1' && params.get('localDemo') !== '1') return;

  const $ = (id) => document.getElementById(id);
  const apiBase = params.get('api') || '/api/demo';
  let latest = null;
  let polling = false;
  let page = 0;
  let recordingRequest = false;
  let recordingError = '';

  const style = document.createElement('style');
  style.textContent = `
    .local-demo-grid { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(360px,.8fr); gap:14px; margin-top:14px; }
    .local-demo-grid .body { min-width:0; }
    .local-demo-controls { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:10px; }
    .local-demo-controls button, .local-demo-controls select { background:#102335; color:#d8efff; border:1px solid #395a70; border-radius:6px; padding:5px 8px; font:inherit; font-size:11px; }
    .local-demo-controls button { cursor:pointer; }
    .local-demo-controls button:hover { border-color:#39d5ff; }
    .local-demo-note { color:#7f9bb5; font-size:10px; line-height:1.5; }
    .demo-wave-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; }
    .demo-wave-window { border:1px solid rgba(181,140,255,.25); border-radius:9px; overflow:hidden; background:#07111e; }
    .demo-wave-title { padding:5px 8px; color:#d8caff; border-bottom:1px solid rgba(181,140,255,.16); font-size:10px; }
    .demo-wave-window canvas { display:block; width:100%; height:118px; }
    .demo-score { font-size:12px; color:#abc8db; margin-bottom:4px; min-height:20px; }
    #localDemoCurve { display:block; width:100%; height:320px; background:#07111e; border-radius:8px; }
    @media(max-width:1100px) { .local-demo-grid { grid-template-columns:1fr; } }
    @media(max-width:650px) { .demo-wave-grid { grid-template-columns:1fr; } }
  `;
  document.head.append(style);

  const oldPlaceholder = document.querySelector('.eeg')?.closest('section');
  const host = oldPlaceholder || document.querySelector('main') || document.body;
  const section = document.createElement('section');
  section.className = 'local-demo-grid';
  section.id = 'localDemoSection';
  section.innerHTML = `
    <section class="panel">
      <div class="head"><div class="title" id="localDemoEegTitle">EEG</div><div class="hint" id="localDemoStatus">等待演示服务</div></div>
      <div class="body">
        <div class="local-demo-controls">
          <span class="local-demo-note" id="localDemoEegSource"></span>
          <label class="local-demo-note">通道页
            <select id="localDemoPage"><option value="0">1–16</option><option value="1">17–32</option><option value="2">33–48</option><option value="3" id="localDemoLastPage">49–64</option></select>
          </label>
          <button id="localDemoStart" type="button">开始</button>
          <button id="localDemoPause" type="button">暂停</button>
          <button id="localDemoStop" type="button">停止</button>
        </div>
        <div class="local-demo-controls" id="localDemoRecordControls" style="display:none">
          <button id="localDemoRecord" type="button" disabled>录制 EEG</button>
          <span class="local-demo-note" id="localDemoRecording"></span>
        </div>
        <div class="demo-wave-grid" id="localDemoWaves"></div>
        <div class="local-demo-note" id="localDemoWaveInfo">等待 EEG 数据；显示每页 16 路，按窗口分组。</div>
      </div>
    </section>
    <section class="panel">
      <div class="head"><div class="title">认知负荷 </div><div class="hint" id="localDemoPredictionStatus"></div></div>
      <div class="body">
        <div id="localDemoScore" class="demo-score">等待</div>
        <canvas id="localDemoCurve" width="720" height="320" role="img" aria-label="认知负荷连续解码曲线，纵轴0至100"></canvas>
        <div class="local-demo-note" id="localDemoPredictionFile"></div>
      </div>
    </section>`;
  if (oldPlaceholder) oldPlaceholder.replaceWith(section); else host.append(section);

  const waves = $('localDemoWaves');
  for (let i = 0; i < 4; i += 1) {
    const box = document.createElement('div');
    box.className = 'demo-wave-window';
    box.innerHTML = `<div class="demo-wave-title" id="localDemoWaveTitle${i}">通道 —</div><canvas id="localDemoWave${i}" width="560" height="150"></canvas>`;
    waves.append(box);
  }

  function finiteNumber(value, fallback = 0) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function drawWindow(canvas, channels, labels, start, count) {
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#07111e'; ctx.fillRect(0, 0, w, h);
    const n = Math.max(1, Math.min(count, channels.length - start));
    for (let i = 0; i < n; i += 1) {
      const series = Array.isArray(channels[start + i]) ? channels[start + i] : [];
      const base = (i + .5) * h / n;
      const vals = series.map(finiteNumber);
      const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
      let variance = 0;
      for (const v of vals) variance += (v - mean) * (v - mean);
      const sd = vals.length > 1 ? Math.sqrt(variance / (vals.length - 1)) : 1;
      const scale = Math.max(1, sd * 2.7);
      ctx.strokeStyle = '#1a3448'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, base); ctx.lineTo(w, base); ctx.stroke();
      ctx.fillStyle = '#abc8db'; ctx.font = '10px sans-serif'; ctx.fillText(labels[start + i] || `Ch ${start + i + 1}`, 5, base - h / n * .27);
      ctx.strokeStyle = `hsl(${170 + i * 32}, 85%, 70%)`; ctx.lineWidth = 1.1;
      ctx.beginPath();
      for (let x = 0; x < w; x += 2) {
        const idx = vals.length > 1 ? Math.min(vals.length - 1, Math.round(x / (w - 1) * (vals.length - 1))) : 0;
        const z = Math.max(-1, Math.min(1, (vals[idx] - mean) / scale));
        const y = base - z * (h / n * .37);
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }

  function drawEeg(eeg = {}) {
    const channels = Array.isArray(eeg.samples) ? eeg.samples : [];
    const labels = Array.isArray(eeg.channels) ? eeg.channels : channels.map((_, i) => `Ch ${i + 1}`);
    const first = Number(page) * 16;
    for (let i = 0; i < 4; i += 1) {
      const start = first + i * 4;
      const last = Math.min(start + 4, labels.length);
      $(`localDemoWaveTitle${i}`).textContent = last > start ? `通道 ${start + 1}–${last} · ${labels.slice(start, last).join(' / ')}` : '通道 —';
      drawWindow($(`localDemoWave${i}`), channels, labels, start, 4);
    }
    const a = finiteNumber(eeg.startSeconds, 0), b = finiteNumber(eeg.endSeconds, a);
    $('localDemoWaveInfo').textContent = `${a.toFixed(1)}–${b.toFixed(1)} 秒 · ${labels.length || '—'} 路 · ${eeg.sampleRate || '—'} Hz · ${eeg.units || '原始单位'} · 每页16路分窗显示`;
  }

  function drawCurve(history = [], elapsed = 0) {
    const c = $('localDemoCurve'), ctx = c.getContext('2d');
    const w = c.width, h = c.height, left = 48, right = w - 18, top = 24, bottom = h - 38;
    const points = (Array.isArray(history) ? history : []).map((p) => ({
      time: finiteNumber(p.time ?? p.t ?? p.end_seconds),
      score: p.score == null ? null : Math.max(0, Math.min(100, finiteNumber(p.score))),
    }));
    const end = Math.max(60, finiteNumber(elapsed, points.length ? points[points.length - 1].time : 0));
    const begin = end - 60;
    const x = (t) => left + (t - begin) / 60 * (right - left);
    const y = (v) => bottom - v / 100 * (bottom - top);
    ctx.clearRect(0, 0, w, h); ctx.font = '10px sans-serif';
    ctx.fillStyle = '#abc8db'; ctx.textAlign = 'right';
    for (const value of [0, 25, 50, 75, 100]) {
      ctx.strokeStyle = '#163247'; ctx.beginPath(); ctx.moveTo(left, y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
      ctx.fillText(String(value), left - 8, y(value) + 3);
    }
    ctx.textAlign = 'center';
    for (let t = Math.ceil(begin / 10) * 10; t <= end; t += 10) {
      ctx.strokeStyle = '#163247'; ctx.beginPath(); ctx.moveTo(x(t), top); ctx.lineTo(x(t), bottom); ctx.stroke();
      ctx.fillStyle = '#abc8db'; ctx.fillText(`${Math.floor(t / 60)}:${String(Math.max(0, Math.floor(t % 60))).padStart(2, '0')}`, x(t), bottom + 16);
    }
    ctx.fillText('时间（分:秒）', (left + right) / 2, h - 3);
    ctx.save(); ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip();
    ctx.strokeStyle = '#39d5ff'; ctx.lineWidth = 2; ctx.beginPath();
    let previous = null, latestPoint = null;
    for (const p of points) {
      if (p.score == null || p.time < begin - 1 || p.time > end) { previous = null; continue; }
      if (!previous || p.time - previous.time > 1.5) ctx.moveTo(x(p.time), y(p.score)); else ctx.lineTo(x(p.time), y(p.score));
      previous = p; latestPoint = p;
    }
    ctx.stroke();
    if (latestPoint) { ctx.fillStyle = '#59efad'; ctx.beginPath(); ctx.arc(x(latestPoint.time), y(latestPoint.score), 3.5, 0, Math.PI * 2); ctx.fill(); }
    ctx.restore();
  }

  async function post(action, body = {}) {
    const response = await fetch(`${apiBase}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `${action} failed`);
    return data;
  }

  $('localDemoPage').addEventListener('change', (event) => { page = Number(event.target.value) || 0; drawEeg(latest?.eeg || {}); });
  $('localDemoStart').addEventListener('click', async () => {
    try { await post('start'); } catch (error) { $('localDemoStatus').textContent = error.message; }
  });
  $('localDemoPause').addEventListener('click', async () => {
    try { await post(latest?.status === 'paused' ? 'resume' : 'pause'); } catch (error) { $('localDemoStatus').textContent = error.message; }
  });
  $('localDemoStop').addEventListener('click', async () => {
    try { await post('stop'); } catch (error) { $('localDemoStatus').textContent = error.message; }
  });
  $('localDemoRecord').addEventListener('click', async () => {
    if (recordingRequest) return;
    recordingRequest = true;
    recordingError = '';
    $('localDemoRecord').disabled = true;
    try {
      const action = latest?.eegRecording?.status === 'recording' ? 'stop' : 'start';
      latest = await post('eeg/record/' + action);
    } catch (error) {
      recordingError = error.message;
    } finally {
      recordingRequest = false;
      refresh();
    }
  });

  async function refresh() {
    if (polling) return; polling = true;
    try {
      const response = await fetch(`${apiBase}/state`, { cache: 'no-store' });
      if (!response.ok) throw new Error('本地回放接口不可用');
      const data = await response.json(); latest = data;
      const live = data.eegSource === 'live';
      const playback = data.status === 'paused' ? '已暂停' : data.message || '等待开始';
      const status = data.error || `${playback} · ${finiteNumber(data.elapsedSeconds).toFixed(1)} / ${finiteNumber(data.totalSeconds).toFixed(1)} 秒`;
      $('localDemoEegTitle').textContent = live ? 'EEG · 博睿康实时波形' : 'EEG · 本地数据回放';
      $('localDemoEegSource').textContent = live ? 'EEGCap64-V3.0' : 'Session 3 · MATBdiff · 高负荷';
      $('localDemoStatus').textContent = live ? data.eegStatus : status;
      $('localDemoPredictionStatus').textContent = status;
      $('localDemoPredictionFile').textContent = data.predictionFile || '';
      $('localDemoLastPage').textContent = live ? '49–64' : '49–63';
      $('localDemoStart').textContent = live ? '播放预测' : '开始';
      $('localDemoPause').textContent = data.status === 'paused' ? '继续' : '暂停';
      $('localDemoStop').textContent = live ? '停止预测' : '停止';
      $('localDemoRecordControls').style.display = live ? 'flex' : 'none';
      if (live) {
        const recording = data.eegRecording;
        const active = recording?.status === 'recording';
        $('localDemoRecord').textContent = active ? '停止并保存 EEG' : '录制 EEG';
        $('localDemoRecord').disabled = recordingRequest || (!active && !data.eeg);
        const summary = recording
          ? `${active ? '录制中' : recording.error ? '录制中断' : '已保存'} · ${recording.sampleCount} 样本 · ${recording.folder}`
          : '诊断日志：logs/demo_eeg.log';
        $('localDemoRecording').textContent = recordingError || (recording?.error ? `${summary} · ${recording.error}` : summary);
      }
      const current = data.prediction?.score ?? data.prediction?.loadScore;
      const segment = data.prediction?.segment || data.predictionSegment;
      $('localDemoScore').textContent = current == null
        ? '等待预测结果文件'
        : `当前负荷 ${finiteNumber(current).toFixed(1)} / 100 · ${(finiteNumber(data.prediction?.time ?? data.prediction?.end_seconds)).toFixed(1)} 秒${segment ? ` · ${segment}` : ''}`;
      drawEeg(data.eeg || {}); drawCurve(data.scoreHistory || data.predictions || [], data.elapsedSeconds || 0);
    } catch (error) {
      $('localDemoStatus').textContent = error.message;
      $('localDemoRecord').disabled = true;
      drawEeg();
    } finally { polling = false; }
  }
  refresh(); setInterval(refresh, 250);
})();
