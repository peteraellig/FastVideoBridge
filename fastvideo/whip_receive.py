"""One authenticated LAN WHIP publisher, decoded video/stereo PCM -> OMT or Full NDI."""
import asyncio
import ctypes as C
import hmac
import re
import threading
import time
import uuid
import struct
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from fractions import Fraction
from collections import deque
from statistics import median
from types import SimpleNamespace
from .receive_network import receive_network
from .diagnostics import Timings
from .connection_health import ConnectionHealth, OutputSettling
from .output_workers import OutputWorkers
import av
import numpy as np
from aiohttp import web
from aiortc import RTCPeerConnection,RTCConfiguration,RTCSessionDescription,RTCBundlePolicy
from aiortc.rtp import RtcpSrPacket
from aiortc.sdp import candidate_from_sdp
from .sources import OMT,OMTFrame,bind,OMT_INTERLACED,woven_frame
from .auth_throttle import AuthThrottle
from .fixed_ice import reserve_udp,attach_fixed_ice

# Set in the receiver process: decode and output run in a separate media process.
MEDIA_PROCESS=False
_video_sink=None
# Packets per video picture the receive buffer can hold (aiortc default 128 ≈ 150 kB).
VIDEO_JITTER_PACKETS=2048
RECEIVE_STATS={'jitter_overflows':0,'largest_picture_bytes':0}
# A session without media for this long gives way to a new authenticated connection. The sending
# Bridge notices a break after 3 s and waits 3 s more before it reconnects.
SILENT_SESSION_SECONDS=3

