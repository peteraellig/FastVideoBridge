'use strict';
const $=id=>document.getElementById(id),video=$('video');
if(new URL(location.href).searchParams.get('access')){
  $('personalLinkNotice').hidden=false;
  setTimeout(()=>{$('personalLinkNotice').hidden=true;},10000);
}
const canvas=document.createElement('canvas');canvas.width=1280;canvas.height=16;
const ctx=canvas.getContext('2d',{willReadFrequently:true});
let pc=null,sessionURL=null,callbackId=null,lastFrameAt=0,lastStamp=null,lastValidAt=0;
let samples=[],instant=null,rejected=0,measured=0,renderTimer=0,clockJump=false,previousWall=null,previousMono=null;
let clockEstimate=null,clockBusy=false,statsBusy=false,previousStats=null,latestStats={};
let receiveSamples=[],measurementGeneration=0;
let mediaStream=null,audioReceived=false;
let previousStatsForRate=null;
let markerEnabled=false,diagnosticWarning="";
const estimatedClockMode=/iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
let connectAttempt=0,connectController=null,resumeAfterPageHide=false;
let wantConnected=true,connectBusy=false,retryTimer=null,retryAttempt=0,connectionStartedAt=0;
let startupRecoveryTimer=null,startupRecoveryUsed=false;
let playbackBlocked=false,playbackAttempt=0;
let receiverLatencySupport=0,previousAudioStats=null;
let audioOutputContext=null,audioOutputOscillator=null;
let previousBandwidthStats=null;
function primeAudioOutput(){
  const appleMobile=/iPhone|iPad|iPod/.test(navigator.userAgent)||
    (navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  const AudioContextType=window.AudioContext||window.webkitAudioContext;
  if(!appleMobile||!AudioContextType)return;
  try{
    if(!audioOutputContext||audioOutputContext.state==='closed'){
      audioOutputContext=new AudioContextType({latencyHint:'interactive',sampleRate:48000});
      const silence=audioOutputContext.createGain();silence.gain.value=0;
      audioOutputOscillator=audioOutputContext.createOscillator();
      audioOutputOscillator.connect(silence);silence.connect(audioOutputContext.destination);
      audioOutputOscillator.start();
    }
    // Called synchronously from the click. This renders silence only; the
    // program audio stays on the same video element and shared RTP clock.
    audioOutputContext.resume().catch(()=>{});
  }catch{ /* Unsupported audio output configuration leaves native playback. */ }
}
function releaseAudioOutput(){
  const context=audioOutputContext;audioOutputContext=null;
  try{audioOutputOscillator?.stop();}catch{}
  audioOutputOscillator=null;
  if(context)context.close().catch(()=>{});
}
function receiveBitrate(current,previous){
  if(!previous||current.id!==previous.id||current.timestamp<=previous.timestamp||current.bytes<previous.bytes)return null;
  return (current.bytes-previous.bytes)*8/(current.timestamp-previous.timestamp)/1000;
}
function configureReceiverLatency(receiver){
  if(!receiver)return 0;
  let applied=0;
  // Identical audio/video targets preserve the browser's AV synchronization.
  // Feature detection avoids pretending unsupported Safari versions obey this.
  try{if('jitterBufferTarget' in receiver){receiver.jitterBufferTarget=50;applied|=1;}}catch{}
  try{if('playoutDelayHint' in receiver){receiver.playoutDelayHint=.05;applied|=2;}}catch{}
  return applied;
}
function updateConnectionButton(){
  const active=wantConnected&&(connectBusy||pc!==null||retryTimer!==null);
  $('connect').textContent=active?'Disconnect':'Connect';
  $('connect').classList.toggle('running',active);
  $('connect').setAttribute('aria-pressed',String(active));
  $('connect').disabled=false;
}
function clearStartupRecovery(){
  if(startupRecoveryTimer!==null)clearTimeout(startupRecoveryTimer);
  startupRecoveryTimer=null;
}
function armStartupRecovery(peer){
  if(startupRecoveryUsed||startupRecoveryTimer!==null)return;
  startupRecoveryTimer=setTimeout(async()=>{
    startupRecoveryTimer=null;
    if(peer!==pc||!wantConnected||document.hidden||lastFrameAt||playbackBlocked)return;
    startupRecoveryUsed=true;
    message('Restarting playback once.');
    await disconnect(false);
    if(wantConnected)await connect(false);
  },4000);
}
function clearRetry(){if(retryTimer!==null)clearTimeout(retryTimer);retryTimer=null;}
function retryConnection(delay){
  if(!wantConnected||retryTimer!==null)return;
  const wait=delay??Math.min(10000,1000*2**Math.min(retryAttempt++,4));
  $('connection').textContent=`Reconnecting in ${Math.ceil(wait/1000)} s …`;
  retryTimer=setTimeout(async()=>{retryTimer=null;if(!wantConnected)return;if(connectBusy){retryConnection(1000);return;}if(await organiserDisconnected())return;await disconnect(false);await connect(false);},wait);
}
async function organiserDisconnected(){
  if(!sessionURL)return false;
  try{const response=await fetch(sessionURL+'/state',{cache:'no-store',signal:AbortSignal.timeout(3000)});if(response.ok&&(await response.json()).disconnected){await stopByOrganiser();return true;}}catch{}
  return false;
}
async function stopByOrganiser(){await disconnect(true);message('Disconnected by the organiser. Click Connect to join again.');}
function message(text=''){$('message').textContent=text;}
function resetMeasurement(){previousBandwidthStats=null;samples=[];instant=null;lastFrameAt=0;lastStamp=null;lastValidAt=0;rejected=0;measured=0;clockJump=false;previousWall=null;previousMono=null;clockEstimate=null;previousStats=null;latestStats={};receiveSamples=[];measurementGeneration++;diagnosticWarning='';$('transportPath').textContent='—';$('sourceKind').textContent='Source: —';}
function onFrame(now,meta,peer){
  if(peer!==pc)return;
  if(playbackBlocked){playbackBlocked=false;$('playbackStart').hidden=true;message();}
  lastFrameAt=performance.now();
  if(Number.isFinite(meta.receiveTime)&&Number.isFinite(meta.expectedDisplayTime)){
    const delta=meta.expectedDisplayTime-meta.receiveTime;
    if(delta>=0){receiveSamples.push({t:lastFrameAt,v:delta});receiveSamples=receiveSamples.filter(s=>lastFrameAt-s.t<=1000);}
  }
  if(!markerEnabled){if(pc)callbackId=video.requestVideoFrameCallback((now,meta)=>onFrame(now,meta,peer));return;}
  try{
    ctx.drawImage(video,0,0,1280,16,0,0,1280,16);
    const stamp=FastVideoTiming.decode(ctx.getImageData(0,0,1280,16).data);
    const wall=Date.now(),mono=performance.now();
    if(previousWall!==null && Math.abs((wall-previousWall)-(mono-previousMono))>30){clockJump=true;samples=[];instant=null;clockEstimate=null;}
    previousWall=wall;previousMono=mono;
    if(stamp!==null && stamp!==lastStamp){
      lastStamp=stamp;
      if(!clockJump && Number.isFinite(meta.expectedDisplayTime)){
        const epochOffset=wall-(performance.timeOrigin+mono);
        const delta=FastVideoTiming.displayUTC(meta)+epochOffset-stamp;
        // No clamping: negative results indicate a clock/measurement problem.
        if(delta>=-30000 && delta<=30000){instant=delta;lastValidAt=mono;measured++;samples.push({t:mono,v:delta});samples=samples.filter(s=>mono-s.t<=1000);}
        else{instant=null;samples=[];rejected++;diagnosticWarning=`Implausible latency measurement (${Math.round(delta)} ms). Check clock synchronization.`;}
      }
    }else if(stamp===null){instant=null;rejected++;}
  }catch(e){rejected++;diagnosticWarning=`Unable to read timestamp: ${e.name}`;}
  render();
  if(pc)callbackId=video.requestVideoFrameCallback((now,meta)=>onFrame(now,meta,peer));
}
function render(){
  const now=performance.now();samples=samples.filter(s=>now-s.t<=1000);
  const fresh=lastValidAt>0&&now-lastValidAt<1500&&!clockJump&&!document.hidden&&lastFrameAt&&now-lastFrameAt<1500;
  renderDiagnostics(fresh?instant:null);
  const live=pc?.connectionState==='connected'&&lastFrameAt>0&&now-lastFrameAt<1500;
  $('connection').textContent=live?'Connected':pc?.connectionState==='connected'?'No video':connectBusy?'Connecting …':'Not connected';
  $('connection').classList.toggle('signal-good',!!live);
  $('connection').classList.toggle('signal-bad',!live);
}

function diagnosticReport(raw){
  const now=performance.now();receiveSamples=receiveSamples.filter(s=>now-s.t<=1000);
  const adjusted=Number.isFinite(raw)&&clockEstimate?raw+clockEstimate.offset:null;
  return {raw_latency_ms:raw,adjusted_estimate_ms:adjusted,
    viewer_clock_ahead_ms:clockEstimate?-clockEstimate.offset:null,
    clock_uncertainty_ms:clockEstimate?.uncertainty??null,http_rtt_ms:clockEstimate?.rtt??null,
    webrtc_rtt_ms:latestStats.rtt??null,jitter_buffer_ms:latestStats.buffer??null,
    decode_ms:latestStats.decode??null,receive_to_display_ms:FastVideoTiming.median(receiveSamples.map(s=>s.v)),
    fps:latestStats.fps??null, path_is_relay:latestStats.path ? Number(latestStats.path==="Relay") : -1, protocol_is_tcp:latestStats.protocol ? Number(latestStats.protocol==="tcp") : -1};
}
function renderDiagnostics(raw){
  const d=diagnosticReport(raw),number=v=>Number.isFinite(v)?v.toFixed(1).replace(/\.0$/,''):'—';
  const live=pc?.connectionState==='connected'&&lastFrameAt&&performance.now()-lastFrameAt<1500;
  const clockMismatch=clockEstimate&&Math.abs(clockEstimate.offset)>clockEstimate.uncertainty+20;
  const useEstimate=estimatedClockMode||!!clockMismatch;
  const displayedLatency=useEstimate?d.adjusted_estimate_ms:d.raw_latency_ms;
  const validLatency=live&&Number.isFinite(displayedLatency)&&displayedLatency>=0;
  $('latencyLabel').textContent=useEstimate?'Latency (estimated)':'Latency';
  $('adjusted').title=useEstimate?'From FastVideo input; estimated clock correction, uncertainty '+(Number.isFinite(d.clock_uncertainty_ms)?Math.round(d.clock_uncertainty_ms)+' ms':'unknown'):'From FastVideo input to browser display; synchronized computer clocks required';
  $('latencyMetric').hidden=!validLatency;
  $('adjusted').textContent=validLatency?Math.round(displayedLatency):'—';
  $('buffer').textContent=live?number(d.jitter_buffer_ms):'—';
  $('decode').textContent=live?number(d.decode_ms):'—';
  $('rtt').textContent=live?number(d.webrtc_rtt_ms):'—';
  $('receiveFps').textContent=live?number(d.fps):'—';
  $('receiveBitrate').textContent=live&&Number.isFinite(latestStats.bitrate)?latestStats.bitrate.toFixed(2):'—';
}

async function probeClocks(){
  if(!pc||clockBusy||clockJump)return;
  clockBusy=true;const generation=measurementGeneration,batch=[];
  try{
    for(let i=0;i<6;i++){
      const t0=Date.now(),p0=performance.now();
      const response=await fetch('/api/clock',{cache:'no-store',signal:AbortSignal.timeout(3000)});
      if(!response.ok)throw Error('Clock comparison unavailable');
      const server=await response.json(),p3=performance.now(),t3=Date.now();
      const sample=FastVideoTiming.clockSample(t0,server.received_ms,server.sent_ms,t3,p3-p0);
      if(generation!==measurementGeneration||!pc)return;
      if(sample)batch.push(sample);
      await new Promise(resolve=>setTimeout(resolve,70));
    }
    if(batch.length>=3&&generation===measurementGeneration){batch.sort((a,b)=>a.rtt-b.rtt);clockEstimate=batch[0];}
  }catch{ /* A missing clock estimate leaves latency unavailable. */ }
  finally{clockBusy=false;}
}
function updateSourceKind(status){
  $('viewerLogo').hidden=!!status.viewer_hide_logo;
  $('viewerBrandText').hidden=!status.viewer_hide_logo;
  const title=status.viewer_title??'FastVideo Live',split=title.indexOf(';');
  $('viewerTitle').textContent=split<0?title:title.slice(0,split).trim()+'\n'+title.slice(split+1).trim();
  const kind=status.source?.kind;
  const name=kind==='ndi'?'NDI':kind==='omt'?'OMT':kind==='sdi'?'SDI':kind==='test'?'Test pattern':'—';
  $('sourceKind').textContent=`Source: ${name}`;
}
let sourceStatusBusy=false;
async function refreshSourceKind(){
  if(!pc||document.hidden||sourceStatusBusy)return;
  sourceStatusBusy=true;const generation=measurementGeneration;
  try{
    const response=await fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(3000)});
    if(!response.ok)throw Error('Streaming server unavailable');
    const status=await response.json();
    if(pc&&generation===measurementGeneration)updateSourceKind(status);
  }catch{if(generation===measurementGeneration)$('sourceKind').textContent='Source: —';}
  finally{sourceStatusBusy=false;}
}
async function collectDiagnostics(){
  const peer=pc,generation=measurementGeneration;
  if(!peer||peer.connectionState!=='connected'||statsBusy||document.hidden)return;
  statsBusy=true;
  try{
    const stats=await peer.getStats();if(peer!==pc||generation!==measurementGeneration)return;
    const audio=[...stats.values()].find(s=>s.type==='inbound-rtp'&&(s.kind==='audio'||s.mediaType==='audio'));
    if(audio){const codec=stats.get(audio.codecId);$('audioDiagnostics').textContent=`${codec?.mimeType||'Audio'} | ${codec?.channels||2} channels | ${(codec?.clockRate||48000)/1000} kHz${Number.isFinite(audio.audioLevel)?` | Level ${audio.audioLevel>0?(20*Math.log10(audio.audioLevel)).toFixed(1):'−∞'} dBFS`:''}`;}
    const inbound=[...stats.values()].find(s=>s.type==='inbound-rtp'&&(s.kind==='video'||s.mediaType==='video'));
    if(inbound){
      const bandwidth={id:`${inbound.id}/${audio?.id||''}`,timestamp:inbound.timestamp,
        bytes:inbound.bytesReceived+(audio?.bytesReceived||0)};
      latestStats.bitrate=receiveBitrate(bandwidth,previousBandwidthStats);
      previousBandwidthStats=bandwidth;
      latestStats.codec=stats.get(inbound.codecId)?.mimeType||'—';
      const fmtp=stats.get(inbound.codecId)?.sdpFmtpLine||'',profileId=fmtp.match(/profile-level-id=([0-9a-f]{6})/i)?.[1]?.toLowerCase();
      const profile=profileId?.startsWith('64')?'High':profileId?.startsWith('4d')?'Main':profileId?.startsWith('42')?'Baseline':null;
      $('videoCodec').textContent=latestStats.codec.replace('video/','').toUpperCase();
      const previous=previousStats?.id===inbound.id?previousStats:null;
      latestStats.buffer=FastVideoTiming.intervalAverage(inbound,previous,'jitterBufferDelay','jitterBufferEmittedCount');
      latestStats.decode=FastVideoTiming.intervalAverage(inbound,previous,'totalDecodeTime','framesDecoded');
      latestStats.fps=Number.isFinite(inbound.framesPerSecond)?inbound.framesPerSecond:null;
      const transport=stats.get(inbound.transportId);
      const pair=stats.get(transport?.selectedCandidatePairId)||[...stats.values()].find(s=>s.type==='candidate-pair'&&s.nominated&&s.state==='succeeded');
      latestStats.rtt=Number.isFinite(pair?.currentRoundTripTime)?pair.currentRoundTripTime*1000:null;
      const local=stats.get(pair?.localCandidateId),remote=stats.get(pair?.remoteCandidateId);
      latestStats.protocol=local?.protocol||remote?.protocol||null;
      latestStats.path=local&&remote ? (local.candidateType==="relay"||remote.candidateType==="relay"?"Relay":"Direct") : null;
      $("transportPath").textContent=local&&remote ? ` ${latestStats.path} · ${(local.protocol||remote.protocol||"?").toUpperCase()}`.trim() : "—";
      previousStats={...inbound};
    }
    const now=performance.now(),fresh=lastValidAt&&now-lastValidAt<1500&&!clockJump;
    const raw=fresh?FastVideoTiming.median(samples.filter(s=>now-s.t<=1000).map(s=>s.v)):null;
    const report=diagnosticReport(raw),url=sessionURL;
    report.receive_bitrate_mbps=latestStats.bitrate;
    report.receiver_latency_support=receiverLatencySupport;
    report.audio_output_primed=Number(audioOutputContext?.state==='running');
    report.audio_output_base_latency_ms=audioOutputContext?.baseLatency*1000;
    report.audio_output_latency_ms=audioOutputContext?.outputLatency*1000;
    if(audio){
      const prior=previousAudioStats?.id===audio.id?previousAudioStats:null;
      report.audio_jitter_buffer_ms=FastVideoTiming.intervalAverage(audio,prior,'jitterBufferDelay','jitterBufferEmittedCount');
      report.audio_jitter_target_ms=FastVideoTiming.intervalAverage(audio,prior,'jitterBufferTargetDelay','jitterBufferEmittedCount');
      report.audio_jitter_minimum_ms=FastVideoTiming.intervalAverage(audio,prior,'jitterBufferMinimumDelay','jitterBufferEmittedCount');
      report.audio_concealed_samples=audio.concealedSamples;
      report.audio_inserted_samples=audio.insertedSamplesForDeceleration;
      report.audio_removed_samples=audio.removedSamplesForAcceleration;
      previousAudioStats={...audio};
    }
    if(inbound){
      Object.assign(report,{packets_lost:inbound.packetsLost,packets_received:inbound.packetsReceived,frames_dropped:inbound.framesDropped,
        nack_count:inbound.nackCount,pli_count:inbound.pliCount,frames_decoded:inbound.framesDecoded,frames_received:inbound.framesReceived,
        video_jitter_target_ms:FastVideoTiming.intervalAverage(inbound,previousStatsForRate,'jitterBufferTargetDelay','jitterBufferEmittedCount'),
        video_jitter_minimum_ms:FastVideoTiming.intervalAverage(inbound,previousStatsForRate,'jitterBufferMinimumDelay','jitterBufferEmittedCount'),
        playback_paused:Number(video.paused),playback_ready_state:video.readyState,playback_muted:Number(video.muted),
        displayed_frame_age_ms:lastFrameAt?performance.now()-lastFrameAt:-1});
      const prior=previousStatsForRate;
      if(prior&&inbound.timestamp>prior.timestamp)report.receive_fps=(inbound.framesReceived-prior.framesReceived)*1000/(inbound.timestamp-prior.timestamp);
      previousStatsForRate={...inbound};
    }
    if(url&&peer===pc){const response=await fetch(url+'/diagnostics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(report),signal:AbortSignal.timeout(3000)});if(response.status===410&&peer===pc)await stopByOrganiser();}
  }catch{ /* Unsupported stats or a closed session leave a dash, never an invented measurement. */ }
  finally{statsBusy=false;}
}
async function gathered(peer,signal){
  if(peer.iceGatheringState==='complete')return;
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{peer.removeEventListener('icegatheringstatechange',check);reject(Error('Unable to gather local network addresses.'));},8000);
    function check(){if(peer.iceGatheringState==='complete'){clearTimeout(timer);peer.removeEventListener('icegatheringstatechange',check);resolve();}}
    peer.addEventListener('icegatheringstatechange',check);
    signal.addEventListener('abort',()=>{clearTimeout(timer);peer.removeEventListener('icegatheringstatechange',check);reject(Error('Connection setup cancelled'));},{once:true});check();});
}
async function startPlayback(peer,userGesture=false){
  if(peer!==pc||!video.srcObject||(playbackBlocked&&!userGesture))return;
  const attempt=++playbackAttempt;
  try{await video.play();if(peer===pc&&attempt===playbackAttempt){playbackBlocked=false;$('playbackStart').hidden=true;message();}}
  catch(error){
    if(peer!==pc||attempt!==playbackAttempt)return;
    // A reconnect has no fresh user gesture on iOS. Resume the picture muted;
    // sound can be enabled explicitly once the new stream is playing.
    if(error.name==='NotAllowedError'&&!video.muted){
      video.muted=true;$('sound').textContent='Enable sound';
      $('audioStatus').textContent=audioReceived?'Opus received · muted':'Waiting for audio';
      try{await video.play();if(peer===pc&&attempt===playbackAttempt){playbackBlocked=false;$('playbackStart').hidden=true;message();}return;}catch(retryError){error=retryError;}
    }
    if(peer!==pc||attempt!==playbackAttempt)return;
    if(error.name==='NotAllowedError'){
      playbackBlocked=true;clearStartupRecovery();
      $('playbackStart').hidden=false;
      message('Please tap Start playback.');
    }else if(error.name!=='AbortError')message(`Playback start: ${error.name}.`);
  }
}
async function disconnect(manual=true){
  clearStartupRecovery();releaseAudioOutput();playbackAttempt++;playbackBlocked=false;$('playbackStart').hidden=true;
  connectAttempt++;if(connectController)connectController.abort();connectController=null;connectBusy=false;
  if(manual){wantConnected=false;clearRetry();}
  const url=sessionURL;sessionURL=null;const previous=pc;pc=null;
  if(callbackId!==null && video.cancelVideoFrameCallback)video.cancelVideoFrameCallback(callbackId);
  callbackId=null;
  video.onloadedmetadata=null;video.pause();
  if(mediaStream)for(const track of mediaStream.getTracks())track.stop();
  if(previous){previous.ontrack=null;previous.onconnectionstatechange=null;previous.close();}
  video.srcObject=null;video.load();mediaStream=null;audioReceived=false;
  video.muted=true;$('sound').textContent='Enable sound';
  previousStatsForRate=null;
  $('audioStatus').textContent='Not connected';
  $('audioDiagnostics').textContent='Audio/Opus · —';
  $('videoCodec').textContent='—';
  if(url)fetch(url,{method:'DELETE',keepalive:true,signal:AbortSignal.timeout(3000)}).catch(()=>{});
  $('clientId').hidden=true;$('clientId').textContent='';
  $('connection').textContent='Not connected';resetMeasurement();render();updateConnectionButton();
}
async function connect(manual=true){
  if(manual){wantConnected=true;retryAttempt=0;startupRecoveryUsed=false;clearRetry();}
  if(connectBusy||pc)return;
  connectBusy=true;const attempt=++connectAttempt,controller=new AbortController();connectController=controller;
  const deadline=setTimeout(()=>controller.abort(),20000);connectionStartedAt=performance.now();
  updateConnectionButton();message();resetMeasurement();
  if(!video.requestVideoFrameCallback){message('This browser does not support frame-based measurements. Please use an up-to-date browser.');clearTimeout(deadline);connectBusy=false;wantConnected=false;updateConnectionButton();return;}
  try{
    const statusResponse=await fetch('/api/status',{cache:'no-store',signal:controller.signal});
    if(!statusResponse.ok)throw Error('Streaming server unavailable');
    const senderStatus=await statusResponse.json();
    if(attempt!==connectAttempt||!wantConnected)return;
    const availableCodecs=senderStatus.video_codecs||[senderStatus.video_codec||'h264'];
    markerEnabled=senderStatus.timestamp_marker===true;
    updateSourceKind(senderStatus);
    $('screen').classList.toggle('clean',markerEnabled);
    const iceResponse=await fetch("/api/ice",{cache:"no-store",signal:controller.signal});
    if(!iceResponse.ok)throw Error("ICE configuration unavailable");
    const iceConfig=await iceResponse.json();
    if(attempt!==connectAttempt||!wantConnected)return;
    const peer=new RTCPeerConnection(iceConfig);pc=peer;
    receiverLatencySupport=0;previousAudioStats=null;
    for(const [index,kind] of ['video','audio'].entries()){
      const transceiver=peer.addTransceiver(kind,{direction:'recvonly'});
      receiverLatencySupport|=configureReceiverLatency(transceiver?.receiver)<<(index*2);
    }
    mediaStream=null;video.muted=true;
    video.onloadedmetadata=()=>startPlayback(peer);
    peer.ontrack=e=>{
      if(pc!==peer)return;
      // WebKit must receive a populated stream, not an empty MediaStream that
      // becomes playable only after subsequent addTrack calls.
      if(e.streams?.length)mediaStream=e.streams[0];
      else if(!mediaStream)mediaStream=new MediaStream([e.track]);
      else if(!mediaStream.getTracks().includes(e.track))mediaStream.addTrack(e.track);
      if(video.srcObject!==mediaStream)video.srcObject=mediaStream;
      if(e.track.kind==='video')callbackId=video.requestVideoFrameCallback((now,meta)=>onFrame(now,meta,peer));
      else audioReceived=true;
      startPlayback(peer);
      $('audioStatus').textContent=audioReceived?(video.muted?'Opus received · muted':'Opus received · sound on'):'Waiting for audio';
    };
    peer.onconnectionstatechange=()=>{if(pc!==peer)return;$('connection').textContent={connected:'Connected',connecting:'Connecting …',disconnected:'Connection interrupted',failed:'Connection failed',closed:'Disconnected'}[peer.connectionState]||peer.connectionState;
      if(peer.connectionState==='connected'){clearRetry();retryAttempt=0;message();startPlayback(peer);armStartupRecovery(peer);}
      else if(peer.connectionState==='disconnected')retryConnection(3000);
      else if(peer.connectionState==='failed'){message('Direct media connection failed. Check UDP/NAT or router port forwarding. Reconnecting.');retryConnection();}};
    await peer.setLocalDescription(await peer.createOffer());await gathered(peer,controller.signal);
    if(attempt!==connectAttempt||pc!==peer||!wantConnected)return;
    const preferredCodec=new URL(location.href).searchParams.get('codec');
    const selectedCodec=FastVideoCodec.select(availableCodecs,peer.localDescription.sdp,preferredCodec);
    if(!selectedCodec){const error=Error('This browser does not offer AV1 or a compatible H264 format for WebRTC.');error.unsupportedCodec=true;throw error;}
    const access=new URL(location.href).searchParams.get('access')||'';
    let clientKey='';
    if(access){try{clientKey=sessionStorage.getItem('fastvideo-viewer-instance');if(!clientKey){clientKey=crypto.randomUUID();sessionStorage.setItem('fastvideo-viewer-instance',clientKey);}}catch{clientKey=crypto.randomUUID();}}
    const response=await fetch(`/whep?codec=${selectedCodec}`,{method:'POST',headers:{'Content-Type':'application/sdp','X-FastVideo-Access':access,'X-FastVideo-Client':clientKey},body:peer.localDescription.sdp,signal:controller.signal});
    if(!response.ok){
      const detail=await response.text();
      console.warn('Viewer connection rejected:',response.status);
      if(response.status===403||(response.status===409&&detail.includes('personal link'))){const error=Error(response.status===409?'This link is already in use. Ask the organiser for another connection.':'This viewer link is invalid, disabled, or a personal link is required.');error.unsupportedCodec=true;throw error;}
      const errors={400:'The server rejected the WebRTC connection.',409:'Viewer capacity reached. Please try again later.',429:'Too many connection attempts. Please try again later.',503:'The video source or streaming service is unavailable. Retrying.',504:'Media connection setup timed out. Retrying.'};
      throw Error(errors[response.status]||`Connection request failed (HTTP ${response.status}).`);
    }
    const url=new URL(response.headers.get('Location'),location.href).href;
    if(attempt!==connectAttempt||pc!==peer||!wantConnected){fetch(url,{method:'DELETE',keepalive:true,signal:controller.signal}).catch(()=>{});return;}
    sessionURL=url;
    const clientId=new URL(url).pathname.split('/').pop();
    const userCode=response.headers.get('X-FastVideo-User');
    $('clientId').textContent=userCode?`User ${userCode} · ${clientId.slice(0,8)}`:`Client ${clientId.slice(0,8)}`;$('clientId').title=clientId;$('clientId').hidden=false;
    await peer.setRemoteDescription({type:'answer',sdp:await response.text()});startPlayback(peer);probeClocks();
  }catch(e){if(attempt===connectAttempt&&wantConnected){if(e.unsupportedCodec){message(e.message);await disconnect(true);return;}message(e.name==='AbortError'?'Connection setup timed out. Retrying.':e.name==='TypeError'?'Unable to reach the streaming server. Retrying.':e.name==='Error'?e.message:`Connection failed (${e.name}).`);await disconnect(false);retryConnection();updateConnectionButton();}}
  finally{clearTimeout(deadline);if(attempt===connectAttempt){connectBusy=false;updateConnectionButton();}}
}
let viewerClockFrame=null;
function renderViewerClock(){
  viewerClockFrame=null;
  if($('viewerClock').hidden)return;
  const now=new Date(),pad=n=>String(n).padStart(2,'0');
  $('viewerClockTime').textContent=`${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}.${pad(Math.floor(now.getMilliseconds()/10))}`;
  viewerClockFrame=requestAnimationFrame(renderViewerClock);
}
$('connection').onclick=()=>{
  const show=$('viewerClock').hidden;
  $('viewerClock').hidden=!show;
  $('connection').setAttribute('aria-pressed',String(show));
  if(show)renderViewerClock();
  else if(viewerClockFrame!==null){cancelAnimationFrame(viewerClockFrame);viewerClockFrame=null;}
};
$('connect').onclick=()=>wantConnected&&(connectBusy||pc||retryTimer!==null)?disconnect(true):connect(true);
$('playbackStart').onclick=()=>{primeAudioOutput();return startPlayback(pc,true);};
$('sound').onclick=()=>{primeAudioOutput();video.muted=!video.muted;startPlayback(pc,true);$('sound').textContent=video.muted?'Enable sound':'Mute sound';$('audioStatus').textContent=audioReceived?(video.muted?'Opus received · muted':'Opus received · sound on'):'Waiting for audio';};
$('fullscreen').onclick=()=>$('screen').requestFullscreen().catch(e=>message(`Fullscreen is unavailable (${e.name}).`));

