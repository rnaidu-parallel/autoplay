const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const test = require('node:test');
const script = fs.readFileSync(path.join(__dirname, '../start-one-hour.cjs'), 'utf8');
const runId = '899b7b90-3273-4054-b3b5-e254e9800c15';

async function trial({credits = false, gate = true} = {}) {
  let now = 0, live = false, recording = true, stoppedAt, connected = false;
  const writes = [];
  const proof = {runId, accepted: gate, model: 'google/gemini-3.8-flash', provider: 'Google AI Studio',
                 reasoning: 'low', cachedTokens: {'actor-visual': 9476, 'actor-state': 6518, director: 3646}};
  const tail = Buffer.from(credits ? '{"type":"model_error","error":"HTTP 402 insufficient credits"}\n' : '');
  const fakeFs = {
    readFileSync: file => JSON.stringify(file.endsWith('status.json') ? {closed: false} : proof),
    appendFileSync: () => {}, writeFileSync: (file, text) => writes.push({file, text}),
    mkdirSync: () => {}, renameSync: () => {}, openSync: () => 1, closeSync: () => {},
    fstatSync: () => ({size: tail.length}), readSync: (_file, buffer) => tail.copy(buffer),
  };
  const obs = {close() {}, async call(name) {
    if (name === 'GetStreamStatus') return {outputActive: live, outputDuration: now};
    if (name === 'GetCurrentProgramScene') return {currentProgramSceneName: 'Autoplay'};
    if (name === 'StartStream') live = true;
    if (name === 'StopStream') { stoppedAt = now; live = false; }
    if (name === 'GetOutputList') return {outputs: [
      {outputName: 'adv_stream', outputActive: live}, {outputName: 'multi-output', outputActive: live},
      {outputName: 'adv_file_output', outputActive: recording}]};
    if (name === 'GetRecordStatus') return {outputActive: recording};
    if (name === 'StopRecord') { recording = false; return {outputPath: 'recording.mkv'}; }
    return {};
  }};
  const clock = class extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } };
  await vm.runInNewContext(script, {require: name => name === 'node:fs' ? fakeFs : name === './obs-client.cjs'
    ? {connect: async () => { connected = true; return obs; }} : require(name),
    process: {argv: ['node', 'watchdog', runId], pid: 1}, Buffer, Date: clock,
    __dirname: path.join(__dirname, '..'), console: {log() {}, error() {}},
    setTimeout: (callback, ms) => { now += ms; queueMicrotask(callback); }});
  return {stoppedAt, live, recording, connected, writes};
}

test('stops streams and recording at one hour, and queues gameplay hold', async () => {
  const result = await trial();
  assert.equal(result.stoppedAt, 3600000);
  assert.equal(result.live, false);
  assert.equal(result.recording, false);
  assert(result.writes.some(row => JSON.parse(row.text).kind === 'hold'));
});
test('credit exhaustion stops early', async () => {
  const result = await trial({credits: true});
  assert.equal(result.stoppedAt, 0);
  assert.equal(result.live, false);
});
test('an unaccepted cache gate never connects to OBS', async () => {
  await assert.rejects(trial({gate: false}), /gate has not passed/);
});
