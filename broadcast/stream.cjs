// Start, stop or inspect the live stream through the local OBS websocket. No gate, no deadline:
// the run decides when the day ends and the operator decides when the broadcast ends.
//   node broadcast/stream.cjs status
//   node broadcast/stream.cjs start
//   node broadcast/stream.cjs stop
const {connect} = require('./obs-client.cjs');

async function main() {
  const command = process.argv[2] || 'status';
  const obs = await connect();
  try {
    const scene = (await obs.call('GetCurrentProgramScene')).currentProgramSceneName;
    const stream = await obs.call('GetStreamStatus');
    const outputs = (await obs.call('GetOutputList')).outputs
      .filter(output => output.outputKind !== 'virtualcam_output');
    const report = () => console.log(JSON.stringify({
      scene, streamActive: stream.outputActive, streamSeconds: Math.round((stream.outputDuration || 0) / 1000),
      outputs: outputs.map(output => ({name: output.outputName, kind: output.outputKind, active: output.outputActive})),
    }, null, 1));
    if (command === 'status') return report();
    if (command === 'start') {
      if (stream.outputActive) throw Error('A stream is already running.');
      if (scene !== 'Autoplay') await obs.call('SetCurrentProgramScene', {sceneName: 'Autoplay'});
      await obs.call('StartStream');
      await new Promise(resolve => setTimeout(resolve, 5000));
      const after = await obs.call('GetStreamStatus');
      const outputsAfter = (await obs.call('GetOutputList')).outputs;
      console.log(JSON.stringify({started: after.outputActive, reconnecting: after.outputReconnecting,
        outputs: outputsAfter.map(o => ({name: o.outputName, active: o.outputActive}))}, null, 1));
      return;
    }
    if (command === 'stop') {
      if (stream.outputActive) await obs.call('StopStream');
      for (const output of outputs) if (output.outputActive && output.outputKind !== 'replay_buffer')
        try { await obs.call('StopOutput', {outputName: output.outputName}); } catch (error) { console.log(String(error.message)); }
      await obs.call('SetCurrentProgramScene', {sceneName: 'Break'});
      console.log('stopped');
      return;
    }
    throw Error(`unknown command ${command}`);
  } finally {
    obs.close();
  }
}

main().catch(error => { console.error(error.message); process.exit(1); });
