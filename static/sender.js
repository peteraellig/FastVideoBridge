'use strict';
const $=id=>document.getElementById(id);
let activeSource=null,encodingInitialized=false,senderRunning=false,controlBusy=false,activeEncoderBackend=null,fallbackAvailable=false;
let viewerTitleInitialized=false,viewerLimitInitialized=false;
let selectedSource=null,sourceDraftChanged=false;
const sourceKey=s=>s?JSON.stringify([s.kind,s.address]):null;
function sourceChoice(){const option=$('source').selectedOptions?.[0];try{return JSON.parse(option?.dataset.source||'null');}catch{return null;}}
function updateSourceSelection(){
  const draft=sourceChoice(),pending=sourceKey(draft)!==sourceKey(selectedSource);
  $('setSource').disabled=controlBusy||!draft||!pending;
  $('sourceSelectionStatus').textContent=pending?'Selection not applied · click Set source':selectedSource?`Confirmed: ${selectedSource.kind.toUpperCase()} · ${selectedSource.kind==='test'?'Internal test pattern':selectedSource.name}`:'Choose a source and click Set source';
  $('sourceSelectionStatus').style.color=pending?'#ffbd69':'';
}
async function api(path, options){const r=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(8000),...options});if(!r.ok){const detail=await r.text();console.warn('Sender API error:',detail);const errors={400:'Invalid settings or source request.',403:'Open streamer control directly on this computer using localhost or 127.0.0.1.',404:'Source or connection not found.',503:'Streaming service unavailable.'};if(r.status===400&&['/api/source','/api/start'].includes(path)){const text=detail.trim();throw Error(/^(Invalid source|Source is no longer|Select a source|Choose a source|Enter an OMT|Unable to open source)/.test(text)?text:'Unable to open the selected source. Check the input and server log.');}throw Error(errors[r.status]||`Request failed (HTTP ${r.status}).`);}return r.json();}
function message(text=''){$('message').textContent=text;}
let connectionMessageTimer=null;
function connectionMessage(text=''){clearTimeout(connectionMessageTimer);const element=$('connectionMessage');element.textContent=text;element.title=text;if(text)connectionMessageTimer=setTimeout(()=>connectionMessage(),5000);}
async function discover(){
  $('refresh').disabled=$('start').disabled=$('setSource').disabled=true;
  try{const result=await api('/api/sources');const old=$('source').selectedOptions[0]?.dataset.key;$('source').replaceChildren();
    const key=sourceKey;
    result.sources.forEach((s,i)=>{const o=document.createElement('option');o.value=i;o.dataset.key=key(s);o.dataset.source=JSON.stringify(s);o.textContent=s.kind==='test'?'TEST · Internal test pattern':`${s.kind.toUpperCase()} · ${s.name}`;$('source').append(o);});
    // Preserve the active source; otherwise prefer the tested OMT input.
    const selected=[...$('source').options].find(o=>o.dataset.key===(old || key(selectedSource) || key(activeSource)));
    if(selected)$('source').value=selected.value;
    else {const idx=result.sources.findIndex(s=>s.kind==='omt');if(idx>=0)$('source').value=idx;}
    message(Object.keys(result.errors).map(k=>`${k.toUpperCase()}: Source discovery failed.`).join('\n'));
  }catch(e){message(e.message);}finally{$('refresh').disabled=$('start').disabled=controlBusy;updateSourceSelection();}
}
async function action(path,data){
  controlBusy=true;$('source').disabled=$('refresh').disabled=$('setSource').disabled=$('start').disabled=$('omtStart').disabled=$('applyEncoding').disabled=$('applyH264Encoding').disabled=true;
  try{await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})});message();await status();}
  catch(e){message(e.message);}finally{controlBusy=false;$('source').disabled=$('refresh').disabled=$('start').disabled=$('omtStart').disabled=$('applyEncoding').disabled=false;$('applyH264Encoding').disabled=!fallbackAvailable;updateSourceSelection();}
}
let linkSignature='';
let connectionPrevious=new Map(), connectionPollBusy=false;
function viewerTotals(peers,previous,now){
  let av1=0,h264=0,mbps=0,sampled=0;
  for(const c of peers){
    if(c.state!=='connected')continue;
    if(c.codec==='av1')av1++;if(c.codec==='h264')h264++;
    const old=previous.get(c.id),bytes=c.video_payload_bytes+c.audio_payload_bytes;
    if(old&&now>old.at&&bytes>=old.totalBytes){mbps+=(bytes-old.totalBytes)*8/(now-old.at)/1000;sampled++;}
  }
  return {av1,h264,mbps:sampled||!av1&&!h264?mbps:null};
}
async function connections(){
  if(connectionPollBusy)return;connectionPollBusy=true;
  try{
    const result=await api('/api/connections'),now=performance.now(),next=new Map();
    const totals=viewerTotals(result.connections,connectionPrevious,now);
    $('codecViewers').textContent=`${totals.av1} AV1 · ${totals.h264} H.264`;
    $('viewerBandwidth').textContent=Number.isFinite(totals.mbps)?`${totals.mbps.toFixed(2)} Mbit/s`:'—';
    $('connectionRows').replaceChildren();
    for(const c of result.connections){
      const previous=connectionPrevious.get(c.id),elapsed=previous?(now-previous.at)/1000:0;
      const rate=elapsed>0?(c.video_payload_bytes-previous.bytes)*8/elapsed/1e6:null;
      const delta=previous?Math.max(0,c.video_drops-previous.drops):0;
      next.set(c.id,{at:now,bytes:c.video_payload_bytes,totalBytes:c.video_payload_bytes+c.audio_payload_bytes,drops:c.video_drops});
      const row=document.createElement('tr'),b=c.browser;
      const n=x=>Number.isFinite(x)?x.toFixed(1):'—';
      const route=b&&b.path_is_relay>=0 ? ` / ${b.path_is_relay===1?"Relay":"Direct"}${b.protocol_is_tcp>=0?(b.protocol_is_tcp===1?" TCP":" UDP"):""}` : "";
      const cells=[`${c.id.slice(0,8)} / ${(c.codec||'').toUpperCase()}`,c.state+route,`${n(rate)} Mbit/s`,`${c.video_drops} (+${delta}) / ${c.audio_drops}`,
        b?`${b.packets_lost??'—'} / ${n(b.receive_fps)} / ${n(b.fps)}`:`${c.video_reported_lost} / — / —`,
        b?`${n(b.jitter_buffer_ms)} / ${n(b.decode_ms)} ms`:'—',`${c.nack_requests} / ${c.pli_requests+c.fir_requests}`,
        `${n(c.max_write_ms)} ms`];
      for(const [index,value] of cells.entries()){const cell=document.createElement('td');cell.textContent=value;
        if(index===0&&c.user_name){const user=document.createElement('small');user.className='viewer-browser';user.textContent=c.user_name;cell.prepend(user);}
        if(index===0&&b?.browser_name){const browser=document.createElement('small');browser.className='viewer-browser';browser.textContent=`${b.browser_name} ${b.browser_version||''}`.trim();cell.append(browser);}
        row.append(cell);}
      row.dataset.search=[c.user_name||'General viewer',c.id,...cells,b?.browser_name||'',b?.browser_version||''].join(' ');
      const controls=document.createElement('td'),button=document.createElement('button');button.textContent='Disconnect';button.className='connection-disconnect';button.onclick=async()=>{button.disabled=true;try{await api('/api/connections/'+c.id+'/disconnect',{method:'POST'});connectionMessage('Viewer disconnected.');await connections();}catch(e){connectionMessage(e.message);button.disabled=false;}};controls.append(button);row.append(controls);
      if(delta>0)row.className='error';$('connectionRows').append(row);
    }
    connectionPrevious=next;
    filterConnections();
    $('connectionSummary').textContent=`${result.connections.length} connections · total drops (+ since last update)`;
  }catch(e){$('connectionSummary').textContent=e.message;$('codecViewers').textContent=$('viewerBandwidth').textContent='—';}finally{connectionPollBusy=false;}
}
let statusPollBusy=false,statusMessage=null;
let statusFailureAt=null,statusOutageBusy=false;
let previousStreamCounters=null;
function streamAlert(s){
  if(typeof s.stream_warning==='string')return s.stream_warning;
  const previous=previousStreamCounters;
  previousStreamCounters={frames:s.frames,video:s.frame_deadline_drops,audio:s.audio_buffer_drops,missing:s.audio_missing_samples,uptime:s.uptime_seconds};
  const alerts=[];
  if(s.error)alerts.push(/Quellzeitcodes/.test(s.error)?'Audio/video source timestamps do not match':'Source error · check the input');
  if(s.gateway_error)alerts.push('WebRTC service unavailable');
  if(s.gateway_bridge_error)alerts.push('Stream transport interrupted');
  if(s.publisher_error)alerts.push('Encoder error · check the server log');
  if(s.running&&!s.signal)alerts.push(`No video signal${s.source?.name?' · '+s.source.name:''}`);
  if(s.running&&s.signal){
    const encoders=[[s,(s.video_codec||'AV1').toUpperCase()],[s.h264_fallback,'H.264']];
    for(const [e,name] of encoders){
      if(e&&e.encode_sample_count>0&&Number.isFinite(e.encode_ms_p95)&&Number.isFinite(e.encode_budget_ms)&&(e.encode_ms_p95>e.encode_budget_ms||e.encode_over_budget_recent/e.encode_sample_count>=.05))
        alerts.push(`${name} encoding over budget · P95 ${e.encode_ms_p95.toFixed(1)} / ${e.encode_budget_ms.toFixed(1)} ms`);
    }
    if(previous&&s.uptime_seconds>=previous.uptime&&s.frames>=previous.frames){
      const video=s.frame_deadline_drops-previous.video,audio=s.audio_buffer_drops-previous.audio,missing=s.audio_missing_samples-previous.missing;
      if(video>0)alerts.push(`${video} input video frame${video===1?'':'s'} skipped`);
      if(audio>0||missing>0)alerts.push('Audio input gap or buffer overrun');
    }
  }
  return alerts.join(' · ');
}
function renderStreamAlert(text){$('streamAlarm').textContent=text;$('streamAlarm').hidden=!text;}
function statusNotice(text){statusMessage=text;message(text);}
async function status(){
  if(statusPollBusy)return;statusPollBusy=true;
  try{const s=await api('/api/status');
    if(statusFailureAt!==null&&!statusOutageBusy){
      const started=statusFailureAt,ended=Date.now();statusOutageBusy=true;
      api('/api/warnings/outage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({started_ms:started,ended_ms:ended})})
        .then(()=>{if(statusFailureAt===started)statusFailureAt=null;}).catch(()=>{}).finally(()=>{statusOutageBusy=false;});
    }
    if(statusMessage!==null&&$('message').textContent===statusMessage)message();
    statusMessage=null;activeSource=s.source;senderRunning=s.running===true;$('start').textContent=senderRunning?'Stop streaming':'Start streaming';$('start').classList.toggle('running',senderRunning);$('start').setAttribute('aria-pressed',String(senderRunning));$('signal').textContent=s.running?(s.signal?'Signal present':'No signal'):'Stopped';
    selectedSource=s.selected_source??s.source??null;
    if(!sourceDraftChanged){const match=[...($('source').options||[])].find(o=>o.dataset.key===sourceKey(selectedSource));if(match)$('source').value=match.value;}
    if(sourceKey(sourceChoice())===sourceKey(selectedSource))sourceDraftChanged=false;
    updateSourceSelection();
    $('signal').className=senderRunning&&s.signal?'signal-good':'signal-bad';
    renderStreamAlert(streamAlert(s));
    activeEncoderBackend=s.encoder_backend;
    fallbackAvailable=!!s.h264_fallback;
    $('h264EncoderSettings').hidden=!fallbackAvailable;
    $('mainEncoderLabel').textContent=(s.video_codec||'AV1').toUpperCase();
    $('applyH264Encoding').disabled=controlBusy||!fallbackAvailable;
    if(!encodingInitialized){
      $('bitrate').value=String(s.video_target_bitrate_bps);
      if(s.encoder_backend==='qsv'){
        $('preset').replaceChildren();
        for(const [value,label] of [['medium','Medium · Balanced'],['slow','Slow · Quality'],['veryslow','Veryslow · Best quality']]){
          const option=document.createElement('option');option.value=value;option.textContent=label;$('preset').append(option);
        }
        $('preset').value=s.qsv_preset||'medium';
      }else $('preset').value=s.nvenc_preset||'p7';
      $('h264Bitrate').value=String(s.h264_fallback_bitrate||4000000);
      $('h264Preset').value=s.h264_fallback_qsv_preset||'medium';
      encodingInitialized=true;
    }
    if(!viewerTitleInitialized){$('viewerTitleInput').value=s.viewer_title??'FastVideo Live';viewerTitleInitialized=true;}
    if(document.activeElement!==$('hideViewerLogo'))$('hideViewerLogo').checked=!!s.viewer_hide_logo;
    $('preset').disabled=!['nvenc','qsv'].includes(s.encoder_backend);
    if(!viewerLimitInitialized){$('viewerLimit').value=s.max_viewers;viewerLimitInitialized=true;}
    $('fps').textContent=s.signal?s.output_fps.toFixed(1):'—';$('viewers').textContent=`${s.viewers} / ${s.max_viewers}`;
    const n=s.uptime_seconds;$('uptime').textContent=`${Math.floor(n/3600)}:${String(Math.floor(n/60)%60).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
    const num=(v,d=1)=>Number.isFinite(v)?v.toFixed(d):'—',ms=v=>Number.isFinite(v)?`${v.toFixed(1)} ms`:'—';
    if(!viewerLimitInitialized){$('viewerLimit').value=s.max_viewers;viewerLimitInitialized=true;}
    $('fps').textContent=s.signal?`${num(s.output_fps)} fps`:'—';
    const shownSource=s.source||selectedSource;
    $('activeSource').textContent=shownSource?`${shownSource.kind.toUpperCase()} · ${shownSource.kind==='test'?'Internal test pattern':shownSource.name}`:'No source';
    $('activeSource').className=senderRunning&&s.signal?'signal-good':'signal-bad';
    $('streamSourceStatus').textContent=`${$('activeSource').textContent} · ${$('signal').textContent}`;
    $('streamSourceStatus').className=$('signal').className;
    $('inputFormat').textContent=s.input_format?`${s.input_format.width} × ${s.input_format.height} / ${num(s.input_format.fps)}p`:'—';
    $('outputFormat').textContent=`${s.width} × ${s.height}`;
    $('encoding').textContent=s.encoder||'—';
    $('mainBitrate').textContent=`${(s.video_codec||'').toUpperCase()} · ${num(s.video_target_bitrate_bps/1e6)} Mbit/s`;
    $('fallbackBitrate').textContent=s.h264_fallback?`${num((s.h264_fallback.target_bitrate_bps||s.h264_fallback_bitrate)/1e6)} Mbit/s`:'Inactive';
    $('qualityLevel').textContent=s.qsv_preset||(s.nvenc_preset?.toUpperCase())||(Number.isFinite(s.av1_preset)?`Preset ${s.av1_preset} / ${s.av1_parallelism} Threads`:'—');
    $('fallbackQualityLevel').textContent=s.h264_fallback_qsv_preset||'—';
    $('encoderProfile').textContent=s.h264_profile||(s.h264_fallback?'Baseline':'—');
    const measured=Number.isFinite(s.encode_ms_p95)&&s.encode_sample_count>0;
    const timingClass=!measured?'':s.encode_ms_p95>s.encode_budget_ms?'timing-critical':s.encode_over_budget_recent>0?'timing-warning':'timing-good';
    $('encodeTimes').textContent=ms(s.encode_ms_p95);
    $('encodeTimes').title=`Average: ${ms(s.encode_ms_mean)} · ${s.encode_sample_count||0} measured frames`;
    $('encodeTimes').className=timingClass;
    $('encodeBudget').textContent=measured&&Number.isFinite(s.encode_budget_ms)?`${s.encode_over_budget_recent>0?'Exceeded':'Within budget'} · ${ms(s.encode_budget_ms)} · ${s.encode_over_budget_recent} / ${s.encode_sample_count} over`:'—';
    $('encodeBudget').className=timingClass;
    $('audioFormat').textContent=s.audio_codec||'—';
    $('previewEnabled').checked=s.input_preview_enabled!==false;
    $('audioLevel').textContent=Number.isFinite(s.audio_peak_dbfs)?`${s.audio_peak_dbfs.toFixed(1)} dBFS`:'—';
    const peak=Number.isFinite(s.audio_peak_dbfs)?Math.max(-60,Math.min(0,s.audio_peak_dbfs)):-60;
    $('sourceMeter').setAttribute('aria-valuenow',String(peak));$('sourceMeterFill').style.width=`${(peak+60)/60*100}%`;
    $('streamState').textContent=senderRunning?(s.signal?'Live':'Waiting for signal'):'Stopped';
    $('sourceRecoveries').textContent=String(s.source_recoveries??0);
    $('markerState').textContent=s.timestamp_marker?'On':'Off';
    $('workingDirectory').textContent='C:\\Fastvideo';
    const d=s.viewer_diagnostics;
    $('diagVideoBuffer').textContent=ms(d?.jitter_buffer_ms);$('diagAudioBuffer').textContent=ms(d?.audio_jitter_buffer_ms);
    $('diagDecode').textContent=ms(d?.decode_ms);$('diagRTT').textContent=ms(d?.webrtc_rtt_ms);
    $('diagFPS').textContent=d?`${num(d.fps)} fps`:'—';
    $('diagBandwidth').textContent=Number.isFinite(d?.receive_bitrate_mbps)?`${num(d.receive_bitrate_mbps,2)} Mbit/s`:'—';
    $('diagPath').textContent=d&&d.path_is_relay>=0?`${d.path_is_relay===1?'Relay':'Direct'} · ${d.protocol_is_tcp===1?'TCP':'UDP'}`:'—';
    $('viewerDiagnostics').textContent=d?`${num(d.age_seconds)} s`:'—';
    $('diagLatency').textContent=ms(d?.raw_latency_ms);$('diagAdjusted').textContent=ms(d?.adjusted_estimate_ms);
    $('diagClock').textContent=Number.isFinite(d?.viewer_clock_ahead_ms)?`${d.viewer_clock_ahead_ms>=0?'+':''}${num(d.viewer_clock_ahead_ms)} ms`:'—';
    $('diagAudioOutput').textContent=d&&Number.isFinite(d.audio_output_primed)?(d.audio_output_primed?'Low-latency output active':'Standard'):'—';
    if(s.error || s.publisher_error || s.gateway_error || s.gateway_bridge_error){const detail=s.error || s.publisher_error || s.gateway_error || s.gateway_bridge_error;console.warn('Streaming pipeline error:',detail);statusNotice('Streaming error. Check the source and server log.');}
    const sig=JSON.stringify(s.viewer_links);if(sig!==linkSignature){linkSignature=sig;$('links').replaceChildren();
      s.viewer_links.forEach(link=>{const row=document.createElement('div');row.className='row';const a=document.createElement('a');a.href=link;a.textContent=link;a.target='_blank';a.rel='noopener';
        const b=document.createElement('button');b.textContent='Copy link';b.onclick=async()=>{try{await navigator.clipboard.writeText(link);b.textContent='Copied';setTimeout(()=>b.textContent='Copy link',1800);}catch{message(`Select and copy this link: ${link}`);}};row.append(a,b);$('links').append(row);});}
  }catch(e){if(statusFailureAt===null)statusFailureAt=Date.now();$('signal').textContent='Status unavailable';$('signal').className=$('activeSource').className=$('streamSourceStatus').className='signal-bad';$('streamSourceStatus').textContent=`${$('activeSource').textContent||'Source'} · Status unavailable`;renderStreamAlert('Sender unreachable · status update failed');statusNotice(`Status request failed: ${e.message}`);}
  finally{statusPollBusy=false;}
}
let systemPollBusy=false;
async function systemStatus(){
  if(systemPollBusy)return;systemPollBusy=true;
  try{
    const s=await api('/api/system');
    const model=name=>name?name.replace(/\(R\)|\(TM\)/g,'').replace(/\s+/g,' ').trim():'—';
    $('systemCPUName').textContent=model(s.cpu_name);
    $('systemGPUName').textContent=model(s.gpu_name);
    $('systemGPUName').title=(s.gpu_names||[]).map(model).join(' / ');
    $('systemCPU').textContent=Number.isFinite(s.cpu_percent)?`${s.cpu_percent.toFixed(1)} %`:'—';
    $('systemRAM').textContent=`${(s.ram_used_bytes/2**30).toFixed(1)} / ${(s.ram_total_bytes/2**30).toFixed(1)} GiB`;
    $('serverStart').textContent=new Date(s.server_started_at).toLocaleString('en-GB',{timeZone:'Europe/Zurich',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  }catch{$('systemCPU').textContent=$('systemRAM').textContent=$('serverStart').textContent='—';}
  finally{systemPollBusy=false;}
}
$('refresh').onclick=discover;
$('source').onchange=()=>{sourceDraftChanged=true;updateSourceSelection();};
$('setSource').onclick=()=>{const source=sourceChoice();if(source)return action('/api/source',{source});};
let warningLogOpen=false,warningLogBusy=false;
async function loadWarningLog(){
  if(!warningLogOpen||warningLogBusy)return;warningLogBusy=true;
  try{
    const data=await api('/api/warnings');$('warningRows').replaceChildren();
    for(const entry of data.entries){
      const row=document.createElement('tr');
      if(entry.status==='Active')row.className='warning-active';
      const started=new Date(entry.started_at).toLocaleString('en-GB',{timeZone:'Europe/Zurich',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
      const duration=Number.isFinite(entry.duration_seconds)?`${entry.duration_seconds.toFixed(1)} s`:'—';
      for(const value of [started,duration,entry.status,entry.message]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
      $('warningRows').append(row);
    }
    $('warningLogSummary').textContent=data.entries.length?`${data.entries.length} warning events · latest first`:'No warnings recorded.';
  }catch(e){$('warningLogSummary').textContent=`Unable to load warning log: ${e.message}`;}
  finally{warningLogBusy=false;}
}
$('viewWarningLog').onclick=()=>{warningLogOpen=!warningLogOpen;$('warningLogPanel').hidden=!warningLogOpen;$('viewWarningLog').textContent=warningLogOpen?'Hide log':'View log';$('viewWarningLog').setAttribute('aria-expanded',String(warningLogOpen));if(warningLogOpen)loadWarningLog();};
$('refreshWarningLog').onclick=loadWarningLog;
setInterval(()=>{if(!document.hidden)loadWarningLog();},5000);
$('saveViewerTitle').onclick=async()=>{
  $('saveViewerTitle').disabled=true;
  try{const result=await api('/api/viewer/display',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({viewer_title:$('viewerTitleInput').value})});$('viewerTitleInput').value=result.viewer_title;message();}
  catch(e){message(e.message);}
  finally{$('saveViewerTitle').disabled=false;}
};
$('applyEncoding').onclick=()=>action('/api/configure',{bitrate:Number($('bitrate').value),...(activeEncoderBackend==='qsv'?{qsv_preset:$('preset').value}:{nvenc_preset:$('preset').value})});
$('applyH264Encoding').onclick=()=>action('/api/configure',{h264_fallback_bitrate:Number($('h264Bitrate').value),h264_fallback_qsv_preset:$('h264Preset').value});
setInterval(connections,2000);connections();
$('start').onclick=()=>{if(!senderRunning&&!selectedSource){message('Choose a source in Setup and click Set source first.');return;}return action(senderRunning?'/api/stop':'/api/start',{});};

$('omtStart').onclick=()=>action('/api/source',{omt_address:$('omtAddress').value.trim()});
status().then(discover);setInterval(status,1500);

systemStatus();setInterval(systemStatus,2000);

function updateSenderClock(){const now=new Date();$('senderClock').textContent=now.toLocaleTimeString('en-GB',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});$('senderClock').dateTime=now.toISOString();}
updateSenderClock();setInterval(updateSenderClock,1000);

function showSenderView(){
  const requested=location.hash.slice(1),view=['dashboard','whip','connections','links','users','setup','help'].includes(requested)?requested:'dashboard';
  if(view!=='connections')connectionMessage();
  document.querySelectorAll('[data-panel]').forEach(panel=>panel.hidden=panel.dataset.panel!==view);
  document.querySelectorAll('[data-view]').forEach(link=>{const active=link.dataset.view===view;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
}
window.addEventListener('hashchange',showSenderView);showSenderView();

let previewReady=false,previewFront=true;
function clearInputPreview(text){previewReady=false;$('inputPreview').hidden=$('inputPreviewBack').hidden=true;$('previewState').hidden=false;$('previewState').textContent=text;}
async function updateInputPreview(){
  try{
    if(document.hidden)return;
    if(!senderRunning){clearInputPreview('No signal');return;}
    const response=await fetch('/api/preview',{cache:'no-store',signal:AbortSignal.timeout(2500)});
    if(response.status===204){clearInputPreview(response.headers.get('X-Preview-State')==='paused'?'Preview paused':'No signal');return;}
    if(!response.ok)throw Error('Preview unavailable');
    const blob=await response.blob();
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('Preview unavailable'));reader.readAsDataURL(blob);});
    if(!senderRunning)return;
    const next=previewReady&&previewFront?$('inputPreviewBack'):$('inputPreview');
    next.src=data;await next.decode();
    if(!senderRunning){clearInputPreview('No signal');return;}
    if(!previewReady){
      $('inputPreviewBack').src=data;
      $('inputPreview').style.opacity='1';previewFront=true;previewReady=true;
    }else{
      previewFront=!previewFront;$('inputPreview').style.opacity=previewFront?'1':'0';
    }
    $('inputPreview').hidden=$('inputPreviewBack').hidden=false;$('previewState').hidden=true;
  }catch{
    clearInputPreview('Preview unavailable');
  }finally{setTimeout(updateInputPreview,200);}
}
updateInputPreview();

$('previewEnabled').onchange=async()=>{const checkbox=$('previewEnabled');checkbox.disabled=true;try{await api('/api/preview/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:checkbox.checked})});await status();}catch(e){message(e.message);}finally{checkbox.disabled=false;}};

$('hideViewerLogo').onchange=async()=>{
  const box=$('hideViewerLogo');box.disabled=true;
  try{const result=await api('/api/viewer/display',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({viewer_hide_logo:box.checked})});box.checked=result.viewer_hide_logo;message();}
  catch(e){box.checked=!box.checked;message(e.message);}
  finally{box.disabled=false;}
};

$('setViewerLimit').onclick=async()=>{
  const button=$('setViewerLimit'),limit=Number($('viewerLimit').value);
  if(!Number.isInteger(limit)||limit<1||limit>80){message('Choose 1 to 80 viewers.');return;}
  button.disabled=true;
  try{const result=await api('/api/viewer/capacity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_viewers:limit})});$('viewerLimit').value=result.max_viewers;message();await status();}
  catch(e){connectionMessage(e.message);}finally{button.disabled=false;}
};

$('disconnectGeneralConnections').onclick=async()=>{
 const button=$('disconnectGeneralConnections');button.disabled=true;
 try{const result=await api('/api/connections/disconnect-general',{method:'POST'});connectionMessage(`${result.disconnected} general viewers disconnected.`);await connections();}
 catch(e){connectionMessage(e.message);}finally{button.disabled=false;}
};

function filterConnections(){
 const normalize=value=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
 const words=normalize($('connectionSearch').value||'').trim().split(/\s+/).filter(Boolean);
 for(const row of $('connectionRows').children){const text=normalize(row.dataset.search||'');row.hidden=!words.every(word=>text.includes(word));}
}
$('connectionSearch').oninput=filterConnections;
