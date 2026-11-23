"""Run the saved model on full S3 Easy then Difficult; prepare one score column."""
import json
from pathlib import Path
import sys
import time
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from online_agmnet.runtime.common import load_artifact,seed_all
    from online_agmnet.runtime.data import load_record
    from online_agmnet.runtime.streaming import OnlinePredictor
    seed_all(42,1)
    model,config=load_artifact(ROOT/'model/sub01_local_v1','cpu')
    predictor=OnlinePredictor(model,config,calibration_seconds=0,spd_calibration='none',
                              ema_alpha=.6,online_spd_rate=0)
    output=ROOT/'outputs'/('s3_scores_'+time.strftime('%Y%m%d_%H%M%S'))
    output.mkdir(parents=True)
    scores=[];records=[];rows=[]
    for task in ('MATBeasy','MATBdiff'):
        raw,meta=load_record(ROOT/'data',1,3,task)
        predictor.start_record(meta['record_id'])
        first=len(scores)+1;phases=Counter()
        for start in range(0,raw.shape[1],50):
            for row in predictor.feed(raw[:,start:start+50],config['channels']):
                score=row['p_difficult']*100 if row['phase']=='test' else None
                if score is not None:assert 0<=score<=100
                row['loadScore']=score
                scores.append(score);rows.append(row);phases[row['phase']]+=1
        expected=(raw.shape[1]-500)//250+1
        assert len(scores)-first+1==expected
        records.append(dict(**meta,durationSeconds=raw.shape[1]/500,
                            excelStartRow=first,excelEndRow=len(scores),phases=dict(phases)))
        print(task,records[-1]['durationSeconds'],'seconds',expected,'windows',dict(phases),flush=True)
    metadata={'checkpoint':str(ROOT/'model/sub01_local_v1/model.pt'),
              'checkpointSha256':config['checkpoint_sha256'],'windowSeconds':1,'strideSeconds':.5,
              'scoreDefinition':'100 * EMA-smoothed model p_difficult','emaAlpha':.6,
              'calibrationSeconds':0,'spdOnlineRate':0,'warmupSecondsPerRecord':2,
              'missingScore':'blank cell for warmup/rejected window; no fabricated score',
              'order':['MATBeasy','MATBdiff'],'header':False,'columns':1,'totalRows':len(scores),
              'numericRows':sum(x is not None for x in scores),'recordings':records}
    (output/'scores.json').write_text(json.dumps(scores,allow_nan=False),encoding='utf-8')
    (output/'provenance.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    (output/'window_audit.json').write_text(json.dumps(rows,allow_nan=False),encoding='utf-8')
    print('OUTPUT',output,flush=True)


if __name__=='__main__':main()
