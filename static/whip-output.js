"use strict";
let whipBusy=false,whipStatusBusy=false;
async function whipApi(method='GET',data){
  const response=await fetch('/api/whip',{method,cache:'no-store',signal:AbortSignal.timeout(15000),...(data?{headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
  if(!response.ok)throw Error(response.status===403?'Open this control page locally using 127.0.0.1.':await response.text());
  return response.json();
}
function showWhip(data){
  $('whipState').textContent=data.state||'Stopped';$('whipState').style.color=data.state==='connected'?'#54e7bc':data.enabled?'#ffbd69':'';
  $('whipFormat').textContent=data.codec?data.codec.toUpperCase()+' / Opus stereo':'—';
  $('whipBytes').textContent=((data.video_bytes||0)/1000000).toFixed(2)+' / '+((data.audio_bytes||0)/1000000).toFixed(2)+' MB';
  $('whipDrops').textContent=(data.video_drops||0)+' / '+(data.audio_drops||0);$('whipAttempts').textContent=data.attempts||0;
  $('whipNotice').textContent=data.detail||'';
  $('whipStop').disabled=whipBusy||!data.enabled;$('whipStart').disabled=whipBusy||data.enabled;
  for(const id of ['whipEndpoint','whipToken','whipCodec'])$(id).disabled=whipBusy||data.enabled;
}
async function refreshWhip(){if(whipStatusBusy||whipBusy||location.hash!=='#whip'||document.hidden)return;whipStatusBusy=true;try{showWhip(await whipApi());}catch(e){$('whipNotice').textContent=e.message;}finally{whipStatusBusy=false;}}
$('whipForm').onsubmit=async event=>{event.preventDefault();whipBusy=true;$('whipStart').disabled=true;try{const data=await whipApi('POST',{url:$('whipEndpoint').value.trim(),token:$('whipToken').value.trim(),codec:$('whipCodec').value});whipBusy=false;showWhip(data);}catch(e){whipBusy=false;$('whipStart').disabled=false;$('whipNotice').textContent=e.message;}finally{whipBusy=false;}};
$('whipStop').onclick=async()=>{whipBusy=true;$('whipStop').disabled=true;try{const data=await whipApi('DELETE');whipBusy=false;showWhip(data);}catch(e){$('whipNotice').textContent=e.message;$('whipStop').disabled=false;}finally{whipBusy=false;}};
window.addEventListener('hashchange',refreshWhip);setInterval(refreshWhip,1000);refreshWhip();
