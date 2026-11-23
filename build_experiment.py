from pathlib import Path

source = Path('MATB_Game_v2.html').read_text(encoding='utf-8')
source = source.replace('<title>MATB Cognitive Load Arena</title>', '<title>MATB Experiment Tasks</title>')
source = source.replace('</head>', '''<script src="MATB_Experiment_Timing.js"></script><style>
body.embedded .topbar,body.embedded main.app > section.panel,body.embedded .monitor-stack {display:none!important}
body.embedded {padding:4px!important} body.embedded .app {max-width:none}
body.embedded .task-disabled {display:none!important}
body.embedded.solo .layout {display:block!important;max-width:950px;margin:10px auto}
body.embedded.solo .task-column:has(> .panel:not(.task-disabled)) {display:flex!important}
body.embedded.solo .task-column:not(:has(> .panel:not(.task-disabled))) {display:none!important}
body.embedded.solo #radarCanvas {height:300px}
body.embedded.low:not(.solo) .layout {grid-template-columns:minmax(280px,1fr) minmax(0,1.6fr)!important;max-width:1160px;width:92%;margin:10px auto!important;gap:12px!important}
body.embedded.low:not(.solo) .center-stack {grid-column:2;grid-row:1}
body.embedded.low:not(.solo) .right-stack {grid-column:1;grid-row:1}
body.embedded.low:not(.solo) .left-stack {display:none!important}
</style></head>''')
source = source.replace("const event = { task, type,", "const event = { task, type,")
source = source.replace('state.events.push(event);', "state.events.push(event); window.dispatchEvent(new CustomEvent('matb-task-event', {detail:event}));")
source = source.replace("const duration = clamp(Number($('durationInput').value) || 120, 30, 1800);", "const duration = Number($('durationInput').value) || 300;")
source = source.replace("state.responses.push({ task, correct, rtMs });", "state.responses.push({ task, correct, rtMs });")
# Cancel the outstanding frame when Skip stops a task and immediately starts another.
source = source.replace("const now = () => performance.now();", "let frameId = null; const now = () => performance.now();")
source = source.replace('requestAnimationFrame(loop)', 'frameId = requestAnimationFrame(loop)')
source = source.replace('function endSession(auto = false) {', 'function endSession(auto = false) { cancelAnimationFrame(frameId);')
# Match the literature's gauge-alert and communication counts per five minutes.
source = source.replace("const monitorNames =", """let eventPlan = {monitor:[],comms:[]}, eventCursor = {monitor:0,comms:0};
      function nextScheduled(task) { eventCursor[task] += 1; return eventPlan[task][eventCursor[task]] ?? Infinity; }
      function initSchedule() {
        const profile = MATBTiming.profiles[window.experimentLoad || 'low'];
        eventCursor = {monitor:0,comms:0};
        eventPlan = {monitor:isTaskEnabled('monitor') ? MATBTiming.schedule(state.durationMs,profile.monitorPerMinute,Math.random,4300-state.difficulty*250) : [],comms:isTaskEnabled('comms') ? MATBTiming.schedule(state.durationMs,profile.commsPerMinute,Math.random,6500) : []};
        state.monitorNextAt = eventPlan.monitor[0] ?? Infinity; state.comms.nextAt = eventPlan.comms[0] ?? Infinity;
      }
      const monitorNames =""")
source = source.replace("logEvent('session', 'start', { trial: $('seedInput').value });", "initSchedule(); logEvent('session', 'start', { trial: $('seedInput').value, eventPlan, rates:MATBTiming.profiles[window.experimentLoad || 'low'] });")
source = source.replace('state.elapsedMs + random(5200 - d * 400, 9000 - d * 700)', "nextScheduled('monitor')")
source = source.replace('state.elapsedMs + random(9000 - state.difficulty * 600, 13000 - state.difficulty * 600)', "nextScheduled('comms')")
source = source.replace("{ channel: m.name });", "{ channel: m.name, eventIndex:eventCursor.monitor+1, scheduledOnsetMs:eventPlan.monitor[eventCursor.monitor] });")
source = source.replace("logEvent('comms', 'prompt', { expected: code });", "logEvent('comms', 'prompt', { expected: code, eventIndex:eventCursor.comms, scheduledOnsetMs:eventPlan.comms[eventCursor.comms-1] });")
source = source.replace("wallTime: new Date().toISOString(), difficulty:", "...MATBTiming.stamp(), difficulty:")
# Use input-time clock, not the last animation frame, for discrete responses.
for fn in ['function acknowledgeMonitor(i) {', 'function answerComms(actual) {', 'function answerRadar(key) {']:
    source = source.replace(fn, fn + ' if(state.running && !state.paused) state.elapsedMs = now() - state.startAt;')
# Disturbance acts on the aircraft; target geometry and base difficulty stay matched.
source = source.replace('const error = Math.hypot(state.tracking.x', '''if (window.experimentDisturbance) {
          state.tracking.x = clamp(state.tracking.x + (Math.sin(t*2.3)*34 + Math.sin(t*5.7)*15)*dt/1000,22,canvas.width-22);
          state.tracking.y = clamp(state.tracking.y + (Math.cos(t*1.9)*30 + Math.sin(t*4.1)*13)*dt/1000,22,canvas.height-22);
        }
        const error = Math.hypot(state.tracking.x''')
source = source.replace('initPanels(); resetSession();', '''window.matbExperiment = {
        configure(load, duration, trial, tasks = null) {
          if(state.running) throw new Error('Task engine already running');
          const axis = state.throttle.axisIndex;
          resetSession(); state.throttle.axisIndex = axis; state.keys.clear();
          $('durationInput').value = duration; $('difficultyInput').value = 3;
          $('seedInput').value = trial;
          document.querySelectorAll('[data-task-toggle]').forEach(input => {
            input.checked = tasks ? tasks.includes(input.dataset.taskToggle) : ['tracking','monitor','radar'].includes(input.dataset.taskToggle) || (load === 'high' && ['throttle','comms'].includes(input.dataset.taskToggle));
          });
          window.experimentDisturbance = load === 'high';
          window.experimentLoad = tasks ? 'high' : load;
          const radarPanel = document.querySelector('[data-task-panel="radar"]');
          const radarColumn = document.querySelector(load === 'low' && !tasks ? '.right-stack' : '.left-stack');
          if(load === 'low' && !tasks) radarColumn.append(radarPanel); else radarColumn.prepend(radarPanel);
          document.body.classList.toggle('low',load === 'low');
          document.body.classList.toggle('solo',Boolean(tasks));
          state.enabledTasks = readEnabledTasks(); applyDisabledTaskUi();
        },
        start: () => { state.keys.clear(); beginSession(); },
        stop: () => endSession(false),
        calibrate: startThrottleCalibration,
        axis: cycleThrottleAxis,
        snapshot: () => ({ elapsedMs:state.elapsedMs, tracking:{...state.tracking}, throttle:{value:state.throttle.value,raw:state.throttle.raw,axisIndex:state.throttle.axisIndex,padId:state.throttle.padId}, metrics:{...state.metrics} }),
        ready: () => !state.throttle.calibrating,
      };
      window.addEventListener('blur', () => state.keys.clear());
      document.body.classList.add('embedded');
      initPanels(); resetSession();''')
Path('MATB_Experiment_Tasks.html').write_text(source, encoding='utf-8')
