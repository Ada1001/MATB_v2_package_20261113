"""Check session-bound replay and split monitor using real S3 data."""
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

URL='http://127.0.0.1:8765'
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge')
    context=browser.new_context(viewport={'width':1920,'height':1080})
    def wait_state(predicate):
        until=time.monotonic()+60
        while time.monotonic()<until:
            state=context.request.get(URL+'/api/agmnet/state').json()
            if predicate(state):return state
            time.sleep(.1)
        raise AssertionError(state)
    context.request.post(URL+'/api/agmnet/stop')
    wait_state(lambda d:d['status'] in ('stopped','completed','idle','error'))
    errors=[]
    main=context.new_page()
    main.on('pageerror',lambda e:errors.append(str(e)))
    main.goto(URL+'/MATB_Game_v2.html')
    assert main.locator('#agmStart').count()==0
    assert main.locator('#agmStop').count()==0
    assert main.locator('#durationInput').input_value()=='300'
    with main.expect_popup() as popup:main.locator('#monitorBtn').click()
    monitor=popup.value
    monitor.on('pageerror',lambda e:errors.append(str(e)))
    monitor.wait_for_selector('#modelEEG')
    assert not monitor.locator('#eegCanvas').is_visible()
    boxes=monitor.locator('#agmGrid > .panel').all()
    left,right=[box.bounding_box() for box in boxes]
    assert left['x']<right['x'] and left['y']==right['y']
    assert monitor.locator('#modelEEG').bounding_box()['height']==240
    records=[]
    for difficulty in [1,2,3,4,5]:
        main.locator('#difficultyInput').fill(str(difficulty))
        main.locator('#startBtn').click()
        wait_state(lambda d:d.get('difficulty')==difficulty and d['status']=='running' and d.get('record',{}).get('label')==(0 if difficulty<=2 else 1) and (d.get('prediction') or {}).get('phase')=='test')
        assert main.locator('#difficultyInput').is_disabled()
        d=context.request.get(URL+'/api/agmnet/state').json()
        assert 299<d['totalSeconds']<300
        history=d['scoreHistory']
        assert history[0]['time']==1
        assert len({point['time'] for point in history})==len(history)
        assert abs(d['prediction']['loadScore']-100*d['prediction']['p_difficult'])<1e-8
        records.append({'difficulty':difficulty,'task':d['record']['task'],'seconds':d['totalSeconds']})
        if difficulty==1:
            main.locator('#pauseBtn').click()
            wait_state(lambda d:d['status']=='paused')
            main.wait_for_timeout(200)
            before=context.request.get(URL+'/api/agmnet/state').json()['elapsedSeconds']
            main.wait_for_timeout(800)
            after=context.request.get(URL+'/api/agmnet/state').json()['elapsedSeconds']
            assert after==before,(before,after)
            main.locator('#pauseBtn').click()
            wait_state(lambda d:d['status']=='running')
            wait_state(lambda d:d['elapsedSeconds']>=after+.6)
        if difficulty==5:
            monitor.wait_for_function("Number(document.getElementById('loadCurve').dataset.points)>=8")
            monitor.reload()
            monitor.wait_for_function("Number(document.getElementById('loadCurve').dataset.points)>=8")
            assert monitor.locator('#lowBar, #highBar, #lowProb, #highProb').count()==0
            assert monitor.locator('#loadCurve').bounding_box()['height']==240
            monitor.locator('#waveGroup').select_option('56')
            monitor.wait_for_function("document.querySelectorAll('#taskList .task-row').length===5")
            monitor.wait_for_timeout(300)
            monitor.screenshot(path=str(Path(__file__).with_name('results')/'monitor_preview.png'),full_page=True)
            main.screenshot(path=str(Path(__file__).with_name('results')/'main_preview.png'),full_page=True)
        main.locator('#endBtn').click()
        wait_state(lambda d:d['status']=='stopped')
        assert main.locator('#difficultyInput').is_enabled()
    main.locator('#durationInput').fill('30')
    main.clock.install()
    main.locator('#startBtn').click()
    wait_state(lambda d:d['status']=='running' and (d.get('prediction') or {}).get('phase')=='test')
    main.clock.fast_forward(31000)
    wait_state(lambda d:d['status']=='stopped')
    assert main.locator('#startBtn').is_enabled()
    legacy=context.new_page();legacy.goto(URL+'/MATB_Monitor_v2.html')
    assert legacy.locator('#modelEEG').count()==0
    assert legacy.locator('#eegCanvas').is_visible()
    assert not errors,errors
    result={'status':'passed','browserErrors':errors,'conditions':records,'checks':['automatic replay start','all five difficulty mappings','pause/resume','end stops replay','automatic timeout stops replay','split layout','240px EEG','continuous score curve','score matches model probability','history resets per session','history restored on reload','no probability bars','no replay buttons','legacy monitor isolated']}
    Path(__file__).with_name('results').joinpath('browser_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    browser.close()
