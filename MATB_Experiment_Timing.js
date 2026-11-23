/* Shared rates and clock for the experiment controller and task frame. */
(() => {
  'use strict';
  const profiles=Object.freeze({low:Object.freeze({monitorPerMinute:1.2,commsPerMinute:0}),high:Object.freeze({monitorPerMinute:6,commsPerMinute:4})});
  function stamp(){const timestampMs=Date.now();const performanceEpochMs=performance.timeOrigin+performance.now();return {timestampMs,performanceEpochMs,wallTime:new Date(timestampMs).toISOString(),localTime:new Date(timestampMs).toLocaleString('sv-SE')+'.'+String(timestampMs%1000).padStart(3,'0'),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone};}
  function schedule(durationMs,rate,random=Math.random,responseWindowMs=0){
    const count=Math.round(durationMs*rate/60000);
    if(!count)return [];
    const slot=durationMs/count;
    return Array.from({length:count},(_,i)=>Math.min(Math.round((i+.5+(random()-.5)*.4)*slot),durationMs-responseWindowMs-50));
  }
  window.MATBTiming=Object.freeze({profiles,stamp,schedule,source:{authors:'Nixon & Charles (2017)',doi:'10.1007/s10111-017-0431-5',table:2,adaptation:'SYSMON uses gauge alerts only; COMM visual choice replaces original auditory tuning; not a universal MATB standard'},jitterFraction:.2});
})();
