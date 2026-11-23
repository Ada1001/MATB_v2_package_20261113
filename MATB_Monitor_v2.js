(() => {
  const $ = (id) => document.getElementById(id);
  const channel = 'matb-v2-monitor';
  let lastPayload = null;
  let placeholderPhase = 0;

  const taskNames = {
    tracking: '01 Tracking',
    monitor: '02 System Monitoring',
    throttle: '03 Throttle',
    comms: '04 Visual Communication',
    radar: '05 Radar',
    session: 'Session',
  };

  function setConnection(connected, label = '主屏已连接') {
    const box = $('connection');
    box.classList.toggle('live', connected);
    box.querySelector('span').textContent = label;
  }

  function formatTime(ms) {
    const seconds = Math.max(0, Math.round((Number(ms) || 0) / 1000));
    return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  }

  function renderMetrics(metrics = {}, session = {}) {
    const accuracy = metrics.accuracy ?? metrics.overall?.accuracy;
    const meanRt = metrics.meanRtMs ?? metrics.meanRt ?? metrics.overall?.meanRtMs;
    const load = Number(metrics.load ?? metrics.workload ?? metrics.overall?.load ?? 0);
    $('accuracy').textContent = accuracy == null ? '—' : `${Math.round(Number(accuracy) <= 1 ? Number(accuracy) * 100 : Number(accuracy))}%`;
    $('rt').textContent = meanRt ? `${Math.round(meanRt)} ms` : '—';
    $('events').textContent = metrics.eventCount ?? session.eventCount ?? 0;
    $('markers').textContent = metrics.markers ?? session.markers ?? 0;
    $('load').style.width = `${Math.round(Math.max(0, Math.min(1, load)) * 100)}%`;
    $('loadText').textContent = session.ended ? '已结束' : session.running === false ? '待机' : load < .35 ? '低负荷' : load < .68 ? '中负荷' : '高负荷';
    $('trialLabel').textContent = `试次：${session.trial || '—'} · 难度 ${session.difficulty ?? '—'}`;
    $('clock').textContent = formatTime(session.elapsedMs ?? session.tMs ?? 0);
  }

  function renderTasks(tasks = {}, autoControl = {}) {
    const controlled = autoControl?.active ? autoControl.task : '';
    const rowClass = (task, extra = '') => `task-row ${extra}${controlled === task ? ' auto-control' : ''}`;
    const rowStatus = (task, status) => controlled === task ? 'AUTO · 100%' : status;
    const rows = [];
    const tracking = tasks.tracking || {};
    const trackingOff = tracking.enabled === false || tracking.status === 'OFF';
    rows.push(`<div class="${rowClass('tracking', trackingOff ? 'disabled' : '')}"><b>01 Tracking</b><span>${trackingOff ? '任务已关闭' : `保持率 ${tracking.holdPercent ?? tracking.hold ?? '—'} · 得分 ${tracking.score ?? '—'}`}</span><em>${trackingOff ? 'DISABLED' : rowStatus('tracking', tracking.status || 'ACTIVE')}</em></div>`);
    const monitor = tasks.monitor || tasks.monitoring || {};
    const monitorOff = monitor.enabled === false || monitor.status === 'OFF';
    rows.push(`<div class="${rowClass('monitor', monitorOff ? 'disabled' : monitor.fault ? 'alert' : '')}"><b>02 Monitoring</b><span>${monitorOff ? '任务已关闭' : `${monitor.channel || '通道监测'} · ${monitor.fault ? '故障待确认' : '稳定'}`}</span><em>${monitorOff ? 'DISABLED' : rowStatus('monitor', monitor.fault ? 'FAULT' : 'STABLE')}</em></div>`);
    const throttle = tasks.throttle || {};
    const throttleZone = Array.isArray(throttle.zone) ? `${throttle.zone[0]}–${throttle.zone[1]}%` : '—';
    const throttleOff = throttle.enabled === false || throttle.status === 'OFF';
    rows.push(`<div class="${rowClass('throttle', throttleOff ? 'disabled' : '')}"><b>03 Throttle</b><span>${throttleOff ? '任务已关闭' : `${throttle.modeLabel || throttle.mode || '加速'} · ${throttle.value ?? '—'}% · 最佳 ${throttleZone}`}</span><em>${throttleOff ? 'DISABLED' : rowStatus('throttle', throttle.status || 'ACTIVE')}</em></div>`);
    const comms = tasks.comms || tasks.communication || {};
    const commsOff = comms.enabled === false || comms.status === 'OFF';
    rows.push(`<div class="${rowClass('comms', commsOff ? 'disabled' : '')}"><b>04 Visual</b><span>${commsOff ? '任务已关闭' : `频道 ${comms.expected ?? comms.channel ?? '—'}`}</span><em>${commsOff ? 'DISABLED' : rowStatus('comms', comms.status || 'READY')}</em></div>`);
    const radar = tasks.radar || {};
    const radarOff = radar.enabled === false || radar.status === 'OFF';
    rows.push(`<div class="${rowClass('radar', radarOff ? 'disabled' : radar.active ? 'alert' : '')}"><b>05 Radar</b><span>${radarOff ? '任务已关闭' : `${radar.side ? (radar.side === 'left' ? '左前' : '右前') : '扫描中'} · ${radar.distance ?? '—'}`}</span><em>${radarOff ? 'DISABLED' : rowStatus('radar', radar.active ? 'APPROACH' : 'SCAN')}</em></div>`);
    $('taskList').innerHTML = rows.join('');
  }

  function eventText(event = {}) {
    const task = taskNames[event.task] || event.task || 'event';
    const type = event.type || 'update';
    const result = event.rtMs != null ? `${Math.round(event.rtMs)}ms` : type === 'error' || type === 'timeout' ? 'MISS' : 'OK';
    return { task, type, result, bad: type === 'error' || type === 'timeout' };
  }

  function renderEvents(events = []) {
    if (!events.length) { $('eventLog').innerHTML = '<div class="empty">等待事件…</div>'; return; }
    $('eventLog').innerHTML = events.slice(-20).reverse().map((event) => {
      const info = eventText(event);
      return `<div class="event ${info.bad ? 'bad' : ''}"><time>${formatTime(event.tMs)}</time><b>${info.task} · ${info.type}</b><em>${info.result}</em></div>`;
    }).join('');
  }

  function drawEeg(samples) {
    const canvas = $('eegCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const channels = Array.isArray(samples) && samples.length ? samples : [null, null, null, null];
    const count = Math.min(8, channels.length);
    for (let c = 0; c < count; c += 1) {
      const base = h * (c + .5) / count;
      ctx.beginPath();
      for (let x = 0; x < w; x += 3) {
        const supplied = Array.isArray(channels[c]) ? Number(channels[c][x % channels[c].length] || 0) : 0;
        const y = base - supplied * 14 - Math.sin((x + placeholderPhase * 6) / (19 + c * 4)) * 2;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = `hsla(${185 + c * 28}, 88%, 70%, .78)`;
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }
  }

  function renderEeg(payload = {}) {
    const status = $('eegStatus');
    if (!status) return;
    status.textContent = payload.status || (payload.channels ? `${payload.channels.length} channels` : '未连接');
    drawEeg(payload.samples || payload.data);
  }

  function normalize(message) {
    if (!message) return null;
    if (message.payload) return message.payload;
    return message;
  }

  function render(payload) {
    const data = normalize(payload) || {};
    lastPayload = data;
    setConnection(true);
    const session = data.session || data;
    const metrics = data.metrics || data.summary || {};
    renderMetrics(metrics, session);
    renderTasks(data.tasks || data.taskState || {}, data.autoControl || {});
    renderEvents(data.events || []);
    if (data.eeg) renderEeg(data.eeg);
  }

  function receive(message) {
    const data = normalize(message);
    if (!data) return;
    if (message.type === 'event' && lastPayload) {
      const events = [...(lastPayload.events || []), data.event || message.event || data];
      render({ ...lastPayload, events });
      return;
    }
    if (message.type === 'eeg') {
      renderEeg(data.eeg || data);
      return;
    }
    render(data);
  }

  const bus = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(channel) : null;
  if (bus) bus.addEventListener('message', (event) => receive(event.data));
  window.addEventListener('storage', (event) => {
    if (event.key !== 'matb-v2-state' || !event.newValue) return;
    try { receive(JSON.parse(event.newValue)); } catch { /* ignore malformed snapshots */ }
  });

  function publishReady() {
    if (bus) bus.postMessage({ type: 'monitor-ready', t: Date.now() });
    setConnection(false, '等待主屏连接');
  }

  setInterval(() => { placeholderPhase += 1; if (!lastPayload?.eeg) drawEeg(); }, 80);
  drawEeg();
  publishReady();
})();
