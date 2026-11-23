"""Show live Neuracle EEG or recorded S3 EEG alongside exported predictions."""
from __future__ import annotations

import argparse
import csv
import errno
import hashlib
import json
import logging
import math
import os
import socket
import threading
import time
import uuid
import webbrowser
import zipfile
from contextlib import ExitStack
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from http.client import HTTPConnection, HTTPException
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import numpy as np
from scipy.io import loadmat


ROOT = Path(__file__).resolve().parents[1]
EEG_LOG_PATH = ROOT / "logs" / "demo_eeg.log"
EEG_LOG = logging.getLogger("matb.demo.eeg")
SERVER_ID = hashlib.sha256(str(ROOT).encode("utf-8") + Path(__file__).read_bytes()).hexdigest()
SESSION_ID = "S3"
DATA_ROOT = ROOT / "data" / "sub-01" / "sub-01" / f"ses-{SESSION_ID}"
PREDICTION_FILE = ROOT / "data" / "真实预测.xlsx"
DEMO_PREDICTION_FILE = ROOT / "data" / "S3.xlsx"
DEMO_SCORE_INTERVAL_SECONDS = 0.5
CONDITION = "diff"

CONDITIONS = {
    CONDITION: {
        "name": "MATB Difficult",
        "label": "S3 · 高负荷",
        "set": DATA_ROOT / "eeg" / "MATBdiff.set",
    },
}

DISPLAY_DECIMATION = 8  # 500 Hz -> 62.5 Hz display stream
DISPLAY_WINDOW_SECONDS = 2.5


