"""Bounded, loopback-only encoded media bridge to the Go gateway.

FV01 header: magic, kind(0 primary video/1 shared audio/2 H264/3 H265), keyframe flag, reserved,
signed media ticks, common NTP media time, payload length; network byte order.
Video payload is the publisher's FVPK bundle; audio payload is one Opus packet.
No image decoding, per-viewer encoding, or timing inferred from IPC arrival.
"""
import asyncio
import struct
from fastvideo import encoder

HEADER = struct.Struct('!4sBBHqQI')


class GatewayBridge:
    def __init__(self, publisher, timeline, port=9999, hevc=None):
        self.publisher, self.timeline, self.port = publisher, timeline, port
        self.hevc=hevc
        self.task = None
        self.connected = False
        self.error = None
        self.frames = 0

    def start(self):
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _run(self):
        while True:
            writer = None
            subscription = None
            fallback_subscription = None
            workers = []
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', self.port), 3)
                subscription = self.publisher.subscribe(True)
                if self.publisher.fallback:
                    fallback_subscription = self.publisher.subscribe_fallback()
                self.connected = True
                self.error = None
                lock = asyncio.Lock()
                video_codec = encoder.snapshot()["video_codec"]

                async def send(track, kind, rate):
                    while True:
                        packet = await track.recv()
                        pts = int(packet.pts * packet.time_base * rate)
                        data = bytes(packet)
                        # H264 IDR (type 5) may be fragmented as FU-A (type 28).
                        keyframe = False
                        if kind != 1:
                            packet_codec="h265" if kind==3 else "h264" if kind==2 else video_codec
                            pos = 4
                            while pos < len(data):
                                length = struct.unpack_from('!H', data, pos)[0]
                                pos += 2
                                payload = data[pos:pos+length]
                                if packet_codec=="av1":
                                    keyframe = keyframe or bool(payload and payload[0]&8)
                                elif packet_codec=="h265":
                                    from .hevc_whip import is_key
                                    keyframe=keyframe or is_key(payload)
                                elif payload and ((payload[0] & 31) == 5 or
                                    ((payload[0] & 31) == 28 and len(payload)>1 and (payload[1]&31)==5)):
                                    keyframe = True
                                # STAP-A can contain parameter sets and an IDR.
                                if packet_codec=="h264" and payload and (payload[0]&31)==24:
                                    offset=1
                                    while offset+2<=len(payload):
                                        size=struct.unpack_from('!H',payload,offset)[0];offset+=2
                                        if size>0 and offset+size<=len(payload) and (payload[offset]&31)==5:keyframe=True
                                        offset+=size
                                pos += length
                        header = HEADER.pack(b'FV01',kind,int(keyframe),0,pts,self.timeline.ntp(pts,rate),len(data))
                        async with lock:
                            writer.write(header + data)
                            await asyncio.wait_for(writer.drain(), 1)
                        self.frames += 1

                workers = [asyncio.create_task(send(subscription.video,0,90000)),
                           asyncio.create_task(send(subscription.audio,1,48000))]
                if fallback_subscription:
                    workers.append(asyncio.create_task(send(fallback_subscription.video,2,90000)))
                if self.hevc:
                    workers.append(asyncio.create_task(send(self.hevc,3,90000)))
                done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.connected = False
                for task in workers:task.cancel()
                await asyncio.gather(*workers,return_exceptions=True)
                if subscription:subscription.stop()
                if fallback_subscription:fallback_subscription.stop()
                if writer:
                    writer.close()
                    try:await asyncio.wait_for(writer.wait_closed(),1)
                    except (Exception,asyncio.CancelledError):pass
            await asyncio.sleep(1)

    def snapshot(self):
        return {'gateway_bridge_connected':self.connected,'gateway_bridge_error':self.error,
                'gateway_bridge_frames':self.frames}
