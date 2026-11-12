"""One AV encoder pair, bounded packet subscriptions, individual WebRTC SRTP."""
import asyncio
from collections import deque
from fractions import Fraction
import logging
import struct
import time

import av
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError
from aiortc.codecs.base import Encoder
from fastvideo.engine import VideoTrack, AudioTrack
from fastvideo.sync import TimedOpusEncoder
from fastvideo import encoder

log=logging.getLogger(__name__)


def bundle(payloads):
    return b'FVPK'+b''.join(struct.pack('!H',len(p))+p for p in payloads)


class PacketEncoder(Encoder):
    def __init__(self,kind):self.kind=kind;self.target_bitrate=2_000_000
    def encode(self,frame,force_keyframe=False):
        raise RuntimeError('Shared publisher must send encoded packets, not raw frames')
    def pack(self,packet):
        data=bytes(packet)
        if self.kind=='video':
            if not data.startswith(b'FVPK'):raise ValueError('Invalid shared video packet')
            payloads=[];offset=4
            while offset<len(data):
                size=struct.unpack_from('!H',data,offset)[0];offset+=2
                if offset+size>len(data):raise ValueError('Truncated shared payload')
                payloads.append(data[offset:offset+size]);offset+=size
        else:payloads=[data]
        return payloads,int(packet.pts*packet.time_base*(48000 if self.kind=='audio' else 90000))


class PacketTrack(MediaStreamTrack):
    def __init__(self,kind,subscription):
        super().__init__();self.kind=kind;self.subscription=subscription
        self.queue=asyncio.Queue(maxsize=5 if kind=='video' else 10)
    async def recv(self):
        if self.readyState!='live':raise MediaStreamError
        data=await self.queue.get()
        if data is None:raise MediaStreamError
        payload,pts,rate=data
        packet=av.Packet(payload);packet.pts=pts;packet.time_base=Fraction(1,rate)
        return packet
    def stop(self):
        if self.readyState=='live':
            super().stop()
            while not self.queue.empty():self.queue.get_nowait()
            self.queue.put_nowait(None)


class Subscription:
    def __init__(self,publisher,audio):
        self.publisher=publisher;self.video=PacketTrack('video',self)
        self.audio=PacketTrack('audio',self) if audio else None
        self.needs_keyframe=True;self.closed=False
    def stop(self):
        if self.closed:return
        self.closed=True;self.publisher.subscribers.discard(self)
        self.video.stop()
        if self.audio:self.audio.stop()


