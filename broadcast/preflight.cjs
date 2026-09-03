// Record fifteen seconds locally. Never start a stream or launch the game.
const {connect}=require('./obs-client.cjs');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 const obs=await connect();
 let started=false;
 try {
  if((await obs.call('GetRecordStatus')).outputActive || (await obs.call('GetStreamStatus')).outputActive)
   throw Error('Stop existing recording and streaming before this preflight.');
  if((await obs.call('GetCurrentProgramScene')).currentProgramSceneName!=='Autoplay')
   throw Error('Select the Autoplay scene before this preflight.');
  await obs.call('StartRecord');
  started=true;
  await sleep(1000);
  if(!(await obs.call('GetRecordStatus')).outputActive)
   throw Error('OBS did not start recording. Check for an open wizard or error dialog.');
  await sleep(14000);
  const result=await obs.call('StopRecord');
  started=false;
  console.log(JSON.stringify({recording:result.outputPath,stats:await obs.call('GetStats')}));
 }finally{
  try {if(started && (await obs.call('GetRecordStatus')).outputActive)await obs.call('StopRecord');}
  finally {obs.close();}
 }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
