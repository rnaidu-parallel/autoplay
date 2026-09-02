import base64, json, os
from pathlib import Path
from autoplay_harness.openrouter import OpenRouterClient, OpenRouterError
from autoplay_harness.prompts import DIRECTOR_SYSTEM_PROMPT
from autoplay_harness.tools import DIRECTOR_TOOLS
root = Path.cwd()
key = os.environ.get('OPENROUTER_API_KEY')
if not key:
    for line in (root / '.env').read_text().splitlines():
        if line.strip().startswith('OPENROUTER_API_KEY='):
            key = line.split('=',1)[1].strip().strip('\"').strip("'")
run = root / 'harness/runs/ca77bf60-9448-4686-bb13-7540fc3b3d4b'
events = [json.loads(line) for line in (run/'events.jsonl').read_text().splitlines()]
obs = next(e for e in events if e['type']=='observation' and e['state'].get('worldReady'))
frame = run / 'frames' / (obs['frame_id'] + '.jpg')
image = 'data:image/jpeg;base64,' + base64.b64encode(frame.read_bytes()).decode()
context = json.dumps({'role':'director', 'objective_ledger': {'active': {'goal':'Plant five existing Parsnip Seeds on empty tilled Farm soil', 'success_condition':'location is Farm, plantedCrops >= 5', 'milestone':'Reach Farm and plant five seeds'}}, 'game_state':obs['state'], 'frame':{'frame_id':obs['frame_id']}})
client = OpenRouterClient(key, OpenRouterClient.GEMINI_MODEL, 'director-required-probe')
results=[]
for index in range(2):
    try:
        result = client.choose_tool(DIRECTOR_SYSTEM_PROMPT, context, image, DIRECTOR_TOOLS, 'director')
        row={'call':index+1,'tool':result.name,'arguments':result.arguments,'usage':result.usage,'attempts':result.attempts}
    except OpenRouterError as error:
        row={'call':index+1,'error':str(error),'usage':error.usage,'attempts':error.attempts}
    results.append(row)
    print(json.dumps(row),flush=True)
(root/'artifacts/async-farming-2026-09-02/director-required-probe.json').write_text(json.dumps(results,indent=2))
