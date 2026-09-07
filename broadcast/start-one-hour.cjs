// Start the verified Gemini trial and stop it independently of the monitoring agent.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {connect} = require('./obs-client.cjs');
const root = path.resolve(__dirname, '..');
const runId = process.argv[2];
if (!runId || !/^[a-f0-9-]{36}$/.test(runId)) throw Error('Supply the verified run ID.');
const run = path.join(root, 'harness/runs', runId);
const proof = JSON.parse(fs.readFileSync(path.join(__dirname, 'local/gemini38-stream-gate.json'), 'utf8'));
if (proof.runId !== runId || proof.accepted !== true || proof.model !== 'google/gemini-3.8-flash'
    || proof.provider !== 'Google AI Studio' || proof.reasoning !== 'low'
    || !['actor-visual', 'actor-state', 'director'].every(role => proof.cachedTokens[role] > 0))
  throw Error('The Gemini cache and gameplay gate has not passed for this run.');
const readControl = () => JSON.parse(fs.readFileSync(path.join(run, 'control/status.json'), 'utf8'));
const logPath = path.join(__dirname, 'local', `one-hour-${runId}.jsonl`);
const log = value => fs.appendFileSync(logPath, JSON.stringify({at: new Date().toISOString(), ...value}) + '\n');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function creditFailure() {
  const file = fs.openSync(path.join(run, 'events.jsonl'), 'r');
  try {
    const size = fs.fstatSync(file).size;
    const start = Math.max(0, size - 65536);
    const buffer = Buffer.alloc(size - start);
    fs.readSync(file, buffer, 0, buffer.length, start);
    return buffer.toString('utf8').split('\n').some(line => {
      try {
        const event = JSON.parse(line);
        return event.type === 'model_error' && /\b402\b|insufficient credits|credit limit|spend limit/i.test(line);
      } catch { return false; } // A tail can begin or end in a partial event.
    });
  } finally { fs.closeSync(file); }
}

function hold() {
  if (readControl().closed) return;
  const id = crypto.randomUUID().replaceAll('-', '');
  const inbox = path.join(run, 'control/inbox');
  fs.mkdirSync(inbox, {recursive: true});
  const command = {id, run_id: runId, kind: 'hold', message: '', support: 0, at: new Date().toISOString()};
  const temp = path.join(inbox, `.${id}.tmp`);
  fs.writeFileSync(temp, JSON.stringify(command));
  fs.renameSync(temp, path.join(inbox, `${Date.now()}000000-${id}.json`));
  log({holdQueued: id});
}

(async () => {
  if (readControl().closed) throw Error('Gameplay is already stopped.');
  const obs = await connect();
  let started = false, reason = 'startup_failed';
  try {
    if ((await obs.call('GetStreamStatus')).outputActive) throw Error('A stream is already running.');
    if ((await obs.call('GetCurrentProgramScene')).currentProgramSceneName !== 'Autoplay')
      throw Error('Select and verify the Autoplay scene first.');
    started = true;
    await obs.call('StartStream');
    let stream;
    for (let i = 0; i < 40; i++) {
      stream = await obs.call('GetStreamStatus');
      if (stream.outputActive) break;
      await sleep(250);
    }
    if (!stream.outputActive) throw Error('Public stream failed to start.');
    const start = Date.now() - stream.outputDuration;
    const deadline = start + 3600000;
    const session = {runId, startedAt: new Date(start).toISOString(), deadline: new Date(deadline).toISOString(),
                     pid: process.pid, logPath};
    fs.writeFileSync(path.join(__dirname, 'local/gemini38-live.json'), JSON.stringify(session, null, 2));
    log({started: session});
    console.log(JSON.stringify(session));
    while (Date.now() < deadline) {
      try {
        const control = readControl();
        if (control.closed) { reason = control.reason || 'gameplay_closed'; break; }
        if (creditFailure()) { reason = 'credits_exhausted'; break; }
        if (!(await obs.call('GetStreamStatus')).outputActive) { reason = 'stream_stopped'; break; }
      } catch (error) { log({monitorReadError: error.message}); }
      await sleep(Math.min(1000, Math.max(0, deadline - Date.now())));
    }
    if (Date.now() >= deadline) reason = 'one_hour_limit';
  } finally {
    if (started) {
      log({stopping: reason});
      try {
        if ((await obs.call('GetStreamStatus')).outputActive) await obs.call('StopStream');
        for (const output of (await obs.call('GetOutputList')).outputs)
          if (output.outputName === 'multi-output' && output.outputActive)
            await obs.call('StopOutput', {outputName: output.outputName});
        if ((await obs.call('GetRecordStatus')).outputActive) log(await obs.call('StopRecord'));
        await obs.call('SetCurrentProgramScene', {sceneName: 'Break'});
        for (let i = 0; i < 40; i++) {
          const outputs = (await obs.call('GetOutputList')).outputs;
          if (outputs.every(output => !output.outputActive)) { log({allOutputsStopped: true}); break; }
          await sleep(250);
          if (i === 39) throw Error('An OBS output did not stop. Operator attention required.');
        }
      } finally { hold(); }
    }
    obs.close();
  }
})().catch(error => { log({error: error.message}); console.error(error.message); process.exitCode = 1; });
