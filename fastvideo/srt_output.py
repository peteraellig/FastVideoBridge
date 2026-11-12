"""Independent SRT caller: shared H264 packets, source PCM -> AAC, bounded mux worker."""
import asyncio
from fractions import Fraction
import json
import logging
import socket
import queue
import re
import struct
import threading
import time

import av
from .engine import VideoTrack
from .av1 import unpack as unpack_av1

log = logging.getLogger(__name__)
# Highest WHIP/SRT video bitrate offered; NVENC itself stops at 10 Mbit/s.
MAX_STREAM_BITRATE = 10_000_000


def validate(data):
    host = str(data.get('host', '')).strip()
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,253}', host):
        raise ValueError('Enter an IPv4 address or hostname')
    port, latency, aes = data.get('port'), data.get('latency'), data.get('aes', 128)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    if type(latency) is not int or not 20 <= latency <= 10000:
        raise ValueError('Latency must be between 20 and 10000 ms')
    if type(aes) is not int or aes not in (128, 192, 256):
        raise ValueError('AES must be 128, 192 or 256')
    streamid, password = str(data.get('streamid', '')), str(data.get('passphrase', ''))
    if len(streamid.encode()) > 512 or any(c in streamid for c in '\r\n\x00'):
        raise ValueError('Stream ID must be at most 512 bytes without line breaks')
    if password and (not 10 <= len(password.encode()) <= 79 or any(c in password for c in '\r\n\x00')):
        raise ValueError('Passphrase must contain 10 to 79 bytes')
    codec, bitrate = data.get('codec','h264'), data.get('bitrate',4000000)
    if codec not in ('h264','h265'):
        raise ValueError('Choose H264 or H265.')
    if type(bitrate) is not int or not 1000000 <= bitrate <= 12000000 or bitrate % 500000:
        raise ValueError('Bitrate must be 1–10 Mbit/s in 0.5 Mbit/s steps')
    # Upper limit is 10 Mbit/s; settings saved before 0.0.3.1 with up to 12 still load.
    bitrate = min(bitrate, MAX_STREAM_BITRATE)
    keyframe,cbr=data.get('keyframe',1),data.get('cbr',False)
    if type(keyframe) not in (int,float) or not .5<=keyframe<=10 or (keyframe*2)%1:
        raise ValueError('Keyframe interval must be 0.5–10 seconds in 0.5 second steps')
    if type(cbr) is not bool:raise ValueError('CBR must be enabled or disabled')
    audio_bitrate=data.get('audio_bitrate',256000)
    if type(audio_bitrate) is not int or audio_bitrate not in (128000,192000,256000,320000,384000,512000):raise ValueError('Choose a supported AAC stereo bitrate')
    preset=data.get('preset','medium')
    if preset not in ('medium','slow','veryslow'):raise ValueError('Choose a supported hardware quality preset')
    return dict(host=host, port=port, latency=latency, aes=aes, streamid=streamid, passphrase=password,codec=codec,bitrate=bitrate,keyframe=keyframe,cbr=cbr,preset=preset,audio_bitrate=audio_bitrate)


class SocketSink:
    def __init__(self, connection): self.connection = connection
    def write(self, data):
        self.connection.sendall(data)
        return len(data)
    def writable(self): return True
    def seekable(self): return False
    def flush(self): pass


def annexb(bundle):
    """Reassemble one complete frame from our shared RFC 6184 payload bundle."""
    if not bundle.startswith(b'FVPK'): raise ValueError('Invalid video bundle')
    output, pos, fragment, key = bytearray(), 4, None, False
    while pos < len(bundle):
        if pos + 2 > len(bundle): raise ValueError('Truncated payload length')
        length = struct.unpack_from('!H', bundle, pos)[0]; pos += 2
        payload = bundle[pos:pos + length]; pos += length
        if len(payload) != length or not payload: raise ValueError('Truncated video payload')
        kind = payload[0] & 31
        if 1 <= kind <= 23:
            output.extend(b'\x00\x00\x00\x01' + payload); key |= kind == 5
        elif kind == 24:
            offset = 1
            while offset < len(payload):
                if offset + 2 > len(payload): raise ValueError('Truncated STAP-A')
                size = struct.unpack_from('!H', payload, offset)[0]; offset += 2
                nal = payload[offset:offset + size]; offset += size
                if not nal or len(nal) != size: raise ValueError('Invalid STAP-A')
                output.extend(b'\x00\x00\x00\x01' + nal); key |= nal[0] & 31 == 5
        elif kind == 28 and len(payload) >= 2:
            if payload[1] & 128:
                fragment = bytearray([(payload[0] & 224) | (payload[1] & 31)])
            elif fragment is None: raise ValueError('Missing FU-A start')
            fragment.extend(payload[2:])
            if payload[1] & 64:
                key |= fragment[0] & 31 == 5
                output.extend(b'\x00\x00\x00\x01' + fragment); fragment = None
        else: raise ValueError('Unsupported H264 payload')
    if fragment is not None: raise ValueError('Incomplete FU-A')
    return bytes(output), key


