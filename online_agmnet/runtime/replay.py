"""Causal session-3 replay and post-hoc evaluation of easy/difficult predictions."""
import argparse
import csv
from pathlib import Path
import time

import numpy as np

try:
    from .common import classification_metrics, load_artifact, seed_all, write_json
    from .data import load_record, recording_order
    from .streaming import OnlinePredictor
except ImportError:
    from common import classification_metrics, load_artifact, seed_all, write_json
    from data import load_record, recording_order
    from streaming import OnlinePredictor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact', default='online_agmnet/artifacts/sub01')
    p.add_argument('--data-dir', default='data/COG-BCI')
    p.add_argument('--output-dir', default='online_agmnet/results/sub01_s3')
    p.add_argument('--device', default='cpu')
    p.add_argument('--calibration-seconds', type=float, default=60.)
    p.add_argument('--spd-calibration', choices=['none', 'mean', 'full'], default='mean')
    p.add_argument('--ema-alpha', type=float, default=.6, help='Weight of new probability; 1 disables smoothing')
    p.add_argument('--online-spd-rate', type=float, default=0., help='Post-prediction mean-only geodesic update; experimental [0,.05]')
    p.add_argument('--packet-samples', type=int, default=50)
    p.add_argument('--realtime', action='store_true', help='Pace packet delivery at recorded 500 Hz (about 10 minutes)')
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--log-every', type=int, default=1, help='Print every N scored windows; default prints each 0.5s prediction')
    args = p.parse_args()
    if not 1 <= args.packet_samples <= 250:
        p.error('packet-samples must be 1..250 (<=0.5 second packet latency)')
    out = Path(args.output_dir)
    if (out/'predictions.csv').exists():
        raise FileExistsError('Replay output exists; choose a fresh output directory')
    out.mkdir(parents=True, exist_ok=True)
    seed_all(args.seed, args.threads)
    model, config = load_artifact(args.artifact, args.device)
    order = recording_order(args.data_dir, config['subject'], 3)
    print('S3 notebook order:', order, flush=True)
    print('Calibration is an unlabeled prefix of the FIRST arriving selected recording only.', flush=True)
    if args.spd_calibration != 'none' and args.calibration_seconds:
        print('WARNING: prefix covers one task condition; mean-only adaptation may help or hurt. Compare frozen baseline.', flush=True)
    predictor = OnlinePredictor(model, config, args.calibration_seconds, args.spd_calibration,
                                args.ema_alpha, args.online_spd_rate)
    predictions, all_rows, records, packet_times = [], [], [], []
    fields = ['record_id', 'start_sample', 'end_sample', 'end_seconds', 'phase', 'quality',
              'prediction', 'label', 'confidence', 'p_easy', 'p_difficult', 'raw_prediction',
              'raw_confidence', 'raw_p_easy', 'raw_p_difficult', 'spd_updated_after_prediction',
              'processing_ms', 'emission_raw_sample', 'delivery_lag_ms', 'realtime_lag_ms', 'true_label']
    with (out/'predictions.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for task in order:
            raw, meta = load_record(args.data_dir, config['subject'], 3, task)
            records.append(meta)
            predictor.start_record(meta['record_id'])
            clock_start = time.perf_counter()
            for start in range(0, raw.shape[1], args.packet_samples):
                stop = min(start+args.packet_samples, raw.shape[1])
                if args.realtime:
                    deadline = clock_start+stop/500.
                    while time.perf_counter() < deadline:
                        time.sleep(min(.02, max(0., deadline-time.perf_counter())))
                begin = time.perf_counter()
                outputs = predictor.feed(raw[:, start:stop], config['channels'])
                packet_times.append((time.perf_counter()-begin)*1000)
                for row in outputs:
                    # Labels enter here, AFTER the inference engine has returned.
                    row.update(true_label=meta['label'], emission_raw_sample=stop,
                               delivery_lag_ms=(stop-row['end_sample'])/500.*1000,
                               realtime_lag_ms=max(0., (time.perf_counter()-clock_start-row['end_seconds'])*1000)
                               if args.realtime else None)
                    all_rows.append(row)
                    if row['phase'] == 'test':
                        predictions.append(row)
                        if len(predictions) % max(1, args.log_every) == 0:
                            print(f"{row['record_id']} t={row['end_seconds']:.1f}s "
                                  f"prediction={row['label']} confidence={row['confidence']:.3f} "
                                  f"processing={row['processing_ms']:.1f}ms", flush=True)
                    writer.writerow(row)
                stream.flush()
    if not predictor.calibrated:
        raise RuntimeError('Calibration did not finish')
    if not predictions:
        raise RuntimeError('No scored S3 predictions; inspect quality counts in predictions.csv')
    y = [r['true_label'] for r in predictions]
    smoothed = [[r['p_easy'], r['p_difficult']] for r in predictions]
    raw_prob = [[r['raw_p_easy'], r['raw_p_difficult']] for r in predictions]
    phases = {phase: sum(r['phase'] == phase for r in all_rows) for phase in sorted({r['phase'] for r in all_rows})}
    measured = np.array([r['processing_ms'] for r in predictions])
    candidate_test = len(predictions)+phases.get('rejected', 0)
    metrics = dict(subject=config['subject'], test_session=3, task_order=order,
                   scored_window_metrics=classification_metrics(y, smoothed),
                   raw_window_metrics=classification_metrics(y, raw_prob),
                   per_record={record['record_id']: classification_metrics(
                       [r['true_label'] for r in predictions if r['record_id'] == record['record_id']],
                       [[r['p_easy'], r['p_difficult']] for r in predictions if r['record_id'] == record['record_id']])
                       for record in records},
                   phases=phases, valid_test_coverage=len(predictions)/candidate_test,
                   conservative_accuracy_rejections_as_errors=sum(r['prediction'] == r['true_label'] for r in predictions)/candidate_test,
                   processing_ms=dict(median=float(np.median(measured)), p95=float(np.quantile(measured, .95)),
                                      max=float(measured.max()), fraction_over_500ms=float((measured > 500).mean())),
                   packet_processing_ms=dict(p95=float(np.quantile(packet_times, .95)), max=float(max(packet_times))),
                   realtime_lag_ms=dict(p95=float(np.quantile([r['realtime_lag_ms'] for r in predictions], .95)),
                                        max=max(r['realtime_lag_ms'] for r in predictions)) if args.realtime else None,
                   runtime=dict(device=args.device, realtime_pacing=args.realtime, packet_samples=args.packet_samples),
                   options=vars(args), calibration_events=predictor.events,
                   checkpoint_sha256=config['checkpoint_sha256'], smoke_test_model=config['smoke_test'],
                   limitations=['Overlapping windows are correlated; no independent-window significance claim.',
                                'Confidence is a temperature-scaled model score, not guaranteed correctness.',
                                'Only selected MATB tasks replayed; intervening tasks/gaps are not reconstructed.',
                                'Compute time excludes device transport and file loading. Filters are causal and introduce delay.',
                                'S3 calibration is single-condition and unlabeled; improvement is not guaranteed.'])
    write_json(out/'metrics.json', metrics)
    write_json(out/'recordings.json', records)
    write_json(out/'calibration_events.json', predictor.events)
    print('Scored S3 metrics:', metrics['scored_window_metrics'], flush=True)
    print('Raw prediction metrics:', metrics['raw_window_metrics'], flush=True)
    print('Coverage:', metrics['valid_test_coverage'], '; timing:', metrics['processing_ms'], flush=True)
    print('Saved:', out, flush=True)


if __name__ == '__main__':
    main()
