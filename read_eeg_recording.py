"""Load preserved samples and separately saved MATB markers (no signal processing)."""
import argparse
import csv
import json
from pathlib import Path
import numpy as np

def load_recording(folder):
    folder=Path(folder)
    meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    channels=meta['config']['channels'];count=(folder/'raw_eeg.f32').stat().st_size//(channels*4)
    if count!=meta['sampleCount']:print('Warning: metadata count differs from raw file; loading all complete samples (possible interrupted recording).')
    samples=np.memmap(folder/'raw_eeg.f32',dtype='<f4',mode='r',shape=(count,channels)) if count else np.empty((0,channels),dtype=np.float32)
    if (folder/'markers.csv').exists():
        with (folder/'markers.csv').open(encoding='utf-8-sig',newline='') as f:markers=list(csv.DictReader(f))
    elif meta.get('schema') == 'matb-demo-eeg-recording-v1':
        markers=[]
    else:
        markers=[json.loads(line) for line in (folder/'markers.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    return samples,markers,meta

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);parser.add_argument('--export-npy',action='store_true');args=parser.parse_args()
    samples,markers,meta=load_recording(args.folder)
    print('EEG shape:',samples.shape,'sampling rate:',meta['config']['sampleRate'],'markers:',len(markers))
    print('Trigger index:',meta['config']['triggerIndex'],'units: native_unspecified')
    print('Sample matching is estimated; read metadata and receive_chunks.csv for timing limitations.')
    if args.export_npy:np.save(args.folder/'raw_eeg.npy',samples)
