"""Single-layer AV1 RTP sender for pinned aiortc 1.15 (AOM AV1 RTP spec).

Packets aggregate prefix OBUs with the first frame fragment (W=0). Temporal delimiters
are omitted, size fields removed. No scalable coding / dependency extension.
"""
from collections import deque
from fractions import Fraction
import time
import av
from .diagnostics import Timings
from aiortc.codecs.base import Encoder
from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtcpFeedback
from aiortc.codecs import CODECS
from aiortc import rtp


def leb(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_leb(data, offset):
    result = 0
    for shift in range(0, 56, 7):
        if offset >= len(data):
            raise ValueError("Truncated AV1 OBU length")
        b = data[offset]; offset += 1
        result |= (b & 127) << shift
        if not b & 128:
            return result, offset
    raise ValueError("Invalid AV1 OBU length")


def obus(data):
    offset = 0
    while offset < len(data):
        start = offset
        header = data[offset]; offset += 1
        if header & 0x81 or not header & 2:
            raise ValueError("Expected sized low-overhead AV1 OBU")
        if header & 4:
            offset += 1
        extension = data[start + 1:offset]
        size, offset = read_leb(data, offset)
        end = offset + size
        if end > len(data):
            raise ValueError("Truncated AV1 OBU")
        kind = (header >> 3) & 15
        if kind not in (2, 8, 15):
            yield kind, bytes([header & ~2]) + extension + data[offset:end]
        offset = end


def packetize(data, mtu=1200):
    packets = []; payload = bytearray(); start_fragment = False; end_fragment = False; new_sequence = False
    for kind, obu in obus(data):
        offset = 0
        while offset < len(obu):
            room = mtu - 1 - len(payload) - 3
            if room <= 0:
                packets.append(bytes([(0x80 if start_fragment else 0) | (0x40 if end_fragment else 0) | (8 if new_sequence else 0)]) + payload)
                payload = bytearray(); start_fragment = offset > 0; new_sequence = False
                room = mtu - 4
            size = min(room, len(obu)-offset)
            if kind == 1 and not packets:
                new_sequence = True
            payload.extend(leb(size) + obu[offset:offset+size])
            offset += size; end_fragment = offset < len(obu)
            if end_fragment:
                packets.append(bytes([(0x80 if start_fragment else 0) | 0x40 | (8 if new_sequence else 0)]) + payload)
                payload = bytearray(); start_fragment = True; new_sequence = False
    if payload:
        packets.append(bytes([(0x80 if start_fragment else 0) | (8 if new_sequence else 0)]) + payload)
    return packets


def unpack(packets):
    """Test helper: reconstruct sized OBUs, rejecting missing fragments."""
    result = bytearray(); pending = bytearray()
    for packet in packets:
        h = packet[0]
        if h & 0x30 or (h & 0x80 != 0) != bool(pending):
            raise ValueError("Invalid / missing AV1 fragment")
        offset = 1
        while offset < len(packet):
            size, offset = read_leb(packet, offset)
            if offset + size > len(packet):
                raise ValueError("Truncated RTP OBU")
            pending.extend(packet[offset:offset+size]); offset += size
            if offset == len(packet) and h & 0x40:
                break
            n = 2 if pending[0] & 4 else 1
            result.extend(bytes([pending[0] | 2]) + pending[1:n] + leb(len(pending)-n) + pending[n:])
            pending.clear()
    if pending:
        raise ValueError("Incomplete AV1 fragment")
    return bytes(result)


def context(width, height, fps, bitrate, preset=8, parallelism=6):
    c = av.CodecContext.create("libsvtav1", "w")
    c.width, c.height = width, height
    c.pix_fmt = "yuv420p"
    c.framerate, c.time_base = Fraction(fps), Fraction(1, 90000)
    c.bit_rate, c.gop_size = bitrate, round(fps * 2)
    c.options = {"preset": str(preset), "svtav1-params": f"pred-struct=1:rtc=1:lookahead=0:lp={parallelism}:rc=2:tune=1"}
    return c


def qsv_context(width,height,fps,bitrate,preset="medium"):
    c=av.CodecContext.create('av1_qsv','w')
    c.width,c.height=width,height
    c.pix_fmt='nv12'
    c.framerate,c.time_base=Fraction(fps),Fraction(1,90000)
    c.bit_rate,c.gop_size=bitrate,round(fps*2)
    c.max_b_frames=0
    c.options={'preset':preset,'async_depth':'1','look_ahead':'0','low_delay_brc':'1'}
    return c


def check_qsv(fps=50,preset="medium"):
    encoder=Av1Encoder(fps,2_000_000,backend='qsv',qsv_preset=preset)
    for i in range(3):
        frame=av.VideoFrame(1280,720,'nv12')
        for plane in frame.planes:plane.update(bytes(plane.buffer_size))
        frame.pts,frame.time_base=i*90000//fps,Fraction(1,90000)
        encoder.encode(frame,force_keyframe=i==0)


class Av1Encoder(Encoder):
    def __init__(self, fps=25, bitrate=2_000_000,preset=8,parallelism=6,backend="svt-av1",qsv_preset="medium"):
        self.backend=backend
        self.qsv_preset=qsv_preset
        self.fps, self.bitrate = fps, bitrate
        self.preset,self.parallelism = preset,parallelism
        self.codec = None
        self.encode_times = deque(maxlen=100)
        self.timings = Timings()

    @property
    def target_bitrate(self):
        return self.bitrate

    @target_bitrate.setter
    def target_bitrate(self, value):
        # Fixed bitrate for the LAN comparison. REMB must not reopen SVT every
        # second. Congestion adaptation is required before Internet rollout.
        pass

    def encode(self, frame, force_keyframe=False):
        if self.codec is None:
            self.codec = (qsv_context(frame.width,frame.height,self.fps,self.bitrate,self.qsv_preset) if self.backend=="qsv" else context(frame.width, frame.height, self.fps, self.bitrate,self.preset,self.parallelism))
        frame.pict_type = av.video.frame.PictureType.I if force_keyframe else av.video.frame.PictureType.NONE
        start = time.perf_counter()
        output = self.codec.encode(frame)
        self.encode_times.append((time.perf_counter()-start)*1000)
        self.timings.add("codec_convert_upload_encode", start)
        if len(output) != 1:
            raise RuntimeError("AV1 encoder must emit exactly one picture per input in low-delay mode")
        start = time.perf_counter()
        payloads = packetize(bytes(output[0]))
        self.timings.add("av1_packetize", start)
        return payloads, int(frame.pts * frame.time_base * 90000)

    def pack(self, packet):
        return packetize(bytes(packet)), int(packet.pts * packet.time_base * 90000)

    def __del__(self):
        if self.codec is not None:
            try:
                self.codec.encode(None)
            except Exception:
                pass


def register(fps=25):
    # Chromium offers AV1 on PT 45 (RFC 7941 expanded dynamic range).
    # aiortc 1.15 otherwise retains its local PT and breaks browser reception.
    rtp.DYNAMIC_PAYLOAD_TYPES = set(rtp.DYNAMIC_PAYLOAD_TYPES) | set(range(35, 64))
    if not any(c.mimeType.lower() == "video/av1" for c in CODECS["video"]):
        CODECS["video"].extend([
            RTCRtpCodecParameters(mimeType="video/AV1", clockRate=90000, payloadType=103,
                parameters={"profile": "0", "level-idx": "8" if fps > 30 else "5", "tier": "0"},
                rtcpFeedback=[RTCRtcpFeedback(type="nack"), RTCRtcpFeedback(type="nack", parameter="pli"), RTCRtcpFeedback(type="goog-remb")]),
            RTCRtpCodecParameters(mimeType="video/rtx", clockRate=90000, payloadType=104, parameters={"apt": 103})])
    # Level 3.1 allows 31,950,720 luma samples/s; 720p50 needs level 4.0.
    for codec in CODECS["video"]:
        if codec.mimeType.lower() == "video/av1":
            codec.parameters["level-idx"] = "8" if fps > 30 else "5"
