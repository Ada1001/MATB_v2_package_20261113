"""Compare predeclared replay settings on exactly the same scored windows."""
import argparse
import csv
import json
from pathlib import Path

try:
    from .common import classification_metrics, write_json
except ImportError:
    from common import classification_metrics, write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', nargs='+', required=True, help='name=path/to/replay_results')
    p.add_argument('--output-dir', required=True)
    args = p.parse_args()
    datasets, manifests = {}, {}
    for arg in args.runs:
        name, path = arg.split('=', 1)
        if name in datasets:
            raise ValueError('Duplicate run name: '+name)
        path = Path(path)
        manifests[name] = json.loads((path/'metrics.json').read_text(encoding='utf-8'))
        with (path/'predictions.csv').open(newline='') as stream:
            rows = [row for row in csv.DictReader(stream) if row['phase'] == 'test']
        keys = [(r['record_id'], int(r['start_sample']), int(r['end_sample'])) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate window records: '+name)
        datasets[name] = dict(zip(keys, rows))
    if len({m['checkpoint_sha256'] for m in manifests.values()}) != 1:
        raise ValueError('Comparison requires the same trained checkpoint')
    common = sorted(set.intersection(*(set(rows) for rows in datasets.values())))
    if not common:
        raise ValueError('No common scored windows')
    truth = [int(next(iter(datasets.values()))[key]['true_label']) for key in common]
    summary = {}
    for name, rows in datasets.items():
        if [int(rows[key]['true_label']) for key in common] != truth:
            raise ValueError('Ground truth differs across runs')
        summary[name] = dict(original_scored_windows=len(rows), common_scored_windows=len(common),
                             raw=classification_metrics(truth, [[float(rows[k]['raw_p_easy']), float(rows[k]['raw_p_difficult'])] for k in common]),
                             smoothed=classification_metrics(truth, [[float(rows[k]['p_easy']), float(rows[k]['p_difficult'])] for k in common]),
                             calibration=manifests[name]['calibration_events'])
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out/'common_window_comparison.json', summary)
    with (out/'common_windows.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['record_id', 'start_sample', 'end_sample', 'true_label'])
        writer.writerows((*key, label) for key, label in zip(common, truth))
    print(json.dumps(summary, indent=2))
    print('Settings must be predeclared; selecting settings from S3 results makes S3 development data.')


if __name__ == '__main__':
    main()