class DemoEEGRecording:
    def __init__(self, folder, endpoint, sample_rate, channel_count):
        self.folder = folder
        self.info = {
            "schema": "matb-demo-eeg-recording-v1",
            "status": "recording",
            "startedAt": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "config": {
                "endpoint": endpoint, "montage": "EEGCap64-V3.0",
                "sampleRate": sample_rate, "channels": channel_count, "triggerIndex": channel_count - 1,
            },
            "dtype": "<f4",
            "layout": "sample-major",
            "sampleCount": 0,
            "savedBytes": 0,
        }
        self.files = ExitStack()
        folder.mkdir(parents=True, exist_ok=False)
        try:
            self.raw = self.files.enter_context((folder / "raw_eeg.f32").open("wb"))
            chunks = self.files.enter_context((folder / "receive_chunks.csv").open("w", encoding="utf-8", newline="", buffering=1))
            self.chunks = csv.writer(chunks)
            self.chunks.writerow(["received_at", "recv_bytes", "start_sample_zero_based", "sample_count", "saved_bytes", "pending_bytes"])
            self._write_metadata()
        except Exception:
            self.files.close()
            raise

    def append(self, payload, count, recv_bytes, pending_bytes):
        if payload:
            self.raw.write(payload)
            self.raw.flush()
        start_sample = self.info["sampleCount"]
        self.info["sampleCount"] += count
        self.info["savedBytes"] += len(payload)
        self.chunks.writerow([datetime.now().astimezone().isoformat(timespec="milliseconds"),
                             recv_bytes, start_sample, count, len(payload), pending_bytes])

    def finish(self, error=None):
        self.info.update(status="error" if error else "saved", error=error,
                         endedAt=datetime.now().astimezone().isoformat(timespec="milliseconds"))
        self.files.close()
        self._write_metadata()

    def _write_metadata(self):
        temp = self.folder / "metadata.tmp"
        temp.write_text(json.dumps(self.info, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.folder / "metadata.json")

    def snapshot(self):
        return dict(self.info, folder=str(self.folder))


class LiveEEG:
    sample_rate = 1000
    channel_count = 65  # 64 EEG channels followed by the Trigger channel.
    decimation = 16
    channels = [f"CH{i:03d}" for i in range(1, 65)]

    def __init__(self, host="127.0.0.1", port=8712, output=ROOT / "recordings" / "demo"):
        self.address = (host, port)
        self.endpoint = f"{host}:{port}"
        self.output = output
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.latest = (f"等待 EEGRecorder 转发 {self.endpoint}", None)
        self.recording = None
        self.last_recording = None
        self.thread = threading.Thread(target=self._run, name="demo-live-eeg", daemon=True)

    def snapshot(self):
        with self.lock:
            status, eeg = self.latest
            recording = self.recording.snapshot() if self.recording else self.last_recording
            return dict(eegStatus=status, eeg=eeg, eegRecording=recording)

    def start_recording(self):
        with self.lock:
            if self.recording:
                return
            if self.latest[1] is None:
                raise ValueError("尚未收到实时 EEG 样本，请先确认 EEGRecorder 正在转发。")
            name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
            self.recording = DemoEEGRecording(self.output / name, self.endpoint, self.sample_rate, self.channel_count)
            EEG_LOG.info("Recording started endpoint=%s folder=%s", self.endpoint, self.recording.folder)

    def stop_recording(self, error=None):
        with self.lock:
            self._finish_recording(error)

    def _finish_recording(self, error=None):
        if not self.recording:
            return
        recording, self.recording = self.recording, None
        try:
            recording.finish(error)
        except OSError as exc:
            recording.info.update(status="error", error=str(exc))
            EEG_LOG.exception("Recording finalization failed folder=%s", recording.folder)
        self.last_recording = recording.snapshot()
        EEG_LOG.info("Recording ended folder=%s samples=%d status=%s error=%s",
                     recording.folder, recording.info["sampleCount"], recording.info["status"], recording.info.get("error"))

    def _run(self):
        last_error = None
        last_logged = 0
        while not self.stopping.is_set():
            connected = False
            try:
                with socket.create_connection(self.address, timeout=2) as connection:
                    connected = True
                    connection.settimeout(0.25)
                    EEG_LOG.info("TCP connected endpoint=%s frame_bytes=%d", self.endpoint, self.channel_count * 4)
                    with self.lock:
                        self.latest = (f"已连接 {self.endpoint}，等待样本", None)
                    self._receive(connection)
            except OSError as exc:
                error = str(exc) if connected else f"无法连接 DataSending：{exc}"
                if error != last_error or time.monotonic() - last_logged >= 15:
                    EEG_LOG.warning("TCP unavailable endpoint=%s winerror=%s: %s",
                                    self.endpoint, getattr(exc, "winerror", None), error)
                    last_error, last_logged = error, time.monotonic()
                with self.lock:
                    self.latest = (f"实时 EEG 暂不可用（{self.endpoint}）：{error}", None)
                    self._finish_recording(error)
            self.stopping.wait(1)
        self.stop_recording()

    def _receive(self, connection):
        frame_bytes = self.channel_count * 4
        capacity = math.ceil(DISPLAY_WINDOW_SECONDS * self.sample_rate / self.decimation)
        pending = b""
        window = np.empty((0, len(self.channels)), dtype=np.float32)
        total_samples = 0
        last_sample = time.monotonic()
        next_update = last_sample
        received_bytes = 0
        while not self.stopping.is_set():
            if time.monotonic() - last_sample >= 3:
                detail = "未收到任何 TCP 字节" if not received_bytes else (
                    f"此前收到 {received_bytes} 字节、{total_samples} 个完整样本，剩余 {len(pending)} 字节")
                raise TimeoutError(f"已连接，但 3 秒内没有新的完整样本：{detail}")
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError(f"转发已断开，已收到 {received_bytes} 字节、{total_samples} 个样本")
            last_received = time.monotonic()
            if not received_bytes:
                EEG_LOG.info("First TCP receive endpoint=%s recv_bytes=%d", self.endpoint, len(chunk))
            received_bytes += len(chunk)
            pending += chunk
            count = len(pending) // frame_bytes
            payload, pending = pending[:count * frame_bytes], pending[count * frame_bytes:]
            with self.lock:
                if self.recording:
                    try:
                        self.recording.append(payload, count, len(chunk), len(pending))
                    except OSError as exc:
                        EEG_LOG.exception("Recording write failed")
                        self._finish_recording(str(exc))
            if not count:
                continue
            last_sample = last_received
            first = (-total_samples) % self.decimation
            rows = np.frombuffer(payload, dtype="<f4").reshape(count, self.channel_count)
            window = np.concatenate((window, rows[first::self.decimation, :len(self.channels)]))[-capacity:]
            total_samples += count
            if len(window) and last_received >= next_update:
                last_display_sample = ((total_samples - 1) // self.decimation) * self.decimation
                first_display_sample = last_display_sample - (len(window) - 1) * self.decimation
                eeg = {
                    "channels": self.channels,
                    "samples": np.nan_to_num(window.T, nan=0.0, posinf=0.0, neginf=0.0).tolist(),
                    "sampleRate": self.sample_rate / self.decimation,
                    "units": "原始单位",
                    "startSeconds": first_display_sample / self.sample_rate,
                    "endSeconds": last_display_sample / self.sample_rate,
                }
                with self.lock:
                    self.latest = ("实时 EEG 已连接", eeg)
                next_update = last_received + 0.1


def _scalar(value, default=0):
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return default
        value = value.reshape(-1)[0]
    try:
        return value.item()
    except AttributeError:
        return value


def _label(value):
    value = _scalar(value, "")
    return str(value).strip()


def load_record(condition=CONDITION):
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    spec = CONDITIONS[condition]
    set_path = spec["set"]
    if not set_path.exists():
        raise FileNotFoundError(f"找不到 EEG 元数据：{set_path}")
    meta = loadmat(set_path, squeeze_me=True, struct_as_record=False)
    channels = [_label(getattr(ch, "labels", "")) for ch in np.atleast_1d(meta.get("chanlocs", []))]
    channels = [name or f"CH{i + 1:02d}" for i, name in enumerate(channels)]
    nbchan = int(_scalar(meta.get("nbchan"), len(channels)))
    pnts = int(_scalar(meta.get("pnts"), 0))
    srate = float(_scalar(meta.get("srate"), 500))
    datfile = _label(meta.get("datfile") or meta.get("data"))
    fdt_path = set_path.parent / (datfile or (set_path.stem + ".fdt"))
    if not fdt_path.exists():
        raise FileNotFoundError(f"找不到 EEG 数据：{fdt_path}")
    expected = nbchan * pnts
    actual = fdt_path.stat().st_size // 4
    if expected <= 0 or actual < expected:
        raise ValueError(f"EEG 文件长度不匹配：期望 {expected}，实际 {actual}")
    data = np.memmap(fdt_path, dtype="<f4", mode="r", shape=(nbchan, pnts), order="F")
    duration = pnts / srate
    return {
        "condition": condition,
        "name": spec["name"],
        "label": spec["label"],
        "setPath": str(set_path.relative_to(ROOT)),
        "fdtPath": str(fdt_path.relative_to(ROOT)),
        "channels": channels[:nbchan],
        "nbchan": nbchan,
        "pnts": pnts,
        "srate": srate,
        "durationSeconds": duration,
        "data": data,
    }


def _xlsx_values(path: Path):
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for item in root.findall("x:si", ns):
                shared.append("".join(t.text or "" for t in item.findall(".//x:t", ns)))
        sheet = next(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet"))
        root = ET.fromstring(archive.read(sheet))
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        cells = {}
        max_row = 0
        for cell in root.findall(".//x:c", ns):
            ref = cell.attrib.get("r", "")
            if not ref.startswith("A") or not ref[1:].isdigit():
                continue
            row_number = int(ref[1:])
            value = cell.find("x:v", ns)
            inline = cell.find("x:is", ns)
            if value is not None:
                text = value.text or ""
                if cell.attrib.get("t") == "s":
                    try:
                        text = shared[int(text)]
                    except (ValueError, IndexError):
                        pass
                cells[row_number] = text
            elif inline is not None:
                cells[row_number] = "".join(t.text or "" for t in inline.findall(".//x:t", ns))
            else:
                cells[row_number] = ""
            max_row = max(max_row, row_number)
        return [cells.get(row_number, "") for row_number in range(1, max_row + 1)]


def _prediction_segments(raw):
    """Parse a one-column prediction file and preserve blank-row segments."""
    segments = []
    current = []
    for item in raw:
        text = str(item).strip()
        if not text:
            if current:
                segments.append(current)
                current = []
            continue
        try:
            value = float(text.replace(",", ""))
        except (TypeError, ValueError):
            # A header is ignored. A non-numeric row between values acts as a
            # separator, which also supports exported files with blank/header rows.
            if current:
                segments.append(current)
                current = []
            continue
        if math.isfinite(value):
            current.append(value)
    if current:
        segments.append(current)
    return segments


def load_scores(path=PREDICTION_FILE):
    filename = path.relative_to(ROOT).as_posix()
    if not path.is_file():
        raise FileNotFoundError(f"找不到预测文件：{filename}")
    segments = _prediction_segments(_xlsx_values(path))
    if path == DEMO_PREDICTION_FILE:
        if len(segments) not in (1, 2):
            raise ValueError(f"{filename} 的 A 列应有一段连续分数，或用空行分隔的低、高负荷两段")
    elif len(segments) != 2:
        raise ValueError(f"{filename} 的 A 列应有用空行分隔的低、高负荷两段")
    scores = segments[-1]
    if any(not 0 <= value <= 100 for value in scores):
        raise ValueError(f"{filename} 的分数应在 0–100 范围内")
    return scores, filename, "S3 高负荷预测" if len(segments) == 2 else "S3 预测结果"


class DemoReplay:
    def __init__(self, live_eeg=None):
        self.live_eeg = live_eeg
        self.server_id = SERVER_ID + ("-live" if live_eeg else "")
        self.prediction_path = DEMO_PREDICTION_FILE if live_eeg else PREDICTION_FILE
        self.prediction_file = self.prediction_path.relative_to(ROOT).as_posix()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.thread = None
        self.state = {
            "status": "idle",
            "source": "local-demo",
            "message": "等待开始本地演示",
            "condition": CONDITION,
            "record": None,
            "predictionFile": self.prediction_file,
            "predictionSegment": None,
            "prediction": None,
            "scoreHistory": [],
            "eeg": None,
            "elapsedSeconds": 0,
            "totalSeconds": 0,
        }

    def snapshot(self):
        with self.lock:
            state = dict(self.state, eegSource="live" if self.live_eeg else "replay")
            if self.live_eeg:
                state.update(self.live_eeg.snapshot())
                state["eegLog"] = str(EEG_LOG_PATH)
            return state

    def start(self, condition=CONDITION):
        if condition not in CONDITIONS:
            raise ValueError("本地演示使用 S3 高负荷数据（diff）")
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("演示正在播放，请先停止")
            self.stop_event.clear()
            self.pause_event.clear()
            self.state.pop("error", None)
            self.state.update(
                status="loading", message="读取预测文件…" if self.live_eeg else "读取本地 EEG 与预测文件…", condition=condition,
                record=None, prediction=None, scoreHistory=[], eeg=None,
                predictionSegment=None,
                elapsedSeconds=0, totalSeconds=0,
            )
            self.thread = threading.Thread(target=self._run, args=(condition,), daemon=True)
            self.thread.start()

    def pause(self):
        self.pause_event.set()
        with self.lock:
            if self.state["status"] == "running":
                self.state["status"] = "paused"

    def resume(self):
        self.pause_event.clear()
        with self.lock:
            if self.state["status"] == "paused":
                self.state["status"] = "running"

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()

    def _run(self, condition):
        try:
            record = None if self.live_eeg else load_record(condition)
            scores, prediction_file, prediction_segment = load_scores(self.prediction_path)
            if self.live_eeg:
                sample_interval = DEMO_SCORE_INTERVAL_SECONDS
                playback_duration = len(scores) * sample_interval
            else:
                playback_duration = record["durationSeconds"]
                sample_interval = playback_duration / max(1, len(scores) - 1)
            times = np.arange(len(scores)) * sample_interval
            public_record = {k: v for k, v in record.items() if k != "data"} if record else None
            with self.lock:
                self.state.update(
                    status="running", message="预测回放中" if self.live_eeg else "本地数据播放中", record=public_record,
                    predictionFile=prediction_file, predictionSegment=prediction_segment,
                    totalSeconds=playback_duration,
                )
            start_clock = time.perf_counter()
            paused_total = 0.0
            pause_started = None
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    if pause_started is None:
                        pause_started = time.perf_counter()
                    time.sleep(0.05)
                    continue
                if pause_started is not None:
                    paused_total += time.perf_counter() - pause_started
                    pause_started = None
                elapsed = min(playback_duration, max(0.0, time.perf_counter() - start_clock - paused_total))
                eeg = replay_eeg_window(record, elapsed) if record else None
                # Hold each score until the next position on the replay timeline.
                score_index = min(len(scores) - 1, int(math.floor(elapsed / sample_interval + 1e-9)))
                score = float(scores[score_index])
                history = [{"time": float(t), "score": float(v)} for t, v in zip(times[:score_index + 1], scores)]
                with self.lock:
                    self.state.update(
                        status="running", elapsedSeconds=elapsed,
                        eeg=eeg, scoreHistory=history,
                        prediction={
                            "score": score,
                            "time": elapsed,
                            "source": prediction_file,
                            "segment": prediction_segment,
                        },
                    )
                if elapsed >= playback_duration:
                    break
                time.sleep(0.1)
            with self.lock:
                self.state["status"] = "stopped" if self.stop_event.is_set() else "completed"
                self.state["message"] = "播放已停止" if self.stop_event.is_set() else "片段播放完毕"
        except Exception as exc:
            with self.lock:
                self.state.update(status="error", message=str(exc), error=str(exc))


def replay_eeg_window(record, elapsed):
    rate = record["srate"]
    end_sample = min(record["pnts"], int(round(elapsed * rate)))
    start_sample = max(0, end_sample - int(round(DISPLAY_WINDOW_SECONDS * rate)))
    samples = record["data"][:, start_sample:end_sample:DISPLAY_DECIMATION]
    return {
        "channels": record["channels"],
        "samples": np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0).tolist(),
        "sampleRate": rate / DISPLAY_DECIMATION,
        "units": "uV",
        "startSeconds": start_sample / rate,
        "endSeconds": end_sample / rate,
    }


def make_handler(replay: DemoReplay):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def log_message(self, *_args):
            return

        def respond(self, data, status=200):
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/demo/state":
                return self.respond(replay.snapshot())
            if path == "/api/demo/config":
                return self.respond({
                    "source": "local-demo",
                    "serverId": replay.server_id,
                    "eegSource": "live" if replay.live_eeg else "replay",
                    "session": SESSION_ID,
                    "conditions": {k: {"name": v["name"], "label": v["label"]} for k, v in CONDITIONS.items()},
                    "predictionFile": replay.prediction_file,
                })
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 4096)
                body = json.loads(self.rfile.read(length) or b"{}")
                if path == "/api/demo/start":
                    replay.start(body.get("condition", CONDITION))
                elif path == "/api/demo/pause":
                    replay.pause()
                elif path == "/api/demo/resume":
                    replay.resume()
                elif path == "/api/demo/stop":
                    replay.stop()
                elif path == "/api/demo/eeg/record/start" and replay.live_eeg:
                    replay.live_eeg.start_recording()
                elif path == "/api/demo/eeg/record/stop" and replay.live_eeg:
                    replay.live_eeg.stop_recording()
                else:
                    return self.respond({"error": "unknown endpoint"}, 404)
                return self.respond(replay.snapshot())
            except Exception as exc:
                if path.startswith("/api/demo/eeg/record/"):
                    EEG_LOG.warning("Recording API failed path=%s: %s", path, exc, exc_info=True)
                return self.respond({"error": str(exc)}, 400)

    return Handler


class DemoHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        # Windows SO_REUSEADDR can let two listeners bind the same port.
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def is_current_demo(port, server_id=SERVER_ID):
    connection = HTTPConnection("127.0.0.1", port, timeout=0.5)
    try:
        connection.request("GET", "/api/demo/config")
        response = connection.getresponse()
        if response.status == 200:
            config = json.loads(response.read(16384))
            return isinstance(config, dict) and config.get("serverId") == server_id
    except (OSError, HTTPException, ValueError):
        pass
    finally:
        connection.close()
    return False


def bind_demo(replay, port, open_browser):
    # Try the next fixed port so later launches can find and reuse the fallback.
    ports = [port]
    if open_browser and port > 0:
        if port < 65535:
            ports.append(port + 1)
        ports.append(0)
    for candidate in ports:
        try:
            server = DemoHTTPServer(("127.0.0.1", candidate), make_handler(replay))
            return server, server.server_port
        except OSError as exc:
            in_use = (exc.errno in (errno.EADDRINUSE, errno.EACCES)
                      or getattr(exc, "winerror", None) in (10048, 10013))
            if not open_browser or not in_use or not candidate:
                raise SystemExit(f"Cannot start the local demo server on 127.0.0.1:{candidate}: {exc}") from exc
            if is_current_demo(candidate, replay.server_id):
                print(f"Reusing the running MATB demo on port {candidate}.", flush=True)
                return None, candidate
            print(f"Port {candidate} is occupied. Trying another port.", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--live-eeg", action="store_true")
    args = parser.parse_args()
    replay = DemoReplay(LiveEEG() if args.live_eeg else None)
    server, port = bind_demo(replay, args.port, args.open_browser)
    base_url = f"http://127.0.0.1:{port}"
    main_url = f"{base_url}/MATB_Game_v2.html?demo=1"
    print(f"Main screen: {main_url}", flush=True)
    print(f"Monitor:     {base_url}/MATB_Monitor_v2.html?demo=1", flush=True)
    if server is None:
        webbrowser.open(main_url)
        return
    try:
        if replay.live_eeg:
            EEG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(EEG_LOG_PATH, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            EEG_LOG.addHandler(handler)
            EEG_LOG.setLevel(logging.INFO)
            print(f"Live EEG log: {EEG_LOG_PATH}", flush=True)
            EEG_LOG.info("Live EEG started endpoint=%s sample_rate=%d channels=%d",
                         replay.live_eeg.endpoint, replay.live_eeg.sample_rate, replay.live_eeg.channel_count)
            replay.live_eeg.thread.start()
        if args.open_browser:
            webbrowser.open(main_url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        replay.stop()
        if replay.live_eeg and replay.live_eeg.thread.ident is not None:
            replay.live_eeg.stopping.set()
            replay.live_eeg.thread.join(timeout=3)
        server.server_close()


if __name__ == "__main__":
    main()
