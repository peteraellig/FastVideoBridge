'use strict';
const $=id=>document.getElementById(id);
// Form field ids per connection type (kind + suffix). Shared by saved outputs, productions and profiles.
const FORM_FIELDS={whip:{url:'Endpoint',token:'Token',codec:'Codec',bitrate:'Bitrate',preset:'Quality',audio_bitrate:'AudioBitrate'},receive:{mode:'Mode',viewer_url:'ViewerUrl',codec:'Codec',whep_token:'BearerToken',token:'Token',name:'Name',listen_port:'Port',public_ip:'PublicIp',endpoint_name:'EndpointName',output_kind:'OutputKind',sdi_device:'SdiDevice',sdi_interlaced:'SdiInterlaced',ndi_audio_minus20:'NdiAudioMinus20'},srt:{host:'Host',port:'Port',latency:'Latency',streamid:'Streamid',passphrase:'Passphrase',aes:'Aes',codec:'Codec',bitrate:'Bitrate',preset:'Quality',audio_bitrate:'AudioBitrate',keyframe:'Keyframe',cbr:'Cbr'}};
const NUMERIC_FORM_FIELDS=new Set(['bitrate','audio_bitrate','listen_port','port','latency','aes','keyframe','sdi_device']);
const MAX_BITRATE=10000000; // WHIP/SRT video; settings saved earlier with up to 12 Mbit/s show as 10
let busy=false,polling=false,running=false,initialized=false,selected=null,controlError='',errorUntil=0,stream={};
const outputEnabled={whip:false,srt:false,receive:false};
function showToggle(button,active,label){button.textContent=active?'Running · press to stop':label;button.classList.toggle('stop',active);button.classList.toggle('primary',!active);button.setAttribute('aria-pressed',String(active));button.disabled=busy;}
const key=s=>s?JSON.stringify([s.kind,s.address]):'';
for(const prefix of ['whip','srt']){
  for(let bitrate=1000000;bitrate<=MAX_BITRATE;bitrate+=500000){const o=document.createElement('option');o.value=bitrate;o.textContent=(bitrate/1e6).toFixed(1).replace('.0','')+' Mbit/s';$(prefix+'Bitrate').append(o);}
  $(prefix+'Bitrate').value='4000000';
}
const COPY_ICON='<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="5.5" width="8.5" height="8.5" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M3 10.5V3.5A1.5 1.5 0 0 1 4.5 2h6.5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
async function copyText(text){
  try{await navigator.clipboard.writeText(text);return true;}
  catch{const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';document.body.append(area);area.select();
    let ok=false;try{ok=document.execCommand('copy');}catch{}area.remove();return ok;}
}
function copyButton(getText,label){
  const button=document.createElement('button');button.type='button';button.className='copy-btn';button.title=label;button.setAttribute('aria-label',label);button.innerHTML=COPY_ICON;
  button.onclick=async()=>{const ok=await copyText(getText());button.classList.toggle('copied',ok);button.title=ok?'Copied':'Copy failed';
    setTimeout(()=>{button.classList.remove('copied');button.title=label;},1500);};
  return button;
}
// Rebuild only when the text changes: the status refresh must not discard the "copied" feedback.
function setCopyLines(element,lines,placeholder,label){
  const key=JSON.stringify([lines,placeholder]);if(element.dataset.copyKey===key)return;element.dataset.copyKey=key;
  if(!lines.length){element.textContent=placeholder;return;}
  element.replaceChildren(...lines.map(line=>{const row=document.createElement('span');row.className='copy-line';const text=document.createElement('span');text.textContent=line;row.append(text,copyButton(()=>line,label));return row;}));
}
async function api(path,method='GET',data){const r=await fetch(path,{method,cache:'no-store',signal:AbortSignal.timeout(20000),...(data?{headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});if(!r.ok)throw Error(await r.text());return r.json();}
async function action(fn){if(busy)return;busy=true;try{await fn();controlError='';}catch(e){controlError=e.message;errorUntil=Date.now()+10000;$('inputNotice').textContent=e.message;}finally{busy=false;await refresh();}}
async function discover(cached=false){const result=await api('/api/sources'+(cached?'?cached=1':''));$('source').replaceChildren();for(const s of result.sources){const o=document.createElement('option');o.value=JSON.stringify(s);o.textContent=s.kind==='test'?'TEST · Internal test pattern':`${s.kind.toUpperCase()} · ${s.name}`;o.selected=key(s)===key(selected);$('source').append(o);}}
$('discover').onclick=()=>action(discover);
function sourceSetFeedback(button,success=true){
 button.dataset.originalLabel??=button.textContent;
 clearTimeout(button.sourceSetTimer);
 button.textContent=success?'Source set':'Error';button.classList.toggle('source-set',success);button.classList.toggle('source-set-error',!success);
 button.sourceSetTimer=setTimeout(()=>{button.textContent=button.dataset.originalLabel;button.classList.remove('source-set','source-set-error');},1800);
}
async function setConfirmedSource(button,data){
 button.dataset.originalLabel??=button.textContent;
 clearTimeout(button.sourceSetTimer);button.classList.remove('source-set','source-set-error');
 button.textContent='Checking…';button.disabled=true;
 try{await api('/api/source','POST',typeof data==='function'?data():data);sourceSetFeedback(button);}
 catch(error){sourceSetFeedback(button,false);throw error;}
 finally{button.disabled=false;}
}
$('setSource').onclick=()=>action(()=>setConfirmedSource($('setSource'),()=>({source:JSON.parse($('source').value)})));
$('inputAudio').onchange=()=>{inputAudioDraft=true;};
$('setInputAudio').onclick=()=>action(async()=>{const [mode,index]=$('inputAudio').value.split(':');const state=await api('/api/input-audio','POST',{mode,index:Number(index)});inputAudioDraft=false;inputAudioKey='';renderInputAudio(state);audioNote(['Saved',inputAudioText(state)].filter(Boolean).join(' · '));});
$('setOutputMode').onclick=()=>action(async()=>{await api('/api/output-format','POST',{output_mode:$('outputMode').value,interlaced:$('interlacedMode').value});$('formatNotice').textContent='Output format saved. Frame rate follows input.';});
$('setOmt').onclick=()=>action(()=>setConfirmedSource($('setOmt'),{omt_address:$('omt').value.trim()}));
for(const id of ['inputToggle','inputToggleMain'])$(id).onclick=()=>action(()=>api(running?'/api/stop':'/api/start','POST',{}));
// SDI card running an interlaced mode (field dominance not 'prog'): field rate + i.
function sdiInterlaced(s){const m=s.output_kind==='sdi'&&s.sdi?.mode;if(!m||m.field_dominance===1886547815)return '';return `${m.width}x${m.height}-${Number((m.fps_n/m.fps_d*2).toFixed(2))}i · SDI`;}
function pictureFormat(v,rate,codec=''){
 if(!v?.width||!v?.height)return '—';
 // Broadcast notation as in FastVideo: 1920x1080-50p, 1920x1080-50i (field rate, deinterlaced), 1920x1080-50psf.
 const fps=Number(rate??v.fps),ok=Number.isFinite(fps)&&fps>0;
 // woven: interlaced picture kept as it is (25 frames carry 50 fields).
 const rateText=!ok?'':v.scan==='psf'?`-${Number((fps*2).toFixed(2))}psf`:v.scan==='woven'?`-${Number((fps*2).toFixed(2))}i`:`-${Number(fps.toFixed(2))}${v.scan==='i'?'i':'p'}`;
 return `${v.width}x${v.height}${rateText}${v.scan==='i'?' · deinterlaced':v.scan==='woven'?' · fields kept':''}${codec?' · '+codec.toUpperCase():''}`;
}
// Input badge: the format that was read, in short broadcast notation (Input 1080p50, Input 1080i50).
function inputBadge(v){
 if(!v?.height)return 'Signal present';
 const fps=Number(v.fps),ok=Number.isFinite(fps)&&fps>0,doubled=v.scan==='psf'||v.scan==='woven';
 const scan=v.scan==='psf'?'psf':v.scan==='i'||v.scan==='woven'?'i':'p';
 return `Input ${v.height}${scan}${ok?Number((doubled?fps*2:fps).toFixed(2)):''}`;
}
// Setup -> Input audio: stereo pairs and single channels of the running source, as in FastVideo.
// Without a running source the eight SDI channels are offered, so it can be set beforehand.
let inputAudioKey='',inputAudioDraft=false;
function inputAudioValue(a){return a?`${a.mode}:${a.index}`:'pair:1';}
function inputAudioOptions(channels,current){
 const n=channels||8,options=[];
 for(let pair=1;pair<=Math.floor(n/2);pair++)options.push([`pair:${pair}`,`${pair*2-1} / ${pair*2} · Stereo`]);
 for(let ch=1;ch<=n;ch++)options.push([`mono:${ch}`,`${ch} · Mono → L + R`]);
 if(!options.some(o=>o[0]===current)){const [mode,index]=current.split(':');options.push([current,(mode==='mono'?`${index} · Mono`:`${index*2-1} / ${index*2} · Stereo`)+' · not in this source']);}
 return options;
}
function inputAudioText(a){
 if(!a?.channels)return '';
 const used=a.active[0]===a.active[1]?`channel ${a.active[0]} on both sides`:`channels ${a.active[0]} / ${a.active[1]}`;
 return a.available?`Source has ${a.channels} channels · sending ${used}`:`Source has only ${a.channels} channel${a.channels===1?'':'s'} · sending ${used} instead`;
}
function audioNote(text){const note=$('inputAudioNotice');note.textContent=text;note.title=text;}
let inputAudioShort='';
function renderInputAudio(a){
 if(!a)return;const select=$('inputAudio'),current=inputAudioValue(a),key=`${a.channels}|${current}`;
 if(key!==inputAudioKey){inputAudioKey=key;const keep=inputAudioDraft?select.value:current;
  select.replaceChildren(...inputAudioOptions(a.channels,current).map(([value,label])=>new Option(label,value)));
  select.value=[...select.options].some(o=>o.value===keep)?keep:current;}
 if(!inputAudioDraft)audioNote(inputAudioText(a));
 // Sidebar: what is sent (the channels in use once the source is known, else the saved choice).
 const [left,right]=a.active||(a.mode==='mono'?[a.index,a.index]:[a.index*2-1,a.index*2]);
 // Shown in the input badge behind the picture format: Input 1080p50 · Audio 1+2.
 inputAudioShort=left===right?`Audio ${left} mono`:`Audio ${left}+${right}`;
}
// Laptops show their picture through the processor graphics while the encoder runs on the NVIDIA card:
// name the adapter of the encoder first, then the others.
function gpuNames(system,backend){
 const names=system.gpu_names?.length?system.gpu_names:[system.gpu_name].filter(Boolean);
 const maker={nvenc:/nvidia/i,qsv:/intel/i,amf:/amd|radeon/i}[backend];
 const used=maker?names.filter(name=>maker.test(name)):[];
 return [...used,...names.filter(name=>!used.includes(name))];
}
const sentRateSamples={};
function sentVideoRate(prefix,s,now=performance.now()){
 const count=s.video_frames;
 const active=s.enabled&&['connected','sending'].includes(s.state);
 if(!active||!Number.isFinite(count)){delete sentRateSamples[prefix];return '—';}
 // Use the sender clock paired with the completed-picture counter. Rendering
 // can wait for unrelated status requests or a throttled browser timer.
 const serverClock=Number.isFinite(s.send_sample_time_ms);
 now=serverClock?s.send_sample_time_ms:now;
 const session=JSON.stringify([s.send_session_id,s.attempts,s.codec,serverClock]);
 let data=sentRateSamples[prefix];
 if(!data||data.session!==session||count<data.samples.at(-1).count||now<data.samples.at(-1).now){
  data={session,samples:[]};sentRateSamples[prefix]=data;
 }
 if(data.samples.length&&now===data.samples.at(-1).now)return data.rate||"—";
 data.samples.push({now,count});
 // Keep a roughly three-second window, including its preceding baseline.
 while(data.samples.length>2&&data.samples[1].now<=now-3000)data.samples.shift();
 const first=data.samples[0],elapsed=(now-first.now)/1000;
 data.rate=elapsed>=.5?((count-first.count)/elapsed).toFixed(1)+' fps':'—';
 return data.rate;
}
function showOutput(prefix,s){
 $(prefix+'Fps').textContent=sentVideoRate(prefix,s);
 const present=stream.running&&stream.signal&&!stream.error;
 $(prefix+'InputFormat').textContent=present?pictureFormat(stream.input_format):'—';
 $(prefix+'OutputFormat').textContent=present&&s.enabled?pictureFormat(stream.input_format?.scan==='woven'&&stream.height===stream.input_format.height?{...stream,scan:'woven'}:stream,stream.format_fps,s.codec):'—';

 const warning=!!(s.enabled&&s.warning);
 const card=$(prefix+'State').closest('.card');card.classList.toggle('connected-card',['connected','sending'].includes(s.state));card.classList.toggle('warning-card',warning);
 const state=$(prefix+'State');state.textContent=s.state||'stopped';state.className='badge '+(warning?'warn':['connected','sending'].includes(s.state)?'good':s.enabled?'warn':'');
 const notice=$(prefix+'Notice');notice.textContent=[warning?s.warning_text:'',s.detail].filter(Boolean).join(' · ');notice.title=notice.textContent;notice.classList.toggle("has-warning",!!(warning||s.detail));
 outputEnabled[prefix]=!!s.enabled;showToggle($(prefix+'Start'),!!s.enabled,'Start '+prefix.toUpperCase());
 for(const field of $(prefix+'Form').querySelectorAll('input,select'))field.disabled=busy||s.enabled;
 $(prefix+'Attempts').textContent=s.attempts||0;
 if(s.enabled){if(s.audio_bitrate)$(prefix+'AudioBitrate').value=String(s.audio_bitrate);if(s.codec)$(prefix+'Codec').value=s.codec;if(s.bitrate)$(prefix+'Bitrate').value=String(Math.min(s.bitrate,MAX_BITRATE));if(s.preset)$(prefix+'Quality').value=s.preset;}
 if(prefix==='srt'&&s.enabled&&s.host){$('srtHost').value=s.host;$('srtPort').value=s.port;$('srtLatency').value=s.latency;$('srtAes').value=s.aes;$('srtKeyframe').value=s.keyframe??1;$('srtCbr').checked=!!s.cbr;}
 if(prefix==='whip'){$('whipFrames').textContent=`${s.video_frames||0} / ${s.audio_frames||0}`;$('whipBytes').textContent=((s.video_bytes||0)/1e6).toFixed(2)+' / '+((s.audio_bytes||0)/1e6).toFixed(2)+' MB';$('whipDrops').textContent=`${s.video_drops||0} / ${s.audio_drops||0}`;}
 else{$('srtFrames').textContent=`${s.video_frames||0} / ${s.audio_frames||0}`;$('srtDrops').textContent=s.queue_drops||0;}
}
for(const prefix of ['whip','srt']){
 $(prefix+'Form').onsubmit=e=>{e.preventDefault();action(async()=>{
  const encoding={codec:$(prefix+'Codec').value,bitrate:Number($(prefix+'Bitrate').value),preset:$(prefix+'Quality').value,audio_bitrate:Number($(prefix+'AudioBitrate').value)};
  const data=prefix==='whip'?{...encoding,url:$('whipEndpoint').value.trim(),token:$('whipToken').value.trim()}:{...encoding,host:$('srtHost').value.trim(),port:Number($('srtPort').value),latency:Number($('srtLatency').value),streamid:$('srtStreamid').value,passphrase:$('srtPassphrase').value,aes:Number($('srtAes').value),keyframe:Number($('srtKeyframe').value),cbr:$('srtCbr').checked};
  showOutput(prefix,await api('/api/'+prefix,'POST',data));
 });};
 $(prefix+'Start').onclick=()=>{if(outputEnabled[prefix])action(async()=>showOutput(prefix,await api('/api/'+prefix,'DELETE')));else $(prefix+'Form').requestSubmit();};
}
async function refresh(){
 if(polling||document.hidden)return;polling=true;
 try{
  const sample=await api('/api/dashboard');
  const results=['status','whip','srt','system','receive'].map(key=>({status:'fulfilled',value:sample[key]}));
  for(let i=0;i<results.length;i++){
   if(results[i].status!=='fulfilled'){if(i<3)$(i===0?'inputNotice':i===1?'whipNotice':'srtNotice').textContent='Control service unavailable';if(i===0){$('inputPicture').classList.remove('signal-good');$('inputPicture').classList.add('signal-bad');$('inputState').textContent='Unavailable';$('inputState').className='badge bad';}continue;}
   const s=results[i].value;
   if(i===0){
    stream=s;running=!!(s.input_enabled??s.running);selected=s.selected_source;
    renderInputAudio(s.input_audio);$('selected').textContent=selected?`${selected.kind.toUpperCase()} · ${selected.name}`:'No source selected';
    for(const id of ['inputToggle','inputToggleMain'])showToggle($(id),running,'Start input');
    const good=running&&s.signal&&!s.error;$('inputPicture').classList.toggle('signal-good',!!good);$('inputPicture').classList.toggle('signal-bad',!good);$('inputState').textContent=good?[inputBadge(s.input_format),inputAudioShort].filter(Boolean).join(' · '):running?'No signal':'Stopped';$('inputState').className='badge '+(good?'good':'bad');
    $('fps').textContent=`${Number.isFinite(s.output_fps)?s.output_fps.toFixed(1):'—'} fps`;$('fps').title=s.width&&s.height?`${s.width}×${s.height} · source ${s.format_fps} fps`:'';$('whipCodec').querySelector('option[value=av1]').disabled=s.video_codec!=='av1';$('encoding').textContent=`${(s.video_codecs||[s.video_codec,'h264']).filter(Boolean).map(c=>c.toUpperCase()).join(' + ')} · ${s.encoder_backend||'hardware'}`;
    $('audioPeak').textContent=`${Number.isFinite(s.audio_peak_dbfs)?s.audio_peak_dbfs.toFixed(1):'—'} dBFS`;
    $('encodeP95').textContent=`${Number.isFinite(s.encode_ms_p95)?s.encode_ms_p95.toFixed(1):'—'} ms`;
    const up=Math.floor(s.uptime_seconds||0);$('uptime').textContent=[Math.floor(up/3600),Math.floor(up%3600/60),up%60].map(v=>String(v).padStart(2,'0')).join(':');
    if(!busy){$('inputNotice').textContent=(Date.now()<errorUntil?controlError:'')||s.stream_warning||s.error||'';$('inputNotice').title=$('inputNotice').textContent;}
    if(!initialized){initialized=true;}
    if(running)$('preview').src='/api/preview?t='+Date.now();
   }else if(i===1)showOutput('whip',s);else if(i===2)showOutput('srt',s);
   else if(i===4){showReceive(s);}
   else {$('system').textContent=`${Number.isFinite(s.cpu_percent)?s.cpu_percent.toFixed(1):'—'} %`;$('ram').textContent=`${s.ram_used_bytes?(s.ram_used_bytes/1073741824).toFixed(1):'—'} GB`;$('hardware').textContent=[s.cpu_name,...gpuNames(s,stream.encoder_backend)].filter(Boolean).join(' · ')||'—';$('hardware').title=$('hardware').textContent;}
  }
 }catch(e){$('inputNotice').textContent='Control service unavailable';}finally{polling=false;}
}
setInterval(()=>{$('clock').textContent=new Date().toLocaleTimeString('en-GB');},250);setInterval(refresh,1000);async function restoreOutputForms(){
 const saved=await api('/api/output-settings');$('outputMode').value=saved.format?.output_mode||'follow';$('interlacedMode').value=saved.format?.interlaced||'deinterlace';
 const fields=FORM_FIELDS;
 for(const kind of ['whip','srt','receive'])for(const [key,suffix] of Object.entries(fields[kind])){if(saved[kind]?.[key]!==undefined){const el=$(kind+suffix);if(el.type==='checkbox')el.checked=saved[kind][key];else el.value=kind==='receive'&&key==='listen_port'&&!saved[kind][key]?56208:key==='bitrate'?Math.min(saved[kind][key],MAX_BITRATE):saved[kind][key];}}
}
loadSdiDevices(true).then(restoreOutputForms).then(refresh).then(()=>action(()=>discover(true))).catch(e=>{$('inputNotice').textContent=e.message;});

let receivePreviewBusy=false,receivePreviewSignal=false;
async function updateReceivePreview(s){
 const good=s.state==='receiving';receivePreviewSignal=good;$('receivePicture').classList.toggle('signal-good',good);$('receivePicture').classList.toggle('signal-bad',!good);
 if(!good){$('receivePreview').hidden=true;$('receivePreview').removeAttribute('src');$('receivePreviewEmpty').hidden=false;return;}
 if(receivePreviewBusy||document.hidden||$('page-main').hidden)return;
 receivePreviewBusy=true;
 try{
  const response=await fetch('/api/receive/preview',{cache:'no-store',signal:AbortSignal.timeout(3000)});
  if(response.status===204){$('receivePreview').hidden=true;$('receivePreviewEmpty').hidden=false;return;}
  if(!response.ok)return;
  const bytes=new Uint8Array(await response.arrayBuffer());
  if(!receivePreviewSignal)return;
  $('receivePreview').src='data:image/jpeg;base64,'+btoa(String.fromCharCode(...bytes));$('receivePreview').hidden=false;$('receivePreviewEmpty').hidden=true;
 }catch{}finally{receivePreviewBusy=false;}
}
const CODEC_NAMES={h264:'H.264',hevc:'H.265',h265:'H.265',av1:'AV1'};
// Stream key: 32 random bytes as base64url (256 bit). RFC 6749 asks that guessing a token is at
// most 2^-128 likely; short keys like 123456 are only slowed down by the receiver's throttle.
function newStreamKey(){const bytes=crypto.getRandomValues(new Uint8Array(32));return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
function updateKeyHint(){const key=$('receiveToken').value;$('receiveKeyHint').textContent=key&&key.length<16?'Weak key: it can be guessed. Use Generate key for receivers reachable from the Internet.':'';}
let lastReceive=null;
// One text with address and key; the sending Bridge splits it when pasted into its WHIP address field.
// URL = address for senders on the Internet (public IP), LAN = same network; pasting uses URL.
function senderConnection(){const s=lastReceive||{};const lan=(s.urls||[])[0]||'';const url=s.public_url||lan;const key=$('receiveToken').value;
  if(!url||!key)return '';
  return `FastVideo Bridge WHIP\nURL: ${url}\n`+(s.public_url&&lan?`LAN: ${lan}\n`:'')+`Key: ${key}`
    +(s.public_url?'':'\n(No public IP saved: this address works only in this network. Settings → Receiver network → Detect, Save.)');}
// Settings -> receiver network: the addresses a sender uses, and the router rule that belongs to them.
function showReach(s){
 if(s.mode==='whep')return;
 const lan=s.urls||[];let host='';try{host=lan.length?new URL(lan[0]).hostname:'';}catch{}
 setCopyLines($('reachPublic'),s.public_url?[s.public_url]:[],'No public IPv4 saved · LAN only','Copy WHIP URL');
 setCopyLines($('reachLan'),lan,'No LAN address found','Copy WHIP URL');
 const port=s.listen_port||'—';$('ruleExternal').textContent=$('ruleInternal').textContent=port;
 $('ruleAddress').textContent=host||'—';$('ruleAddress').title=lan.length>1?'This PC has several addresses: '+lan.map(url=>{try{return new URL(url).hostname;}catch{return url;}}).join(', '):'';
}
function showReceive(s){
  lastReceive=s;updateKeyHint();showReach(s);
 const receiving=s.state==='receiving';
 // The incoming codec is known from the decoder; the plain codec field stays empty for WHIP.
 s.codec=s.codec||(s.decoder?CODEC_NAMES[s.decoder.codec]||s.decoder.codec:'');
 $('receiveInputFormat').textContent=receiving?pictureFormat(s.input_video_format,s.video_rate,s.codec):'—';
 $('receiveOutputFormat').textContent=receiving?(sdiInterlaced(s)||pictureFormat(s.video_format,s.video_rate,s.output_kind==='ndi'?'Full NDI':s.output_kind==='sdi'?'SDI':'OMT')):'—';
if(s.enabled)$('receiveMode').value=s.mode||'whip';updateReceiveMode();updateReceivePreview(s);if(s.mode!=='whep'&&s.enabled&&!s.listen_port)$('firewallLegacy').checked=true;$('receiveState').closest('.card').classList.toggle('connected-card',['connected','receiving'].includes(s.state));$('receiveState').closest('.card').classList.toggle('warning-card',!!s.warning);const state=$('receiveState');state.textContent=s.state;state.className='badge '+(s.warning?'warn':['connected','receiving'].includes(s.state)?'good':s.enabled?'warn':'');outputEnabled.receive=!!s.enabled;showToggle($('receiveStart'),!!s.enabled,$('receiveMode').value==='whep'?'Connect':'Start receiver');for(const el of [...$('receiveForm').querySelectorAll('input,select'),$('receivePort'),$('receivePublicIp'),$('receiveEndpointName'),$('saveReceiveSettings')])el.disabled=busy||s.enabled;updateReceiveMode();if(s.mode==='whep')setCopyLines($('receiveUrls'),s.viewer_url?[s.viewer_url]:[],'Paste a FastVideo viewer link','Copy viewer link');
else setCopyLines($('receiveUrls'),[s.public_url,...(s.urls||[])].filter(Boolean),'No LAN address found','Copy WHIP URL');const output=(s.enabled?s.output_kind: $('receiveOutputKind').value||s.output_kind||'omt').toUpperCase();$('receiveOutputTitle').textContent=output;$('receiveOutputLabel').textContent=output+' output';if(s.enabled){$('receiveOutputKind').value=s.output_kind||'omt';if(s.sdi_device!=null)$('receiveSdiDevice').value=String(s.sdi_device);$('receiveNdiAudioMinus20').checked=!!s.ndi_audio_minus20;$('receiveSdiInterlaced').checked=!!s.sdi_interlaced;}$('receiveSdiInterlaced').disabled=busy||!!s.enabled;$('receiveNdiAudioMinus20').disabled=busy;updateNdiAudioOption();{const source=s.output_source||s.omt_source||'';
  // OMT/NDI names are pasted into vMix and similar; an SDI connector name is not.
  if(source&&s.output_kind!=='sdi')setCopyLines($('receiveOmt'),[source],'—','Copy source name');
  else setCopyLines($('receiveOmt'),[],source||'—','');}const v=s.video_format,a=s.audio_format;const picture=v?`${v.width}×${v.height}p${Number(s.video_rate||0).toFixed(2).replace(/\.?0+$/,'')} · ${s.codec||'—'}`:'';const audio=s.audio_receiving&&a?`Opus · ${a.channels===2?'stereo':a.channels+' channels'} · ${a.sample_rate/1000} kHz`:'No audio';$('receiveMedia').textContent=s.state==='receiving'?`Receiving OK · ${picture} · ${audio}`:s.enabled?'Waiting for signal':'Stopped';$('receiveStats').textContent=s.settling?`${s.video_frames} video / ${s.audio_frames} audio · Synchronising output… loss counters start at 0 when settled`:`${s.video_frames} video / ${s.audio_frames} audio · ${s.video_queue_drops??s.queue_drops} video / ${s.audio_queue_drops??0} audio drops · ${s.sync}`+(s.sdi?` | SDI: ${s.sdi.completion.late} late / ${s.sdi.completion.dropped+s.sdi.video_drops} video / ${s.sdi.audio_drops} audio drops`:'');const timing=s.output_timings?.video_output_including_thread_wait;const dec=s.decoder;const decoderText=dec?`Decoder: ${dec.hardware?'Hardware '+dec.backend.toUpperCase():'Software'}${dec.timings?.decode_including_gpu_download?' · '+dec.timings.decode_including_gpu_download.p95_ms.toFixed(1)+' ms':''}`:'';const diagnosis=timing?`${output} output p95: ${timing.p95_ms.toFixed(1)} ms · Queue peak: ${s.queue_max?.video||0} video / ${s.queue_max?.audio||0} audio`:'';$('receiveNotice').classList.toggle('has-warning',!!(s.warning||s.detail));$('receiveNotice').textContent=[s.warning_text,s.detail||[decoderText,diagnosis].filter(Boolean).join(' · ')].filter(Boolean).join(' · ');}
function updateReceiveMode(){
 const pull=$('receiveMode').value==='whep';
 $('receiveViewerRow').hidden=$('receiveCodecRow').hidden=$('receiveBearerRow').hidden=!pull;
 $('receiveTokenRow').hidden=pull;
 $('receiveViewerUrl').required=pull;
 $('receiveToken').required=!pull;
 $('receiveTokenLabel').textContent='Stream key / bearer token';
 $('receiveProtocolTitle').textContent=pull?'WHEP receive':'WHIP receive';
 $('receiveUrlLabel').textContent=pull?'Viewer link':'WHIP URL';
 for(const id of ['receivePort','receivePublicIp','receiveEndpointName','saveReceiveSettings'])$(id).disabled=busy||outputEnabled.receive||pull;
 if(!outputEnabled.receive)showToggle($('receiveStart'),false,pull?'Connect':'Start receiver');
}
$('receiveMode').onchange=updateReceiveMode;
$('receiveForm').onsubmit=e=>{e.preventDefault();action(()=>api('/api/receive','POST',{mode:$('receiveMode').value,viewer_url:$('receiveViewerUrl').value.trim(),codec:$('receiveCodec').value,whep_token:$('receiveBearerToken').value,token:$('receiveToken').value,name:$('receiveName').value.trim(),listen_port:Number($('receivePort').value),public_ip:$('receivePublicIp').value.trim(),endpoint_name:$('receiveEndpointName').value.trim(),output_kind:$('receiveOutputKind').value,sdi_device:$('receiveSdiDevice').value===''?null:Number($('receiveSdiDevice').value),sdi_interlaced:$('receiveSdiInterlaced').checked,ndi_audio_minus20:$('receiveNdiAudioMinus20').checked}));};
$('saveReceiveSettings').onclick=()=>action(async()=>{
 const button=$('saveReceiveSettings');clearTimeout(button.feedbackTimer);button.textContent='Saving…';button.classList.remove('source-set','source-set-error');
 try{
  const status=await api('/api/receive/settings','POST',{listen_port:Number($('receivePort').value),public_ip:$('receivePublicIp').value.trim(),endpoint_name:$('receiveEndpointName').value.trim()});
  showReceive(status);button.textContent='Saved';button.classList.add('source-set');$('receiveSettingsNotice').textContent=status.public_url?`Saved. Internet address: ${status.public_url}`:'Saved.';
 }catch(error){button.textContent='Error';button.classList.add('source-set-error');$('receiveSettingsNotice').textContent=error.message;throw error;}
 finally{button.feedbackTimer=setTimeout(()=>{button.textContent='Save network settings';button.classList.remove('source-set','source-set-error');},1800);}
});
function updateNdiAudioOption(){$('receiveNdiAudioRow').hidden=$('receiveOutputKind').value!=='ndi';const sdi=$('receiveOutputKind').value==='sdi';$('receiveSdiRow').hidden=!sdi;$('receiveSdiDevice').required=sdi;$('refreshSdiDevices').disabled=busy||outputEnabled.receive;}
$('receiveOutputKind').onchange=updateNdiAudioOption;
$('refreshSdiDevices').onclick=()=>action(loadSdiDevices);
$('generateReceiveKey').onclick=()=>{$('receiveToken').value=newStreamKey();updateKeyHint();$('receiveNotice').textContent='New key created. Start (or restart) the receiver, then use Copy for sender.';};
$('receiveToken').addEventListener('input',updateKeyHint);
// Feedback on the button itself, like Save (the receiver notice is on Main, not on this page).
$('detectReceivePublicIp').onclick=async()=>{const button=$('detectReceivePublicIp');button.disabled=true;button.textContent='Detecting…';
  try{const data=await api('/api/receive/public-ip');$('receivePublicIp').value=data.public_ip;button.textContent='Detected · Save';button.title=`Public IP ${data.public_ip}. Click Save to use it.`;}
  catch(e){button.textContent='Not detected';button.title=e.message;}
  finally{button.disabled=false;clearTimeout(button.feedbackTimer);button.feedbackTimer=setTimeout(()=>{button.textContent='Detect';},4000);}};
// Fresh receiver state at the click: a saved public IP must not wait for the next dashboard refresh.
$('copyForSender').onclick=async()=>{try{lastReceive=await api('/api/receive');}catch{}const text=senderConnection();
  if(!text){$('receiveNotice').textContent='Start the receiver and enter a key first.';return;}
  $('receiveNotice').textContent=await copyText(text)?'Address and key copied. Paste them into the WHIP address field of the sender.':'Copy failed.';};
$('copyReach').onclick=async()=>{const button=$('copyReach');try{lastReceive=await api('/api/receive');}catch{}const text=senderConnection();
  clearTimeout(button.feedbackTimer);button.textContent=!text?'Enter a key on Main':await copyText(text)?'Copied':'Copy failed';
  button.feedbackTimer=setTimeout(()=>{button.textContent='Copy for sender';},1800);};
// Pasting "Copy for sender" from a receiver fills address and key at once.
for(const id of ['whipEndpoint','whipToken'])$(id).addEventListener('paste',event=>{const text=event.clipboardData?.getData('text')||'';
  const url=text.match(/URL:\s*(\S+)/),key=text.match(/Key:\s*(\S+)/);if(!url||!key)return;
  event.preventDefault();$('whipEndpoint').value=url[1];$('whipToken').value=key[1];});
$('receiveNdiAudioMinus20').onchange=()=>action(()=>api('/api/receive/audio','POST',{ndi_audio_minus20:$('receiveNdiAudioMinus20').checked}));
$('receiveStart').onclick=()=>{if(outputEnabled.receive)action(()=>api('/api/receive','DELETE'));else $('receiveForm').requestSubmit();};

function updatePortHelp(){const port=Number($('receivePort').value);const publicIp=$('receivePublicIp').value.trim()||'PUBLIC-IP';$('helpReceivePort').textContent=port||'Not set';$('helpSrtPort').textContent=$('srtPort').value||'Not set';$('helpControlPort').textContent=location.port||(location.protocol==='https:'?'443':'80');document.querySelectorAll('[data-help-receive-port]').forEach(el=>el.textContent=port||'P');document.querySelectorAll('[data-help-public-ip]').forEach(el=>el.textContent=publicIp);document.querySelectorAll('[data-help-receive-url]').forEach(el=>el.textContent=`http://${publicIp}:${port||'P'}/api/${$('receiveEndpointName').value.trim()||'whip-input'}`);}
for(const button of document.querySelectorAll('[data-page]'))button.onclick=()=>{for(const b of document.querySelectorAll('[data-page]')){const active=b===button;b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}for(const page of document.querySelectorAll('.page'))page.hidden=page.id!=='page-'+button.dataset.page;updatePortHelp();showProductionTitle();};
document.querySelectorAll('[data-help-receive-url]').forEach(el=>el.after(copyButton(()=>el.textContent,'Copy example address')));
$('receivePort').addEventListener('input',updatePortHelp);$('srtPort').addEventListener('input',updatePortHelp);

function showGpu(data,applied){
  const status=$('gpuStatus'),button=$('applyGpu'),gpus=(data.gpus||[]).join(', ')||'unknown';
  button.hidden=!(data.nvidia&&data.nvidia_profile!=='max');
  if(data.message)status.textContent=data.message;
  else if(!data.nvidia)status.textContent=`No NVIDIA graphics card found (${gpus}). No change needed.`;
  else if(data.nvidia_profile==='max')status.textContent=applied?'Maximum performance is now set for Bridge. Restart Bridge to use it.':`Maximum performance is set for Bridge (${gpus}).`;
  else status.textContent=`Bridge uses the driver's power-saving default on ${gpus}. Set maximum performance below.`;
}
async function checkGpu(){try{showGpu(await api('/api/gpu'));}catch(e){$('gpuStatus').textContent='Graphics check failed: '+e.message;}}
$('applyGpu').onclick=async()=>{const button=$('applyGpu');button.disabled=true;$('gpuStatus').textContent='Applying…';try{showGpu(await api('/api/gpu','POST'),true);}catch(e){$('gpuStatus').textContent='Could not apply: '+e.message;}finally{button.disabled=false;}};
for(const button of document.querySelectorAll('[data-page="help"]'))button.addEventListener('click',checkGpu);

function firewallOptions(){return {role:$('firewallRole').value,scope:$('firewallScope').value,port:Number($('receivePort').value),legacy:$('firewallLegacy').checked,ndi_output:$('receiveOutputKind').value==='ndi'};}
$('configureFirewall').onclick=async()=>{const button=$('configureFirewall');button.disabled=true;try{await api('/api/firewall','POST',firewallOptions());$('firewallNotice').textContent='Windows confirmation opened. Complete it on this PC; this message does not confirm that rules were applied.';}catch(e){$('firewallNotice').textContent=e.message;}finally{button.disabled=false;}};
$('downloadFirewall').onclick=async()=>{try{const o=firewallOptions();const response=await fetch('/static/Configure-Firewall.ps1');if(!response.ok)throw Error('Firewall script unavailable');let script=await response.text();if(!Number.isInteger(o.port)||o.port<1024||o.port>65535)throw Error('Choose a valid receiver port in Settings');script=script.replace('[int]$ReceivePort = 56208','[int]$ReceivePort = '+o.port).replace("[string]$Role = 'Receiver'","[string]$Role = '"+o.role+"'").replace("[string]$Scope = 'LAN'","[string]$Scope = '"+o.scope+"'").replace('[switch]$NdiOutput,','[switch]$NdiOutput = $'+(o.ndi_output?'true':'false')+',').replace('[switch]$LegacyReceiver,','[switch]$LegacyReceiver = $'+(o.legacy?'true':'false')+',');const url=URL.createObjectURL(new Blob(['\ufeff'+script],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='Configure-FastVideo-Bridge-Firewall.ps1';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);$('firewallNotice').textContent='Script downloaded. Run on the destination PC and confirm the displayed rules.';}catch(e){$('firewallNotice').textContent=e.message;}};

// Settings -> Connection log: read when Settings opens and on Refresh, not with every status sample.
function logDuration(seconds){return [Math.floor(seconds/3600),Math.floor(seconds%3600/60),seconds%60].map(v=>String(v).padStart(2,'0')).join(':');}
async function loadConnectionLog(){
 const body=$('connectionRows');
 try{
  const rows=(await api('/api/connection-log')).entries;
  if(!rows.length){body.innerHTML='<tr><td colspan="6">No connections yet.</td></tr>';return;}
  body.replaceChildren(...rows.map(row=>{
   const tr=document.createElement('tr');tr.className='log-'+row.status.toLowerCase();
   const data=row.megabytes!=null?row.megabytes.toFixed(1)+' MB':row.pictures?row.pictures+' pictures':'—';
   const other=[row.peer||'unknown',(row.codec||'').toUpperCase(),row.attempts?row.attempts+' attempts':'',row.detail].filter(Boolean).join(' · ');
   for(const text of [new Date(row.started_at).toLocaleString('en-GB'),logDuration(row.duration_seconds),row.kind,row.status,data,other]){const td=document.createElement('td');td.textContent=text;tr.append(td);}
   tr.lastChild.title=other;return tr;}));
 }catch(e){body.innerHTML='<tr><td colspan="6">Log unavailable</td></tr>';}
}
$('refreshConnectionLog').onclick=loadConnectionLog;
$('openConnectionLog').onclick=()=>window.open('/api/connection-log?format=text','FastVideoBridgeLog','width=1100,height=800');
for(const button of document.querySelectorAll('[data-page="settings"]'))button.addEventListener('click',loadConnectionLog);

let productionRows=[],activeProduction=null;
function showProductionTitle(){const main=document.querySelector('[data-page="main"]').classList.contains('active');$('productionTitle').textContent=main&&activeProduction?'Production: '+activeProduction.name:'OMT / NDI / SDI → WHIP / SRT · WHIP / WHEP → OMT / NDI / SDI';}
async function refreshProductions(selectedId){const data=await api('/api/productions');productionRows=data.presets;activeProduction=data.active;const selection=selectedId??($('productionSelect').value||data.active?.id||'');$('productionSelect').replaceChildren(new Option('New production…',''));for(const row of productionRows)$('productionSelect').append(new Option(row.name,row.id));$('productionSelect').value=selection;const selectedRow=productionRows.find(row=>row.id===selection);if(selectedRow)$('productionName').value=selectedRow.name;showProductionTitle();}
$('productionSelect').onchange=()=>{const row=productionRows.find(p=>p.id===$('productionSelect').value);$('productionName').value=row?.name||'';$('productionNotice').textContent='';};
function productionForms(){const forms={};const fields=FORM_FIELDS;const numeric=NUMERIC_FORM_FIELDS;for(const [kind,map]of Object.entries(fields)){forms[kind]={};for(const [key,suffix]of Object.entries(map)){const el=$(kind+suffix);forms[kind][key]=el.type==='checkbox'?el.checked:numeric.has(key)?(key==='sdi_device'&&el.value===''?null:Number(el.value)):el.value;}}return forms;}
async function productionAction(load){if(busy)return;const id=$('productionSelect').value;if(load&&!id){$('productionNotice').textContent='Choose a saved production';return;}if(load&&!confirm('Load this production? Current input and outputs will stop.'))return;busy=true;$('productionSave').disabled=$('productionLoad').disabled=true;try{const data=await api('/api/productions','POST',load?{action:'load',id}:{action:'save',id:id||null,name:$('productionName').value.trim(),forms:productionForms(),output_mode:$('outputMode').value});if(load){await restoreOutputForms();await refreshProductions(id);$('productionNotice').textContent='Production loaded. Connections are stopped.';}else{await refreshProductions(data.saved.id);$('productionName').value=data.saved.name;$('productionNotice').textContent='Production saved.';}}catch(e){$('productionNotice').textContent=e.message;}finally{busy=false;$('productionSave').disabled=$('productionLoad').disabled=false;await refresh();}}
$('productionSave').onclick=()=>productionAction(false);$('productionLoad').onclick=()=>productionAction(true);refreshProductions().catch(e=>{$('productionNotice').textContent=e.message;});

async function loadSdiDevices(cached=false){
 const select=$('receiveSdiDevice'),old=select.value;
 try{const data=await api('/api/sdi/devices'+(cached?'?cached=1':''));select.replaceChildren(new Option(data.devices.length?'Choose a connector':'No Blackmagic output available',''));for(const device of data.devices){if(device.output)select.append(new Option(device.name+((device.output_busy??device.busy)?' · in use':''),String(device.index)));}if(old)select.value=old;}
 catch{select.replaceChildren(new Option('Blackmagic driver unavailable',''));}
 updateNdiAudioOption();
}

// Help opens in its own window: closing that window with X must not close the Bridge controls.
const HELP_WINDOW=new URLSearchParams(location.search).get('view')==='help';
if(HELP_WINDOW){
  document.body.classList.add('help-window');document.title='FastVideo Bridge · Help';
  document.querySelector('[data-page="help"]')?.click();
}else{
  for(const button of document.querySelectorAll('[data-page="help"]'))button.onclick=event=>{
    event.preventDefault();
    const help=window.open('/?view=help','FastVideoBridgeHelp','width=1150,height=900');
    if(help)help.focus();
  };
}
