"""Common source timeline, bounded PCM placement and RTCP media-time mapping."""
from collections import deque
import math
import struct
import time

import numpy as np
from .media_clock import media_time
from aiortc.rtp import RtcpSrPacket
from aiortc.codecs.opus import OpusEncoder

RATE = 48000
BLOCK = 960
NTP_UNIX_OFFSET = 2208988800


class SourceStartup:
    """Confirm successive source timestamps before anchoring live A/V time."""
    def __init__(self, stable_seconds=.75):
        self.previous = None
        self.origin = None
        self.stable_seconds = stable_seconds

    def observe(self, source_time, mono=None):
        if not 0 <= source_time < (1 << 63)-1:
            raise ValueError('Source timecode missing; cannot synchronize audio')
        mono=media_time() if mono is None else mono
        previous=self.previous
        self.previous=(source_time,mono)
        if previous is None:
            self.origin=(source_time,mono)
            return False
        elapsed=(source_time-previous[0])/10_000_000
        arrival=mono-previous[1]
        # A cached first frame may precede the live source by a second or more.
        # Never synthesize timestamps or alter an established live timeline.
        total_source=(source_time-self.origin[0])/10_000_000
        total_arrival=mono-self.origin[1]
        if elapsed<=0 or abs(elapsed-arrival)>.25 or abs(total_source-total_arrival)>.1:
            self.origin=(source_time,mono)
            return False
        return total_arrival>=self.stable_seconds


class Timeline:
    def __init__(self, lead=.020):
        self.lead = lead
        self.source_origin = None
        self.mono = None
        self.wall = None
        self.phase = 0.0
        self.errors = deque(maxlen=125)

    def observe_video(self, source_time, mono=None, wall=None):
        mono = media_time() if mono is None else mono
        if not 0 <= source_time < (1 << 63)-1:
            raise ValueError("Quellzeitcode fehlt; synchroner Ton wird nicht geraten")
        if self.source_origin is None:
            self.source_origin = source_time
            self.mono = mono
            self.wall = time.time() if wall is None else wall
        else:
            elapsed = (source_time-self.source_origin)/10_000_000
            error = mono-self.mono-elapsed
            if abs(error-self.phase) > 1:
                raise ValueError(f"Source clock discontinuity: source advanced {elapsed:.3f} s, arrival advanced {mono-self.mono:.3f} s; reconnect input")
            self.errors.append(error)
            # Follow slow source-clock drift, not individual late packets.
            target = sorted(self.errors)[len(self.errors)//10]
            self.phase += max(-.00005, min(.00005, (target-self.phase)*.01))

    def ticks(self, source_time, rate):
        if self.source_origin is None:
            return None
        return round((source_time-self.source_origin)*rate/10_000_000)

    def deadline(self, ticks, rate):
        return self.mono+self.phase+self.lead+ticks/rate

    def ntp(self, ticks, rate):
        value = self.wall+self.phase+self.lead+ticks/rate+NTP_UNIX_OFFSET
        whole = math.floor(value)
        return (whole << 32) | round((value-whole)*(1 << 32))


class AudioBuffer:
    """Place samples by source time; never let a viewer backlog grow forever."""
    def __init__(self, capacity=RATE):
        self.chunks = deque()
        self.capacity = capacity
        self.last_end = None
        self.discontinuities = 0
        self.dropped = 0
        self.missing = 0
        self.last_read = None
        self.last_read_missing = 0
        self.last_read_buffer_end = None

    def append(self, start, samples):
        # Sender timestamp rounding / packet boundaries need not create gaps.
        if self.last_end is not None and abs(start-self.last_end) <= 2:
            start = self.last_end
        elif self.last_end is not None and abs(start-self.last_end) > RATE//100:
            self.discontinuities += 1
        self.last_end = start+samples.shape[1]
        self.chunks.append((start, samples))
        while self.chunks and self.last_end-self.chunks[0][0] > self.capacity:
            self.chunks.popleft(); self.dropped += 1

    def read(self, start, count=BLOCK):
        out = np.zeros((2,count), np.float32)
        coverage = np.zeros(count, bool)
        while self.chunks and self.chunks[0][0]+self.chunks[0][1].shape[1] <= start:
            self.chunks.popleft()
        for position, data in self.chunks:
            lo, hi = max(start,position), min(start+count,position+data.shape[1])
            if hi > lo:
                out[:,lo-start:hi-start] = data[:,lo-position:hi-position]
                coverage[lo-start:hi-start] = True
            if position >= start+count:
                break
        self.last_read = start
        self.last_read_missing = count-int(coverage.sum())
        self.last_read_buffer_end = self.last_end
        self.missing += self.last_read_missing
        return out

    def contains(self,start,count=BLOCK):
        cursor=start
        for position,data in self.chunks:
            end=position+data.shape[1]
            if end<=cursor:continue
            if position>cursor:return False
            cursor=max(cursor,end)
            if cursor>=start+count:return True
        return False


class TimedOpusEncoder(OpusEncoder):
    """Preserve common audio PTS instead of resetting it at viewer connection."""
    def __init__(self,bitrate=256000):
        super().__init__()
        self.codec.bit_rate = bitrate
        self.codec.options = {"application": "audio", "frame_duration": "20"}

    def encode(self, frame, force_keyframe=False):
        packets = []
        for resampled in self.resampler.resample(frame):
            packets.extend(self.codec.encode(resampled))
        # Packet PTS includes Opus's lookahead/pre-roll. Keep that timeline too.
        return ([bytes(p) for p in packets], packets[0].pts) if packets else ([], None)


def sync_sender(sender, timeline):
    """Pinned-aiortc adapter: SR must pair RTP with media time, not send time.

    Encoding audio/video takes different amounts of time. The transport hook
    records the actual outgoing packet's RTP timestamp and common source PTS;
    reports use that exact pair even while the next picture is being encoded.
    aiortc already assigns the same CNAME to both senders in a peer connection.
    """
    if getattr(sender, "_fastvideo_sync", False):
        return
    sender._fastvideo_sync = True
    sender._fv_ticks = None
    sender._fv_pair = None
    sender._fv_rate = RATE if sender.kind == "audio" else 90000
    encode = sender._next_encoded_frame

    async def next_encoded(codec):
        frame = await encode(codec)
        if frame is not None:
            sender._fv_ticks = frame.timestamp
        return frame
    sender._next_encoded_frame = next_encoded

    transport = sender.transport
    if not hasattr(transport, "_fv_senders"):
        transport._fv_senders = {}
        send = transport._send_rtp

        async def send_rtp(data):
            pair = None; owner = None
            if len(data) >= 12 and data[0] >> 6 == 2 and not 192 <= data[1] <= 223:
                ssrc = struct.unpack_from("!I",data,8)[0]
                owner = transport._fv_senders.get(ssrc)
                if owner is not None and owner._fv_ticks is not None:
                    pair = (struct.unpack_from("!I",data,4)[0], timeline.ntp(owner._fv_ticks,owner._fv_rate))
            await send(data)
            if pair is not None:
                owner._fv_pair = pair
        transport._send_rtp = send_rtp
    transport._fv_senders[sender._ssrc] = sender
    send_rtcp = sender._send_rtcp

    async def reports(packets):
        for packet in packets:
            if isinstance(packet,RtcpSrPacket) and sender._fv_pair is not None:
                packet.sender_info.rtp_timestamp,packet.sender_info.ntp_timestamp = sender._fv_pair
                sender._RTCRtpSender__lsr = (packet.sender_info.ntp_timestamp >> 16) & 0xffffffff
        await send_rtcp(packets)
    sender._send_rtcp = reports