def encoded_frame(bundle, codec):
    if codec=='h264': return annexb(bundle)
    if codec!='av1' or not bundle.startswith(b'FVPK'): raise ValueError('Invalid codec bundle')
    pos, payloads = 4, []
    while pos<len(bundle):
        if pos+2>len(bundle): raise ValueError('Truncated AV1 length')
        length=struct.unpack_from('!H',bundle,pos)[0];pos+=2
        payload=bundle[pos:pos+length];pos+=length
        if not payload or len(payload)!=length: raise ValueError('Truncated AV1 payload')
        payloads.append(payload)
    return unpack_av1(payloads),any(payload[0]&8 for payload in payloads)


def native_context(codec, bitrate, fps, width, height, keyframe=1, cbr=False, preset='medium',backend='qsv'):
    name='hevc' if codec=='h265' else codec
    c=av.CodecContext.create(name+'_qsv' if backend=='qsv' else name+'_nvenc' if backend=='nvenc' else 'libx265' if codec=='h265' else 'libx264','w')
    c.width,c.height,c.pix_fmt=width,height,'nv12'
    c.time_base,c.framerate=Fraction(1,90000),Fraction(fps)
    c.bit_rate,c.max_b_frames,c.gop_size=bitrate,0,round(fps*keyframe)
    c.options={'preset':preset,'async_depth':'1','low_delay_brc':'0'}
    if codec=='h264': c.options.update(profile='high',look_ahead='0',forced_idr='1')
    if codec=='av1': c.options['look_ahead']='0'
    if backend=='nvenc':c.options={'preset':{'medium':'p4','slow':'p5','veryslow':'p6'}[preset],'tune':'ull','rc':'cbr' if cbr else 'vbr','zerolatency':'1','rc-lookahead':'0','forced-idr':'1'}
    elif backend=='cpu':c.options={'preset':'veryfast','tune':'zerolatency'}
    if cbr:c.options.update(maxrate=str(bitrate),minrate=str(bitrate),bufsize=str(bitrate))
    return c


