"""End-to-end fake Neuracle TCP, browser, ACK retry, LSL and disk recording checks."""
import csv
import json
import socket
import struct
import tempfile
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
from eeg_bridge import Recorder,make_handler

def main():
    with tempfile.TemporaryDirectory(prefix='matb_eeg_test_', ignore_cleanup_errors=True) as tmp:
        source=socket.socket();source.bind(('127.0.0.1',0));source.listen(1);source.settimeout(15)
        stopping=threading.Event();sent=bytearray()
        def stream():
            conn,_=source.accept();index=0
            try:
                while not stopping.is_set():
                    values=[float(index+i) if ch<64 else float((index+i)//100%10) for i in range(40) for ch in range(65)]
                    packet=struct.pack('<'+str(len(values))+'f',*values);sent.extend(packet)
                    # Deliberate byte-level fragmentation, not aligned to samples/floats.
                    conn.sendall(packet[:253]);conn.sendall(packet[253:5001]);conn.sendall(packet[5001:]);index+=40;time.sleep(.04)
            except (ConnectionError,OSError):pass
            finally:conn.close()
        threading.Thread(target=stream,daemon=True).start()
        recorder=Recorder(Path(tmp));server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(recorder))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        errors=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(accept_downloads=True,viewport={'width':1440,'height':1080})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/MATB_Experiment.html')
            page.wait_for_function("document.querySelector('#engine').contentWindow.matbExperiment")
            page.locator('#eegOptions').evaluate('(el)=>el.open=true')
            page.check('#eegEnabled');page.fill('#eegPort',str(source.getsockname()[1]));page.check('#eegConfirmed')
            if recorder.lsl_available:page.check('#eegLSL')
            page.click('#beginDirect');page.wait_for_selector('[name=kss]')
            assert recorder.samples>0 and recorder.active
            if recorder.lsl_available:
                import pylsl
                eegs=pylsl.resolve_byprop('source_id',recorder.session+'-eeg',timeout=3)
                events=pylsl.resolve_byprop('source_id',recorder.session+'-markers',timeout=3)
                assert eegs and events
                inlet=pylsl.StreamInlet(eegs[0]);inlet.open_stream(timeout=3)
                sample,stamp=inlet.pull_sample(timeout=3);assert len(sample)==65 and stamp>0
                marker_in=pylsl.StreamInlet(events[0]);marker_in.open_stream(timeout=3)
            # Deliberately lose one marker HTTP response/request. Client retries the queue.
            blocked={'count':0}
            def lose_once(route):
                if not blocked['count']:blocked['count']+=1;route.abort()
                else:route.continue_()
            page.route('**/api/eeg/markers',lose_once)
            page.click('#skip') # KSS skipped -> open rest
            page.wait_for_function('window.EEGBridge.status().pending===0',timeout=12000)
            if recorder.lsl_available:
                marker,stamp=marker_in.pull_sample(timeout=3);assert json.loads(marker[0])['label'] and stamp>0
            page.wait_for_timeout(300)
            with page.expect_download() as download:page.click('#export')
            experiment=json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
            assert experiment['eegRecording']['session']==recorder.session
            expected={e['index'] for e in experiment['events']}
            # Duplicate submission is acknowledged without writing duplicate markers.
            body={'session':recorder.session,'experimentId':experiment['id'],'events':[experiment['events'][0]]}
            before=recorder.marker_count;recorder.markers(body);assert recorder.marker_count==before
            page.on('dialog',lambda dialog:dialog.accept())
            with page.expect_download():page.click('#abort')
            page.wait_for_function('!window.EEGBridge.status().recording',timeout=20000)
            folder=recorder.folder
            markers=[json.loads(x) for x in (folder/'markers.jsonl').read_text(encoding='utf-8').splitlines()]
            assert expected<={m['index'] for m in markers}
            assert len({m['index'] for m in markers})==len(markers)
            assert markers[-1]['label']=='experiment_abort'
            assert all(m['syncMethod']=='browser_perf_http_clock_fit' for m in markers)
            assert all(m['clockRttMs']>=0 for m in markers)
            raw=(folder/'raw_eeg.f32').read_bytes();assert raw==bytes(sent[:len(raw)])
            meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
            with (folder/'device_triggers.csv').open(encoding='utf-8',newline='') as f:
                triggers=list(csv.DictReader(f))
            expected_triggers=[(i,i//100%10) for i in range(100,len(raw)//(65*4),100) if i//100%10]
            assert expected_triggers
            assert [(int(row['sample_index_zero_based']),float(row['trigger_value'])) for row in triggers]==expected_triggers
            assert all(float(row['time_from_first_sample_s'])==i/1000 for row,(i,_) in zip(triggers,expected_triggers))
            assert meta['deviceTriggerCount']==len(triggers)
            assert meta['sampleCount']==len(raw)//(65*4) and meta['status']=='stopped'
            with (folder/'markers.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            assert len(rows)==len(markers) and any(row['nearestSampleZeroBased'] for row in rows)
            assert not errors,errors
            if recorder.lsl_available:inlet.close_stream();marker_in.close_stream()
            browser.close()
        stopping.set();source.close();server.shutdown();server.server_close()
        print(f'PASS: fragmented 65-channel TCP bytes preserved; {meta["sampleCount"]} samples; {len(markers)} labels; browser clock sync, dropped-request retry, deduplication, final flush and separate files; LSL={recorder.lsl_available}')

if __name__=='__main__':main()