class Publisher:
    def __init__(self,engine,video_factory=None,fallback=False,fallback_bitrate=4_000_000,fallback_preset="medium"):
        self.video_factory=video_factory
        self.fallback=None
        self.engine=engine;self.subscribers=set();self.tasks=[];self.error=None
        self.keyframe_requested=True;self.last_forced=0
        self.video_encodes=0;self.audio_encodes=0;self.video_drops=0;self.audio_drops=0
        self.encoder_resets=0
        self.encode_times=deque(maxlen=100)
        self.video_encoder=None;self.audio_encoder=None
        self.video_taps=set();self.audio_taps=set()
        self.video_encoder_lock=asyncio.Lock()
        self.audio_encoder_lock=asyncio.Lock()
        if fallback:
            from .isolated_encoder import IsolatedEncoder
            self.fallback=Publisher(engine,video_factory=lambda:IsolatedEncoder("h264",engine.fps,fallback_bitrate,preset=fallback_preset,profile="baseline"))

    @property
    def error(self):
        return self._error or (self.fallback.error if self.fallback else None)

    @error.setter
    def error(self,value):self._error=value

    def subscribe_fallback(self):
        if self.fallback is None:raise RuntimeError("H264 fallback is not enabled")
        return self.fallback.subscribe(audio=False)

    async def prepare(self):
        # QSV device initialization can hold Python's GIL for over one second.
        # Warm each retained encoder before capture establishes its live clock.
        def warm():
            if self.video_encoder is None:
                self.video_encoder=self.video_factory() if self.video_factory else encoder.make_video_encoder()
                frame=av.VideoFrame(self.engine.width,self.engine.height,'nv12')
                for plane in frame.planes:plane.update(bytes(plane.buffer_size))
                frame.pts=-round(90000/self.engine.fps);frame.time_base=Fraction(1,90000)
                # Warm the encoder at the probed source rate, so the first live picture reuses it.
                frame.opaque=Fraction(self.engine.fps)
                self.video_encoder.encode(frame,True)
        await asyncio.to_thread(warm)
        if self.fallback:await self.fallback.prepare()

    def subscribe(self,audio=True):
        if self.error:raise RuntimeError(self.error)
        subscription=Subscription(self,audio);self.subscribers.add(subscription)
        self.request_keyframe()
        if not self.tasks:
            if self.video_encoder is None:
                self.video_encoder=self.video_factory() if self.video_factory else encoder.make_video_encoder()
            self.tasks=[asyncio.create_task(self._video())]
        if audio and self.audio_encoder is None:
            self.audio_encoder=TimedOpusEncoder(getattr(self.engine,"whip_audio_bitrate",256000))
            self.tasks.append(asyncio.create_task(self._audio()))
        return subscription

    async def set_audio_bitrate(self,bitrate):
        async with self.audio_encoder_lock:
            self.engine.whip_audio_bitrate=bitrate
            if self.audio_encoder and self.audio_encoder.codec.bit_rate!=bitrate:
                self.audio_encoder=TimedOpusEncoder(bitrate)

    def request_keyframe(self):
        self.keyframe_requested=True
        if self.fallback:self.fallback.request_keyframe()

    async def stop(self):
        if self.fallback:await self.fallback.stop()
        for subscription in list(self.subscribers):subscription.stop()
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tasks=[]
        if hasattr(self.video_encoder,"close"):await asyncio.to_thread(self.video_encoder.close)
        self.video_encoder=None;self.audio_encoder=None

    async def _encode(self, encode, *args):
        # Cancelling to_thread does not stop native encoder work. Finish that
        # work before stop() releases the encoder or a replacement is started.
        worker=asyncio.create_task(asyncio.to_thread(encode,*args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await worker
            except Exception:
                log.exception('Encoder failed while stopping')
            raise

    async def _video(self):
        track=VideoTrack(self.engine)
        prepared=None
        try:
            while True:
                if not self.subscribers:
                    await asyncio.sleep(.05);continue
                if prepared is None:
                    frame=await track.recv()
                else:
                    frame=await prepared
                    prepared=None
                # Prepare at most ONE next picture while this one is encoded.
                # Source timing still controls recv(); no FIFO, busy polling or
                # additional scheduling delay is introduced. This overlaps the
                # colour/copy worker with hardware encoding/IPC of this frame.
                prepared=asyncio.create_task(track.recv(),name='bridge-next-video-frame')
                force=self.keyframe_requested and time.monotonic()-self.last_forced>=.5
                if force:self.keyframe_requested=False;self.last_forced=time.monotonic()
                start=time.perf_counter()
                async with self.video_encoder_lock:
                    try:
                        payloads,pts=await self._encode(self.video_encoder.encode,frame,force)
                    except (RuntimeError,OSError) as enc_exc:
                        log.warning('Encoder-Subprocess abgestürzt, wird neu gestartet: %s',enc_exc)
                        if hasattr(self.video_encoder,'close'):
                            await asyncio.to_thread(self.video_encoder.close)
                        else:
                            self.video_encoder.codec=None
                        self.encoder_resets+=1
                        if force:self.keyframe_requested=True
                        continue
                self.encode_times.append((time.perf_counter()-start)*1000)
                if not payloads:
                    if force:self.keyframe_requested=True
                    continue
                self.video_encodes+=1
                data=bundle(payloads)
                for tap in list(self.video_taps):tap(data,pts)
                for subscription in list(self.subscribers):
                    queue=subscription.video.queue
                    if queue.full():
                        while not queue.empty():queue.get_nowait();self.video_drops+=1
                        subscription.needs_keyframe=True
                        if not force:self.request_keyframe()
                    if subscription.needs_keyframe:
                        if not force:continue
                        subscription.needs_keyframe=False
                    queue.put_nowait((data,pts,90000))
        except asyncio.CancelledError:pass
        except Exception as exc:self._failed(exc)
        finally:
            track.stop()
            if prepared is not None:
                prepared.cancel()
                await asyncio.gather(prepared,return_exceptions=True)

    async def _audio(self):
        track=AudioTrack(self.engine)
        try:
            while True:
                if not any(s.audio for s in self.subscribers):
                    await asyncio.sleep(.02);continue
                frame=await track.recv()
                for tap in list(self.audio_taps):tap(frame)
                async with self.audio_encoder_lock:
                    payloads,pts=await self._encode(self.audio_encoder.encode,frame)
                if not payloads:continue
                self.audio_encodes+=1
                for payload in payloads:
                    for subscription in list(self.subscribers):
                        if subscription.audio:
                            queue=subscription.audio.queue
                            if queue.full():queue.get_nowait();self.audio_drops+=1
                            queue.put_nowait((payload,pts,48000))
        except asyncio.CancelledError:pass
        except Exception as exc:self._failed(exc)
        finally:track.stop()

    def _failed(self,exc):
        self.error=str(exc);log.exception('Shared encoder stopped')
        for subscription in list(self.subscribers):subscription.stop()

    def snapshot(self):
        ordered=sorted(self.encode_times)
        budget=1000/float(self.engine.fps) if self.engine is not None else 20
        return {'h264_fallback':self.fallback.snapshot() if self.fallback else None,
                'target_bitrate_bps':getattr(self.video_encoder,"target_bitrate",None),
                'keyframe_interval_frames':getattr(getattr(self.video_encoder,'codec',None),'gop_size',None),
                'shared_encoding':True,'video_encode_count':self.video_encodes,'audio_encode_count':self.audio_encodes,'encoder_resets':self.encoder_resets,
                'encode_ms_mean':round(sum(self.encode_times)/len(self.encode_times),2) if self.encode_times else None,
                'encode_ms_p95':round(ordered[min(len(ordered)-1,int(len(ordered)*.95))],2) if ordered else None,
                'encode_budget_ms':budget,'encode_over_budget_recent':sum(value>budget for value in ordered),
                'encode_sample_count':len(ordered),
                'viewer_video_queue_drops':self.video_drops,'viewer_audio_queue_drops':self.audio_drops,
                'encoder_stage_timings':self.video_encoder.timings.snapshot() if hasattr(self.video_encoder,'timings') else {},
                'publisher_error':self.error}
