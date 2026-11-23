"""Model and artifact utilities shared by training and deployment."""
import hashlib
import json
from pathlib import Path
import random
import sys

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PROJECT = HERE/'vendor' if (HERE/'vendor').is_dir() else HERE.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT/'TSMNet'))
from src.cl_tsmnet.datasets import COG_EEG_CHANNELS
from src.cl_tsmnet.training import build_ms_tgc_spddsbn

CHANNELS = list(COG_EEG_CHANNELS)


def seed_all(seed=42, threads=1):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def model_code_hashes():
    paths = sorted((PROJECT/'src/cl_tsmnet').glob('*.py'))
    paths += sorted((PROJECT/'TSMNet/spdnets').glob('*.py'))
    return {str(p.relative_to(PROJECT)).replace('\\', '/'): sha(p) for p in paths}


def build_model(config, device='cpu'):
    if str(device).startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable; use --device cpu')
    return build_ms_tgc_spddsbn(
        PROJECT, nchannels=len(config['channels']), nsamples=config['target_fs'],
        nclasses=2, domains=[1, 2, 3], variant='ms_tgc_spddsbn',
        **config['architecture']).to(torch.device(device))


def normalize(windows, config):
    center = np.asarray(config['normalizer']['center'], dtype=np.float32)
    scale = np.asarray(config['normalizer']['scale'], dtype=np.float32)
    return ((windows-center[..., None])/scale[..., None]).astype(np.float32)


def load_artifact(folder, device='cpu'):
    folder = Path(folder)
    config = json.loads((folder/'config.json').read_text(encoding='utf-8'))
    if config.get('schema_version') != 1:
        raise ValueError('Unsupported artifact schema')
    for name, expected in config['model_code_sha256'].items():
        if sha(PROJECT/name) != expected:
            raise ValueError('Model code differs from training artifact: '+name)
    if sha(folder/'model.pt') != config['checkpoint_sha256']:
        raise ValueError('Checkpoint checksum mismatch')
    if sha(HERE/'streaming.py') != config['streaming_code_sha256']:
        raise ValueError('Streaming preprocessing differs from training artifact')
    model = build_model(config, device)
    model.load_state_dict(torch.load(folder/'model.pt', map_location='cpu', weights_only=True), strict=True)
    model.eval()
    return model, config


@torch.no_grad()
def spd_features(model, windows, config, batch_size=16):
    output = []
    for start in range(0, len(windows), batch_size):
        x = torch.from_numpy(normalize(windows[start:start+batch_size], config)).to(model.graph_device)
        output.append(model.spd_branch.manifold_features(model._weighted_graph_maps(x)).detach().cpu())
    if not output:
        raise ValueError('Cannot estimate SPD statistics from zero windows')
    return torch.cat(output)


def fit_stats(model, features, domain, preserve_variance=False):
    """Fit one domain; leave shared learned parameters and other domains intact."""
    bn = model.spddsbnorm.get_domain_obj(torch.tensor(domain))
    previous = bn.running_var_test.detach().clone()
    model.eval()
    model.spd_branch.refit_domain_features(features, torch.full((len(features),), domain, dtype=torch.long))
    if preserve_variance:
        with torch.no_grad():
            bn.running_var.copy_(previous)
            bn.running_var_test.copy_(previous)


def classification_metrics(y, probabilities):
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score, log_loss
    y, probabilities = np.asarray(y, dtype=int), np.asarray(probabilities, dtype=float)
    if not len(y):
        return {'n_windows': 0}
    if (not np.isfinite(probabilities).all() or (probabilities < 0).any() or
            not np.allclose(probabilities.sum(axis=1), 1., atol=1e-5, rtol=0)):
        raise ValueError('Invalid class probabilities')
    probabilities = probabilities/probabilities.sum(axis=1, keepdims=True)
    predictions = probabilities.argmax(axis=1)
    result = dict(n_windows=len(y), accuracy=float(accuracy_score(y, predictions)),
                  balanced_accuracy=float(balanced_accuracy_score(y, predictions)) if len(np.unique(y)) == 2 else None,
                  macro_f1=float(f1_score(y, predictions, labels=[0, 1], average='macro', zero_division=0)),
                  confusion_matrix=confusion_matrix(y, predictions, labels=[0, 1]).tolist(),
                  nll=float(log_loss(y, probabilities, labels=[0, 1])),
                  class_counts={str(c): int((y == c).sum()) for c in (0, 1)})
    return result
