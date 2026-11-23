"""Start the correct local bridge without silently reusing an old static server."""
import json
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

def ready():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8766/api/eeg/status',timeout=1) as response:
            status = json.load(response)
            return 'clockDomain' in status and 'diagnosticLog' in status
    except Exception:return False

def main():
    root=Path(__file__).resolve().parent
    if not ready():
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',8766))==0:
                print('Port 8766 is occupied by an older MATB bridge or another application.')
                print('Stop the process listening on port 8766, then run start_experiment.bat again.')
                return 1
        flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
        process=subprocess.Popen([sys.executable,str(root/'eeg_bridge.py'),'--port','8766'],cwd=root,creationflags=flags)
        for _ in range(50):
            if ready():break
            if process.poll() is not None:
                print('EEG bridge failed to start. Run python eeg_bridge.py for details.');return 1
            time.sleep(.1)
        else:print('EEG bridge startup timeout.');return 1
    webbrowser.open('http://127.0.0.1:8766/MATB_Experiment.html')
    print('MATB EEG bridge opened. Recordings are saved under: '+str(root/'recordings'))
    print('EEG diagnostics: '+str(root/'logs'/'eeg_bridge.log'))
    return 0

if __name__=='__main__':sys.exit(main())