class SrtOutput:
    def __init__(self, server):
        self.server = server
        self.task = None
        self.state, self.detail = 'stopped', ''
        self.attempts = self.video_frames = self.audio_frames = self.drops = 0
        self.config = None
        self.connection = None
        self.warning_until=0;self.warning_text=""

    def snapshot(self):
        audio_bitrate=self.config.get('audio_bitrate',256000) if self.config else 256000
        result = dict(enabled=self.task is not None, state=self.state, detail=self.detail, warning=time.monotonic()<self.warning_until,warning_text=self.warning_text if time.monotonic()<self.warning_until else "",
                    attempts=self.attempts, video_frames=self.video_frames, audio_frames=self.audio_frames,
                    send_sample_time_ms=time.perf_counter()*1000,
                    queue_drops=self.drops, format=(self.config['codec'].upper() if self.config else 'H264')+f' / AAC stereo · {audio_bitrate//1000} kbit/s', audio_bitrate=audio_bitrate, transport='datarhei/gosrt · pure Go')
        if self.config:
            result.update({key:self.config[key] for key in ('host','port','latency','aes','codec','bitrate','keyframe','cbr','preset')})
            result['encrypted']=bool(self.config['passphrase'])
        return result

    async def start(self, config):
        await self.stop()
        self.config = config
        self.attempts = self.video_frames = self.audio_frames = self.drops = 0
        self.state, self.detail = 'connecting', ''
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            self.close_connection()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = self.config = None
        self.state, self.detail = 'stopped', ''

    def close_connection(self):
        if self.connection:
            try: self.connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.connection.close()

    async def _run(self):
        while True:
            if not self.server.engine.snapshot()['running']:
                self.state, self.detail = 'waiting', 'Waiting for input'
                await asyncio.sleep(.5); continue
            try:
                self.attempts += 1
                await self._session()
            except asyncio.CancelledError: raise
            except Exception:
                self.close_connection()
                self.connection = None
                # Native exceptions can include the destination/password. Never expose them.
                self.state, self.detail = 'retrying', 'SRT output interrupted. Check receiver, network and encryption settings.'
            await asyncio.sleep(3)

    async def _session(self):
        publisher = self.server.publisher
        signature=(self.server.engine.width,self.server.engine.height,self.server.engine.fps)
        codec_name,bitrate=self.config['codec'],self.config['bitrate']
        video=None
        from .isolated_encoder import IsolatedEncoder
        native=IsolatedEncoder(codec_name,self.server.engine.fps,bitrate,self.config['preset'],raw=True,keyframe=self.config['keyframe'],cbr=self.config['cbr'],backend=__import__('fastvideo.encoder',fromlist=['snapshot']).snapshot()['encoder_backend'])
        frame=av.VideoFrame(self.server.engine.width,self.server.engine.height,'nv12')
        for plane in frame.planes:plane.update(bytes(plane.buffer_size))
        frame.pts,frame.time_base=-round(90000/self.server.engine.fps),Fraction(1,90000)
        try:await publisher._encode(native.encode,frame,True)
        except BaseException:
            await asyncio.to_thread(native.close)
            raise
        events = queue.Queue(maxsize=32)
        overflow, finished = threading.Event(), threading.Event()
        result = []
        last_video = [time.monotonic()]
        self.state, self.detail = 'connecting', 'Opening SRT caller connection'
        def connect():
            connection = socket.create_connection(('127.0.0.1', self.server.args.bridge_port+1), timeout=5)
            self.connection = connection
            raw = json.dumps(self.config).encode()
            connection.sendall(struct.pack('!I', len(raw))+raw)
            if connection.recv(1) != b'\x01':
                connection.close()
                raise RuntimeError('SRT handshake failed')
            connection.settimeout(3)
            return connection
        connecting = asyncio.create_task(asyncio.to_thread(connect))
        try:
            connection = await asyncio.shield(connecting)
        except BaseException:
            self.close_connection()
            await asyncio.gather(connecting, return_exceptions=True)
            await asyncio.to_thread(native.close)
            raise
        self.state, self.detail = 'connected', 'SRT caller connected · pure Go'

        def enqueue(kind, data, pts):
            if kind == 'video': last_video[0] = time.monotonic()
            try: events.put_nowait((kind, data, pts))
            except queue.Full:
                self.drops += 1; self.warning_until=time.monotonic()+6;self.warning_text='SRT send queue overloaded';overflow.set()

        video_hook = lambda data, pts: enqueue('video', data, pts)
        audio_hook = lambda frame: enqueue('audio', frame, frame.pts)

        def mux():
            container = None
            try:
                container = av.open(SocketSink(connection), 'w', format='mpegts', options={'flush_packets':'1', 'mpegts_flags':'resend_headers', 'max_delay':'0'})
                vs = container.add_mux_stream('hevc' if codec_name=='h265' else codec_name, rate=self.server.engine.fps,
                    width=self.server.engine.width,height=self.server.engine.height,time_base=Fraction(1,90000))
                aus = container.add_stream('aac', rate=48000)
                aus.layout, aus.bit_rate = 'stereo', self.config['audio_bitrate']
                resampler = av.AudioResampler(format='fltp', layout='stereo', rate=48000)
                origin = None
                while not finished.is_set():
                    try: kind, data, pts = events.get(timeout=.2)
                    except queue.Empty: continue
                    if kind in ('video','raw'):
                        raw,key=encoded_frame(data,codec_name) if kind=='video' else data
                        if origin is None:
                            if not key: continue
                            origin = Fraction(pts, 90000)
                        packet = av.Packet(raw)
                        packet.pts = packet.dts = pts - round(origin * 90000)
                        packet.duration = round(90000 / self.server.engine.fps)
                        packet.time_base, packet.stream, packet.is_keyframe = Fraction(1, 90000), vs, key
                        container.mux(packet); self.video_frames += 1
                        self.state, self.detail = 'sending', 'Caller · '+codec_name.upper()+f' / AAC stereo · {self.config["audio_bitrate"]//1000} kbit/s'
                    elif origin is not None and Fraction(pts, 48000) >= origin:
                        # Copy metadata and samples; WHIP keeps ownership of its original frame.
                        frame = av.AudioFrame.from_ndarray(data.to_ndarray(), format='s16', layout='stereo')
                        frame.sample_rate, frame.time_base = 48000, Fraction(1, 48000)
                        frame.pts = pts - round(origin * 48000)
                        for converted in resampler.resample(frame):
                            for packet in aus.encode(converted): container.mux(packet)
                        self.audio_frames += 1
            except Exception:
                # The caller only sees "failed" and retries; keep the reason for the log.
                log.exception('SRT output stopped')
                result.append(False)
            finally:
                if container:
                    try: container.close()
                    except Exception: pass
                finished.set()

        worker = threading.Thread(target=mux, daemon=True, name='Bridge-SRT-mux')
        native_task=None
        async def produce():
            track=VideoTrack(self.server.engine)
            context=native
            first=True
            try:
                while True:
                    frame=await track.recv()
                    if (frame.width,frame.height,frame.opaque)!=signature:
                        return
                    packets=await publisher._encode(context.encode,frame,first)
                    first=False
                    for raw,key,pts in packets:
                        last_video[0]=time.monotonic()
                        enqueue('raw',(raw,key),pts)
            finally:track.stop()
        if video:
            video.video_taps.add(video_hook)
            video.request_keyframe()
        else:native_task=asyncio.create_task(produce())
        publisher.audio_taps.add(audio_hook)
        worker.start()
        try:
            while not finished.is_set() and not overflow.is_set():
                if publisher is not self.server.publisher or not publisher.tasks or not self.server.engine.snapshot()['running'] or time.monotonic()-last_video[0]>3: break
                if native_task and native_task.done(): native_task.result();break
                await asyncio.sleep(.1)
            raise RuntimeError('Session ended')
        finally:
            if video:video.video_taps.discard(video_hook)
            publisher.audio_taps.discard(audio_hook)
            if native_task:
                native_task.cancel()
                await asyncio.gather(native_task,return_exceptions=True)
            finished.set()
            self.close_connection()
            await asyncio.to_thread(worker.join, 3)
            await asyncio.to_thread(native.close)
            self.connection = None
