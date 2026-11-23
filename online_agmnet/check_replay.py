"""Integration checks using real S3 EEG and the supplied, unmodified checkpoint."""
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from online_agmnet.demo_server import Replay


def check(calibration, seconds, realtime):
    replay = Replay()
    started = time.perf_counter()
    replay.start(calibration=calibration, realtime=realtime, limit=seconds)
    replay.thread.join(180)
    assert not replay.thread.is_alive(), 'Replay timeout'
    state = replay.snapshot()
    assert state['status'] == 'completed', state
    rows = [json.loads(line) for line in (Path(state['output'])/'predictions.jsonl').read_text().splitlines()]
    tests = [row for row in rows if row['phase']=='test']
    assert tests and {row['groundTruth'] for row in tests} == {0, 1}
    assert len(state['eeg']['channels']) == 62
    assert state['eeg']['endSeconds'] == seconds
    assert all(row['end_seconds'] <= seconds for row in rows)
    assert all(isinstance(row['systemTimestampMs'], int) for row in rows)
    assert all(row['replayWindowEndMs']-row['replayWindowStartMs']==1000 for row in rows)
    events = json.loads((Path(state['output'])/'calibration_events.json').read_text())
    assert all(abs(row['p_easy']+row['p_difficult']-1) < 1e-5 for row in tests)
    if calibration:
        assert events and all(event['labels_used'] is False for event in events)
        first_id = rows[0]['record_id']
        assert not any(row['record_id']==first_id and row['end_seconds']<=calibration+0.5 for row in tests)
        assert any(row['phase']=='calibration' for row in rows)
    elapsed = time.perf_counter()-started
    if realtime:
        assert elapsed >= seconds*2, elapsed
    result = dict(calibration=calibration, secondsPerRecord=seconds, realtime=realtime,
                  wallSeconds=round(elapsed, 2), windows=len(rows), predictions=len(tests), output=state['output'])
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    results = [check(60, 65, False), check(0, 5, True)]
    target = Path(__file__).with_name('results')/'integration_validation.json'
    target.write_text(json.dumps(results, indent=2), encoding='utf-8')
