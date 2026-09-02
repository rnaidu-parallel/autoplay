import json
from datetime import datetime
from pathlib import Path
from autoplay_harness.report import summarize
root = Path.cwd()
runs={'glm_low':'9f6a04bf-345d-4906-8866-b6932f0a75b0','gemini_auto':'ca77bf60-9448-4686-bb13-7540fc3b3d4b','gemini_required':'11e5af90-06c0-420e-be05-4cf7461a0ddb'}
out={}
for name,run_id in runs.items():
    path=root/'harness/runs'/run_id/'events.jsonl'
    events=[json.loads(line) for line in path.read_text().splitlines()]
    row=summarize(path)
    row['run_id']=run_id
    start=next(e for e in events if e['type']=='director_review_started')
    results=[e for e in events if e['type']=='tool_result']
    row['autonomous_seconds']=round((datetime.fromisoformat(results[-1]['at'])-datetime.fromisoformat(start['at'])).total_seconds(),3)
    final=next(e['result']['state'] for e in reversed(results) if e['result'].get('state'))
    row['final']={k:final.get(k) for k in ('location','day','time','plantedCrops','wateredCrops','tilledTiles','stamina')}
    row['final']['seeds']=sum(i['stack'] for i in final['inventory'] if i['name']=='Parsnip Seeds')
    row['planting_results']=[{'status':e['result']['status'],'controls':e['result'].get('controls_executed'), 'tiles':e['result'].get('tiles_planted'),'bridge_ms':e['bridge_ms']} for e in results if e['tool']=='plant_seeds']
    out[name]=row
(root/'artifacts/async-farming-2026-09-02/metrics.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
