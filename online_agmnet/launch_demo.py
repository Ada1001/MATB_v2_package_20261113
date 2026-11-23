"""Start the inference-enabled local demo with this Python environment."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
import json

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8765'


def ready():
    try:
        with urllib.request.urlopen(URL+'/api/agmnet/state', timeout=1) as response:
            return 'source' in json.load(response)
    except Exception:
        return False


def main():
    if not ready():
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 8765)) == 0:
                raise RuntimeError('Port 8765 is occupied by another server. Close the old MATB static server and retry.')
        import torch, geoopt, mne, scipy
        logs = ROOT/'online_agmnet/results'
        logs.mkdir(exist_ok=True)
        with (logs/'server.log').open('ab') as log:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            subprocess.Popen([sys.executable, str(ROOT/'online_agmnet/demo_server.py')],
                             cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
        for _ in range(40):
            if ready():
                break
            time.sleep(.25)
        else:
            raise RuntimeError('Server failed to start. See online_agmnet/results/server.log')
    webbrowser.open(URL+'/MATB_Game_v2.html')
    print('Demo opened. Use the monitor button to open the secondary screen.')
    print('Start Experiment also starts EEG replay: difficulty 1-2 Easy, 3-5 Difficult.')


if __name__ == '__main__':
    main()
