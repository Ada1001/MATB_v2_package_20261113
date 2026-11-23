"""Original MATB demo: real-time COG-BCI S3 replay and genuine AGMNet inference."""
import argparse
import json
import threading
import time
import sys
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

class Replay:
    def __init__(self):
        self.lock=threading.RLock();self.commands=threading.Lock();self.stop_event=threading.Event();self.pause_event=threading.Event();self.thread=None
        self.state={'status':'idle','source':'COG-BCI sub-01 S3 真实数据回放（非现场脑电）','prediction':None,'eeg':None,'scoreHistory':[]}

    def start(self,calibration=60,realtime=True,limit=None,difficulty=None):
        if calibration not in (0,60):raise ValueError('校准只支持 0 或 60 秒')
        if difficulty is not None and difficulty not in (1,2,3,4,5):raise ValueError('难度必须为 1–5')
        with self.lock:
            if self.thread and self.thread.is_alive():raise ValueError('回放正在运行，请先停止')
            self.stop_event.clear()
            self.pause_event.clear()
            self.state={'status':'loading','source':'COG-BCI sub-01 S3 真实数据回放（非现场脑电）','prediction':None,'eeg':None,'error':None,'calibrationSeconds':calibration,'difficulty':difficulty,'scoreHistory':[]}
            self.thread=threading.Thread(target=self.run,args=(calibration,realtime,limit,difficulty),daemon=True);self.thread.start()

    def session(self,difficulty):
        if difficulty not in (1,2,3,4,5):raise ValueError('难度必须为 1–5')
        with self.commands:
            self.stop_event.set()
            if self.thread:self.thread.join(15)
            self.start(calibration=0,difficulty=difficulty)

    def pause(self,paused):
        if paused:self.pause_event.set()
        else:self.pause_event.clear()
        with self.lock:
            if self.state['status'] in ('running','paused'):self.state['status']='paused' if paused else 'running'

    def snapshot(self):
        with self.lock:return json.loads(json.dumps(self.state))

    def run(self,calibration,realtime,limit,difficulty):
        try:
            import numpy as np
            from online_agmnet.runtime.common import seed_all,load_artifact
            from online_agmnet.runtime.data import load_record,recording_order
            from online_agmnet.runtime.streaming import OnlinePredictor
            seed_all(42,1)
            model,config=load_artifact(ROOT/'model/sub01_local_v1','cpu')
            predictor=OnlinePredictor(model,config,calibration_seconds=calibration,spd_calibration='mean' if calibration else 'none',ema_alpha=.6,online_spd_rate=0)
            cache=ROOT/'online_agmnet/cache';cache.mkdir(exist_ok=True)
            output=ROOT/'online_agmnet/results'/(time.strftime('%Y%m%d_%H%M%S')+'_'+str(time.time_ns()%1000000000));output.mkdir(parents=True,exist_ok=True)
            tasks=recording_order(ROOT/'data',1,3) if difficulty is None else ['MATBeasy' if difficulty<=2 else 'MATBdiff']
            manifest={'source':'COG-BCI dataset replay, not live acquisition','subject':1,'session':3,'difficulty':difficulty,'artifact':'model/sub01_local_v1','packetSamples':50,'inputFs':500,'calibrationSeconds':calibration,'realtime':realtime,'testLimitSecondsPerRecord':limit,'recordings':[]}
            with (output/'predictions.jsonl').open('w',encoding='utf-8',buffering=1) as log:
                for task in tasks:
                    if self.stop_event.is_set():break
                    with self.lock:self.state.update(status='loading',record=None,prediction=None,eeg=None,elapsedSeconds=0,totalSeconds=0,scoreHistory=[])
                    # Adapter re-extracts .set/.fdt from the source ZIP, verifies size/rate,
                    # picks channels by name and converts EEGLAB microvolts to volts via MNE.
                    raw,meta=load_record(ROOT/'data',1,3,task)
                    if meta['channels']!=config['channels']:raise ValueError('数据通道顺序与模型不一致')
                    np.save(cache/(meta['record_id']+'.npy'),raw)
                    (cache/(meta['record_id']+'.json')).write_text(json.dumps(meta,indent=2),encoding='utf-8')
                    manifest['recordings'].append(meta)
                    (output/'session.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
                    predictor.start_record(meta['record_id'])
                    with self.lock:self.state.update(status='running',record=meta,prediction=None,elapsedSeconds=0,totalSeconds=raw.shape[1]/500,output=str(output))
                    origin=time.perf_counter();epoch_ms=time.time_ns()//1000000
                    meta['replayStartSystemMs']=epoch_ms
                    (output/'session.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
                    count=raw.shape[1] if limit is None else min(raw.shape[1],int(limit*500))
                    sample_clock=np.empty(count,dtype=np.int64);paused_ms=0
                    for start in range(0,count,50):
                        if self.pause_event.is_set():
                            pause_start=time.perf_counter()
                            while self.pause_event.is_set() and not self.stop_event.is_set():
                                with self.lock:self.state['status']='paused'
                                self.stop_event.wait(.05)
                            pause_duration=time.perf_counter()-pause_start
                            origin+=pause_duration;paused_ms+=pause_duration*1000
                        if self.stop_event.is_set():break
                        with self.lock:self.state['status']='running'
                        end=min(start+50,count)
                        if realtime and self.stop_event.wait(max(0,origin+end/500-time.perf_counter())):break
                        if self.stop_event.is_set():break
                        sample_clock[start:end]=epoch_ms+round(paused_ms)+np.arange(start,end)*2
                        rows=predictor.feed(raw[:,start:end],config['channels'])
                        for row in rows:
                            # Labels are attached only AFTER predictor.feed; never model inputs.
                            row.update(groundTruth=meta['label'],systemTimestampMs=time.time_ns()//1000000,
                                       replayWindowStartMs=int(sample_clock[row['start_sample']]),
                                       replayWindowEndMs=int(sample_clock[row['end_sample']-1])+2)
                            row['loadScore']=row['p_difficult']*100 if row['phase']=='test' and 'p_difficult' in row else None
                            log.write(json.dumps(row)+'\n')
                        a=max(0,end-2500)
                        eeg={'channels':config['channels'],'samples':(raw[:,a:end:4]*1e6).tolist(),'units':'uV','sampleRate':125,'status':'真实 S3 回放','startSeconds':a/500,'endSeconds':end/500}
                        with self.lock:
                            self.state['scoreHistory'].extend({'time':r['end_seconds'],'score':r['loadScore']} for r in rows)
                            self.state['scoreHistory']=self.state['scoreHistory'][-1200:]
                            self.state.update(eeg=eeg,elapsedSeconds=end/500,lagMs=max(0,(time.perf_counter()-origin-end/500)*1000),prediction=rows[-1] if rows else self.state['prediction'])
                    if self.stop_event.is_set():break
                (output/'calibration_events.json').write_text(json.dumps(predictor.events,indent=2),encoding='utf-8')
            with self.lock:self.state['status']='stopped' if self.stop_event.is_set() else 'completed'
        except Exception as error:
            import traceback
            traceback.print_exc()
            with self.lock:self.state.update(status='error',error=str(error))

def handler(replay):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT),**kw)
        def log_message(self,*a):pass
        def response(self,data,status=200):
            raw=json.dumps(data,ensure_ascii=False,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass  # Browser closed during polling.
        def do_GET(self):
            if self.path=='/api/agmnet/state':return self.response(replay.snapshot())
            super().do_GET()
        def do_POST(self):
            try:
                from urllib.parse import urlparse
                if self.headers.get('Origin') and urlparse(self.headers['Origin']).netloc!=self.headers.get('Host'):raise ValueError('same-origin only')
                if self.path=='/api/agmnet/start':
                    body=json.loads(self.rfile.read(min(int(self.headers.get('Content-Length',0)),4096)));replay.start(int(body.get('calibration',60)))
                elif self.path=='/api/agmnet/session':
                    body=json.loads(self.rfile.read(min(int(self.headers.get('Content-Length',0)),4096)));replay.session(body.get('difficulty'))
                elif self.path=='/api/agmnet/pause':replay.pause(True)
                elif self.path=='/api/agmnet/resume':replay.pause(False)
                elif self.path=='/api/agmnet/stop':replay.stop_event.set()
                else:return self.response({'error':'unknown endpoint'},404)
                self.response(replay.snapshot())
            except Exception as e:self.response({'error':str(e)},400)
    return Handler

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
    replay=Replay();server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(replay))
    print(f'Open http://127.0.0.1:{args.port}/MATB_Game_v2.html',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:replay.stop_event.set();server.server_close()
