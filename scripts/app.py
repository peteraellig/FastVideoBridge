"""FastVideo: local NDI/OMT to a browser using WHEP and direct WebRTC.

Up to 40 simultaneous viewers sharing one receive/encode pipeline.
No public signaling, STUN, TURN, CDN, cloud service or browser dependency.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastvideo.users import UserStore
import argparse
from datetime import datetime, timezone
import asyncio
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import time
import uuid
from urllib.parse import urlsplit

import av
from fractions import Fraction
from aiohttp import web, ClientSession, ClientTimeout
from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
import ifaddr

from fastvideo.engine import Engine
from fastvideo.sync import sync_sender
from fastvideo.publisher import Publisher
from fastvideo.bridge import GatewayBridge
from fastvideo.sources import Sources
from fastvideo import sources as source_settings
from fastvideo import encoder, APP_NAME, VERSION
from fastvideo.system_metrics import SystemMetrics
from fastvideo.preview import InputPreview
from fastvideo.warning_history import WarningHistory
from fastvideo.connection_log import ConnectionLog, address as connection_address
from fastvideo.srt_output import SrtOutput, validate as validate_srt, MAX_STREAM_BITRATE
from fastvideo.hevc_whip import HevcWhip
from fastvideo.output_settings import OutputSettings
from fastvideo.receiver_feedback import ReceiverFeedback
from fastvideo.receiver_process import ReceiverProcess as WhipReceive
from fastvideo.production_presets import ProductionPresets, ConnectionProfiles

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("fastvideo")


def lan_addresses():
    result = []
    for adapter in ifaddr.get_adapters():
        for entry in adapter.ips:
            if isinstance(entry.ip, str):
                addr = ipaddress.ip_address(entry.ip)
                if addr.is_private and not addr.is_loopback and not addr.is_link_local:
                    result.append(entry.ip)
    return sorted(set(result))


def local_control(request):
    peer = request.transport.get_extra_info("peername")
    try:
        allowed = bool(peer and ipaddress.ip_address(peer[0]).is_loopback)
    except ValueError:
        allowed = False
    if not allowed:
        raise web.HTTPForbidden(text="Sendersteuerung nur direkt auf diesem Rechner über localhost oder 127.0.0.1 öffnen")
    host = request.host.split(":")[0].lower()
    if host not in ("127.0.0.1", "localhost"):
        raise web.HTTPForbidden(text="Sendersteuerung über 127.0.0.1 oder localhost öffnen")
    origin = request.headers.get("Origin")
    if origin and origin != f"http://{request.host}":
        raise web.HTTPForbidden(text="Fremde Webseiten dürfen den Sender nicht steuern")


# What viewers (and a pulling WHEP Bridge) need from /api/status. Everything else is
# diagnostics: only on this PC or with the running WHIP receiver key.
# "application" identifies the Bridge for Start-Bridge.ps1.
PUBLIC_STATUS_FIELDS = ("application", "version", "viewer_title", "viewer_hide_logo", "video_codec", "video_codecs", "timestamp_marker")

# Form fields that productions and connection profiles store per connection type.
CONNECTION_FIELDS = {
    'whip': ('url', 'token', 'codec', 'bitrate', 'preset', 'audio_bitrate'),
    'srt': ('host', 'port', 'latency', 'streamid', 'passphrase', 'aes', 'codec', 'bitrate', 'preset', 'audio_bitrate', 'keyframe', 'cbr'),
    'receive': ('token', 'name', 'listen_port', 'public_ip', 'endpoint_name', 'output_kind', 'ndi_audio_minus20', 'mode',
                'viewer_url', 'codec', 'whep_token', 'sdi_device', 'sdi_interlaced'),
}


def public_status(data):
    result = {key: data.get(key) for key in PUBLIC_STATUS_FIELDS if key in data}
    result["source"] = {"kind": (data.get("source") or {}).get("kind")}
    return result


class Server:
    def __init__(self, args):
        self.args = args
        self.gateway_url = f'http://127.0.0.1:{args.gateway_port}'
        self.sources = Sources()
        self.engine = Engine(self.sources, fps=args.fps,
                             timestamp_marker=args.timestamp_marker)
        self.publisher = Publisher(self.engine,fallback=self.args.gateway and self.args.codec=="av1",fallback_bitrate=getattr(self.args,"h264_fallback_bitrate",4_000_000),fallback_preset=getattr(self.args,"h264_fallback_qsv_preset","medium"))
        # Every isolated encoder consumes NV12, including single-codec H264.
        self.engine.shared_nv12=True
        self.bridge = None
        self.srt = SrtOutput(self)
        self.cached_addresses=lan_addresses()
        self.whip_receiver=WhipReceive(args.port,lambda:list(self.cached_addresses))
        self.receiver_preview=self.whip_receiver.preview
        self.hevc = HevcWhip(self)
        self.production_presets=ProductionPresets(ROOT/'user'/'productions')
        self.connection_profiles={kind:ConnectionProfiles(ROOT/'user'/'profiles'/kind,kind) for kind in CONNECTION_FIELDS}
        self.output_settings=OutputSettings(ROOT/"user"/"bridge-outputs.json")
        self.feedback=ReceiverFeedback(self.gateway_url);self.feedback_task=None
        try:
            saved=self.output_settings.load()
            self.engine.output_mode=saved.get('format',{}).get('output_mode','follow')
            source_settings.KEEP_INTERLACED=saved.get('format',{}).get('interlaced')=='keep'
            audio=saved.get('input_audio',{})
            if source_settings.valid_audio_selection(audio.get('mode'),audio.get('index')):
                source_settings.AUDIO_SELECT=(audio['mode'],audio['index'])
            self.feedback.configure(saved.get('whip',{}))
        except (OSError,ValueError):pass
        try:self.whip_receiver.restore_settings(self.output_settings.load().get('receive',{}))
        except (OSError,ValueError):log.warning('Unable to restore receiver endpoint settings')
        self.whip_receiver.output_mode=self.engine.output_mode
        self.sessions = {}
        self.session_tasks = set()
        self.admin_disconnected = {}
        self.users = UserStore(ROOT/"user")
        self.choices = []
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.system_metrics = SystemMetrics()
        self.input_preview = InputPreview(self.engine)
        self.control_lock = asyncio.Lock()
        self.offer_lock = asyncio.Lock()
        self.viewer_diagnostics = None
        self.diagnostics_at = None
        self.connection_reports = {}
        self.gateway_sids, self.gateway_sids_at = set(), 0.0
        self.recovery_task = None
        self.source_recoveries = 0
        self.pending_recovery = None
        settings_file = ROOT/'fastvideo.settings.json'
        settings = json.loads(settings_file.read_text(encoding='utf-8-sig')) if settings_file.exists() else {}
        self.viewer_title = settings.get('viewer_title', 'FastVideo Live')
        self.viewer_hide_logo = settings.get('viewer_hide_logo', False)
        self.warning_history=WarningHistory(ROOT/'work'/'warnings.jsonl')
        self.connection_log=ConnectionLog(ROOT/'work'/'connections.json')
        # Destination of the WHIP sender, without key: the gateway status does not carry it.
        try:self.whip_target=connection_address(self.output_settings.load().get('whip',{}).get('url',''))
        except (OSError,ValueError):self.whip_target=''
        self.warning_task=None
        self.status_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='bridge-status')
        self.dashboard_cache=None
        self.sdi_devices_cache=None
        self.address_sample_at=0
        self.selected_source=None
        for name in ['selected-source.json','last-source.json']:
            file=ROOT/'work'/name
            if file.exists():
                try:self.selected_source=json.loads(file.read_text(encoding='utf-8'));break
                except (OSError,ValueError):log.warning('Unable to load saved source selection')

    def resolve_source(self, data):
        if not isinstance(data,dict):raise web.HTTPBadRequest(text='Invalid source selection')
        if 'source' in data:
            source=data['source']
            if not isinstance(source,dict):raise web.HTTPBadRequest(text='Invalid source selection')
            for candidate in self.choices+([self.selected_source] if self.selected_source else []):
                if (source.get('kind'),source.get('address'))==(candidate.get('kind'),candidate.get('address')):return dict(candidate)
            raise web.HTTPBadRequest(text='Source is no longer available. Find sources again.')
        if 'omt_address' in data:
            address=str(data['omt_address']).strip()
            if not address.startswith('omt://') or len(address)>1024:raise web.HTTPBadRequest(text='Enter an OMT address in omt://host:port format')
            return {'kind':'omt','name':address,'address':address}
        if 'index' in data:
            index=data['index']
            if type(index) is not int or not 0<=index<len(self.choices):raise web.HTTPBadRequest(text='Select a source first')
            return dict(self.choices[index])
        if self.selected_source:return dict(self.selected_source)
        raise web.HTTPBadRequest(text='Choose a source in Setup and click Set source first')

    def save_source_selection(self, source):
        file=ROOT/'work'/'selected-source.json'
        temporary=file.with_suffix('.tmp')
        temporary.write_text(json.dumps(source),encoding='utf-8');temporary.replace(file)
        self.selected_source=dict(source)

    async def set_source(self, request):
        local_control(request)
        data=await request.json()
        async with self.control_lock:
            source=self.resolve_source(data)
            current=self.engine.source
            if self.input_requested() and (not current or (current['kind'],current['address'])!=(source['kind'],source['address'])):
                await self.activate_source(source)
            self.save_source_selection(source)
        return web.json_response({'selected_source':self.selected_source,'running':self.engine.snapshot()['running']})

    def input_requested(self):
        # A failed capture thread still has an active input/recovery request.
        # It must remain switchable and stoppable until the operator stops it.
        return bool(self.engine.source or getattr(self,'pending_recovery',None) or (self.engine.thread and self.engine.thread.is_alive()))

    async def start_warning_monitor(self, app):
        self.warning_task=asyncio.create_task(self.monitor_warnings())
        self.feedback_task=asyncio.create_task(self.feedback.run())

    async def status_work(self, callback, *args):
        return await asyncio.get_running_loop().run_in_executor(self.status_worker,callback,*args)

    def local_status(self):
        data=self.engine.snapshot()
        data.update(encoder.snapshot())
        data.update(self.publisher.snapshot())
        if self.bridge:data.update(self.bridge.snapshot())
        system=self.system_metrics.snapshot()
        system.update(server_started_at=self.started_at,server_uptime_seconds=int(time.monotonic()-self.started))
        return data,system,self.srt.snapshot()

    def decorate_whip_status(self,state,p):
        state=dict(state)
        state['bitrate']=self.hevc.bitrate if state.get('codec')=='h265' else self.args.h264_fallback_bitrate if state.get('codec')=='h264' and self.publisher.fallback else self.args.bitrate
        state['preset']=self.hevc.preset if state.get('codec')=='h265' else self.args.h264_fallback_qsv_preset if state.get('codec')=='h264' and self.publisher.fallback else self.args.qsv_preset
        state['audio_bitrate']=getattr(self.engine,'whip_audio_bitrate',256000)
        state.update(self.feedback.snapshot())
        state['target']=getattr(self,'whip_target','')
        if state.get('enabled') and p.get('encode_sample_count',0)>30 and p.get('encode_ms_p95',0)>p['encode_budget_ms']:
            state['warning']=True;state['warning_text']=f"Sender encode p95 {p['encode_ms_p95']:.1f} ms > {p['encode_budget_ms']:.1f} ms frame budget"
        if state.get('codec')=='h265' and self.hevc.error:state['detail']=self.hevc.error
        if state.get('enabled') and not self.input_requested():
            # The operator stopped the input: say so, instead of receiver and transport warnings
            # that only describe the missing picture.
            state.update(state='waiting for source',detail='Source stopped',warning=False,warning_text='')
        return state

    async def monitor_warnings(self):
        # One sampler for all control pages. Native queries and warning file I/O
        # have their own worker and never run on the media event loop.
        async with ClientSession(timeout=ClientTimeout(total=1.5)) as client:
            async def gateway(path):
                try:
                    async with client.get(self.gateway_url+path) as response:
                        response.raise_for_status()
                        return await response.json()
                except (OSError,asyncio.TimeoutError,ValueError) as exc:
                    return {'gateway_error':type(exc).__name__}
            while True:
                started=time.monotonic()
                try:
                    if started-self.address_sample_at>=30:
                        self.cached_addresses=await self.status_work(lan_addresses)
                        self.address_sample_at=started
                    (local,system,srt),transport,whip,srt_transport=await asyncio.gather(
                        self.status_work(self.local_status),gateway('/status'),gateway('/whip-output'),gateway('/srt-output'))
                    data=self.decorate_stream_status(local)
                    if self.args.gateway:data.update(transport)
                    await self.status_work(self.warning_history.update,data)
                    data['stream_warning']=self.warning_history.text
                    srt.update(srt_transport)
                    receive=self.whip_receiver.status()
                    whip=self.decorate_whip_status(whip,local)
                    await self.status_work(self.connection_log.update,{'whip':whip,'srt':srt,'receive':receive})
                    self.dashboard_cache={'status':data,'system':system,'srt':srt,
                        'whip':whip,'receive':receive,
                        'sample_time_ms':time.time()*1000}
                except Exception:log.exception('Status sampler failed')
                await asyncio.sleep(max(.05,1-(time.monotonic()-started)))

    async def dashboard(self,request):
        local_control(request)
        if self.dashboard_cache is None:raise web.HTTPServiceUnavailable(text='Status sampler starting')
        age=time.time()*1000-self.dashboard_cache['sample_time_ms']
        if age>4000:raise web.HTTPServiceUnavailable(text='Status sampler is not updating')
        return web.json_response({**self.dashboard_cache,'status_age_ms':round(max(0,age))},headers={'Cache-Control':'no-store'})

    async def warning_log(self, request):
        local_control(request)
        entries=await self.status_work(self.warning_history.snapshot)
        return web.json_response({'entries':entries},headers={'Cache-Control':'no-store'})

    async def connection_history(self, request):
        local_control(request)
        if request.query.get('format')=='text':
            return web.Response(text=await self.status_work(self.connection_log.text),content_type='text/plain',charset='utf-8',headers={'Cache-Control':'no-store'})
        return web.json_response({'entries':await self.status_work(self.connection_log.snapshot)},headers={'Cache-Control':'no-store'})

    async def warning_outage(self, request):
        local_control(request)
        data=await request.json()
        start,end=data.get('started_ms'),data.get('ended_ms')
        if type(start) not in (int,float) or type(end) not in (int,float) or not 0<start<=end<=time.time()*1000+60000:
            raise web.HTTPBadRequest(text='Invalid outage interval')
        await self.status_work(self.warning_history.control_outage,start/1000,end/1000)
        return web.json_response({'recorded':True})

    async def viewer_display(self, request):
        local_control(request)
        data = await request.json()
        title = data.get('viewer_title', self.viewer_title) if isinstance(data, dict) else None
        hide_logo = data.get('viewer_hide_logo', self.viewer_hide_logo) if isinstance(data, dict) else None
        if type(hide_logo) is not bool:
            raise web.HTTPBadRequest(text='Invalid viewer logo setting')
        if not isinstance(title, str) or len(title.strip()) > 80 or any(ord(c) < 32 for c in title):
            raise web.HTTPBadRequest(text='Invalid viewer title')
        title = title.strip()
        async with self.control_lock:
            settings = json.loads((ROOT/'fastvideo.settings.json').read_text(encoding='utf-8-sig'))
            settings['viewer_title'] = title
            settings['viewer_hide_logo'] = hide_logo
            temporary = ROOT / 'fastvideo.settings.tmp'
            temporary.write_text(json.dumps(settings, indent=2), encoding='utf-8')
            temporary.replace(ROOT / 'fastvideo.settings.json')
            self.viewer_title = title
            self.viewer_hide_logo = hide_logo
        return web.json_response({'viewer_title': title, 'viewer_hide_logo': hide_logo})

    async def viewer_capacity(self, request):
        local_control(request)
        data = await request.json()
        limit = data.get('max_viewers') if isinstance(data, dict) else None
        upper = 80 if self.args.gateway else 40
        if type(limit) is not int or not 1 <= limit <= upper:
            raise web.HTTPBadRequest(text=f'Choose 1 to {upper} viewers')
        async with self.control_lock:
            settings = json.loads((ROOT/'fastvideo.settings.json').read_text(encoding='utf-8-sig'))
            old_limit = self.args.max_viewers
            async def gateway_limit(value):
                async with ClientSession(timeout=ClientTimeout(total=3)) as client:
                    async with client.post(self.gateway_url+'/capacity', json={'max_viewers':value}) as response:
                        if response.status != 200:
                            raise web.HTTPServiceUnavailable(text='Viewer limit could not be updated')
            if self.args.gateway:
                await gateway_limit(limit)
            try:
                settings['max_viewers'] = limit
                temporary = ROOT/'fastvideo.settings.tmp'
                temporary.write_text(json.dumps(settings, indent=2), encoding='utf-8')
                temporary.replace(ROOT/'fastvideo.settings.json')
            except Exception:
                if self.args.gateway:
                    await gateway_limit(old_limit)
                raise
            self.args.max_viewers = limit
        return web.json_response({'max_viewers':limit})

    async def gateway_connections(self):
        async with ClientSession(timeout=ClientTimeout(total=3)) as client:
            async with client.get(self.gateway_url+'/connections') as response:
                if response.status!=200:raise web.HTTPServiceUnavailable(text='Connection service unavailable')
                return (await response.json())['connections']

    async def close_user_sessions(self, identifier):
        peers=await self.gateway_connections()
        async with ClientSession(timeout=ClientTimeout(total=3)) as client:
            for peer in peers:
                if peer.get('user_id')==identifier:
                    async with client.delete(self.gateway_url+'/session/'+peer['id']) as response:
                        if response.status!=204:raise web.HTTPServiceUnavailable(text='Unable to close user connection')

    async def saved_outputs(self,request):
        local_control(request)
        try:data=await asyncio.to_thread(self.output_settings.load)
        except Exception:raise web.HTTPServiceUnavailable(text='Saved credentials cannot be opened by this Windows account')
        return web.json_response(data,headers={'Cache-Control':'no-store'})

    async def profiles(self,request):
        # Named settings per connection type. Loading only returns the values for the form;
        # a running output is never stopped or changed here.
        local_control(request)
        kind=request.match_info['kind']
        if kind not in CONNECTION_FIELDS:raise web.HTTPNotFound()
        store=self.connection_profiles[kind]
        result={}
        if request.method=='POST':
            try:
                body=await request.json()
                action,profile_id=body.get('action'),body.get('id')
                if action=='save':
                    settings=body.get('settings')
                    if not isinstance(settings,dict):raise ValueError('Invalid profile settings')
                    settings={k:settings[k] for k in CONNECTION_FIELDS[kind] if k in settings}
                    result['saved']=await asyncio.to_thread(store.save,body.get('name'),settings,profile_id)
                elif action=='load':
                    row,data=await asyncio.to_thread(store.load,profile_id)
                    result.update(profile={'id':row['id'],'name':row['name']},settings=data['settings'])
                elif action=='rename':result['saved']=await asyncio.to_thread(store.rename,profile_id,body.get('name'))
                elif action=='delete':await asyncio.to_thread(store.delete,profile_id)
                else:raise ValueError('Choose Save, Load, Rename or Delete')
            except FileNotFoundError:raise web.HTTPNotFound(text='This profile no longer exists')
            except (OSError,ValueError,KeyError,TypeError) as exc:
                raise web.HTTPBadRequest(text=str(exc) if isinstance(exc,ValueError) else 'Profile cannot be opened by this Windows account')
        result['profiles']=await asyncio.to_thread(store.list)
        return web.json_response(result,headers={'Cache-Control':'no-store'})

    async def productions(self,request):
        local_control(request)
        store=self.production_presets
        if request.method=='GET':
            return web.json_response({'presets':await asyncio.to_thread(store.list),'active':await asyncio.to_thread(store.active)},headers={'Cache-Control':'no-store'})
        body=await request.json()
        if body.get('action')=='save':
            forms=body.get('forms')
            if body.get('output_mode',self.engine.output_mode) not in ('follow','720p'):raise web.HTTPBadRequest(text='Invalid output format')
            if not isinstance(forms,dict) or any(not isinstance(forms.get(k),dict) for k in ('whip','srt','receive')):
                raise web.HTTPBadRequest(text='Invalid production settings')
            forms={kind:{k:forms[kind][k] for k in fields if k in forms[kind]} for kind,fields in CONNECTION_FIELDS.items()}
            async with self.control_lock:
                try:row=await asyncio.to_thread(store.save,body.get('name'),{'forms':{**forms,'format':{'output_mode':body.get('output_mode',self.engine.output_mode)}},'source':self.selected_source},body.get('id'))
                except (ValueError,TypeError) as exc:raise web.HTTPBadRequest(text=str(exc))
            return web.json_response({'saved':row,'active':await asyncio.to_thread(store.active)},headers={'Cache-Control':'no-store'})
        if body.get('action')!='load':raise web.HTTPBadRequest(text='Choose Save or Load')
        try:row,data=await asyncio.to_thread(store.load,body.get('id'))
        except (OSError,ValueError,KeyError,TypeError):raise web.HTTPBadRequest(text='Production preset cannot be opened by this Windows account')
        source=data.get('source');forms=data.get('forms')
        if isinstance(forms,dict) and forms.get('format',{}).get('output_mode','follow') not in ('follow','720p'):raise web.HTTPBadRequest(text='Invalid output format')
        if not isinstance(forms,dict) or any(not isinstance(forms.get(k),dict) for k in ('whip','srt','receive')):raise web.HTTPBadRequest(text='Invalid production preset')
        if source and (not isinstance(source,dict) or source.get('kind') not in ('ndi','omt','test','sdi') or not isinstance(source.get('address'),str)):raise web.HTTPBadRequest(text='Invalid saved source')
        async with self.control_lock, self.offer_lock:
            # Stop transmissions before replacing destinations. Loading never starts a stream.
            async with ClientSession(timeout=ClientTimeout(total=12)) as client:
                try:
                    async with client.delete(self.gateway_url+'/whip-output') as response:
                        if response.status!=200:raise web.HTTPServiceUnavailable(text='WHIP output could not be stopped; preset not loaded')
                except (asyncio.TimeoutError,OSError):raise web.HTTPServiceUnavailable(text='WHIP gateway unavailable; preset not loaded')
            await self.hevc.stop()
            await self.srt.stop()
            await self.whip_receiver.stop()
            await self.close_sessions()
            await self.publisher.stop()
            await asyncio.to_thread(self.engine.stop)
            self.pending_recovery=None
            (ROOT/'work'/'last-source.json').unlink(missing_ok=True)
            await asyncio.to_thread(self.output_settings.replace,forms)
            self.whip_receiver.restore_settings(forms.get('receive',{}))
            self.engine.output_mode=forms.get('format',{}).get('output_mode','follow')
            self.whip_receiver.output_mode=self.engine.output_mode
            self.feedback.configure(forms.get('whip',{}))
            if source:self.save_source_selection(source)
            else:
                self.selected_source=None
                (ROOT/'work'/'selected-source.json').unlink(missing_ok=True)
            await asyncio.to_thread(store.set_active,row)
        return web.json_response({'active':{'id':row['id'],'name':row['name']}},headers={'Cache-Control':'no-store'})

    async def configure_firewall(self,request):
        local_control(request)
        data=await request.json()
        role=data.get('role');scope=data.get('scope');port=data.get('port');legacy=data.get('legacy',False);ndi_output=data.get('ndi_output',False)
        if role not in ('Sender','Receiver','Both') or scope not in ('LAN','Internet') or type(port) is not int or not 1024<=port<=65535 or type(legacy) is not bool or type(ndi_output) is not bool:
            raise web.HTTPBadRequest(text='Invalid firewall options')
        import subprocess,sys,tempfile,shutil
        script=ROOT/'scripts'/'Configure-Firewall.ps1'
        if not script.is_file():raise web.HTTPServiceUnavailable(text='Firewall helper is missing')
        # An elevated process may not see mapped NAS drives. Use a private local copy.
        local_script=Path(tempfile.mkdtemp(prefix='FastVideoBridge-Firewall-'))/'Configure-Firewall.ps1'
        shutil.copyfile(script,local_script)
        args=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',str(local_script),'-ReceivePort',str(port),'-ControlPort',str(self.args.port),'-Role',role,'-Scope',scope,'-ProgramPath',sys.executable]
        if legacy:args.append('-LegacyReceiver')
        if ndi_output:args.append('-NdiOutput')
        subprocess.Popen(args,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
        return web.json_response({'state':'confirmation_requested'})

    async def gpu_performance(self,request):
        local_control(request)
        import subprocess,sys
        script=ROOT/'scripts'/'Configure-GpuPerformance.ps1'
        if not script.is_file():raise web.HTTPServiceUnavailable(text='GPU helper is missing')
        # The profile must name exactly this runtime: NVIDIA matches the full path.
        process=await asyncio.create_subprocess_exec('powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden',
            '-File',str(script),'-Action','Apply' if request.method=='POST' else 'Check','-Program',sys.executable,
            cwd=ROOT,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        try:output,_=await asyncio.wait_for(process.communicate(),60)
        except asyncio.TimeoutError:
            process.kill();raise web.HTTPGatewayTimeout(text='GPU helper did not answer')
        lines=[line for line in output.decode('utf-8','replace').splitlines() if line.startswith('{')]
        if not lines:raise web.HTTPBadGateway(text='GPU helper returned no result')
        return web.json_response(json.loads(lines[-1]),headers={'Cache-Control':'no-store'})

    def input_audio_state(self):
        """Selection, channels of the running source (0 = unknown) and the channels actually used."""
        mode,index=source_settings.AUDIO_SELECT
        channels=source_settings.AUDIO_CHANNELS if self.engine.thread and self.engine.thread.is_alive() else 0
        state={'mode':mode,'index':index,'channels':channels}
        if channels:
            left,right=source_settings.selected_channels(channels)
            state['active']=[left+1,right+1]
            state['available']=[left+1,right+1]==([index,index] if mode=='mono' else [index*2-1,index*2])
        return state

    async def input_audio(self,request):
        local_control(request)
        if request.method=='POST':
            data=await request.json()
            mode,index=data.get('mode'),data.get('index')
            if not source_settings.valid_audio_selection(mode,index):
                raise web.HTTPBadRequest(text='Choose a stereo pair or one channel')
            async with self.control_lock:
                # Applied to the next audio packet of the running source; nothing restarts.
                source_settings.AUDIO_SELECT=(mode,index)
                await asyncio.to_thread(self.output_settings.save,'input_audio',{'mode':mode,'index':index})
        return web.json_response(self.input_audio_state(),headers={'Cache-Control':'no-store'})

    async def output_format(self,request):
        local_control(request)
        if request.method=='POST':
            data=await request.json();mode=data.get('output_mode',self.engine.output_mode)
            interlaced=data.get('interlaced','keep' if source_settings.KEEP_INTERLACED else 'deinterlace')
            if mode not in ('follow','720p'):raise web.HTTPBadRequest(text='Choose Follow input or 720p')
            if interlaced not in ('deinterlace','keep'):raise web.HTTPBadRequest(text='Choose Deinterlace or Keep interlaced')
            async with self.control_lock:
                self.engine.output_mode=mode
                self.whip_receiver.output_mode=mode
                source_settings.KEEP_INTERLACED=interlaced=='keep'
                await asyncio.to_thread(self.output_settings.save,'format',{'output_mode':mode,'interlaced':interlaced})
                # Capture applies resolution and field handling on its next picture; encoders detect the format change.
        return web.json_response({'output_mode':self.engine.output_mode,'interlaced':'keep' if source_settings.KEEP_INTERLACED else 'deinterlace'},headers={'Cache-Control':'no-store'})

    async def receive_control(self,request):
        local_control(request)
        if request.method=='GET' and self.dashboard_cache is not None:
            return web.json_response(self.dashboard_cache['receive'],headers={'Cache-Control':'no-store'})
        if request.method=='POST':
            data=await request.json()
            mode=data.get('mode','whip')
            if mode not in ('whip','whep'):raise web.HTTPBadRequest(text='Choose WHIP input or FastVideo link / WHEP')
            async with self.control_lock:
                try:
                    options={'sdi_device':data.get('sdi_device'),'sdi_interlaced':data.get('sdi_interlaced',False)} if data.get('output_kind')=='sdi' else ({'sdi_interlaced':True} if data.get('sdi_interlaced') is True else {})
                    if mode=='whep':
                        await self.whip_receiver.enable_whep(data.get('viewer_url'),data.get('name','FastVideo Bridge Receive'),data.get('output_kind','omt'),data.get('ndi_audio_minus20',False),data.get('codec','auto'),data.get('whep_token',''),**options)
                    else:
                        await self.whip_receiver.enable(data.get('token'),data.get('name','FastVideo Bridge Receive'),data.get('listen_port',56208),data.get('public_ip',''),data.get('output_kind','omt'),data.get('ndi_audio_minus20',False),data.get('endpoint_name','whip-input'),**options)
                except ValueError as exc:raise web.HTTPBadRequest(text=str(exc))
                saved=await asyncio.to_thread(self.output_settings.load)
                config=dict(saved.get('receive',{}))
                config.update(mode=mode,name=self.whip_receiver.name,output_kind=self.whip_receiver.output_kind,ndi_audio_minus20=self.whip_receiver.ndi_audio_minus20)
                config['sdi_interlaced']=self.whip_receiver.sdi_interlaced
                if data.get('output_kind')=='sdi':config['sdi_device']=self.whip_receiver.sdi_device
                if mode=='whep':config.update(viewer_url=self.whip_receiver.viewer_url,codec=data.get('codec','auto'),whep_token=data.get('whep_token',''))
                else:config.update(token=data.get('token',''),listen_port=self.whip_receiver.listen_port,public_ip=self.whip_receiver.public_ip,endpoint_name=self.whip_receiver.endpoint_name)
                await asyncio.to_thread(self.output_settings.save,'receive',config)
        elif request.method=='DELETE':
            async with self.control_lock:await self.whip_receiver.stop()
        return web.json_response(self.whip_receiver.status(),headers={'Cache-Control':'no-store'})

    async def sdi_devices(self,request):
        local_control(request)
        from fastvideo.decklink import discover
        if request.query.get('cached')=='1' and self.sdi_devices_cache is not None:
            return web.json_response({'devices':self.sdi_devices_cache})
        try:
            devices=await asyncio.to_thread(discover)
            self.sdi_devices_cache=devices
        except Exception:return web.json_response({'devices':[],'error':'Blackmagic Desktop Video driver not available'},headers={'Cache-Control':'no-store'})
        return web.json_response({'devices':devices,'error':''},headers={'Cache-Control':'no-store'})

    async def detect_public_ip(self,request):
        local_control(request)
        from fastvideo.public_ip import detect_public_ipv4
        try:address=await asyncio.to_thread(detect_public_ipv4)
        except OSError as exc:raise web.HTTPServiceUnavailable(text=str(exc))
        return web.json_response({'public_ip':address},headers={'Cache-Control':'no-store'})

    async def receive_network_settings(self,request):
        local_control(request)
        from fastvideo.whip_receive import validate_endpoint_settings
        data=await request.json()
        try:network=validate_endpoint_settings(data.get('listen_port'),data.get('public_ip',''),data.get('endpoint_name','whip-input'))
        except ValueError as exc:raise web.HTTPBadRequest(text=str(exc))
        async with self.control_lock, self.whip_receiver.lock:
            running=self.whip_receiver.enabled
            if running and (network['listen_port'],network['endpoint_name'])!=(self.whip_receiver.listen_port,self.whip_receiver.endpoint_name):
                raise web.HTTPConflict(text='Stop the receiver to change port or endpoint name. The public IP can be saved while it runs.')
            saved=await asyncio.to_thread(self.output_settings.load)
            config=dict(saved.get('receive',{}));config.update(network)
            await asyncio.to_thread(self.output_settings.save,'receive',config)
            if running:
                try:await self.whip_receiver.set_public_ip(network['public_ip'])
                except ValueError as exc:raise web.HTTPBadRequest(text=str(exc))
            else:self.whip_receiver.restore_settings(config)
        return web.json_response(self.whip_receiver.status(),headers={'Cache-Control':'no-store'})

    async def receive_audio_control(self,request):
        local_control(request)
        data=await request.json()
        value=data.get('ndi_audio_minus20')
        if type(value) is not bool:raise web.HTTPBadRequest(text='NDI audio attenuation must be true or false')
        async with self.control_lock:
            # Save only this preference; keep existing endpoint and protected token.
            saved=await asyncio.to_thread(self.output_settings.load)
            config=dict(saved.get('receive',{}));config['ndi_audio_minus20']=value
            await asyncio.to_thread(self.output_settings.save,'receive',config)
            await self.whip_receiver.set_ndi_audio_minus20(value)
        return web.json_response(self.whip_receiver.status(),headers={'Cache-Control':'no-store'})

    async def replace_whip_encoder(self,codec,changes):
        from fastvideo.isolated_encoder import IsolatedEncoder
        async with self.control_lock:
            target=self.publisher.fallback if codec=='h264' and self.publisher.fallback else self.publisher
            bitrate=changes.get('h264_fallback_bitrate',changes.get('bitrate',self.args.h264_fallback_bitrate if target is self.publisher.fallback else self.args.bitrate))
            preset=changes.get('h264_fallback_qsv_preset',changes.get('qsv_preset',self.args.h264_fallback_qsv_preset if target is self.publisher.fallback else self.args.qsv_preset))
            backend=encoder.snapshot()['encoder_backend']
            if codec=='h264' and target is self.publisher and backend=='nvenc':
                replacement=encoder.make_nvenc_encoder(bitrate)
            elif codec=='h264' and target is self.publisher and backend=='qsv':
                replacement=encoder.make_qsv_encoder(bitrate,preset)
            else:
                replacement=IsolatedEncoder(codec,self.engine.fps,bitrate,preset,keyframe=2 if codec=='av1' else 1,profile='baseline' if target is self.publisher.fallback else 'high',backend=encoder.snapshot()['encoder_backend'])
            frame=av.VideoFrame(self.engine.width,self.engine.height,'nv12')
            for plane in frame.planes:plane.update(bytes(plane.buffer_size))
            frame.pts,frame.time_base=-round(90000/self.engine.fps),Fraction(1,90000)
            frame.opaque=Fraction(self.engine.fps)
            try:await target._encode(replacement.encode,frame,True)
            except BaseException:
                if hasattr(replacement,'close'):await asyncio.to_thread(replacement.close)
                raise
            async with target.video_encoder_lock:
                previous=target.video_encoder
                target.video_encoder=replacement
                target.request_keyframe()
                for key,value in changes.items():setattr(self.args,key,value)
                if target is self.publisher:
                    # Encoders created after a source restart must keep this rate.
                    encoder.set_defaults(changes.get('bitrate'),changes.get('qsv_preset'))
            if hasattr(previous,'close'):await asyncio.to_thread(previous.close)

    async def whip_output(self,request):
        local_control(request)
        if request.method=='GET' and self.dashboard_cache is not None:
            return web.json_response(self.dashboard_cache['whip'],headers={'Cache-Control':'no-store'})
        body=await request.read()
        if request.method=='POST':
            try:
                config=json.loads(body)
                codec,bitrate=config.get('codec'),config.get('bitrate')
                audio_bitrate=config.get('audio_bitrate',256000)
                if type(audio_bitrate) is not int or audio_bitrate not in (64000,96000,128000,160000,192000,256000,320000):raise ValueError()
                preset=config.get('preset')
                if preset is not None and preset not in ('veryfast','medium','slow','veryslow'):raise ValueError()
                endpoint=urlsplit(config.get('url',''))
                if codec not in ('h264','h265','av1') or endpoint.scheme not in ('http','https') or not endpoint.hostname or endpoint.username or endpoint.fragment:
                    raise ValueError()
                token=config.get('token','')
                if not isinstance(token,str) or len(token)>8192 or any(c in token for c in '\r\n'):raise ValueError()
                if bitrate is not None and (type(bitrate) is not int or not 1000000<=bitrate<=12000000 or bitrate%500000):raise ValueError()
            except (ValueError,TypeError,AttributeError):raise web.HTTPBadRequest(text='Invalid WHIP endpoint, codec or bitrate')
            # Upper limit is 10 Mbit/s; settings saved before 0.0.3.1 with up to 12 still start.
            if bitrate is not None:bitrate=min(bitrate,MAX_STREAM_BITRATE)
            if codec=='av1' and self.args.codec!='av1':
                raise web.HTTPBadRequest(text='AV1 is not enabled in this installation. Use H264 or H265.')
            current=self.hevc.bitrate if codec=='h265' else self.args.h264_fallback_bitrate if codec=='h264' and self.publisher.fallback else self.args.bitrate
            changes={}
            if codec!='h265':
                fallback=codec=='h264' and self.publisher.fallback
                if bitrate is not None and current!=bitrate:changes['h264_fallback_bitrate' if fallback else 'bitrate']=bitrate
                current_preset=self.args.h264_fallback_qsv_preset if fallback else self.args.qsv_preset
                if preset is not None and current_preset!=preset:changes['h264_fallback_qsv_preset' if fallback else 'qsv_preset']=preset
                if changes:await self.replace_whip_encoder(codec,changes)
        async def forward(method,payload):
            async with ClientSession(timeout=ClientTimeout(total=12)) as client:
                try:
                    async with client.request(method,self.gateway_url+'/whip-output',data=payload,
                                              headers={'Content-Type':'application/json'}) as response:
                        raw=await response.read()
                        if response.status==200:
                            state=json.loads(raw)
                            state=self.decorate_whip_status(state,self.publisher.snapshot())
                            raw=json.dumps(state).encode()
                        return web.Response(status=response.status,body=raw,
                                            headers={'Content-Type':response.headers.get('Content-Type','application/json'),
                                                     'Cache-Control':'no-store'})
                except (asyncio.TimeoutError,OSError):
                    raise web.HTTPServiceUnavailable(text='WHIP gateway unavailable')
        if request.method!='POST':
            if request.method=='DELETE':
                async with self.control_lock:
                    result=await forward(request.method,body)
                    await self.hevc.stop()
                    return result
            return await forward(request.method,body)
        async with self.control_lock:
            source=self.resolve_source({}) if not self.engine.snapshot()['running'] else None
            self.whip_target=connection_address(config.get('url',''))
            result=await forward('POST',body)
            if result.status==200:
                await self.publisher.set_audio_bitrate(audio_bitrate)
                config['audio_bitrate']=audio_bitrate
                await asyncio.to_thread(self.output_settings.save,'whip',config)
                self.feedback.configure(config)
                if codec=='h265':await self.hevc.start(bitrate or current,preset or 'medium')
                else:await self.hevc.stop()
            if result.status==200 and source:
                try:await self.activate_source(source)
                except Exception:
                    await forward('DELETE',b'')
                    raise
            if result.status==200:
                state=json.loads(result.body);state['audio_bitrate']=audio_bitrate
                result=web.json_response(state,headers={'Cache-Control':'no-store'})
            return result

    async def srt_output(self, request):
        local_control(request)
        if request.method=='GET' and self.dashboard_cache is not None:
            return web.json_response(self.dashboard_cache['srt'],headers={'Cache-Control':'no-store'})
        async with self.control_lock:
            if request.method == 'POST':
                try: config = validate_srt(await request.json())
                except (ValueError, TypeError):
                    raise web.HTTPBadRequest(text='Invalid SRT settings. Check destination, port, latency, codec, bitrate, keyframe, CBR and passphrase (10–79 bytes).')
                source = self.resolve_source({}) if not self.engine.snapshot()['running'] else None
                if source: await self.activate_source(source)
                await asyncio.to_thread(self.output_settings.save,'srt',config)
                try: await self.srt.start(config)
                except ValueError as exc: raise web.HTTPBadRequest(text=str(exc))
            elif request.method == 'DELETE': await self.srt.stop()
        status = self.srt.snapshot()
        try:
            async with ClientSession(timeout=ClientTimeout(total=1)) as client:
                async with client.get(self.gateway_url+'/srt-output') as response:
                    if response.status == 200: status.update(await response.json())
        except (asyncio.TimeoutError,OSError): status['transport_state']='unavailable'
        return web.json_response(status, headers={'Cache-Control':'no-store'})

    async def user_list(self,request):
        local_control(request)
        peers=await self.gateway_connections()
        base=self.args.public_base_url or f'http://127.0.0.1:{self.args.port}'
        entries=[]
        for user in self.users.data['users']:
            entries.append({**{k:v for k,v in user.items() if k!='token'},
                'link':base+'/viewer?access='+user['token'],
                'connections':sum(p.get('user_id')==user['id'] for p in peers)})
        return web.json_response({'users':entries,'require_links':self.users.data['require_links'],'general_connections':sum(not p.get('user_id') for p in peers),'allocated_connections':sum(u['limit'] for u in self.users.data['users']),'max_viewers':self.args.max_viewers},headers={'Cache-Control':'no-store'})

    async def user_export(self,request):
        local_control(request)
        folder=self.users.path.parent
        template=folder/'usermail.txt'
        if not template.is_file():template=folder/'usertext.txt'
        try:
            raw=await asyncio.to_thread(template.read_bytes)
            try:body=raw.decode('utf-8-sig').strip()
            except UnicodeDecodeError:body=raw.decode('cp1252').strip()
        except OSError:
            raise web.HTTPBadRequest(text='Mail text unavailable. Add usermail.txt to the user folder.')
        base=self.args.public_base_url or f'http://127.0.0.1:{self.args.port}'
        messages=[]
        for user in self.users.data['users']:
            device_note=(f"\nYou may use this link on up to {user['limit']} of your own devices at the same time. All other rules still apply: do not share this personal link.\n" if user['limit']>1 else '')
            messages.append(f"To: {user['email']}\nName: {user['name']}\nStatus: {'Enabled' if user['enabled'] else 'Disabled'}\nAllowed connections: {user['limit']}\n\nDear user {user['name']},\n\n{body}\n{base}/viewer?access={user['token']}\n{device_note}")
        return web.Response(text=('\n'+'='*64+'\n\n').join(messages),content_type='text/plain',charset='utf-8',headers={'Content-Disposition':'attachment; filename="FastVideo-user-emails.txt"','Cache-Control':'no-store'})

    async def user_save(self,request):
        local_control(request)
        data=await request.json()
        async with self.control_lock,self.offer_lock:
            try:user=self.users.save(data)
            except ValueError as exc:raise web.HTTPBadRequest(text=str(exc))
            if not user['enabled']:await self.close_user_sessions(user['id'])
        return web.json_response({'saved':True,'id':user['id']})

    async def user_replace(self,request):
        local_control(request)
        data=await request.json()
        async with self.control_lock,self.offer_lock:
            try:user=self.users.replace(data.get('id'))
            except ValueError as exc:raise web.HTTPBadRequest(text=str(exc))
            await self.close_user_sessions(user['id'])
        return web.json_response({'replaced':True})

    async def user_delete(self,request):
        local_control(request)
        async with self.control_lock,self.offer_lock:
            identifier=request.match_info['uid']
            if not any(u['id']==identifier for u in self.users.data['users']):raise web.HTTPNotFound()
            await self.close_user_sessions(identifier)
            self.users.remove(identifier)
        return web.Response(status=204)

    async def user_access_mode(self,request):
        local_control(request)
        data=await request.json()
        async with self.control_lock,self.offer_lock:
            try:self.users.require_links(data.get('require_links'))
            except ValueError as exc:raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({'require_links':self.users.data['require_links']})

    async def personal_offer(self,request):
        async with self.offer_lock:
            token=request.headers.get('X-FastVideo-Access','')
            user=self.users.authorize(token)
            if token and not user:raise web.HTTPForbidden(text='This viewer link is invalid or disabled')
            if not user and self.users.data['require_links']:raise web.HTTPForbidden(text='A personal viewer link is required')
            client_key=request.headers.get('X-FastVideo-Client','')
            if user:
                if not 16<=len(client_key)<=80 or not all(c.isalnum() or c=='-' for c in client_key):
                    raise web.HTTPBadRequest(text='Invalid viewer instance')
                peers=await self.gateway_connections()
                mine=[p for p in peers if p.get('user_id')==user['id']]
                same=[p for p in mine if p.get('client_key')==client_key]
                if len(mine)-len(same)>=user['limit']:
                    raise web.HTTPConflict(text='This personal link has reached its connection limit')
                async with ClientSession(timeout=ClientTimeout(total=3)) as client:
                    for peer in same:
                        async with client.delete(self.gateway_url+'/session/'+peer['id']) as response:
                            if response.status!=204:raise web.HTTPServiceUnavailable(text='Unable to renew viewer connection')
            response=await self.gateway_proxy(request, user=user, client_key=client_key)
            if user and response.status==201:response.headers['X-FastVideo-User']=user['id'][:8]
            return response

    async def page(self, request):
        name = "viewer.html"
        if request.path == "/sender":
            local_control(request)
            name = "bridge.html"
        elif request.path == "/" and request.remote in ("127.0.0.1", "::1") and request.host.split(":")[0].lower() in ("127.0.0.1", "localhost"):
            name = "bridge.html"
        return web.FileResponse(ROOT / "static" / name, headers={"Cache-Control": "no-store"})

    async def ice_config(self, request):
        return web.json_response({"iceServers": [{"urls": self.args.stun_urls}] if self.args.stun_urls else [], "mode": "direct", "gateway_port": self.args.gateway_port},headers={"Cache-Control":"no-store"})

    async def discover(self, request):
        local_control(request)
        if request.query.get('cached')=='1' and self.choices:
            return web.json_response({'sources':self.choices,'errors':self.sources.errors})
        async with self.control_lock:
            found = await asyncio.to_thread(self.sources.discover)
            self.choices = found["sources"]
        return web.json_response(found)

    async def status(self, request):
        data=await self.stream_snapshot()
        data['stream_warning']=self.warning_history.text
        if not self.may_read_diagnostics(request):data=public_status(data)
        return web.json_response(data,headers={"Cache-Control":"no-store"})

    def may_read_diagnostics(self,request):
        try:
            local_control(request);return True
        except web.HTTPForbidden:
            return self.whip_receiver.accepts_key(request.headers.get('Authorization'))

    async def stream_snapshot(self):
        if self.dashboard_cache is not None:return dict(self.dashboard_cache['status'])
        local,_,_=await self.status_work(self.local_status)
        return self.decorate_stream_status(local)

    def decorate_stream_status(self,data):
        data['input_enabled']=self.input_requested()
        data.update({"application": APP_NAME, "version": VERSION, "viewers": len(self.sessions), "max_viewers": self.args.max_viewers,
                     "viewer_title": self.viewer_title,
                     "viewer_hide_logo": self.viewer_hide_logo,
                     "selected_source": self.selected_source,
                     "uptime_seconds": int(time.monotonic() - self.started),
                     "viewer_links": ([self.args.public_base_url + "/viewer"] if self.args.public_base_url else []) + [f"http://{addr}:{self.args.port}/viewer" for addr in self.cached_addresses],
                     "source_errors": self.sources.errors,
                     "latency_scope": "Dekodierter FastVideo-Eingang → Browser-Bildausgabe"})
        data["h264_fallback_bitrate"]=self.args.h264_fallback_bitrate
        data["h264_fallback_qsv_preset"]=self.args.h264_fallback_qsv_preset
        data["input_preview_enabled"] = self.input_preview.enabled
        data["input_audio"] = self.input_audio_state()
        data["video_codecs"]=[self.args.codec]+(["h264"] if self.publisher.fallback else [])+(["h265"] if self.hevc.task else [])
        data.update({'process_supervision':(ROOT/'work'/'pid-service.txt').exists(),'source_recoveries':self.source_recoveries})
        if self.viewer_diagnostics is not None:
            data["viewer_diagnostics"] = dict(self.viewer_diagnostics)
            data["viewer_diagnostics"]["age_seconds"] = round(time.monotonic() - self.diagnostics_at, 1)
        return data

    async def system_status(self, request):
        local_control(request)
        data=dict(self.dashboard_cache['system']) if self.dashboard_cache is not None else await self.status_work(self.system_metrics.snapshot)
        data.update(server_started_at=self.started_at,server_uptime_seconds=int(time.monotonic()-self.started))
        return web.json_response(data,headers={"Cache-Control":"no-store"})

    async def preview(self, request):
        local_control(request)
        jpeg = await self.input_preview.get()
        if jpeg is None:
            return web.Response(status=204, headers={"Cache-Control":"no-store",
                "X-Preview-State":"no-signal" if self.input_preview.enabled else "paused"})
        return web.Response(body=jpeg, content_type="image/jpeg", headers={
            "Cache-Control":"no-store", "X-Preview-Work-Ms":f"{self.input_preview.work_ms:.3f}",
            "X-Frame-Wall-Ms":f"{self.input_preview.frame_wall_ms or 0:.1f}"})

    async def receive_preview(self, request):
        local_control(request)
        jpeg = await self.receiver_preview.get()
        if jpeg is None:return web.Response(status=204,headers={'Cache-Control':'no-store'})
        return web.Response(body=jpeg,content_type='image/jpeg',headers={'Cache-Control':'no-store',
            'X-Preview-Work-Ms':f'{self.receiver_preview.work_ms:.3f}',
            'X-Frame-Wall-Ms':f'{self.receiver_preview.frame_wall_ms or 0:.1f}'})

    async def preview_control(self, request):
        local_control(request)
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        data = await request.json()
        if type(data.get("enabled")) is not bool:
            raise web.HTTPBadRequest(text="enabled must be true or false")
        self.input_preview.enabled = data["enabled"]
        return web.json_response({"enabled":self.input_preview.enabled})

    async def clock(self, request):
        # Four timestamps for a diagnostic NTP-style estimate. No clocks are changed.
        received_ms = time.time_ns() / 1_000_000
        sent_ms = time.time_ns() / 1_000_000
        return web.json_response({"received_ms": received_ms, "sent_ms": sent_ms},
                                 headers={"Cache-Control": "no-store"})

    async def is_gateway_session(self, sid):
        # Only viewers the gateway knows may report diagnostics (otherwise anyone could fill
        # "Latest viewer diagnostics"). The list is fetched again at most every 2 s.
        now=time.monotonic()
        if sid not in self.gateway_sids and now-self.gateway_sids_at>2:
            self.gateway_sids_at=now
            try:self.gateway_sids={peer['id'] for peer in await self.gateway_connections()}
            except Exception:return False
        return sid in self.gateway_sids

    async def diagnostics(self, request):
        if request.match_info["sid"] in self.admin_disconnected:
            return web.Response(status=410,text="Disconnected by organiser")
        sid=request.match_info["sid"]
        known=await self.is_gateway_session(sid) if self.args.gateway else sid in self.sessions
        if not known:
            raise web.HTTPNotFound()
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        report = await request.json()
        # Numeric measurements plus browser identity parsed from the HTTP header.
        keys = ("raw_latency_ms", "adjusted_estimate_ms", "viewer_clock_ahead_ms",
                "clock_uncertainty_ms", "http_rtt_ms", "webrtc_rtt_ms",
                "jitter_buffer_ms", "decode_ms", "receive_to_display_ms", "fps")
        keys += ("packets_lost", "packets_received", "frames_dropped", "receive_fps", "nack_count", "pli_count", "path_is_relay", "protocol_is_tcp", "frames_decoded", "frames_received", "playback_paused", "playback_ready_state", "playback_muted", "displayed_frame_age_ms", "receiver_latency_support", "audio_jitter_buffer_ms", "audio_jitter_target_ms", "audio_jitter_minimum_ms", "audio_concealed_samples", "audio_inserted_samples", "audio_removed_samples", "video_jitter_target_ms", "video_jitter_minimum_ms", "audio_output_primed", "audio_output_base_latency_ms", "audio_output_latency_ms", "receive_bitrate_mbps")
        cleaned = {}
        import math
        for key in keys:
            value = report.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value) and abs(value) <= 1_000_000:
                cleaned[key] = round(value, 2)
        from fastvideo.browser_info import browser_info
        cleaned.update(browser_info(request.headers.get("User-Agent","")))
        self.viewer_diagnostics = cleaned
        self.diagnostics_at = time.monotonic()
        self.connection_reports[request.match_info['sid']] = (cleaned, time.monotonic())
        self.connection_reports = {sid: pair for sid, pair in self.connection_reports.items() if time.monotonic()-pair[1] < 30}
        if len(self.connection_reports) > 100:
            self.connection_reports.pop(next(iter(self.connection_reports)))
        return web.Response(status=204)

    async def disconnect_general(self,request):
        local_control(request)
        async with self.offer_lock:
            peers=await self.gateway_connections()
            general=[peer for peer in peers if not peer.get('user_id')]
            async with ClientSession(timeout=ClientTimeout(total=3)) as client:
                for peer in general:
                    self.admin_disconnected[peer['id']]=time.monotonic()
                    async with client.delete(self.gateway_url+'/session/'+peer['id']) as response:
                        if response.status!=204:
                            self.admin_disconnected.pop(peer['id'],None)
                            raise web.HTTPServiceUnavailable(text='Unable to disconnect general viewer')
        return web.json_response({'disconnected':len(general)})

    async def disconnect_connection(self,request):
        local_control(request)
        sid=request.match_info['sid']
        try:uuid.UUID(sid)
        except ValueError:raise web.HTTPBadRequest(text='Invalid connection')
        async with self.offer_lock:
            async with ClientSession(timeout=ClientTimeout(total=3)) as client:
                async with client.delete(self.gateway_url+'/session/'+sid) as response:
                    if response.status!=204:raise web.HTTPServiceUnavailable(text='Unable to disconnect viewer')
            now=time.monotonic()
            self.admin_disconnected={key:at for key,at in self.admin_disconnected.items() if now-at<3600}
            self.admin_disconnected[sid]=now
        return web.json_response({'disconnected':True})

    async def session_state(self,request):
        disconnected=request.match_info['sid'] in self.admin_disconnected
        return web.json_response({'disconnected':disconnected},headers={'Cache-Control':'no-store'})

    async def connections(self, request):
        local_control(request)
        try:
            async with ClientSession(timeout=ClientTimeout(total=3)) as client:
                async with client.get(self.gateway_url+'/connections') as response:
                    data = await response.json()
            names={user['id']:user['name'] for user in self.users.data['users']}
            for entry in data['connections']:
                entry['user_name']=names.get(entry.get('user_id'),'General viewer')
                pair = self.connection_reports.get(entry['id'])
                if pair and time.monotonic()-pair[1] < 30:
                    entry['browser'] = dict(pair[0], age_s=round(time.monotonic()-pair[1],1))
            return web.json_response(data, headers={'Cache-Control':'no-store'})
        except Exception as exc:
            raise web.HTTPServiceUnavailable(text='Verbindungsdiagnose nicht erreichbar') from exc

    async def configure(self, request):
        local_control(request)
        data = await request.json()
        return await self.apply_encoding(data)

    async def apply_encoding(self, data):
        bitrate, preset = data.get('bitrate',self.args.bitrate), data.get('nvenc_preset',self.args.nvenc_preset)
        qsv_preset=data.get('qsv_preset',self.args.qsv_preset)
        fallback_bitrate=data.get('h264_fallback_bitrate',self.args.h264_fallback_bitrate)
        fallback_preset=data.get('h264_fallback_qsv_preset',self.args.h264_fallback_qsv_preset)
        if type(fallback_bitrate) is not int or not 500_000 <= fallback_bitrate <= 12_000_000 or fallback_preset not in ('medium','slow','veryslow'):
            raise web.HTTPBadRequest(text='Ungueltige H.264-Bitrate oder Intel-Qualitaetsstufe')
        if qsv_preset not in ('veryfast','medium','slow','veryslow'):
            raise web.HTTPBadRequest(text='Ungueltige Intel-Qualitaetsstufe')
        if type(bitrate) is not int or not 500_000 <= bitrate <= 12_000_000 or preset not in ('p1','p2','p3','p4','p5','p6','p7'):
            raise web.HTTPBadRequest(text='Ungültige Bitrate oder Encoderstufe')
        async with self.control_lock:
            async with self.offer_lock:
                source = self.engine.source
                await self.close_sessions()
                if self.bridge:await self.bridge.stop()
                await self.publisher.stop()
                await asyncio.to_thread(self.engine.stop)
                self.args.bitrate, self.args.nvenc_preset = bitrate, preset
                self.args.qsv_preset=qsv_preset
                self.args.h264_fallback_bitrate=fallback_bitrate
                self.args.h264_fallback_qsv_preset=fallback_preset
                encoder.install(self.args.encoder,self.args.fps,self.args.codec,bitrate,self.args.h264_profile,preset,self.args.av1_preset,self.args.av1_lp,self.args.qsv_preset)
                encoder.use_shared_packets()
                settings = json.loads((ROOT/'fastvideo.settings.json').read_text(encoding='utf-8-sig'))
                settings.update({'h264_fallback_bitrate':fallback_bitrate,'h264_fallback_qsv_preset':fallback_preset,'qsv_preset':qsv_preset,'bitrate':bitrate,'nvenc_preset':preset,'max_viewers':self.args.max_viewers,
                                 'http_port':self.args.port,'gateway_port':self.args.gateway_port,'bridge_port':self.args.bridge_port})
                temporary = ROOT / 'fastvideo.settings.tmp'
                temporary.write_text(json.dumps(settings,indent=2),encoding='utf-8')
                temporary.replace(ROOT / 'fastvideo.settings.json')
                self.publisher = Publisher(self.engine,fallback=self.args.gateway and self.args.codec=="av1",fallback_bitrate=getattr(self.args,"h264_fallback_bitrate",4_000_000),fallback_preset=getattr(self.args,"h264_fallback_qsv_preset","medium"))
                if source:
                    await self.prepare_source(source)
                    await asyncio.to_thread(self.engine.start,source)
                    self.bridge = GatewayBridge(self.publisher,self.engine.timeline,self.args.bridge_port,hevc=self.hevc)
                    self.bridge.start()
        return web.json_response({'configured':True})

    async def restore(self, app):
        self.recovery_task = asyncio.create_task(self.recover_source())
        source_file = ROOT / 'work' / 'last-source.json'
        if source_file.exists():
            try:
                source = json.loads(source_file.read_text(encoding='utf-8'))
                await self.prepare_source(source)
                await asyncio.to_thread(self.engine.start,source)
                self.bridge = GatewayBridge(self.publisher,self.engine.timeline,self.args.bridge_port,hevc=self.hevc)
                self.bridge.start()
                log.info('Letzte Quelle nach Neustart wieder verbunden')
            except Exception:
                self.pending_recovery = source
                log.exception('Letzte Quelle konnte nicht wieder verbunden werden')

    async def recover_source(self):
        while True:
            # A pipeline fault (source clock jump, capture worker ended) is noticed within a
            # second: the gateway holds its connections for 10 s, so a quick restart keeps the
            # far end connected. A source that could not be opened is retried every 10 s.
            await asyncio.sleep(10 if getattr(self,'pending_recovery',None) else 1)
            pending = getattr(self,'pending_recovery',None)
            if not pending and (not self.engine.source or not (self.engine.error or self.publisher.error)):
                continue
            async with self.control_lock, self.offer_lock:
                # A manual stop or source switch may have won the lock after
                # the preliminary check. Never reuse a stale recovery target.
                pending=getattr(self,'pending_recovery',None)
                if not pending and (not self.engine.source or not (self.engine.error or self.publisher.error)):
                    continue
                source = pending or self.engine.source
                if not source: continue
                try:
                    await self.close_sessions()
                    await self.publisher.stop()
                    await asyncio.to_thread(self.engine.stop)
                    self.publisher = Publisher(self.engine,fallback=self.args.gateway and self.args.codec=="av1",fallback_bitrate=getattr(self.args,"h264_fallback_bitrate",4_000_000),fallback_preset=getattr(self.args,"h264_fallback_qsv_preset","medium"))
                    await self.prepare_source(source)
                    await asyncio.to_thread(self.engine.start,source)
                    self.bridge = GatewayBridge(self.publisher,self.engine.timeline,self.args.bridge_port,hevc=self.hevc)
                    self.bridge.start()
                    self.source_recoveries += 1
                    self.pending_recovery = None
                    self.recovery_failure = None
                    log.warning('Quelle nach Pipelinefehler wieder verbunden')
                except Exception as error:
                    self.pending_recovery = source
                    # A source that stays away is retried every 10 s. Write the full error once,
                    # then one line every five minutes, not a traceback for every attempt.
                    text = str(error) or type(error).__name__
                    if text != getattr(self,'recovery_failure',None):
                        self.recovery_failure, self.recovery_failures = text, 1
                        log.exception('Quellenwiederherstellung fehlgeschlagen')
                    else:
                        self.recovery_failures += 1
                        if self.recovery_failures % 30 == 0:
                            log.warning('Quellenwiederherstellung weiterhin fehlgeschlagen (%s Versuche): %s',self.recovery_failures,text)

    async def close_sessions(self):
        if self.bridge:
            await self.bridge.stop()
            self.bridge = None
        sessions = list(self.sessions.values())
        self.sessions.clear()
        for pc, track in sessions:
            track.stop()
            await pc.close()

    async def start(self, request):
        local_control(request)
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        data = await request.json()
        async with self.control_lock:
            source=self.resolve_source(data)
            await self.activate_source(source)
            self.save_source_selection(source)
        return web.json_response(self.engine.snapshot())

    async def prepare_source(self, source):
        await asyncio.to_thread(self.engine.probe_format,source)
        await self.publisher.prepare()

    async def activate_source(self, source):
        self.pending_recovery=None
        async with self.offer_lock:
            await self.close_sessions()
            await self.publisher.stop()
            self.viewer_diagnostics = None
            self.diagnostics_at = None
            try:
                await asyncio.to_thread(self.engine.stop)
                self.publisher = Publisher(self.engine,fallback=self.args.gateway and self.args.codec=="av1",fallback_bitrate=getattr(self.args,"h264_fallback_bitrate",4_000_000),fallback_preset=getattr(self.args,"h264_fallback_qsv_preset","medium"))
                await self.prepare_source(source)
                await asyncio.to_thread(self.engine.start, source)
                if self.args.gateway:
                    self.bridge = GatewayBridge(self.publisher,self.engine.timeline,self.args.bridge_port,hevc=self.hevc)
                    self.bridge.start()
            except Exception as exc:
                log.exception('Unable to open source: %s',source['name'])
                raise web.HTTPBadRequest(text=f"Unable to open source {source['name']}. Check the input and server log.") from exc
        self.pending_recovery=None
        log.info("Quelle gestartet: %s", source["name"])
        (ROOT / 'work' / 'last-source.json').write_text(json.dumps(source),encoding='utf-8')

    async def stop(self, request):
        local_control(request)
        if request.query.get('preserve') != '1':
            (ROOT/'work'/'last-source.json').unlink(missing_ok=True)
        async with self.control_lock, self.offer_lock:
            self.pending_recovery = None
            await self.close_sessions()
            await self.publisher.stop()
            await asyncio.to_thread(self.engine.stop)
            await self.release_gateway_clients()
        return web.json_response({"stopped": True})

    async def release_gateway_clients(self):
        # A deliberate stop ends the far end's session at once. The gateway otherwise holds its
        # connections for 10 s (for a source restart), and the receiver would warn about missing
        # picture and sound all that time.
        if not self.args.gateway:return
        try:
            async with ClientSession(timeout=ClientTimeout(total=2)) as client:
                async with client.post(self.gateway_url+'/input-stopped') as response:await response.read()
        except (asyncio.TimeoutError,OSError):log.warning('Gateway did not confirm the stopped input')

    async def whep(self, request):
        if self.args.gateway:return await self.personal_offer(request)
        if request.content_type != "application/sdp":
            raise web.HTTPUnsupportedMediaType(text="WHEP erwartet application/sdp")
        sdp = await request.text()
        if len(sdp) > 65536:
            raise web.HTTPRequestEntityTooLarge(max_size=65536, actual_size=len(sdp))
        async with self.offer_lock:
            if len(self.sessions) >= self.args.max_viewers:
                raise web.HTTPConflict(text=f"Maximal {self.args.max_viewers} Viewer. Vorherigen Viewer trennen.")
            if not self.engine.snapshot()["signal"]:
                raise web.HTTPServiceUnavailable(text="Noch kein Videosignal. Quelle am Sender starten.")
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            track = None
            audio_track = None
            session_id = uuid.uuid4().hex
            self.sessions[session_id] = (pc, track)

            @pc.on("connectionstatechange")
            async def statechange():
                log.info("Viewer %s: %s", session_id[:8], pc.connectionState)
                if pc.connectionState in ("failed", "closed"):
                    self.sessions.pop(session_id, None)
                    if track:track.stop()
                    if pc.connectionState != "closed":
                        await pc.close()

            try:
                # Preferences must be applied BEFORE remote SDP negotiation;
                # aiortc computes the common codec list in setRemoteDescription.
                transceiver = pc.addTransceiver("video", direction="sendonly")
                codecs = [c for c in RTCRtpSender.getCapabilities("video").codecs
                          if c.mimeType.lower() in (f"video/{self.args.codec}", "video/rtx")]
                if self.args.codec == "h264" and encoder.snapshot()["encoder_backend"] == "nvenc":
                    prefix = {"high":"6400","main":"4d00"}.get(self.args.h264_profile)
                    if prefix:
                        codecs = [c for c in codecs if c.mimeType.lower()=="video/rtx" or str(c.parameters.get("profile-level-id","")).startswith(prefix)]
                if self.args.codec == "h264":
                    from aiortc.sdp import SessionDescription
                    offered=[c.parameters.get("profile-level-id") for m in SessionDescription.parse(sdp).media for c in m.rtp.codecs if c.mimeType.lower()=="video/h264"]
                    log.info("Viewer bietet H.264-Profile: %s",offered)
                transceiver.setCodecPreferences(codecs)
                # Legacy video-only probes remain supported. Browser offers
                # a second, recvonly audio section for program sound.
                wants_audio = "m=audio " in sdp
                if wants_audio:
                    audio_transceiver = pc.addTransceiver("audio",direction="sendonly")
                    audio_transceiver.setCodecPreferences([c for c in RTCRtpSender.getCapabilities("audio").codecs if c.mimeType.lower()=="audio/opus"])
                await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
                video = [t for t in pc.getTransceivers() if t.kind == "video"]
                if len(video) != 1:
                    raise ValueError("Genau eine Video-Spur erwartet")
                if not video[0]._codecs:
                    raise ValueError("Viewer unterstützt das gewählte Video-Profil nicht; ein kompatibles Profil am Sender wählen")
                track = self.publisher.subscribe(wants_audio)
                self.sessions[session_id] = (pc,track)
                video_sender = pc.addTrack(track.video)
                sync_sender(video_sender,self.engine.timeline)
                video_sender._send_keyframe = self.publisher.request_keyframe
                if wants_audio:
                    audio_track = track.audio
                    audio_sender = pc.addTrack(audio_track)
                    sync_sender(audio_sender,self.engine.timeline)
                await pc.setLocalDescription(await pc.createAnswer())
                log.info("Ausgehandelter Videocodec: %s", video[0]._codecs)
            except Exception as exc:
                self.sessions.pop(session_id, None)
                if track:track.stop()
                if audio_track:
                    audio_track.stop()
                await pc.close()
                log.exception("WHEP-Verhandlung fehlgeschlagen")
                raise web.HTTPBadRequest(text="Video-Verbindung fehlgeschlagen") from exc
            task = asyncio.create_task(self.expire_session(session_id, pc, track))
            self.session_tasks.add(task); task.add_done_callback(self.session_tasks.discard)
            return web.Response(status=201, text=pc.localDescription.sdp,
                                content_type="application/sdp",
                                headers={"Location": f"/session/{session_id}", "Cache-Control": "no-store"})

    async def expire_session(self, sid, pc, track):
        # Close orphaned sessions (e.g. browser crash / failed initial ICE).
        not_connected = 0
        while sid in self.sessions:
            await asyncio.sleep(5)
            if pc.connectionState == "connected":
                not_connected = 0
            else:
                not_connected += 5
            if not_connected >= 20:
                self.sessions.pop(sid, None)
                track.stop()
                await pc.close()
                break

    async def delete_session(self, request):
        if self.args.gateway:return await self.gateway_proxy(request)
        pair = self.sessions.pop(request.match_info["sid"], None)
        if pair:
            pc, track = pair
            track.stop()
            await pc.close()
        return web.Response(status=204)

    async def gateway_proxy(self, request, user=None, client_key=""):
        if request.content_length and request.content_length > 65536:
            raise web.HTTPRequestEntityTooLarge(max_size=65536,actual_size=request.content_length)
        body = await request.read()
        try:
            async with ClientSession(timeout=ClientTimeout(total=12)) as client:
                async with client.request(request.method,self.gateway_url+request.path_qs,
                                          data=body,headers={'Content-Type':request.headers.get('Content-Type',''),
                                            'X-FastVideo-User':user['id'] if user else '',
                                            'X-FastVideo-Client':client_key if user else ''}) as response:
                    headers={key:response.headers[key] for key in ('Content-Type','Location','X-FastVideo-Codec') if key in response.headers}
                    headers['Cache-Control']='no-store'
                    return web.Response(status=response.status,body=await response.read(),headers=headers)
        except Exception as exc:
            raise web.HTTPServiceUnavailable(text='Go-Gateway nicht erreichbar') from exc

    async def keyframe(self, request):
        local_control(request)
        self.publisher.request_keyframe()
        self.hevc.request_keyframe()
        return web.Response(status=204)

    async def cleanup(self, app):
        if self.feedback_task:
            self.feedback_task.cancel()
            await asyncio.gather(self.feedback_task,return_exceptions=True)
        await self.whip_receiver.stop()
        await self.whip_receiver.close()
        await self.hevc.stop()
        await self.srt.stop()
        if self.warning_task:
            self.warning_task.cancel()
            await asyncio.gather(self.warning_task,return_exceptions=True)
        await self.status_work(self.warning_history.close)
        await self.status_work(self.connection_log.close)
        self.status_worker.shutdown(wait=False)
        if self.recovery_task:
            self.recovery_task.cancel()
            await asyncio.gather(self.recovery_task,return_exceptions=True)
        await self.close_sessions()
        await self.publisher.stop()
        await asyncio.to_thread(self.engine.close)
        await asyncio.to_thread(self.sources.close)


@web.middleware
async def headers(request, handler):
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; frame-ancestors 'self'"
    return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8879)
    parser.add_argument("--gateway-port",type=int,default=8880)
    parser.add_argument("--bridge-port",type=int,default=9999)
    parser.add_argument("--public-base-url",default="")
    parser.add_argument("--stun-urls",nargs="*",default=[])
    parser.add_argument("--fps", type=int, default=50, choices=(25, 30, 50))
    parser.add_argument("--encoder", default="auto", choices=("auto", "cpu", "nvenc", "qsv"))
    parser.add_argument("--codec", default="h264", choices=("av1", "h264"))
    parser.add_argument("--bitrate", type=int, default=None)
    parser.add_argument("--h264-profile",default="high",choices=("baseline","main","high"))
    parser.add_argument("--nvenc-preset",default="p4",choices=("p1","p2","p3","p4","p5","p6","p7"))
    parser.add_argument("--qsv-preset",default="medium",choices=("veryfast","medium","slow","veryslow"))
    parser.add_argument("--h264-fallback-bitrate",type=int,default=4_000_000)
    parser.add_argument("--h264-fallback-qsv-preset",default="medium",choices=("medium","slow","veryslow"))
    parser.add_argument("--av1-preset",type=int,default=8,choices=range(8,14))
    parser.add_argument("--av1-lp",type=int,default=6,choices=range(1,7))
    parser.add_argument("--max-viewers",type=int,default=40)
    parser.add_argument("--gateway",action="store_true",help="Go/Pion übernimmt alle Zuschauerverbindungen")
    parser.add_argument("--timestamp-marker", action="store_true", help="Optionaler Messstreifen im Videobild; standardmäßig aus")
    args = parser.parse_args()
    settings_file = ROOT / 'fastvideo.settings.json'
    if settings_file.exists():
        settings = json.loads(settings_file.read_text(encoding='utf-8-sig'))
        import sys
        for name in ('timestamp_marker','h264_fallback_bitrate','h264_fallback_qsv_preset','qsv_preset','codec','encoder','bitrate','nvenc_preset','max_viewers','gateway_port','bridge_port','port','public_base_url','stun_urls'):
            flag = '--'+name.replace('_','-')
            if not any(arg == flag or arg.startswith(flag+'=') for arg in sys.argv[1:]):
                setting_name = 'http_port' if name == 'port' else name
                setattr(args,name,settings.get(setting_name,getattr(args,name)))
    if type(args.timestamp_marker) is not bool:
        parser.error("timestamp_marker must be true or false")
    if type(args.h264_fallback_bitrate) is not int or not 500_000 <= args.h264_fallback_bitrate <= 12_000_000 or args.h264_fallback_qsv_preset not in ("medium","slow","veryslow"):
        parser.error("Ungueltige H.264-Ersatzstream-Einstellungen")
    if args.bitrate is None:
        args.bitrate = 4_000_000 if args.codec == "av1" else 6_000_000
    if not isinstance(args.stun_urls,list) or any(not isinstance(url,str) or not url.startswith("stun:") or len(url)>256 for url in args.stun_urls):
        parser.error("stun_urls muss eine Liste von STUN-Adressen sein")
    args.public_base_url = args.public_base_url.rstrip("/")
    if args.public_base_url:
        public_url = urlsplit(args.public_base_url)
        if public_url.scheme != "https" or not public_url.hostname or public_url.path or public_url.query or public_url.fragment or public_url.username or public_url.password:
            parser.error("public_base_url muss eine HTTPS-Adresse ohne Pfad sein")
    ports=(args.port,args.gateway_port,args.bridge_port)
    if any(type(port) is not int or not 1024 <= port <= 65535 for port in ports) or len(set(ports)) != 3:
        parser.error('HTTP-, Gateway- und Bridge-Port müssen verschieden sein und zwischen 1024 und 65535 liegen')
    capacity = 100 if args.gateway else 40
    if not 1 <= args.max_viewers <= capacity:
        parser.error(f"max-viewers muss zwischen 1 und {capacity} liegen")
    if args.gateway and args.codec=='h264' and args.h264_profile!='high':
        parser.error("Go-Gateway erwartet momentan H.264 High")
    # Fail before native SDK initialization or overwriting a live process file.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((args.host, args.port))
    except OSError:
        parser.error(f"Port {args.port} ist bereits belegt. Laufenden Sender verwenden oder anderen Port wählen.")
    (ROOT / "work").mkdir(exist_ok=True)
    (ROOT / "work" / f"pid-{args.port}.txt").write_text(str(os.getpid()), encoding="ascii")
    (ROOT / "work" / "pid-server.txt").write_text(str(os.getpid()), encoding="ascii")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), RotatingFileHandler(ROOT / "work" / f"fastvideo-{args.port}.log", maxBytes=5_000_000, backupCount=2, encoding="utf-8")])
    if not 500_000 <= args.bitrate <= 12_000_000:
        parser.error("bitrate muss zwischen 500000 und 12000000 liegen")
    if args.nvenc_preset not in ('p1','p2','p3','p4','p5','p6','p7'):
        parser.error('Ungültige Encoderstufe in Einstellungen')
    from fastvideo.process_priority import opt_out
    opt_out()
    encoder.install(args.encoder, args.fps, args.codec, args.bitrate,args.h264_profile,args.nvenc_preset,args.av1_preset,args.av1_lp,args.qsv_preset)
    encoder.use_shared_packets()
    server = Server(args)
    app = web.Application(middlewares=[headers], client_max_size=65536)
    app.add_routes([web.get("/", server.page), web.get("/sender", server.page),
                    web.get("/viewer", server.page), web.get("/api/sources", server.discover),
                    web.get("/api/ice", server.ice_config), web.get("/api/status", server.status),web.get("/api/dashboard",server.dashboard), web.post("/api/start", server.start),
                    web.get("/api/clock", server.clock), web.get("/api/system", server.system_status),
                    web.get("/api/preview", server.preview),
                    web.post("/api/preview/control", server.preview_control),
                    web.post("/api/viewer/display", server.viewer_display),
                    web.post("/api/viewer/capacity", server.viewer_capacity),
                    web.post("/api/source", server.set_source),
                    web.get("/api/warnings", server.warning_log),
                    web.get("/api/connection-log", server.connection_history),
                    web.post("/api/warnings/outage", server.warning_outage),
                    web.get("/api/whip", server.whip_output), web.post("/api/whip", server.whip_output), web.delete("/api/whip", server.whip_output),
                    web.get('/api/output-settings',server.saved_outputs), web.get('/api/srt', server.srt_output), web.post('/api/srt', server.srt_output), web.delete('/api/srt', server.srt_output),
                    web.get("/api/users/export", server.user_export), web.get("/api/users", server.user_list), web.post("/api/users", server.user_save),
                    web.delete("/api/users/{uid}",server.user_delete),
                    web.post("/api/users/replace", server.user_replace), web.post("/api/users/access", server.user_access_mode),
                    web.post("/api/connections/disconnect-general", server.disconnect_general),
                    web.post("/api/connections/{sid}/disconnect", server.disconnect_connection),
                    web.get("/api/connections", server.connections), web.post("/api/configure",server.configure),
                    web.post("/api/keyframe", server.keyframe),
                    web.post("/api/stop", server.stop), web.post("/whep", server.whep),
                    web.get("/session/{sid}/state",server.session_state),
                    web.delete("/session/{sid}", server.delete_session),
                    web.post("/session/{sid}/diagnostics", server.diagnostics)])
    app.add_routes([web.get('/api/sdi/devices',server.sdi_devices),web.get('/api/input-audio',server.input_audio),web.post('/api/input-audio',server.input_audio),web.get('/api/output-format',server.output_format),web.post('/api/output-format',server.output_format),web.get('/api/receive/preview',server.receive_preview),web.get('/api/productions',server.productions),web.post('/api/productions',server.productions),web.get('/api/profiles/{kind}',server.profiles),web.post('/api/profiles/{kind}',server.profiles),web.post('/api/firewall',server.configure_firewall),web.get('/api/gpu',server.gpu_performance),web.post('/api/gpu',server.gpu_performance),web.post('/api/receive/settings',server.receive_network_settings),web.get('/api/receive/public-ip',server.detect_public_ip),web.post('/api/receive/audio',server.receive_audio_control),web.get('/api/receive',server.receive_control),web.post('/api/receive',server.receive_control),web.delete('/api/receive',server.receive_control),
                    web.post('/api/whip-input',server.whip_receiver.offer),web.get('/api/whip-input',lambda request:web.Response(status=204,headers={'Accept-Patch':'application/trickle-ice-sdpfrag'})),
                    web.delete('/api/whip-input/session/{sid}',server.whip_receiver.resource),web.get('/api/whip-input/session/{sid}',server.whip_receiver.resource),web.patch('/api/whip-input/session/{sid}',server.whip_receiver.resource)])
    app.router.add_static("/static/", ROOT / "static")
    # Browser tab icon; the same file is the Windows icon of the installer and shortcuts.
    app.router.add_get("/favicon.ico", lambda request: web.FileResponse(ROOT / "images" / "fastvideobridge.ico", headers={"Cache-Control": "max-age=86400"}))
    app.on_cleanup.append(server.cleanup)
    app.on_startup.append(lambda app: server.whip_receiver.start())
    if args.gateway: app.on_startup.append(server.restore)
    app.on_startup.append(server.start_warning_monitor)
    print(f"Sender: http://127.0.0.1:{args.port}/sender", flush=True)
    for addr in lan_addresses():
        print(f"Viewer: http://{addr}:{args.port}/viewer", flush=True)
    web.run_app(app, host=args.host, port=args.port, print=None, access_log=None)


if __name__ == "__main__":
    main()



