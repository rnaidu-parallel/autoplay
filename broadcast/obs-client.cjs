const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

async function connect() {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'local/obs-studio/config/obs-studio/plugin_config/obs-websocket/config.json'), 'utf8'));
  const ws = new WebSocket(`ws://127.0.0.1:${config.server_port}`);
  const pending = new Map();
  let counter = 0;
  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('OBS connection timed out')), 10000);
    ws.addEventListener('error', () => { clearTimeout(timer); reject(Error('Cannot connect to local OBS')); });
    ws.addEventListener('message', ({data}) => {
      const packet = JSON.parse(data);
      if (packet.op === 0) {
        const auth = packet.d.authentication;
        const identify = {rpcVersion:1, eventSubscriptions:0};
        if(auth) {
          const hash = value => crypto.createHash('sha256').update(value).digest('base64');
          identify.authentication = hash(hash(config.server_password + auth.salt) + auth.challenge);
        }
        ws.send(JSON.stringify({op:1, d:identify}));
      } else if(packet.op === 2) {
        clearTimeout(timer); resolve();
      } else if(packet.op === 7) {
        const request = pending.get(packet.d.requestId);
        if(!request) return;
        pending.delete(packet.d.requestId); clearTimeout(request.timer);
        if(packet.d.requestStatus.result) request.resolve(packet.d.responseData || {});
        else request.reject(Error(`${packet.d.requestType}: ${packet.d.requestStatus.comment || packet.d.requestStatus.code}`));
      }
    });
  });
  try { await ready; } catch(error) { ws.close(); throw error; }
  return {
    call(requestType, requestData={}) {
      return new Promise((resolve,reject)=>{
        const requestId=String(++counter);
        const timer=setTimeout(()=>{pending.delete(requestId);reject(Error(`${requestType} timed out`));},15000);
        pending.set(requestId,{resolve,reject,timer});
        ws.send(JSON.stringify({op:6,d:{requestType,requestId,requestData}}));
      });
    },
    close(){ws.close();},
  };
}
module.exports={connect};
