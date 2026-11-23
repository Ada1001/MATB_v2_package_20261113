"""Local Neuracle raw-TCP recorder, device trigger export and MATB marker bridge.

Run: python eeg_bridge.py --port 8766
"""
import argparse
import csv
import json
import logging
import math
import re
import socket
import struct
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger('matb.eeg')
LOG_PATH = ROOT / 'logs' / 'eeg_bridge.log'

def system_ms():
    return time.time_ns() / 1_000_000

class Recorder:
    def __init__(self, output=ROOT / 'recordings'):
        self.output = Path(output)
        self.lock = threading.RLock()
        self.session = None
        self.thread = None
        self.sock = None
        self.active = False
        self.lsl = None
        self.error = None
        self.samples = 0
        self.bytes = 0
        self.marker_count = 0
        self.trigger_count = 0
        self.lsl_error = None
        self.first_clock = None
        self.last_received = None
        self.lsl_available = False
        try:
            import pylsl
            self.lsl = pylsl
            self.lsl_available = True
        except Exception as exc:
            self.lsl_error = str(exc)

    def clock(self):
        return self.lsl.local_clock() if self.lsl else time.perf_counter()

    def status(self):
        with self.lock:
            return {'active': self.active, 'session': self.session, 'samples': self.samples,
                    'bytes': self.bytes, 'markers': self.marker_count, 'deviceTriggers': self.trigger_count, 'error': self.error,
                    'firstSampleClock': self.first_clock, 'lastReceivedSystemMs': self.last_received,
                    'lslAvailable': self.lsl_available, 'lslError': self.lsl_error,
                    'lslEnabled': bool(getattr(self, 'eeg_outlet', None)),
                    'diagnosticLog': str(LOG_PATH),
                    'output': str(getattr(self, 'folder', '')), 'clockDomain': 'lsl_local_clock' if self.lsl else 'python_perf_counter'}

    def start(self, cfg):
        with self.lock:
            if self.active:
                raise ValueError('已有采集在运行，请先停止。')
            if self.thread and self.thread.is_alive():
                raise ValueError('上一采集尚未退出，请稍后重试。')
            if getattr(self, 'marker_file', None) and not self.marker_file.closed:
                raise ValueError('上一采集尚未保存，请先点击“停止脑电并保存”。')
            if not cfg.get('protocolConfirmed'):
                raise ValueError('必须核对转发协议为无包头、小端 float32、按采样点交织的 TCP 数据。')
            fs = float(cfg['sampleRate'])
            channels = int(cfg['channels'])
            if not 1 <= fs <= 10000 or not 1 <= channels <= 512:
                raise ValueError('采样率或通道数超出范围。')
            host = str(cfg.get('host', '127.0.0.1')).strip()
            port = int(cfg.get('port', 8712))
            delay = float(cfg.get('latencyMs', 0))
            if not math.isfinite(delay) or not 0 <= delay <= 10000:
                raise ValueError('固定延迟必须为 0–10000 ms。')
            trigger = cfg.get('triggerIndex')
            trigger = int(trigger) if trigger not in ('', None) else None
            if trigger is not None and not 0 <= trigger < channels:
                raise ValueError('Trigger 索引使用 0 起始，必须小于总通道数。')
            names = cfg.get('channelNames', [])
            if names and (len(names) != channels or not all(isinstance(n, str) and n for n in names)):
                raise ValueError('通道名数量必须等于转发总通道数。')
            if cfg.get('lsl') and not self.lsl:
                raise ValueError('LSL 未安装或 DLL 不可用。请运行 install_eeg_dependencies.bat，或取消 LSL，仅录制本地文件。')
            self.lsl_error = None if self.lsl_available else self.lsl_error
            LOG.info('Connect attempt host=%s port=%d rate=%g channels=%d trigger_index=%s montage=%s lsl=%s',
                     host, port, fs, channels, trigger, cfg.get('montage'), bool(cfg.get('lsl')))
            try:
                sock = socket.create_connection((host, port), timeout=3)
            except OSError as exc:
                LOG.exception('TCP connect failed host=%s port=%d errno=%s winerror=%s',
                              host, port, exc.errno, getattr(exc, 'winerror', None))
                raise ValueError(f'无法连接 EEGRecorder 的 DataSending 服务 {host}:{port}：{exc}。同一台电脑运行时请填 127.0.0.1。') from exc
            sock.settimeout(.5)
            self.cfg = dict(cfg, sampleRate=fs, channels=channels, triggerIndex=trigger,
                            channelNames=names or [f'CH{i+1:03d}' for i in range(channels)],
                            channelNamesVerified=bool(names), latencyMs=delay)
            self.samples = self.bytes = self.marker_count = self.trigger_count = 0
            self.previous_trigger = 0.0
            self.error = self.first_clock = self.last_received = self.first_system_ms = None
            self.seen = set()
            self.received_event = threading.Event()
            self.stop_event = threading.Event()
            self.session = uuid.uuid4().hex
            participant = re.sub(r'[^\w-]', '_', str(cfg.get('participant', 'P01')))[:60]
            self.folder = self.output / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + participant + '_' + self.session[:8])
            self.started_ms = system_ms()
            self.eeg_outlet = self.marker_outlet = None
            self.sock = sock
            self.raw = self.marker_file = self.chunk_file = self.trigger_file = None
            try:
                self.folder.mkdir(parents=True, exist_ok=False)
                LOG.info('TCP connected session=%s peer=%s folder=%s; saving raw bytes and receive chunk sizes',
                         self.session, sock.getpeername(), self.folder)
                self.raw = (self.folder / 'raw_eeg.f32').open('wb')
                self.marker_file = (self.folder / 'markers.jsonl').open('w', encoding='utf-8', buffering=1)
                self.chunk_file = (self.folder / 'receive_chunks.csv').open('w', encoding='utf-8', newline='', buffering=1)
                self.chunk_writer = csv.writer(self.chunk_file)
                self.chunk_writer.writerow(['start_sample_zero_based', 'sample_count', 'received_bytes', 'total_raw_bytes', 'receive_system_ms', 'receive_clock_s', 'estimated_last_sample_clock_s', 'receipt_minus_estimated_ms'])
                if trigger is not None:
                    self.trigger_file = (self.folder / 'device_triggers.csv').open('w', encoding='utf-8', newline='', buffering=1)
                    self.trigger_writer = csv.writer(self.trigger_file)
                    self.trigger_writer.writerow(['sample_index_zero_based', 'trigger_value', 'time_from_first_sample_s'])
                if cfg.get('lsl'):
                    try:
                        self._create_outlets()
                    except Exception as exc:
                        self._disable_lsl(exc)
                self.active = True
                self._metadata('recording')
                self.thread = threading.Thread(target=self._receive, name='neuracle-tcp', daemon=True)
                self.thread.start()
            except Exception:
                LOG.exception('Recording setup failed session=%s folder=%s', self.session, self.folder)
                sock.close()
                for name in ('raw', 'marker_file', 'chunk_file', 'trigger_file'):
                    handle = getattr(self, name, None)
                    if handle:
                        handle.close()
                self.active = False
                self.session = None
                raise
        if not self.received_event.wait(4):
            with self.lock:
                self.error = '已连接但未收到完整采样点。请确认 DataSending 正在转发，并核对通道数。'
                LOG.warning('No complete sample after 4 seconds session=%s bytes=%d frame_bytes=%d',
                            self.session, self.bytes, channels * 4)
            self.stop()
            raise ValueError(self.error)
        if self.error:
            error = self.error
            self.stop()
            raise ValueError(error)
        return self.status()

    def _disable_lsl(self, exc):
        self.lsl_error = str(exc)
        self.eeg_outlet = self.marker_outlet = None
        LOG.exception('LSL disabled for session=%s; continuing local recording', self.session)

    def _create_outlets(self):
        lsl = self.lsl
        info = lsl.StreamInfo('Neuracle_MATB_EEG', 'EEG', self.cfg['channels'], self.cfg['sampleRate'], 'float32', self.session + '-eeg')
        desc = info.desc()
        desc.append_child_value('manufacturer', 'Neuracle')
        desc.append_child_value('montage', self.cfg.get('montage', 'EEGCap64-V3.0'))
        desc.append_child_value('timestamp_method', 'first TCP receipt minus sample-count duration and configured latency; no device timestamps')
        channels = desc.append_child('channels')
        for index, name in enumerate(self.cfg['channelNames']):
            ch = channels.append_child('channel')
            ch.append_child_value('label', name)
            ch.append_child_value('type', 'Trigger' if index == self.cfg['triggerIndex'] else 'EEG')
            ch.append_child_value('unit', 'native_unspecified')
        self.eeg_outlet = lsl.StreamOutlet(info)
        marker_info = lsl.StreamInfo('MATB_Experiment_Markers', 'Markers', 1, 0, 'string', self.session + '-markers')
        marker_info.desc().append_child_value('encoding', 'JSON; contains numeric code, semantic label and browser/server clocks')
        self.marker_outlet = lsl.StreamOutlet(marker_info)

    def _receive(self):
        pending = b''
        size = self.cfg['channels'] * 4
        fs = self.cfg['sampleRate']
        last_metadata = 0
        last_chunk = time.monotonic()
        try:
            while not self.stop_event.is_set():
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    if time.monotonic() - last_chunk >= 5:
                        raise TimeoutError('脑电转发连续 5 秒没有数据，请检查 EEGRecorder 的采集和转发状态。')
                    continue
                if not chunk:
                    raise ConnectionError('8712 转发连接已断开；请停止并重新建立采集，不会静默拼接断线前后数据。')
                last_chunk = time.monotonic()
                clock = self.clock()
                wall = system_ms()
                self.raw.write(chunk)  # Preserve every received byte, including final partial samples.
                self.raw.flush()
                pending += chunk
                count = len(pending) // size
                payload, pending = pending[:count * size], pending[count * size:]
                with self.lock:
                    if not self.bytes:
                        LOG.info('First TCP receive session=%s bytes=%d frame_bytes=%d', self.session, len(chunk), size)
                    self.bytes += len(chunk)
                    self.last_received = wall
                    if count and self.first_clock is None:
                        self.first_clock = clock - (count - 1) / fs - self.cfg['latencyMs'] / 1000
                        self.first_system_ms = wall - (count - 1) / fs * 1000 - self.cfg['latencyMs']
                    last = self.first_clock + (self.samples + count - 1) / fs if count else None
                    self.chunk_writer.writerow([self.samples, count, len(chunk), self.bytes, wall, clock, last, (clock-last)*1000 if last is not None else ''])
                    if count:
                        if self.trigger_file:
                            self._save_device_triggers(payload, size, fs)
                        if self.eeg_outlet:
                            try:
                                # pylsl accepts flattened sample-major values, but does not flatten tuple rows.
                                values = struct.unpack(f'<{count * self.cfg["channels"]}f', payload)
                                self.eeg_outlet.push_chunk(values, last)
                            except Exception as exc:
                                self._disable_lsl(exc)
                        self.samples += count
                        self.received_event.set()
                    if clock-last_metadata >= 1:
                        self._metadata('recording')
                        last_metadata = clock
        except Exception as exc:
            if not self.stop_event.is_set():
                LOG.exception('TCP receive failed session=%s bytes=%d samples=%d pending_bytes=%d',
                              self.session, self.bytes, self.samples, len(pending))
                with self.lock:
                    self.error = str(exc)
                    self.active = False
            self.received_event.set()
        finally:
            self.sock.close()
            self.raw.close()
            self.chunk_file.close()
            if self.trigger_file:
                self.trigger_file.close()
            with self.lock:
                self.active = False
                self._metadata('error' if self.error else 'stopped')
                LOG.info('TCP receive ended session=%s bytes=%d samples=%d triggers=%d trailing_bytes=%d error=%s',
                         self.session, self.bytes, self.samples, self.trigger_count, len(pending), self.error)

    def _save_device_triggers(self, payload, sample_size, sample_rate):
        trigger_offset = self.cfg['triggerIndex'] * 4
        for index, offset in enumerate(range(trigger_offset, len(payload), sample_size), self.samples):
            value, = struct.unpack_from('<f', payload, offset)
            if math.isfinite(value) and value > 0 and value != self.previous_trigger:
                self.trigger_writer.writerow([index, value, index / sample_rate])
                self.trigger_count += 1
            self.previous_trigger = value if math.isfinite(value) else 0.0

    def markers(self, body):
        with self.lock:
            if body.get('session') != self.session or not getattr(self, 'marker_file', None) or self.marker_file.closed:
                raise ValueError('标签所属采集会话无效或已关闭。')
            events = body.get('events', [])
            if not isinstance(events, list) or len(events) > 500:
                raise ValueError('标签批次数量无效。')
            for event in events:
                identity = (body.get('experimentId'), event['index'])
                if identity in self.seen:
                    continue
                receive_clock = self.clock()
                requested = event.get('bridgeClockS')
                valid = isinstance(requested, (float, int)) and math.isfinite(requested) and abs(requested-receive_clock) < 86400
                stamp = requested if valid else receive_clock
                record = dict(event, experimentId=body.get('experimentId'), recordingSession=self.session,
                              receiveSystemMs=system_ms(), receiveClockS=receive_clock,
                              markerClockS=stamp, syncMethod='browser_perf_http_clock_fit' if valid else 'receipt_fallback',
                              transportDelayEstimateMs=(receive_clock-stamp)*1000)
                record.pop('nearestSampleZeroBased', None)
                self.marker_file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
                self.seen.add(identity)
                self.marker_count += 1
                if self.marker_outlet:
                    try:
                        self.marker_outlet.push_sample([json.dumps(record, ensure_ascii=False)], stamp)
                    except Exception as exc:
                        self._disable_lsl(exc)
            return {'ack': [e['index'] for e in events], 'session': self.session}

    def _metadata(self, status):
        metadata = {'schema': 'neuracle-matb-recording-v1', 'session': self.session, 'status': status,
                    'config': self.cfg, 'startedSystemMs': self.started_ms, 'updatedSystemMs': system_ms(),
                    'firstSampleClockS': self.first_clock, 'firstSampleSystemMsEstimate':self.first_system_ms, 'sampleCount': self.samples, 'receivedBytes': self.bytes,
                    'deviceTriggerCount': self.trigger_count, 'lslError': self.lsl_error,
                    'trailingPartialBytes': self.bytes % (4*self.cfg['channels']), 'error': self.error,
                    'clockDomain': 'lsl_local_clock' if self.lsl else 'python_perf_counter',
                    'dtype': '<f4', 'layout': 'sample-major; rows=samples, columns=forwarded channels',
                    'units': 'native_unspecified; no filtering, scaling, re-referencing or montage inference',
                    'timestamps': 'estimated: first complete sample batch receipt anchors index/fs; device timing/latency not supplied',
                    'warning': 'TCP receipt time is not amplifier acquisition time; upstream loss/drift cannot be measured from headerless float32 alone'}
        temp = self.folder / 'metadata.tmp'
        temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.folder / 'metadata.json')

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError('采集线程尚未停止，不能关闭文件。')
        with self.lock:
            if not self.session:
                return self.status()
            if getattr(self, 'marker_file', None) and not self.marker_file.closed:
                self.marker_file.flush()
                self.marker_file.close()
                self._export_markers()
            self._metadata('error' if self.error else 'stopped')
            LOG.info('Recording finalized session=%s markers=%d folder=%s', self.session, self.marker_count, self.folder)
            return self.status()

    def _export_markers(self):
        keys = ['index', 'code', 'label', 'stage', 'phase', 'block', 'load', 'task', 'type', 'timestampMs', 'wallTime',
                'markerClockS', 'receiveSystemMs', 'receiveClockS', 'clockRttMs', 'syncMethod',
                'nearestSampleZeroBased', 'sampleOffsetMs', 'inRecordingRange', 'reason', 'details']
        with (self.folder/'markers.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            with (self.folder/'markers.jsonl').open(encoding='utf-8') as events:
                for line in events:
                    e = json.loads(line)
                    idx = round((e['markerClockS']-self.first_clock)*self.cfg['sampleRate']) if self.first_clock is not None else None
                    row = {key:e.get(key, '') for key in keys}
                    row.update(nearestSampleZeroBased=idx if idx is not None else '',
                               sampleOffsetMs=(e['markerClockS']-(self.first_clock+idx/self.cfg['sampleRate']))*1000 if idx is not None else '',
                               inRecordingRange=idx is not None and 0 <= idx < self.samples, details=json.dumps(e, ensure_ascii=False))
                    writer.writerow(row)

def make_handler(recorder):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def log_message(self, *args):
            pass

        def reply(self, obj, status=200):
            payload = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/api/eeg/status':
                return self.reply(recorder.status())
            if path == '/api/eeg/clock':
                return self.reply({'clockS': recorder.clock(), 'systemMs': system_ms()})
            if path.startswith('/api/'):
                return self.reply({'error': 'unknown API'}, 404)
            return super().do_GET()

        def do_POST(self):
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                return self.reply({'error': 'same-origin requests required'}, 403)
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 4_000_000:
                    raise ValueError('request size invalid')
                body = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == '/api/eeg/start':
                    result = recorder.start(body)
                elif path == '/api/eeg/markers':
                    result = recorder.markers(body)
                elif path == '/api/eeg/stop':
                    if body.get('session') != recorder.session:
                        raise ValueError('采集会话不匹配。')
                    result = recorder.stop()
                else:
                    return self.reply({'error': 'unknown API'}, 404)
                self.reply(result)
            except Exception as exc:
                LOG.exception('EEG API request failed path=%s', urlparse(self.path).path)
                self.reply({'error': str(exc)}, 400)
    return Handler

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--output', type=Path, default=ROOT/'recordings')
    args = parser.parse_args()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    recorder = Recorder(args.output)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(recorder))
    print(f'MATB + Neuracle bridge: http://127.0.0.1:{server.server_port}/MATB_Experiment.html', flush=True)
    print(f'Recordings: {recorder.output}', flush=True)
    LOG.info('EEG bridge started port=%d output=%s lsl_version=%s lsl_error=%s',
             server.server_port, recorder.output, getattr(recorder.lsl, '__version__', None), recorder.lsl_error)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        recorder.stop()
        server.server_close()

if __name__ == '__main__':
    main()
