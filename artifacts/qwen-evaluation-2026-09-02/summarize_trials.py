import json
from pathlib import Path
from datetime import datetime
from autoplay_harness.report import summarize
root=Path.cwd()
output={}
for label,run_id in [('qwen_low','5c819efe-d98c-4e1c-9a13-ddca689e1277'),('glm_low','b09c3d61-574f-4d89-ba4e-84628c45cef6')]:
    p=root/'harness/runs'/run_id/'events.jsonl'
    events=[json.loads(line) for line in p.read_text().splitlines()]
    row=summarize(p)
    row['run_id']=run_id
    initial=next(e for e in events if e['type']=='director_review_started')
    result_events=[e for e in events if e['type']=='tool_result']
    row['autonomous_seconds']=round((datetime.fromisoformat(result_events[-1]['at'])-datetime.fromisoformat(initial['at'])).total_seconds(),3)
    row['http_429']=sum(a.get('http_status')==429 for e in events for a in e.get('attempts',[]))
    first_state=next(e['state'] for e in events if e['type']=='observation' and e['state'].get('worldReady'))
    row['initial_state']={k:first_state.get(k) for k in ('location','day','time','stamina','health','money','inventoryCounts')}
    row['planting_results']=[{'status':e['result']['status'],'reason':e['result'].get('reason'), 'tiles':e['result'].get('tiles_planted'), 'controls':e['result'].get('controls_executed'),'bridge_ms':e['bridge_ms']} for e in result_events if e['tool']=='plant_seeds']
    output[label]=row
(root/'artifacts/qwen-evaluation-2026-09-02/metrics.json').write_text(json.dumps(output,indent=2))
for label,row in output.items():
    print(json.dumps({k:row[k] for k in ('run_id','autonomous_seconds','actor_model_calls','verified_seed_plantings','http_429','cost_usd','actor_model_ms','cache_hit_rate','initial_goal_evaluation','planting_results')}))
