"""On-demand QSV HEVC feed for the existing bounded Go media bridge."""
import asyncio
import logging
import re
from fractions import Fraction
import av
from aiortc.mediastreams import MediaStreamError
from .engine import VideoTrack
from .isolated_encoder import IsolatedEncoder
from .publisher import bundle


def packetize(data, mtu=1200):
    """RFC 7798 single NAL / FU, single layer, no DONL or aggregation."""
    payloads=[]
    nals=re.split(b'\x00\x00\x00?\x01',data)
    if nals[0]:raise ValueError('Expected HEVC Annex B')
    for nal in nals[1:]:
        if len(nal)<2 or nal[0]&128 or not nal[1]&7:raise ValueError('Invalid HEVC NAL')
        if len(nal)<=mtu:payloads.append(nal);continue
        kind=(nal[0]>>1)&63
        header=bytes([(nal[0]&129)|(49<<1),nal[1]])
        for offset in range(2,len(nal),mtu-3):
            end=min(offset+mtu-3,len(nal))
            payloads.append(header+bytes([kind|(128 if offset==2 else 0)|(64 if end==len(nal) else 0)])+nal[offset:end])
    return payloads


def is_key(payload):
    if len(payload)<2:return False
    kind=(payload[0]>>1)&63
    if kind==49 and len(payload)>3:kind=payload[2]&63
    return 16<=kind<=23


class HevcWhip:
    def __init__(self,server):
        self.server=server
        self.task=None
        self.queue=asyncio.Queue(maxsize=5)
        self.bitrate=4000000
        self.preset='medium'
        self.force=True
        self.error=''
        self.drops=0

    async def start(self,bitrate,preset='medium'):
        if self.task and self.bitrate==bitrate and self.preset==preset:return
        await self.stop()
        self.bitrate=bitrate
        self.preset=preset
        self.task=asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
        self.task=None
        while not self.queue.empty():self.queue.get_nowait()
        self.error=''

    def request_keyframe(self):self.force=True

    async def recv(self):return await self.queue.get()

    async def run(self):
        while True:
            track=None
            context=None
            publisher=self.server.publisher
            if not self.server.engine.snapshot()['running']:
                await asyncio.sleep(.2);continue
            try:
                engine=self.server.engine
                context=IsolatedEncoder('h265',engine.fps,self.bitrate,self.preset,raw=True,backend=__import__('fastvideo.encoder',fromlist=['snapshot']).snapshot()['encoder_backend'])
                f=av.VideoFrame(engine.width,engine.height,'nv12')
                for plane in f.planes:plane.update(bytes(plane.buffer_size))
                f.pts,f.time_base=-round(90000/engine.fps),Fraction(1,90000)
                warm_packets=await publisher._encode(context.encode,f,True)
                parameters={}
                for raw,_,_ in warm_packets:
                    for nal in re.split(b'\x00\x00\x00?\x01',raw)[1:]:
                        if len(nal)>=2 and (nal[0]>>1)&63 in (32,33,34):parameters[(nal[0]>>1)&63]=nal
                track=VideoTrack(self.server.engine)
                self.force=True
                self.error=''
                while publisher is self.server.publisher:
                    frame=await track.recv()
                    if context.format and context.format!=(frame.width,frame.height,frame.opaque):
                        parameters.clear();self.force=True
                    force=self.force;self.force=False
                    for raw,_,pts in await publisher._encode(context.encode,frame,force):
                        nals=re.split(b'\x00\x00\x00?\x01',raw)[1:]
                        present=set()
                        for nal in nals:
                            kind=(nal[0]>>1)&63 if len(nal)>=2 else -1
                            if kind in (32,33,34):parameters[kind]=nal;present.add(kind)
                        if any(16<=((nal[0]>>1)&63)<=23 for nal in nals if len(nal)>=2):
                            raw=b''.join(b'\x00\x00\x00\x01'+parameters[k] for k in (32,33,34) if k in parameters and k not in present)+raw
                        payloads=packetize(raw)
                        key=any(is_key(p) for p in payloads)
                        if self.queue.full():
                            while not self.queue.empty():self.queue.get_nowait();self.drops+=1
                            self.force=True
                            if not key:continue
                        if self.force and not key:continue
                        packet=av.Packet(bundle(payloads));packet.pts=pts;packet.time_base=Fraction(1,90000)
                        self.queue.put_nowait(packet)
            except asyncio.CancelledError:raise
            except MediaStreamError:
                # The input was stopped or is being restarted: not an encoder fault. Wait for it.
                await asyncio.sleep(.2)
            except Exception:
                if not self.error:logging.getLogger(__name__).exception('H265 WHIP encoding stopped')
                self.error='H265 hardware encoder unavailable or input interrupted'
                await asyncio.sleep(1)
            finally:
                if track:track.stop()
                if context:await asyncio.to_thread(context.close)
                while not self.queue.empty():self.queue.get_nowait()
