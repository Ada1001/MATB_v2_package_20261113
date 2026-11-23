"""Train personal AGMNet on S1/S2 only, using causal preprocessing."""
import argparse
import copy
import csv
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

try:
    from .common import (CHANNELS, build_model, classification_metrics, fit_stats,
                         model_code_hashes, normalize, seed_all, sha, spd_features, write_json)
    from .data import source_windows
except ImportError:
    from common import (CHANNELS, build_model, classification_metrics, fit_stats,
                        model_code_hashes, normalize, seed_all, sha, spd_features, write_json)
    from data import source_windows
from src.cl_tsmnet.training import make_optimizer


@torch.no_grad()
def logits_for(model, x, domains, config, batch_size):
    model.eval()
    result = []
    for i in range(0, len(x), batch_size):
        xb = torch.from_numpy(normalize(x[i:i+batch_size], config)).to(model.graph_device)
        db = torch.tensor(domains[i:i+batch_size], dtype=torch.long, device=model.graph_device)
        result.append(model(xb, db).cpu())
    return torch.cat(result)


def source_refit(model, dataset, train, config, batch_size, include_target=False):
    model.eval()
    features = spd_features(model, dataset['x'][train], config, batch_size)
    for domain in (1, 2):
        mask = dataset['domains'][train] == domain
        fit_stats(model, features[mask], domain)
    if include_target:
        # Cold-start fallback derives from S1/S2 TRAINING features only.
        fit_stats(model, features, 3)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', default='data/COG-BCI')
    p.add_argument('--subject', type=int, default=1)
    p.add_argument('--output-dir', default='online_agmnet/artifacts/sub01')
    p.add_argument('--device', default='cpu')
    p.add_argument('--epochs', type=int, default=30)
    p.add_argument('--patience', type=int, default=8)
    p.add_argument('--batch-size', type=int, default=16)
    p.add_argument('--lr', type=float, default=.001)
    p.add_argument('--weight-decay', type=float, default=.0001)
    p.add_argument('--val-fraction', type=float, default=.2)
    p.add_argument('--hidden', type=int, default=64)
    p.add_argument('--subspace', type=int, default=20)
    p.add_argument('--fusion-dim', type=int, default=128)
    p.add_argument('--max-abs-uv', type=float, default=300)
    p.add_argument('--min-std-uv', type=float, default=.1)
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--limit-train-per-record', type=int, default=0, help='Smoke test only; 0 means all')
    p.add_argument('--limit-val-per-record', type=int, default=0, help='Smoke test only; 0 means all')
    args = p.parse_args()
    if args.batch_size < 4 or args.batch_size % 2 or args.epochs < 1 or args.patience < 1:
        p.error('Use even batch-size >=4 and positive epochs/patience')
    if not .05 <= args.val_fraction <= .4 or args.hidden % 4 or args.subspace > args.hidden+1:
        p.error('val-fraction [.05,.4], hidden divisible by 4, subspace <=hidden+1 required')
    if min(args.hidden, args.subspace, args.fusion_dim, args.threads) < 1 or args.max_abs_uv <= 0 or args.min_std_uv < 0:
        p.error('Positive architecture/thread/amplitude parameters required; min-std-uv >=0')
    out = Path(args.output_dir)
    if (out/'config.json').exists() or (out/'history.csv').exists():
        raise FileExistsError('Training output already exists; choose a fresh directory')
    out.mkdir(parents=True, exist_ok=True)
    seed_all(args.seed, args.threads)
    quality = dict(max_abs_uv=args.max_abs_uv, min_std_uv=args.min_std_uv)
    print('Loading S1/S2 only; causal filtering, recording-tail validation, no S3 access', flush=True)
    dataset = source_windows(args.data_dir, args.subject, quality, args.val_fraction,
                             args.limit_train_per_record, args.limit_val_per_record)
    train, val = np.flatnonzero(dataset['roles'] == 'train'), np.flatnonzero(dataset['roles'] == 'val')
    for role, idx in [('train', train), ('val', val)]:
        if set(dataset['y'][idx]) != {0, 1} or set(dataset['domains'][idx]) != {1, 2}:
            raise RuntimeError('Missing class or session in '+role+' after signal rejection')
    flat = dataset['x'][train].transpose(1, 0, 2).reshape(len(CHANNELS), -1)
    center = np.median(flat, axis=1)
    scale = 1.4826*np.median(np.abs(flat-center[:, None]), axis=1)
    if np.any(scale <= 1e-9):
        raise ValueError('Degenerate source normalization; inspect channels')
    config = dict(schema_version=1, subject=args.subject, channels=CHANNELS, input_fs=500, target_fs=250,
                  window_seconds=1., stride_seconds=.5, units='volts', labels={'0': 'easy', '1': 'difficult'},
                  architecture=dict(temporal_hidden=args.hidden, graph_hidden=args.hidden,
                                    subspacedims=args.subspace, fusion_dim=args.fusion_dim),
                  quality=quality, normalizer=dict(center=center.tolist(), scale=scale.tolist()),
                  preprocessing='causal_sos_1_45Hz_notch49_51Hz_antialias80Hz_decimate2_v1',
                  temperature=1., model_code_sha256=model_code_hashes(), seed=args.seed,
                  smoke_test=bool(args.limit_train_per_record or args.limit_val_per_record),
                  selection='S1/S2 per-recording chronological tail source validation loss',
                  source_sessions=[1, 2], target_session=3, training_options=vars(args))
    write_json(out/'source_audit.json', dict(recordings=dataset['audit'], n_train=len(train), n_val=len(val),
                                           target_data_used=False, target_labels_used=False))
    with (out/'source_windows.csv').open('w', newline='') as stream:
        w = csv.writer(stream)
        w.writerow(['record_id', 'end_raw_sample', 'role', 'label', 'domain'])
        w.writerows(zip(dataset['record_ids'], dataset['ends'], dataset['roles'], dataset['y'], dataset['domains']))
    model = build_model(config, args.device)
    optimizer = make_optimizer(model, args.lr, args.weight_decay, model_type='ms_tgc_spddsbn')
    best, best_loss, bad, history = None, float('inf'), 0, []
    rng = np.random.default_rng(args.seed)
    half = args.batch_size//2
    pools = [train[dataset['domains'][train] == d] for d in (1, 2)]
    for epoch in range(1, args.epochs+1):
        model.train()
        pools = [rng.permutation(v) for v in pools]
        steps = int(np.ceil(max(map(len, pools))/half))
        losses = []
        for step in range(steps):
            idx = np.concatenate([v[np.arange(step*half, (step+1)*half) % len(v)] for v in pools])
            xb = torch.from_numpy(normalize(dataset['x'][idx], config)).to(model.graph_device)
            db = torch.tensor(dataset['domains'][idx], dtype=torch.long, device=model.graph_device)
            yb = torch.tensor(dataset['y'][idx], dtype=torch.long, device=model.graph_device)
            optimizer.zero_grad()
            loss = F.cross_entropy(model(xb, db), yb)
            if not torch.isfinite(loss):
                raise RuntimeError('Non-finite training loss; no checkpoint published')
            loss.backward()
            if any(v.grad is not None and not torch.isfinite(v.grad).all() for v in model.parameters()):
                raise RuntimeError('Non-finite gradient; inspect SPD numerics')
            optimizer.step()
            losses.append(float(loss.detach()))
        source_refit(model, dataset, train, config, args.batch_size)
        logits = logits_for(model, dataset['x'][val], dataset['domains'][val], config, args.batch_size)
        labels = torch.tensor(dataset['y'][val], dtype=torch.long)
        val_loss = float(F.cross_entropy(logits, labels))
        metrics = classification_metrics(labels.numpy(), logits.softmax(1).numpy())
        row = dict(epoch=epoch, train_loss=float(np.mean(losses)), val_loss=val_loss,
                   val_accuracy=metrics['accuracy'], val_bacc=metrics['balanced_accuracy'])
        history.append(row)
        print(row, flush=True)
        with (out/'history.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerows(history)
        if val_loss < best_loss-1e-6:
            best, best_loss, bad = copy.deepcopy(model.state_dict()), val_loss, 0
            config['best_epoch'] = epoch
        else:
            bad += 1
            if bad >= args.patience:
                break
    model.load_state_dict(best)
    source_refit(model, dataset, train, config, args.batch_size, include_target=True)
    logits = logits_for(model, dataset['x'][val], dataset['domains'][val], config, args.batch_size)
    labels = torch.tensor(dataset['y'][val], dtype=torch.long)
    candidates = [.5, .75, 1., 1.5, 2., 3., 5.]
    config['temperature'] = min(candidates, key=lambda t: float(F.cross_entropy(logits/t, labels)))
    config['confidence_note'] = 'Softmax score; temperature selected on S1/S2 source validation only, not a target correctness guarantee'
    config['validation_metrics'] = classification_metrics(labels.numpy(), (logits/config['temperature']).softmax(1).numpy())
    config['streaming_code_sha256'] = sha(Path(__file__).with_name('streaming.py'))
    torch.save({k: v.detach().cpu() for k, v in model.state_dict().items()}, out/'model.pt')
    config['checkpoint_sha256'] = sha(out/'model.pt')
    write_json(out/'config.json', config)
    print('Saved personal model:', out, '; source-only validation:', config['validation_metrics'], flush=True)


if __name__ == '__main__':
    main()