def validate_endpoint_settings(port, public_ip, endpoint_name='whip-input', legacy=False):
    if type(port) is not int or not (legacy and port==0 or 1024<=port<=65535):
        raise ValueError('Receiver port must be 1024-65535')
    if not isinstance(public_ip,str):raise ValueError('Enter a public IPv4 address')
    public_ip=public_ip.strip()
    if public_ip:
        try:address=ipaddress.IPv4Address(public_ip)
        except ValueError:raise ValueError('Enter a public IPv4 address')
        if address.is_unspecified or address.is_loopback or address.is_multicast:raise ValueError('Enter a public IPv4 address')
        if not port:raise ValueError('Public reception requires a fixed receiver port')
    if not isinstance(endpoint_name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',endpoint_name):
        raise ValueError('WHIP endpoint name: 1-64 letters, numbers, hyphens or underscores')
    if not port and endpoint_name!='whip-input':raise ValueError('Custom WHIP endpoint requires a fixed receiver port')
    return dict(listen_port=port,public_ip=public_ip,endpoint_name=endpoint_name)


def receive_av1_obus(packets):
    from .av1 import read_leb,leb
    result=bytearray();pending=bytearray()
    for packet in packets:
        if not packet:raise ValueError('Empty AV1 RTP payload')
        header=packet[0];count=(header>>4)&3
        if bool(header&128)!=bool(pending):raise ValueError('Missing AV1 fragment')
        offset=1;index=0
        while offset<len(packet):
            index+=1
            if count and index==count:size=len(packet)-offset
            else:size,offset=read_leb(packet,offset)
            if not size or offset+size>len(packet):raise ValueError('Invalid AV1 element')
            pending.extend(packet[offset:offset+size]);offset+=size
            if offset==len(packet) and header&64:break
            extension=2 if pending[0]&4 else 1
            result.extend(bytes([pending[0]|2])+pending[1:extension]+leb(len(pending)-extension)+pending[extension:]);pending.clear()
        if count and index!=count:raise ValueError('Invalid AV1 element count')
    if pending:raise ValueError('Incomplete AV1 fragment')
    return bytes(result)

def receive_hevc_payload(data):
    # RFC 7798, single layer, sprop-max-don-diff=0 (no DONL).
    if len(data)<2 or data[0]&128 or not data[1]&7:
        raise ValueError('Invalid HEVC payload')
    kind=(data[0]>>1)&63
    if kind<48:return b'\x00\x00\x00\x01'+data
    if kind==49:
        if len(data)<4:raise ValueError('Truncated HEVC fragment')
        fragment=data[2];nal=fragment&63
        if nal>=48 or fragment&192==192:raise ValueError('Invalid HEVC fragment type')
        prefix=b'\x00\x00\x00\x01'+bytes([(data[0]&129)|(nal<<1),data[1]]) if fragment&128 else b''
        return prefix+data[3:]
    if kind==48:
        result=bytearray();offset=2
        while offset<len(data):
            if offset+2>len(data):raise ValueError('Truncated HEVC aggregation')
            size=int.from_bytes(data[offset:offset+2],'big');offset+=2
            if size<2 or offset+size>len(data):raise ValueError('Invalid HEVC aggregation size')
            result.extend(b'\x00\x00\x00\x01'+data[offset:offset+size]);offset+=size
        return bytes(result)
    raise ValueError('Unsupported HEVC packet type')


def register_receive_av1():
    from .av1 import register
    from .receive_decoder import ReceiveDecoder
    from aiortc.codecs import CODECS
    from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtcpFeedback
    register(50)
    # Receive capabilities must not depend on the locally selected encoder.
    # High/Main streams are decodable even when the sender uses another backend.
    for profile, payload in [('64002a', 111), ('4d002a', 113)]:
        if not any(c.mimeType.lower()=='video/h264' and str(c.parameters.get('profile-level-id',''))[:4]==profile[:4] for c in CODECS['video']):
            CODECS['video'].extend([
                RTCRtpCodecParameters(mimeType='video/H264',clockRate=90000,payloadType=payload,
                    parameters={'profile-level-id':profile,'packetization-mode':'1','level-asymmetry-allowed':'1'},
                    rtcpFeedback=[RTCRtcpFeedback(type='nack'),RTCRtcpFeedback(type='nack',parameter='pli')]),
                RTCRtpCodecParameters(mimeType='video/rtx',clockRate=90000,payloadType=payload+1,parameters={'apt':payload})])
    if not any(c.mimeType.lower()=='video/h265' for c in CODECS['video']):
        CODECS['video'].extend([
            RTCRtpCodecParameters(mimeType='video/H265',clockRate=90000,payloadType=115,
                parameters={'profile-id':'1','tier-flag':'0','level-id':'120','sprop-max-don-diff':'0'},
                rtcpFeedback=[RTCRtcpFeedback(type='nack'),RTCRtcpFeedback(type='nack',parameter='pli')]),
            RTCRtpCodecParameters(mimeType='video/rtx',clockRate=90000,payloadType=116,parameters={'apt':115})])
    import aiortc.rtcrtpreceiver as receiver_module
    if getattr(receiver_module,'_bridge_video_receive',False):return
    original_decoder=receiver_module.get_decoder
    original_depayload=receiver_module.depayload
    class VideoDecoder:
        def __init__(self,kind):self.kind=kind;self.codec=None if MEDIA_PROCESS else ReceiveDecoder(kind)
        def decode(self,frame):
            raw=frame.data
            if self.kind=='av1':
                payloads=[];offset=0
                while offset<len(raw):
                    if offset+2>len(raw):raise ValueError('Truncated AV1 payload')
                    size=struct.unpack_from('!H',raw,offset)[0];offset+=2
                    if offset+size>len(raw):raise ValueError('Truncated AV1 payload')
                    payloads.append(raw[offset:offset+size]);offset+=size
                raw=receive_av1_obus(payloads)
            if MEDIA_PROCESS:
                # Decoding happens in the media process; only the compressed picture goes there.
                sink=_video_sink
                if sink:sink(self.kind,raw,frame.timestamp)
                return []
            packet=av.Packet(raw);packet.pts=frame.timestamp;packet.time_base=Fraction(1,90000)
            return self.codec.decode(packet)
    def decoder(codec):
        mime=codec.mimeType.lower()
        return VideoDecoder({'video/h264':'h264','video/h265':'hevc','video/av1':'av1'}[mime]) if mime in ('video/h264','video/h265','video/av1') else original_decoder(codec)
    def depayload(codec,data):
        mime=codec.mimeType.lower()
        if mime=='video/av1':return struct.pack('!H',len(data))+data
        if mime=='video/h265':return receive_hevc_payload(data)
        return original_depayload(codec,data)
    original_buffer=receiver_module.JitterBuffer
    class VideoJitterBuffer(original_buffer):
        # aiortc assembles a video picture from at most 128 packets (~150 kB). Larger pictures,
        # i.e. keyframes at higher bitrates (H.265 12 Mbit/s, H.264 from ~6 Mbit/s), were
        # dropped without counting as loss and re-requested by PLI, often again too large.
        def __init__(self,capacity,prefetch=0,is_video=False):
            super().__init__(VIDEO_JITTER_PACKETS if is_video else capacity,prefetch,is_video)
        def add(self,packet):
            pli,frame=super().add(packet)
            if pli and self._is_video:RECEIVE_STATS['jitter_overflows']+=1
            if frame is not None and self._is_video:
                RECEIVE_STATS['largest_picture_bytes']=max(RECEIVE_STATS['largest_picture_bytes'],len(frame.data))
            return pli,frame
    receiver_module.JitterBuffer=VideoJitterBuffer
    receiver_module.get_decoder=decoder;receiver_module.depayload=depayload;receiver_module._bridge_video_receive=True

class OmtSender:
    def __init__(self,name,interlaced=False):
        self.runtime=OMT()
        self.interlaced=interlaced
        dll=self.runtime.dll
        self.create=bind(dll,'omt_send_create',[C.c_char_p,C.c_int],C.c_void_p)
        self.send=bind(dll,'omt_send',[C.c_void_p,C.POINTER(OMTFrame)],C.c_int)
        self.destroy=bind(dll,'omt_send_destroy',[C.c_void_p],None)
        self.getaddress=bind(dll,'omt_send_getaddress',[C.c_void_p,C.c_char_p,C.c_int],C.c_int)
        self.handle=self.create(name.encode(),100)
        if not self.handle:raise RuntimeError('OMT output could not start')
        address=C.create_string_buffer(1024);self.getaddress(self.handle,address,1024)
        self.address=address.value.decode('utf-8','replace')
        # libOMT has separate native audio/video buffers and locks.
        self.video_lock=threading.Lock();self.audio_lock=threading.Lock()
        self.timings=Timings()
        self.reformatter=av.video.reformatter.VideoReformatter()
        self.resampler=av.AudioResampler(format='fltp',layout='stereo',rate=48000)
        self.nv12_buffer=None
        self.vmx=None;self.vmx_unavailable=False
    def video(self,frame,timestamp,fps):
        # Above 60 fps the SDK selects a different thread policy; retain that
        # native path until it has its own measured direct-encoder comparison.
        if frame.format.name=='nv12' and frame.width%2==0 and frame.height%2==0 and fps<=60 and not self.vmx_unavailable:
            colorspace=709 if frame.height>=720 else 601
            # One serial worker owns this encoder and compressed packet buffer.
            started=time.perf_counter()
            with self.video_lock:
                self.timings.add('video_lock_wait',started)
                if not self.handle:return
                try:
                    if self.vmx is None or self.vmx.signature!=(frame.width,frame.height,colorspace):
                        previous_quality=self.vmx.get_quality(self.vmx.handle) if self.vmx else None
                        if self.vmx:self.vmx.close();self.vmx=None
                        from .vmx_output import VmxOutput
                        self.vmx=VmxOutput(frame.width,frame.height,colorspace)
                        if previous_quality is not None:self.vmx.set_quality(self.vmx.handle,previous_quality)
                    started=time.perf_counter()
                    pointer,length=self.vmx.packet(frame)
                    self.timings.add('video_vmx_encode',started)
                except Exception:
                    logging.getLogger(__name__).exception('Direct VMX unavailable; using standard OMT encoding')
                    if self.vmx:self.vmx.close();self.vmx=None
                    self.vmx_unavailable=True
                else:
                    f=OMTFrame(type=2,timestamp=timestamp,codec=int.from_bytes(b'VMX1','little'),width=frame.width,height=frame.height,stride=frame.width,fps_n=fps.numerator,fps_d=fps.denominator,flags=OMT_INTERLACED if woven_frame(self.interlaced,frame.height,fps) else 0,aspect=frame.width/frame.height,colorspace=colorspace,data=pointer,data_length=length)
                    started=time.perf_counter()
                    self.send(self.handle,C.byref(f))
                    self.timings.add('video_native_send',started)
                    return
        started=time.perf_counter()
        if frame.format.name=='nv12' and len(frame.planes)==2 and frame.planes[0].line_size==frame.planes[1].line_size:
            # Hardware copy-back produces NV12, which libOMT accepts directly.
            # Preserve padded row strides; pack only when planes are not adjacent.
            y,uv=frame.planes;stride=y.line_size
            ysize=stride*frame.height;uvsize=stride*((frame.height+1)//2)
            if y.buffer_size<ysize or uv.buffer_size<uvsize:raise ValueError('Truncated NV12 surface')
            if uv.buffer_ptr==y.buffer_ptr+ysize:
                data=None;pointer=y.buffer_ptr
            else:
                size=ysize+uvsize
                if self.nv12_buffer is None or self.nv12_buffer.size!=size:
                    self.nv12_buffer=np.empty(size,dtype=np.uint8)
                data=self.nv12_buffer;pointer=data.ctypes.data
                C.memmove(pointer,y.buffer_ptr,ysize);C.memmove(pointer+ysize,uv.buffer_ptr,uvsize)
            size=ysize+uvsize;codec=b'NV12'
            self.timings.add('video_nv12_prepare',started)
        else:
            converted=self.reformatter.reformat(frame,format='uyvy422',threads=1)
            self.timings.add('video_colour_convert',started)
            data=np.frombuffer(converted.planes[0],dtype=np.uint8)
            pointer=data.ctypes.data;size=data.nbytes;stride=converted.planes[0].line_size;codec=b'UYVY'
        f=OMTFrame(type=2,timestamp=timestamp,codec=int.from_bytes(codec,'little'),width=frame.width,height=frame.height,stride=stride,fps_n=fps.numerator,fps_d=fps.denominator,flags=OMT_INTERLACED if woven_frame(self.interlaced,frame.height,fps) else 0,aspect=frame.width/frame.height,colorspace=709 if frame.height>=720 else 601,data=pointer,data_length=size)
        started=time.perf_counter()
        with self.video_lock:
            self.timings.add('video_lock_wait',started)
            started=time.perf_counter()
            if self.handle:self.send(self.handle,C.byref(f))
            self.timings.add('video_native_send',started)
    def audio(self,frame,timestamp):
        elapsed=0
        for output in self.resampler.resample(frame):
            data=np.ascontiguousarray(output.to_ndarray(),dtype=np.float32)
            f=OMTFrame(type=4,timestamp=timestamp+elapsed,codec=int.from_bytes(b'FPA1','little'),sample_rate=48000,channels=2,samples=output.samples,data=data.ctypes.data,data_length=data.nbytes)
            started=time.perf_counter()
            with self.audio_lock:
                self.timings.add('audio_lock_wait',started)
                started=time.perf_counter()
                if self.handle:self.send(self.handle,C.byref(f))
                self.timings.add('audio_native_send',started)
            elapsed+=round(output.samples*10000000/48000)
    def close(self):
        with self.video_lock,self.audio_lock:
            if self.handle:self.destroy(self.handle);self.handle=None
            if self.vmx:self.vmx.close();self.vmx=None

class SourceClock:
    def __init__(self,fixed=False):self.report=None;self.offset=None;self.fixed=fixed;self.epoch=None
    def report_time(self,ntp,rtp):self.report=(ntp/(1<<32)-2208988800,rtp)
    def timestamp(self,frame,receiver):
        seconds=float(frame.pts*frame.time_base)
        if self.fixed and self.epoch is not None:return round((self.epoch+seconds)*10000000)
        mapper=getattr(receiver,'_RTCRtpReceiver__timestamp_mapper',None)
        origin=getattr(mapper,'_origin',None)
        if self.report and origin is not None:
            rate=48000 if frame.__class__.__name__=='AudioFrame' else 90000
            stamp=(origin+round(seconds*rate))&0xffffffff
            delta=((stamp-self.report[1]+(1<<31))&0xffffffff)-(1<<31)
            mapped=self.report[0]+delta/rate
            if self.fixed:self.epoch=mapped-seconds
            return round(mapped*10000000)
        if self.offset is None:self.offset=time.time()-seconds
        return round((self.offset+seconds)*10000000)

class WhipReceive:
    def __init__(self,port,addresses):
        register_receive_av1()
        self.port=port;self.addresses=addresses;self.lock=asyncio.Lock();self.throttle=AuthThrottle()
        self.listen_port=56208;self.public_ip='';self.endpoint_name='whip-input';self.runner=None;self.udp_socket=None
        self.preview_frame=None
        self.ndi_audio_minus20=False;self.output_kind="omt";self._output_mode="follow";self.health=ConnectionHealth();self.settling=OutputSettling()
        self._video_receiver=None;self.loop=None
        self.sdi_device=None;self.sdi_interlaced=False
        self.enabled=False;self.key='';self.name='FastVideo Bridge Receive';self.sender=None
        self.output_workers=None
        self.pc=None;self.sid=None;self.tasks=set();self.cleanup_tasks=set();self.clocks={}
        self.frames={'video':0,'audio':0};self.drops=0;self.codec='';self.detail='';self.last_video=0;self.started=0;self.fps=Fraction(50);self.video_format=None;self.input_video_format=None;self.audio_format=None;self.last_audio=0;self.drop_counts={"video":0,"audio":0};self.queue_max={"video":0,"audio":0};self.timings=Timings();self.video_deltas=deque(maxlen=20);self.previous_video_time=None
    def restore_settings(self, settings):
        # Advertise the configured endpoint before starting any listener.
        if self.enabled:
            return
        port = settings.get('listen_port', 56208)
        self.listen_port = port if type(port) is int and 1024 <= port <= 65535 else 56208
        self.public_ip = str(settings.get('public_ip', '')).strip()
        self.endpoint_name=settings.get('endpoint_name','whip-input')
        if not isinstance(self.endpoint_name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',self.endpoint_name):self.endpoint_name='whip-input'
        self.name = settings.get('name', 'FastVideo Bridge Receive')
        self.output_kind = settings.get('output_kind', 'omt') if settings.get('output_kind', 'omt') in ('omt', 'ndi', 'sdi') else 'omt'
        self.sdi_device=settings.get('sdi_device')
        self.sdi_interlaced=settings.get('sdi_interlaced') is True
        self.ndi_audio_minus20 = settings.get('ndi_audio_minus20', False) is True
        # Output resolution is controlled by the shared Bridge format setting.

    @property
    def output_mode(self):return self._output_mode
    @output_mode.setter
    def output_mode(self,mode):
        self._output_mode=mode
        if hasattr(self.sender,'set_output_mode'):self.sender.set_output_mode(mode)

    def decoder_snapshot(self):
        from .receive_decoder import decoder_status
        if MEDIA_PROCESS:return self.sender.state.get('decoder') if self.sender else None
        if self.pc:
            for receiver in self.pc.getReceivers():
                if receiver.track and receiver.track.kind=='video':
                    thread=getattr(receiver,'_RTCRtpReceiver__decoder_thread',None)
                    if thread:return decoder_status(thread.ident)
        return None

    def status(self):
        state='stopped' if not self.enabled else 'receiving' if self.pc and self.pc.connectionState=='connected' and time.monotonic()-self.last_video<3 else 'connected' if self.pc and self.pc.connectionState=='connected' else 'waiting'
        data = dict(decoder=self.decoder_snapshot(),video_queue_drops=self.drop_counts["video"],audio_queue_drops=self.drop_counts["audio"],queue_max=dict(self.queue_max),output_timings=self.timings.snapshot(),omt_timings=self.sender.timings.snapshot() if self.sender and self.output_kind=="omt" else {},video_format=self.video_format,audio_format=self.audio_format,video_rate=float(self.fps) if self.video_format else None,audio_receiving=bool(self.last_audio and time.monotonic()-self.last_audio<3),enabled=self.enabled,state=state,detail=self.detail,name=self.name,listen_port=self.listen_port,public_ip=self.public_ip,endpoint_name=self.endpoint_name,public_url=f'http://{self.public_ip}:{self.listen_port}/api/{self.endpoint_name}' if self.public_ip and self.listen_port else '',urls=[f'http://{ip}:{self.listen_port or self.port}/api/{self.endpoint_name}' for ip in self.addresses()],omt_source=self.sender.address if self.sender and self.output_kind=='omt' else '',video_frames=self.frames['video'],audio_frames=self.frames['audio'],codec=self.codec,queue_drops=self.drops,sync='RTCP synchronized' if len(self.clocks)==2 and all(c.report for c in self.clocks.values()) else 'Waiting for sender clocks',fps=round(self.frames['video']/max(.1,time.monotonic()-self.started),1) if self.started else 0)
        data.update(input_video_format=self.input_video_format,ndi_audio_minus20=self.ndi_audio_minus20,session=self.sid,output_mode=self.output_mode,output_kind=self.output_kind,output_source=self.sender.address if self.sender else '',native_timings=self.sender.timings.snapshot() if self.sender else {},
                    video_age_ms=(time.monotonic()-self.last_video)*1000 if self.last_video else 0,
                    audio_age_ms=(time.monotonic()-self.last_audio)*1000 if self.last_audio else 0)
        data['sdi_device']=self.sdi_device;data['sdi_interlaced']=self.sdi_interlaced
        data['remote_address']=getattr(self,'remote_address','') if self.pc else ''
        data['video_buffer']={'packets':VIDEO_JITTER_PACKETS,'overflows':RECEIVE_STATS['jitter_overflows'],
                              'largest_picture_kb':round(RECEIVE_STATS['largest_picture_bytes']/1024)}
        data['sdi']=self.sender.snapshot() if self.sender and self.output_kind=='sdi' else None
        data.update(self.health.update(data))
        vmx=getattr(self.sender,'vmx',None)
        data['output_encoder']={'backend':'VMX direct' if vmx and self.fps<=60 else 'OMT SDK' if self.output_kind=='omt' else 'NDI SDK',
                                'threads':vmx.threads if vmx and self.fps<=60 else None}
        if self.output_kind=='sdi':data['output_encoder']={'backend':'Blackmagic SDI scheduled output','threads':None}
        if MEDIA_PROCESS and self.sender:self._merge_media(data)
        # Loss counters start at 0 once the output has settled after a (re)connect.
        if self.settling.apply(data):self.health.reset()
        return data
    def _merge_media(self,data):
        # Video is decoded and output in the media process; take its counters.
        m=self.sender.state
        if not m:return
        if m.get('fps'):self.fps=Fraction(m['fps']).limit_denominator(1001)
        data.update(video_queue_drops=m['drops'],queue_drops=self.drops+m['drops'],video_frames=m['frames'],
                    video_format=m['video_format'],input_video_format=m['input_video_format'],
                    video_rate=m['fps'] if m['video_format'] else None,fps=m['output_fps'],
                    output_timings={**data['output_timings'],**m['output']})
        data['queue_max']=dict(data['queue_max'],video=m['queue_max'])
        data['media_packets']=m.get('packets')
        data['output_restarts']=getattr(self,'output_restarts',0)
        # Pictures discarded because the decoder stayed behind: warn for a few seconds.
        skipped=(m.get('packets') or {}).get('skipped',0)
        if skipped>getattr(self,'_skipped_seen',0):self._skipped_at=time.monotonic()
        self._skipped_seen=skipped
        if time.monotonic()-getattr(self,'_skipped_at',-60)<6:
            data['warning']=True;data['warning_text']=' · '.join(filter(None,(data.get('warning_text'),'Decoder cannot keep up · pictures skipped')))
        # Pictures arriving but none leaving (stuck decoder, dead media status) must not look green.
        if data.get('state')=='receiving':
            stale=time.monotonic()-getattr(self.sender,'state_at',0)>2
            silent=(m.get('video_age_s') or 0)>1
            if stale or silent:
                text='Media process status is not updating' if stale else 'Video arrives but no pictures are output'
                data['warning']=True;data['warning_text']=' · '.join(filter(None,(data.get('warning_text'),text)))
    def _forward_video(self,kind,data,pts):
        # aiortc decoder thread: stamp the compressed picture like a decoded one would be.
        clock=self.clocks.get('video');receiver=self._video_receiver
        if clock is None or receiver is None or self.sender is None:return
        timestamp=clock.timestamp(SimpleNamespace(pts=pts,time_base=Fraction(1,90000)),receiver)
        # SDI's hardware playout needs a common epoch: decode, but do not output before reports.
        ready=not(self.output_kind=='sdi' and not all(c.report for c in self.clocks.values()))
        self.sender.video(kind,data,pts,timestamp,ready)
        self.last_video=time.monotonic()
        if not self.started:self.started=self.last_video
    def _media_error(self,message):
        loop,pc=self.loop,self.pc
        if loop is None or (pc is None and not getattr(self.sender,'dead',False)):return
        def failed():
            if self.pc is not pc:return
            if pc is not None:self.detail=('SDI output: '+message) if self.output_kind=='sdi' else 'Output failed. Reconnect the sender.'
            async def release():
                async with self.lock:
                    if pc is not None and self.pc is pc:await self._close_session()
                    await self._revive_output()
            self.background(release())
        loop.call_soon_threadsafe(failed)
    async def _revive_output(self):
        """Start a new media process (decoder and output) after the old one ended by itself, so
        the sender's next connection has an output again. Called with self.lock held."""
        if not MEDIA_PROCESS or not self.enabled or not (self.sender is None or getattr(self.sender,'dead',False)):return
        log=logging.getLogger(__name__)
        old,self.sender=self.sender,None
        if old is not None:await asyncio.to_thread(old.close)
        try:
            self.sender=await asyncio.to_thread(self.make_sender,self.name)
            if self.output_kind=='ndi':self.sender.audio_minus20=self.ndi_audio_minus20
            self.output_restarts=getattr(self,'output_restarts',0)+1
            self.detail=''
            log.warning('Output process ended unexpectedly; a new one was started (%s)',self.output_restarts)
        except Exception as error:
            self.detail='Output could not be restarted: '+(str(error) or type(error).__name__)
            log.error(self.detail)
    def make_sender(self,name):
        if MEDIA_PROCESS:
            from .receive_media import MediaWorker
            return MediaWorker(self.output_kind,name,self.sdi_device,self._output_mode,self._media_error,self.sdi_interlaced)
        if self.output_kind=='sdi':
            from .sdi_output import SdiSender
            return SdiSender(self.sdi_device,self.sdi_interlaced)
        from .ndi_output import NdiSender
        return (NdiSender if self.output_kind=='ndi' else OmtSender)(name,self.sdi_interlaced)
    async def enable(self,key,name,listen_port=0,public_ip="",output_kind="omt",ndi_audio_minus20=False,endpoint_name="whip-input",sdi_device=None,sdi_interlaced=False):
        if type(ndi_audio_minus20) is not bool:raise ValueError("NDI audio attenuation must be true or false")
        if output_kind not in ("omt", "ndi", "sdi"):raise ValueError("Choose OMT, NDI or SDI output")
        if output_kind=='sdi' and (type(sdi_device) is not int or sdi_device<0):raise ValueError('Choose a Blackmagic SDI output connector')
        if type(sdi_interlaced) is not bool:raise ValueError('SDI interlaced must be true or false')
        network=validate_endpoint_settings(listen_port,public_ip,endpoint_name,legacy=True)
        public_ip=network['public_ip']
        if not isinstance(key,str) or not 1<=len(key)<=512 or any(c in key for c in '\r\n\x00'):raise ValueError('Enter a stream key (1-512 characters)')
        if not isinstance(name,str) or not 1<=len(name.encode())<=200 or any(c in name for c in '\r\n\x00'):raise ValueError('Enter an OMT source name (1-200 bytes)')
        async with self.lock:
            await self._stop()
            self.listen_port=listen_port;self.public_ip=public_ip;self.endpoint_name=endpoint_name
            try:
                if listen_port:self.udp_socket=reserve_udp(listen_port)
                self.output_kind=output_kind;self.sdi_device=sdi_device;self.sdi_interlaced=sdi_interlaced
                self.sender=await asyncio.to_thread(self.make_sender,name)
                self.output_workers=OutputWorkers()
                self.output_kind=output_kind
                self.ndi_audio_minus20=ndi_audio_minus20
                if output_kind=="ndi":self.sender.audio_minus20=ndi_audio_minus20
                self.name=name;self.key=key;self.enabled=True;self.detail=''
                if listen_port:
                    app=web.Application(client_max_size=65536)
                    app.router.add_post(f'/api/{self.endpoint_name}',self.offer)
                    app.router.add_get(f'/api/{self.endpoint_name}',lambda request:web.Response(status=204))
                    app.router.add_get(f'/api/{self.endpoint_name}/status',self.remote_status)
                    for method in ('GET','DELETE','PATCH'):app.router.add_route(method,f'/api/{self.endpoint_name}/session/{{sid}}',self.resource)
                    self.runner=web.AppRunner(app,access_log=None);await self.runner.setup()
                    await web.TCPSite(self.runner,'0.0.0.0',listen_port).start()
            except BaseException as error:
                await self._stop()
                if output_kind=='sdi' and isinstance(error,Exception):raise ValueError(str(error)) from None
                raise ValueError('Receiver could not start. Check TCP/UDP port availability and selected output runtime.')
    async def set_ndi_audio_minus20(self,value):
        if type(value) is not bool:raise ValueError("NDI audio attenuation must be true or false")
        async with self.lock:
            self.ndi_audio_minus20=value
            if self.sender and self.output_kind=="ndi":self.sender.audio_minus20=value

    async def stop(self):
        async with self.lock:await self._stop()
    async def _stop(self):
        self.enabled=False
        await self._close_session()
        if self.runner:await self.runner.cleanup();self.runner=None
        if self.udp_socket:self.udp_socket.close();self.udp_socket=None
        if self.sender:await asyncio.to_thread(self.sender.close);self.sender=None
        if self.output_workers:self.output_workers.close();self.output_workers=None
        self.key=''
    async def _close_session(self):
        global _video_sink
        _video_sink=None;self._video_receiver=None
        self.preview_frame=None
        self.health.reset()
        pc=self.pc;self.pc=None;self.sid=None;self.remote_address=''
        if pc:await pc.close()
        tasks=list(self.tasks)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if self.output_workers:await self.output_workers.drain()
        self.tasks.clear();self.clocks={}
        if self.sender and (self.output_kind=='sdi' or MEDIA_PROCESS):await asyncio.to_thread(self.sender.reset_stream)
        if self.enabled and self.listen_port and self.udp_socket is None:
            try:self.udp_socket=reserve_udp(self.listen_port)
            except OSError:self.enabled=False;self.detail='Receiver UDP port is unavailable'
    def authenticate(self,request):
        token=request.headers.get('Authorization','')
        self.throttle.check(request.remote)
        if not self.enabled or not hmac.compare_digest(token.encode(),('Bearer '+self.key).encode()):
            self.throttle.failed(request.remote)
            raise web.HTTPUnauthorized(headers={'WWW-Authenticate':'Bearer'})
        self.throttle.succeeded(request.remote)
    async def set_public_ip(self,public_ip):
        # Port and endpoint need a stop; the public IP only changes the advertised address,
        # which the next sender connection uses.
        self.public_ip=validate_endpoint_settings(self.listen_port or 56208,public_ip,self.endpoint_name)['public_ip']
    def background(self,coroutine):
        # asyncio keeps only a weak reference to tasks: hold it until done, and log a failure.
        # Own set: these tasks close the session, which cancels and awaits self.tasks (itself).
        task=asyncio.create_task(coroutine);self.cleanup_tasks.add(task)
        def done(task):
            self.cleanup_tasks.discard(task)
            if not task.cancelled() and task.exception():
                logging.getLogger(__name__).error('Receiver background task failed',exc_info=task.exception())
        task.add_done_callback(done)
        return task
    async def remote_status(self,request):
        # Only the already authenticated sender can read receiver telemetry.
        self.authenticate(request)
        data=self.status()
        if self.pc:
            data['network']=await receive_network(self.pc)
        return web.json_response(data,headers={'Cache-Control':'no-store'})

    def bind_peer(self,pc):
        RECEIVE_STATS['largest_picture_bytes']=0
        self.session_started=time.monotonic()
        self.frames={'video':0,'audio':0};self.drops=0;self.started=0;self.last_video=0;self.detail='';self.video_format=None;self.input_video_format=None;self.audio_format=None;self.last_audio=0;self.drop_counts={"video":0,"audio":0};self.queue_max={"video":0,"audio":0};self.timings=Timings();self.video_deltas=deque(maxlen=20);self.previous_video_time=None;self.fps=Fraction(50)
        diagnostic_task=asyncio.create_task(self.log_diagnostics(pc))
        self.tasks.add(diagnostic_task);diagnostic_task.add_done_callback(self.tasks.discard)
        @pc.on('track')
        def track_arrived(track):
            global _video_sink
            receiver=next(t.receiver for t in pc.getTransceivers() if t.receiver.track==track)
            clock=SourceClock(fixed=self.output_kind=='sdi');self.clocks[track.kind]=clock
            if track.kind=='video' and MEDIA_PROCESS:
                self._video_receiver=receiver;self.loop=asyncio.get_running_loop();_video_sink=self._forward_video
            original=receiver._handle_rtcp_packet
            async def report(packet):
                if isinstance(packet,RtcpSrPacket):clock.report_time(packet.sender_info.ntp_timestamp,packet.sender_info.rtp_timestamp)
                await original(packet)
            receiver._handle_rtcp_packet=report
            task=asyncio.create_task(self.consume(track,receiver,clock,pc));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
        @pc.on('connectionstatechange')
        async def state_changed():
            if pc.connectionState in ('failed','closed'):
                async def release():
                    async with self.lock:
                        if self.pc is pc:await self._close_session()
                self.background(release())

    def session_silent(self,now=None):
        """True when the current session has received neither picture nor sound for SILENT_SESSION_SECONDS
        (counted from its start while nothing has arrived yet)."""
        now=time.monotonic() if now is None else now
        return now-max(self.last_video,self.last_audio,getattr(self,'session_started',0))>SILENT_SESSION_SECONDS

    async def offer(self,request):
        self.authenticate(request)
        if request.content_type!='application/sdp':raise web.HTTPUnsupportedMediaType(text='Send application/sdp')
        sdp=await request.text()
        async with self.lock:
            self.authenticate(request)
            if self.pc:
                # A sender that reconnects after a network break has the key, but ICE consent keeps
                # the dead session for about 30 s. Replace a session that receives nothing; one
                # that still receives media stays, so a second sender cannot push it out.
                if not self.session_silent():raise web.HTTPConflict(text='A sender is already connected')
                logging.getLogger(__name__).warning('WHIP session %s receives no media; replaced by a new connection',self.sid)
                await self._close_session()
            # An output process that ended while nobody was connected is replaced now.
            await self._revive_output()
            if MEDIA_PROCESS and self.sender is None:raise web.HTTPServiceUnavailable(text='Receiver output is not available')
            pc=RTCPeerConnection(RTCConfiguration(iceServers=[],bundlePolicy=RTCBundlePolicy.MAX_BUNDLE));self.pc=pc;sid=uuid.uuid4().hex;self.sid=sid
            # Who sends: the address the offer came from (the Bridge's own port forwards it in a header).
            self.remote_address=(request.headers.get('X-Bridge-Remote') if request.remote in ('127.0.0.1','::1') else None) or request.remote or ''
            self.bind_peer(pc)
            try:
                await asyncio.wait_for(pc.setRemoteDescription(RTCSessionDescription(sdp,'offer')),10)
                for t in pc.getTransceivers():
                    t.direction='recvonly'
                    if t.kind=='video':
                        for codec in getattr(t.receiver,'_RTCRtpReceiver__codecs',{}).values():
                            if codec.mimeType.lower()=='video/h265' and str(codec.parameters.get('sprop-max-don-diff','0'))!='0':raise ValueError('HEVC DONL is not supported')
                if self.listen_port:
                    if 'a=group:BUNDLE' not in sdp:raise ValueError('BUNDLE required')
                    attach_fixed_ice(pc,self)
                if not any(t.kind=='video' for t in pc.getTransceivers()):raise ValueError('Video required')
                await asyncio.wait_for(pc.setLocalDescription(await pc.createAnswer()),10)
                for section in pc.localDescription.sdp.split('m=')[1:]:
                    if section.startswith(('video','audio')) and section.split()[1]=='0':raise ValueError('No supported media codec')
                video=next(t for t in pc.getTransceivers() if t.kind=='video')
                self.codec=next((c.mimeType.split('/')[-1].upper() for c in getattr(video.receiver,'_RTCRtpReceiver__codecs',{}).values() if not c.mimeType.lower().endswith('/rtx')),'')
            except asyncio.CancelledError:
                await self._close_session()
                raise
            except Exception as error:
                await self._close_session()
                logging.getLogger(__name__).warning('WHIP offer rejected: %s',error)
                raise web.HTTPBadRequest(text='Unsupported or invalid WHIP offer. Use H264, H265 or AV1 with Opus.')
            return web.Response(status=201,text=pc.localDescription.sdp,content_type='application/sdp',headers={'Location':f'/api/{self.endpoint_name}/session/{sid}','Cache-Control':'no-store','ETag':'"'+sid+'"','Accept-Patch':'application/trickle-ice-sdpfrag'})
    async def resource(self,request):
        self.authenticate(request)
        if request.match_info['sid']!=self.sid:raise web.HTTPNotFound()
        if request.method=='PATCH':
            if request.content_type!='application/trickle-ice-sdpfrag':raise web.HTTPUnsupportedMediaType()
            match=request.headers.get('If-Match')
            if match and match not in ('*','"'+self.sid+'"'):raise web.HTTPPreconditionFailed()
            fragment=await request.text()
            async with self.lock:
                if request.match_info['sid']!=self.sid:raise web.HTTPNotFound()
                pc=self.pc;remote=pc.remoteDescription.sdp;mid=None;index=-1;ended=False
                candidates=[]
                for line in fragment.splitlines():
                    if line.startswith(('a=ice-ufrag:','a=ice-pwd:')) and line not in remote.splitlines():
                        raise web.HTTPNotImplemented(text='ICE restart requires reconnecting the sender')
                    if line.startswith('m='):index+=1
                    elif line.startswith('a=mid:'):mid=line[6:]
                    elif line.startswith('a=candidate:'):
                        try:candidate=candidate_from_sdp(line[12:])
                        except (ValueError,AssertionError):raise web.HTTPBadRequest(text='Invalid ICE candidate')
                        if mid is None:raise web.HTTPBadRequest(text='ICE candidate requires a media ID')
                        if mid not in [t.mid for t in pc.getTransceivers()]:raise web.HTTPBadRequest(text='Unknown media ID')
                        candidate.sdpMid=mid;candidate.sdpMLineIndex=index;candidates.append(candidate)
                    elif line=='a=end-of-candidates':ended=True
                for candidate in candidates:await pc.addIceCandidate(candidate)
                if ended:await pc.addIceCandidate(None)
            return web.Response(status=204,headers={'ETag':'"'+self.sid+'"'})
        if request.method=='DELETE':
            async with self.lock:
                if request.match_info['sid']==self.sid:await self._close_session()
        return web.Response(status=204)
    async def log_diagnostics(self,pc):
        path=Path(__file__).resolve().parents[1]/'work'/'receiver-diagnostics.jsonl'
        path.parent.mkdir(exist_ok=True)
        handler=RotatingFileHandler(path,maxBytes=1000000,backupCount=2,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        try:
            while self.pc is pc:
                await asyncio.sleep(5)
                snapshot=self.status()
                network=await receive_network(pc)
                data={key:snapshot[key] for key in ('session','state','warning','warning_text','video_frames','audio_frames','video_queue_drops','audio_queue_drops','queue_max','fps','video_rate','video_format','audio_format','output_timings','omt_timings','native_timings','output_kind','output_encoder','decoder')}
                data.update(time=time.time(),network=network)
                if 'sdi' in snapshot:data['sdi']=snapshot['sdi']
                record=logging.LogRecord('receiver',logging.INFO,'',0,json.dumps(data),(),None)
                await asyncio.to_thread(handler.emit,record)
        finally:
            handler.close()

    @staticmethod
    def scale_output(frame):
        # Match input aspect ratio; changing resolution does not alter media time.
        if frame.width*720 == frame.height*1280:
            # Ordinary 16:9 does not need a full RGB image and black canvas.
            output=frame.reformat(width=1280,height=720,format='nv12',interpolation='BILINEAR')
            output.pts,output.time_base=frame.pts,frame.time_base
            return output
        import cv2
        image=frame.to_ndarray(format='bgr24')
        scale=min(1280/frame.width,720/frame.height)
        width,height=max(2,round(frame.width*scale)),max(2,round(frame.height*scale))
        small=cv2.resize(image,(width,height),interpolation=cv2.INTER_AREA)
        canvas=np.zeros((720,1280,3),np.uint8)
        x,y=(1280-width)//2,(720-height)//2;canvas[y:y+height,x:x+width]=small
        output=av.VideoFrame.from_ndarray(canvas,'bgr24')
        output.pts,output.time_base=frame.pts,frame.time_base
        return output

    def output_video(self,frame,timestamp,fps):
        # Resize and native output share one serial video worker, with no second
        # round-trip through the default thread pool and event loop.
        if self.output_mode=='720p' and (frame.width,frame.height)!=(1280,720):
            started=time.perf_counter()
            frame=self.scale_output(frame)
            self.timings.add('video_output_resize',started)
        self.sender.video(frame,timestamp,fps)
        return frame

    def observe_video_time(self,frame):
        # Estimate source cadence from every decoded frame, including frames
        # skipped to bound latency. Skipping must not slow the OMT format rate.
        seconds=frame.pts*frame.time_base
        declared=(frame.opaque or {}).get('bridge_source_fps') if isinstance(frame.opaque,dict) else None
        from .receive_media import cadence
        self.fps=cadence(self,seconds,declared)

    async def consume(self,track,receiver,clock,pc):
        try:
            while self.pc is pc:
                frame=await track.recv()
                if track.kind=='video':self.observe_video_time(frame)
                q=getattr(track,'_queue',None)
                self.queue_max[track.kind]=max(self.queue_max[track.kind],q.qsize() if q else 0)
                limit=2 if track.kind=='video' else 10
                while q and q.qsize()>limit:
                    newer=q.get_nowait()
                    if newer is None:return
                    frame=newer;self.drops+=1;self.drop_counts[track.kind]+=1
                    if track.kind=="video":self.observe_video_time(frame)
                timestamp=clock.timestamp(frame,receiver)
                # SDI's hardware playout needs a common audio/video epoch. Wait
                # for sender reports instead of scheduling mismatched startup clocks.
                if self.output_kind=='sdi' and not all(c.report for c in self.clocks.values()):continue
                if track.kind=='video':
                    self.input_video_format={'width':frame.width,'height':frame.height}
                    self.codec=next((c.mimeType.split('/')[-1].upper() for c in getattr(receiver,'_RTCRtpReceiver__codecs',{}).values() if not c.mimeType.lower().endswith('/rtx')),'')
                    started=time.perf_counter()
                    frame=await self.output_workers.call('video',self.output_video,self.timings,frame,timestamp,self.fps)
                    self.timings.add('video_output_including_thread_wait',started)
                    self.video_format={'width':frame.width,'height':frame.height}
                    self.last_video=time.monotonic()
                    # Wall time this picture leaves the output (SDI: plus scheduled lead).
                    lead=getattr(self.sender,'schedule_lead_ms',None) if self.output_kind=='sdi' else None
                    self.preview_frame=(self.last_video,frame,time.time()*1000+(lead or 0))
                    if not self.started:self.started=time.monotonic()
                else:
                    started=time.perf_counter()
                    await self.output_workers.call('audio',self.sender.audio,self.timings,frame,timestamp)
                    self.timings.add('audio_output_including_thread_wait',started)
                    self.audio_format={'sample_rate':frame.sample_rate,'channels':len(frame.layout.channels),'codec':'Opus'}
                    self.last_audio=time.monotonic()
                self.frames[track.kind]+=1
        except asyncio.CancelledError:raise
        except Exception as error:
            if self.pc is pc:
                logging.getLogger(__name__).exception('Bridge receive %s processing failed',track.kind)
                self.detail=('SDI output: '+str(error)) if self.output_kind=='sdi' else 'Incoming media stopped. Reconnect the sender.'
                async def release():
                    async with self.lock:
                        if self.pc is pc:await self._close_session()
                self.background(release())
