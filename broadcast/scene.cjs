// Configure the composition only. This script never starts recording or streaming.
const {connect}=require('./obs-client.cjs');
(async()=>{
 const obs=await connect();
 try {
  const existing=(await obs.call('GetInputList')).inputs;
  async function input(name,kind,settings){
   if(existing.some(i=>i.inputName===name)) await obs.call('SetInputSettings',{inputName:name,inputSettings:settings,overlay:true});
   else await obs.call('CreateInput',{sceneName:'Autoplay',inputName:name,inputKind:kind,inputSettings:settings,sceneItemEnabled:true});
  }
  await input('Game','game_capture',{capture_mode:'window',window:'Stardew Valley:SDL_app:StardewModdingAPI.exe',capture_cursor:false,priority:2,capture_overlays:false});
  const windows=await obs.call('GetInputPropertiesListPropertyItems',{inputName:'Game',propertyName:'window'});
  const game=windows.propertyItems.find(item=>String(item.itemValue).includes('StardewModdingAPI.exe'));
  if(!game) throw Error('Open the game before configuring capture. No matching Stardew process was found.');
  await obs.call('SetInputSettings',{inputName:'Game',inputSettings:{window:game.itemValue},overlay:true});
  await input('Game audio','wasapi_process_output_capture',{window:game.itemValue,priority:2});
  await input('Farmer overlay','browser_source',{url:'http://127.0.0.1:8765/',width:1920,height:1080,fps:30,fps_custom:true,shutdown:false,restart_when_active:false,reroute_audio:false});
  const items=(await obs.call('GetSceneItemList',{sceneName:'Autoplay'})).sceneItems;
  const gameId=items.find(item=>item.sourceName==='Game').sceneItemId;
  const overlayId=items.find(item=>item.sourceName==='Farmer overlay').sceneItemId;
  await obs.call('SetSceneItemTransform',{sceneName:'Autoplay',sceneItemId:gameId,sceneItemTransform:{positionX:0,positionY:108,boundsType:'OBS_BOUNDS_SCALE_INNER',boundsWidth:1536,boundsHeight:864}});
  await obs.call('SetSceneItemIndex',{sceneName:'Autoplay',sceneItemId:overlayId,sceneItemIndex:items.length-1});
  await obs.call('SetInputMute',{inputName:'Game audio',inputMuted:false});
  const scenes=(await obs.call('GetSceneList')).scenes;
  if(!scenes.some(s=>s.sceneName==='Break')) await obs.call('CreateScene',{sceneName:'Break'});
  const all=(await obs.call('GetInputList')).inputs;
  if(!all.some(i=>i.inputName==='Break background')) {
   await obs.call('CreateInput',{sceneName:'Break',inputName:'Break background',inputKind:'color_source_v3',inputSettings:{color:4279833613,width:1920,height:1080},sceneItemEnabled:true});
   const label=await obs.call('CreateInput',{sceneName:'Break',inputName:'Break message',inputKind:'text_gdiplus_v3',inputSettings:{text:'A quiet moment on the farm.\nWe will be back shortly.',font:{face:'Segoe UI',size:56,flags:0},color:4293321735},sceneItemEnabled:true});
   await obs.call('SetSceneItemTransform',{sceneName:'Break',sceneItemId:label.sceneItemId,sceneItemTransform:{positionX:280,positionY:420}});
  }
  await obs.call('SetCurrentProgramScene',{sceneName:'Autoplay'});
  console.log(JSON.stringify({scene:'Autoplay',gameCapture:game.itemName,audio:'process-specific',overlay:'audience view',breakScene:true}));
 }finally{obs.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
