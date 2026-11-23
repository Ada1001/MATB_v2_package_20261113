"""Browser integration checks; uses installed Chrome and Playwright for Python."""
import json
import threading
from collections import Counter
from datetime import datetime
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

def audit_completed(data):
    opened=Counter()
    for event in data['events']:
        stamp=event['timestampMs']
        assert isinstance(stamp,int)
        assert round(datetime.fromisoformat(event['wallTime'].replace('Z','+00:00')).timestamp()*1000)==stamp
        label=event['label']
        edge='start' if label.endswith('_start') else 'end' if label.endswith('_end') else None
        if not edge:continue
        kind=label.rsplit('_',1)[0]
        key=(kind,event.get('block') if kind in ('block_low','block_high','rsme','break','task','stage') else None,
             event.get('trainingStep') if kind in ('training','task','stage') else None,
             event.get('task') if kind=='task' else event.get('stage') if kind=='stage' else None)
        opened[key]+=1 if edge=='start' else -1
        assert opened[key]>=0,(key,event)
    assert not any(opened.values()),opened
    for record in data['samples']+data['scales']:
        assert isinstance(record['timestampMs'],int) and record['wallTime']

def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width':1440, 'height':1080}, accept_downloads=True)
        errors=[]
        page.on('pageerror', lambda e: errors.append(str(e)))
        base=f'http://127.0.0.1:{server.server_port}/MATB_Experiment.html'
        page.goto(base+'?test=1')
        page.wait_for_function("document.querySelector('#engine').contentWindow.matbExperiment")
        # Synthetic Gamepad checks axis detection and preservation across blocks.
        page.frame_locator('#engine').locator('body').evaluate("""() => {
          window.testPad={id:'Synthetic flight joystick',index:0,axes:[0,0,0],buttons:Array.from({length:10},()=>({pressed:false}))};
          Object.defineProperty(navigator,'getGamepads',{value:()=>[window.testPad],configurable:true});
        }""")
        page.click('#calibrate')
        for value in [-1,1,-1,1]:
            page.frame_locator('#engine').locator('body').evaluate('(el,v)=>window.testPad.axes[2]=v',value)
            page.wait_for_timeout(400)
        page.wait_for_timeout(800)
        assert page.frame_locator('#engine').locator('body').evaluate('()=>window.matbExperiment.snapshot().throttle.axisIndex')==2
        page.click('#train')
        for task in ['tracking','monitor','throttle','comms','radar']:
            page.wait_for_function('(task)=>document.querySelector("#trainingGuide h2")?.textContent.includes(({tracking:"Tracking",monitor:"Sysmon",throttle:"Throttle",comms:"Communication",radar:"Radar"})[task])',arg=task)
            frame=page.frame_locator('#engine')
            for panel in ['tracking','monitor','throttle','comms','radar']:
                assert frame.locator(f'[data-task-panel="{panel}"]').is_visible() == (panel==task)
            assert page.locator('#trainingGuide').is_visible()
        page.wait_for_selector('#begin')
        page.click('#begin')
        assert page.locator('#submit').is_disabled()
        page.check('[name=kss][value="3"]')
        page.click('#submit')
        page.wait_for_selector('#next')
        for block in range(1,7):
            page.click('#next')
            frame=page.frame_locator('#engine')
            assert frame.locator('[data-task-panel="radar"]').is_visible()
            assert frame.locator('[data-task-panel="throttle"]').is_visible() == (block%2==0)
            assert frame.locator('body').evaluate('()=>window.matbExperiment.snapshot().throttle.axisIndex')==2
            assert frame.locator('body').evaluate('()=>window.experimentDisturbance') == (block%2==0)
            if block==1:
                frame.locator('body').press('d')
            page.wait_for_selector('#rsme')
            assert page.locator('#submit').is_disabled()
            if block==1:
                page.screenshot(path=str(ROOT/'experiment_rsme.png'),full_page=True)
            page.locator('#rsme').fill(str(20+block))
            page.click('#submit')
            if block<6:
                page.wait_for_selector('#next')
                page.wait_for_timeout(150)
                assert '自主休息' in page.locator('h1').inner_text()
        page.click('#post')
        page.wait_for_selector('[name=kss]')
        page.check('[name=kss][value="7"]')
        with page.expect_download() as d:
            page.click('#submit')
        result=json.loads(Path(d.value.path()).read_text(encoding='utf-8'))
        assert result['status']=='completed'
        assert len(result['scales'])==8
        assert [e['load'] for e in result['events'] if e['label'] in ('block_low_start','block_high_start')] == ['low','high']*3
        for phase in ('pre','post'):
            for eyes in ('open','closed'):
                for edge in ('start','end'):
                    assert sum(e['label']==f'rest_{phase}_eyes_{eyes}_{edge}' for e in result['events'])==1
        for block in range(1,7):
            events=[e for e in result['events'] if e['block']==block and e['stage']=='block']
            tasks=['tracking','monitor','throttle','comms','radar'] if block%2==0 else ['tracking','monitor','radar']
            assert [e['task'] for e in events if e['label']=='task_start']==tasks
            assert [e['task'] for e in events if e['label']=='task_end']==tasks
        assert all(b['tMs']>=a['tMs'] for a,b in zip(result['events'],result['events'][1:]))
        assert len(result['samples'])>100
        assert [e['trainingTask'] for e in result['events'] if e['label']=='training_start']==['tracking','monitor','throttle','comms','radar']
        assert not result['hasSkippedStages']
        assert sum(e['label']=='countdown_start' for e in result['events'])==11
        assert sum(e['label']=='countdown_end' for e in result['events'])==11
        audit_completed(result)
        assert not errors,errors
        page.screenshot(path=str(ROOT/'experiment_complete.png'),full_page=True)
        with page.expect_download() as d:
            page.click('#csv')
        assert 'block_high_start' in Path(d.value.path()).read_text(encoding='utf-8-sig')
        # Production durations and abort behaviour.
        page.goto(base)
        page.wait_for_function("document.querySelector('#engine').contentWindow.matbExperiment")
        page.click('#train')
        page.wait_for_function("document.querySelector('#taskCountdown').hidden")
        page.wait_for_timeout(300)
        page.screenshot(path=str(ROOT/'experiment_training.png'),full_page=True)
        page.on('dialog',lambda dialog:dialog.accept())
        with page.expect_download() as d:
            page.click('#abort')
        aborted=json.loads(Path(d.value.path()).read_text(encoding='utf-8'))
        assert aborted['status']=='aborted'
        assert aborted['config']['blockSec']==300 and aborted['config']['restSec']==60
        assert aborted['events'][-1]['label']=='experiment_abort'
        assert sum(e['label']=='training_end' for e in aborted['events'])==1
        # Every live stage can be skipped, including five individual training stages.
        page.goto(base)
        page.wait_for_function("document.querySelector('#engine').contentWindow.matbExperiment")
        page.screenshot(path=str(ROOT/'experiment_setup.png'),full_page=True)
        page.click('#train')
        for index in range(5):
            assert f'{index+1} / 5' in page.locator('#taskBanner').inner_text()
            page.click('#skip') # countdown -> running training
            page.click('#skip')
        assert page.locator('#begin').is_visible()
        page.click('#skip') # training complete -> KSS
        page.click('#skip') # KSS -> eyes open
        page.click('#skip') # eyes open -> eyes closed
        page.click('#skip') # eyes closed -> block ready
        for block in range(1,7):
            page.click('#skip') # ready / break -> block
            assert f'Block {block} / 6' in page.locator('#taskBanner').inner_text()
            page.click('#skip') # countdown -> running block
            page.click('#skip') # block -> RSME
            assert page.locator('#rsme').is_visible()
            page.click('#skip') # RSME -> break / post ready
        page.click('#skip') # post ready -> eyes open
        page.click('#skip') # eyes open -> eyes closed
        page.click('#skip') # eyes closed -> KSS
        with page.expect_download() as d:
            page.click('#skip')
        skipped=json.loads(Path(d.value.path()).read_text(encoding='utf-8'))
        assert skipped['hasSkippedStages']
        assert all(s['skipped'] and s['value'] is None for s in skipped['scales'])
        assert len(skipped['scales'])==8
        assert len([e for e in skipped['events'] if e['label']=='stage_skip'])==42
        assert all(e['reason']=='skipped' for e in skipped['events'] if e['label']=='task_end')
        assert page.locator('#skip').is_disabled()
        audit_completed(skipped)
        # Direct start bypasses all training without producing skipped-training records.
        page.goto(base)
        page.wait_for_function("document.querySelector('#engine').contentWindow.matbExperiment")
        page.click('#beginDirect')
        assert page.locator('[name=kss]').count()==9
        with page.expect_download() as d:
            page.click('#export')
        direct=json.loads(Path(d.value.path()).read_text(encoding='utf-8'))
        assert not direct['trainingRequested'] and not direct['hasSkippedStages']
        assert not any(e['label']=='training_start' for e in direct['events'])
        assert not errors,errors
        browser.close()
        print(f"PASS: five individual training tasks, radar in all blocks, full flow, 42 Skip transitions including countdowns, direct start, scales, markers, CSV, calibration and abort; {len(result['events'])} events, {len(result['samples'])} samples")
    server.shutdown()

if __name__=='__main__':
    main()