document.addEventListener('visibilitychange',()=>{samples=[];instant=null;lastValidAt=0;if(!document.hidden&&wantConnected&&!connectBusy){if(!pc)connect(false);else if(pc.connectionState==='failed'||pc.connectionState==='closed'||pc.connectionState==='disconnected')retryConnection(0);else video.play().catch(()=>{});}});
window.addEventListener('pagehide',()=>{resumeAfterPageHide=wantConnected;disconnect(true);});
window.addEventListener('pageshow',event=>{if(event.persisted&&resumeAfterPageHide){resumeAfterPageHide=false;wantConnected=true;connect(false);}});
renderTimer=setInterval(render,250);render();
setInterval(collectDiagnostics,1000);
setInterval(refreshSourceKind,5000);
setInterval(()=>{if(!document.hidden)probeClocks();},10000);
setInterval(()=>{
  if(!wantConnected||connectBusy||document.hidden||retryTimer!==null||playbackBlocked)return;
  if(!pc){connect(false);return;}
  const now=performance.now();
  if((lastFrameAt&&now-lastFrameAt>8000)||(!lastFrameAt&&now-connectionStartedAt>15000)){
    message('No new video frames. Reconnecting.');retryConnection();
  }
},1000);
// Explicit click is kept available for browsers that restrict autoplay.
connect();

fetch('/api/status',{cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject()).then(updateSourceKind).catch(()=>{});
