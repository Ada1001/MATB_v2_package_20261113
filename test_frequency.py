"""Verify full 5-minute schedules with a controlled animation clock, plus real UI clocks/layout."""
import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_experiment import ROOT, QuietHandler

def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1080},accept_downloads=True)
        page.goto(f'http://127.0.0.1:{server.server_port}/MATB_Experiment.html')
        page.wait_for_function("document.querySelector('#engine').contentWindow.matbExperiment")
        page.click('#beginDirect')
        for _ in range(4):page.click('#skip') # KSS, open, closed, ready -> block
        assert page.locator('#countdownNumber').inner_text()=='3'
        page.screenshot(path=str(ROOT/'experiment_countdown.png'),full_page=True)
        with page.expect_download() as d:page.click('#export')
        preview=json.loads(Path(d.value.path()).read_text(encoding='utf-8'))
        assert not preview['samples']
        assert not any(e['label'] in ('block_low_start','task_start') for e in preview['events'])
        page.wait_for_function("document.querySelector('#countdownNumber').textContent==='2'")
        page.wait_for_function("document.querySelector('#countdownNumber').textContent==='1'")
        page.wait_for_function("document.querySelector('#taskCountdown').hidden")
        frame=page.frame_locator('#engine')
        sysmon=frame.locator('[data-task-panel="monitor"]').bounding_box()
        radar=frame.locator('[data-task-panel="radar"]').bounding_box()
        track=frame.locator('[data-task-panel="tracking"]').bounding_box()
        assert abs(sysmon['x']-radar['x'])<1 and sysmon['y']+sysmon['height']<=radar['y']+1
        assert sysmon['x']<track['x']
        layout=frame.locator('.layout').bounding_box()
        assert layout['width']<=1161 and layout['x']>80
        page.screenshot(path=str(ROOT/'experiment_low_layout.png'),full_page=True)
        page.click('#skip') # stop block
        with page.expect_download() as d:page.click('#export')
        data=json.loads(Path(d.value.path()).read_text(encoding='utf-8'))
        cstart=next(e for e in data['events'] if e['label']=='countdown_start')
        cend=next(e for e in data['events'] if e['label']=='countdown_end')
        block=next(e for e in data['events'] if e['label']=='block_low_start')
        assert cend['tMs']-cstart['tMs']>=2990
        assert block['tMs']>=cend['tMs']
        for e in data['events']:
            assert isinstance(e['timestampMs'],int)
            assert e['wallTime'].endswith('Z') and len(e['wallTime'].split('.')[-1])==4
            assert e['localTime'][-4]=='.' and e['timezone']
        with page.expect_download() as d:page.click('#csv')
        assert 'timestampMs' in Path(d.value.path()).read_text(encoding='utf-8-sig').splitlines()[0]
        engine=page.frames[1]
        report=engine.evaluate('''() => {
          const api=window.matbExperiment;let pending=null,id=0;
          window.requestAnimationFrame=cb=>{pending=cb;return ++id};window.cancelAnimationFrame=()=>pending=null;
          let events=[];window.addEventListener('matb-task-event',e=>events.push(e.detail));
          const result={};
          for(const load of ['low','high']){
            events=[];api.configure(load,300,'schedule-check');api.start();
            const origin=performance.now();
            for(let ms=250;ms<=300250;ms+=250){const cb=pending;pending=null;if(cb)cb(origin+ms);}
            const begin=events.find(e=>e.task==='session'&&e.type==='start');
            const faults=events.filter(e=>e.task==='monitor'&&e.type==='fault');
            const prompts=events.filter(e=>e.task==='comms'&&e.type==='prompt');
            result[load]={faults:faults.length,prompts:prompts.length,plan:begin.eventPlan,
              scheduled:events.filter(e=>e.scheduledOnsetMs!=null).every(e=>e.tMs>=e.scheduledOnsetMs && e.tMs-e.scheduledOnsetMs<251),
              clock:events.every(e=>Number.isInteger(e.timestampMs)&&new Date(e.timestampMs).toISOString()===e.wallTime),
              indices:faults.map(e=>e.eventIndex), commIndices:prompts.map(e=>e.eventIndex)};
          }
          return result;
        }''')
        assert report['low']['faults']==6 and report['low']['prompts']==0,report
        assert report['high']['faults']==30 and report['high']['prompts']==20,report
        for load,n in [('low',6),('high',30)]:
            assert report[load]['clock'] and report[load]['scheduled']
            assert report[load]['indices']==list(range(1,n+1))
        assert report['high']['commIndices']==list(range(1,21))
        assert max(report['high']['plan']['comms'])+6500<300000
        assert max(report['high']['plan']['monitor'])+3550<300000
        print('PASS: centered narrow low layout with 02 above 05 left of 01; system milliseconds/CSV; simulated 5-min low=6 SYSMON, high=30 SYSMON+20 COMM, planned/actual indices and final response windows')
        page.on('dialog',lambda d:d.accept());browser.close()
    server.shutdown()

if __name__=='__main__':main()
