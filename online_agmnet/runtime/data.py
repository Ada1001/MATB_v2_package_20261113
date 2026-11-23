"""COG-BCI input adapter. Ground-truth labels stay outside OnlinePredictor."""
import io
from pathlib import Path
import tempfile
import zipfile

import numpy as np
from scipy.io import loadmat

try:
    from .common import CHANNELS
    from .streaming import CausalWindows, quality_reason
except ImportError:
    from common import CHANNELS
    from streaming import CausalWindows, quality_reason

TASKS = {'MATBeasy': 0, 'MATBdiff': 1}


def recording_order(data_dir, subject, session):
    notebook = loadmat(Path(data_dir)/'notebook.mat', simplify_cells=True)['notebook']
    note = notebook[f'SBJ_{subject}'][f'SESS_{session}']
    order = np.asarray(note['Order']).ravel().tolist()
    if order.count(6) != 1 or order.count(8) != 1:
        raise ValueError('Notebook does not uniquely identify MATB easy/difficult order')
    return ['MATBeasy' if code == 6 else 'MATBdiff' for code in order if code in (6, 8)]


def load_record(data_dir, subject, session, task):
    import mne
    if task not in TASKS:
        raise ValueError('Only MATB easy/difficult is supported')
    archive = Path(data_dir)/f'sub-{subject:02d}.zip'
    suffix = f'/ses-S{session}/eeg/{task}.set'
    with zipfile.ZipFile(archive) as z:
        found = [n for n in z.namelist() if n.replace('\\', '/').endswith(suffix)]
        if len(found) != 1:
            raise FileNotFoundError(f'Expected one {suffix} in {archive}, got {len(found)}')
        name = found[0]
        fdt = name[:-4]+'.fdt'
        header = loadmat(io.BytesIO(z.read(name)), simplify_cells=True)
        header = header.get('EEG', header)
        if float(header['srate']) != 500 or int(header['trials']) != 1:
            raise ValueError('Expected continuous 500 Hz COG-BCI recording')
        if z.getinfo(fdt).file_size != int(header['nbchan'])*int(header['pnts'])*4:
            raise ValueError('EEGLAB signal size mismatch')
        with tempfile.TemporaryDirectory(prefix='agmnet_eeg_') as temporary:
            # Write basenames only; never extract untrusted archive paths.
            for member in (name, fdt):
                with z.open(member) as source, (Path(temporary)/Path(member).name).open('wb') as dest:
                    import shutil
                    shutil.copyfileobj(source, dest)
            raw = mne.io.read_raw_eeglab(Path(temporary)/Path(name).name, preload=True, verbose='ERROR')
            missing = set(CHANNELS)-set(raw.ch_names)
            if missing:
                raise ValueError('Missing model channels: '+str(sorted(missing)))
            eeg = raw.get_data(picks=[raw.ch_names.index(ch) for ch in CHANNELS])
    if not np.isfinite(eeg).all():
        raise ValueError('Non-finite raw signal; explicit gap handling required')
    events = header.get('event', [])
    events = [events] if isinstance(events, dict) else np.asarray(events, dtype=object).ravel()
    if any(str(e.get('type', '')).lower() == 'boundary' and .5 < float(e['latency']) < eeg.shape[1] for e in events):
        raise ValueError('Interior recording boundary: split the input into continuous streams first')
    return eeg, dict(subject=subject, session=session, task=task, label=TASKS[task],
                     record_id=f'sub-{subject:02d}_S{session}_{task}', n_raw_samples=eeg.shape[1],
                     input_fs=500, channels=CHANNELS, signal_zip_crc32=z_crc(archive, fdt))


def z_crc(archive, name):
    with zipfile.ZipFile(archive) as z:
        return z.getinfo(name).CRC


def source_windows(data_dir, subject, quality, val_fraction=.2, limit_train=0, limit_val=0):
    """Tail validation per S1/S2 recording, >=1-second guard, warmup excluded.

    Filtering uses exactly the same live API. No S3 input is opened here.
    """
    xs, ys, domains, roles, ends, identifiers = [], [], [], [], [], []
    audit = []
    for session in (1, 2):
        for task in recording_order(data_dir, subject, session):
            raw, meta = load_record(data_dir, subject, session, task)
            cutoff = int(raw.shape[1]*(1-val_fraction))
            processor = CausalWindows()
            counts = dict(train=0, val=0, warmup=0, guard=0, rejected=0, limited=0)
            for start in range(0, raw.shape[1], 50):
                for item in processor.feed(raw[:, start:start+50]):
                    if item['end_seconds'] <= 2:
                        counts['warmup'] += 1
                        continue
                    if quality_reason(item['window'], quality):
                        counts['rejected'] += 1
                        continue
                    if item['end_sample'] <= cutoff-500:
                        role = 'train'
                    elif item['start_sample'] >= cutoff:
                        role = 'val'
                    else:
                        counts['guard'] += 1
                        continue
                    limit = limit_train if role == 'train' else limit_val
                    if limit and counts[role] >= limit:
                        counts['limited'] += 1
                        continue
                    xs.append(item['window'])
                    ys.append(meta['label'])
                    domains.append(session)
                    roles.append(role)
                    ends.append(item['end_sample'])
                    identifiers.append(meta['record_id'])
                    counts[role] += 1
            audit.append(dict(**meta, validation_cutoff_raw_sample=cutoff, counts=counts))
    if not xs:
        raise RuntimeError('No clean source windows; inspect channel units and quality thresholds')
    return dict(x=np.stack(xs), y=np.array(ys, dtype=np.int64), domains=np.array(domains, dtype=np.int64),
                roles=np.array(roles), ends=np.array(ends), record_ids=identifiers, audit=audit)
