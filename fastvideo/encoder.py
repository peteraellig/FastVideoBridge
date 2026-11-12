"""Process-local encoder selection for the pinned aiortc 1.15.0 adapter.

Shared publishers encode once; per-viewer adapters only forward encoded packets.
"""
from collections import deque
from fractions import Fraction
import logging
import time

import av
from aiortc.codecs import get_encoder as default_get_encoder
from aiortc.codecs.h264 import H264Encoder
from fastvideo.av1 import Av1Encoder, register as register_av1, check_qsv
from fastvideo.sync import TimedOpusEncoder
from fastvideo.diagnostics import Timings
import aiortc.rtcrtpsender

log = logging.getLogger(__name__)
_backend = "cpu"
_fps = 25
_reason = None
_active = None
_codec = "h264"
_bitrate = 4_000_000
_packet_mode = False
_h264_profile = "baseline"
_nvenc_preset = "p7"
_av1_preset = 8
_av1_lp = 6
_qsv_preset = "medium"


def make_nvenc_encoder(bitrate=None):
    result = NvencEncoder(_fps,_h264_profile,_nvenc_preset)
    result.target_bitrate = bitrate or _bitrate
    result.timings = Timings()
    return result


def make_qsv_encoder(bitrate=None, preset=None):
    from .h264_qsv import QsvH264Encoder
    return QsvH264Encoder(_fps, bitrate or _bitrate, preset or _qsv_preset, "high")


def make_video_encoder():
    # Hardware H.264 runs in-process: capture is already isolated, and the
    # subprocess round trip (shared memory + pickle + pipes) cost several ms per frame.
    if _backend == "nvenc" and _codec == "h264":
        return make_nvenc_encoder()
    if _backend == "qsv" and _codec == "h264":
        return make_qsv_encoder()
    if _backend == "cpu" and _codec == "h264" or _backend == "qsv":
        from .isolated_encoder import IsolatedEncoder
        return IsolatedEncoder(_codec,_fps,_bitrate,_qsv_preset,keyframe=2 if _codec=="av1" else 1,backend=_backend,nvenc_preset=_nvenc_preset if _backend=='nvenc' else None)
    if _codec == "av1":
        return Av1Encoder(_fps,_bitrate,_av1_preset,_av1_lp,backend=_backend,qsv_preset=_qsv_preset)
    result = NvencEncoder(_fps,_h264_profile,_nvenc_preset) if _backend == "nvenc" else H264Encoder()
    result.target_bitrate = _bitrate
    return result


def set_defaults(bitrate=None, qsv_preset=None):
    global _bitrate, _qsv_preset
    if bitrate is not None:
        _bitrate = bitrate
    if qsv_preset is not None:
        _qsv_preset = qsv_preset


def use_shared_packets():
    global _packet_mode
    _packet_mode = True


def nvenc_context(width, height, bitrate, fps, profile="baseline", preset="p7"):
    context = av.CodecContext.create("h264_nvenc", "w")
    context.width, context.height = width, height
    # The shared input is already NV12. NVENC accepts it directly; requesting
    # planar YUV420 here caused another CPU conversion in the encoder process.
    context.pix_fmt = "nv12"
    context.framerate = Fraction(fps)
    context.time_base = Fraction(1, 90000)
    context.bit_rate = bitrate
    context.max_b_frames = 0
    context.gop_size = round(fps * 2)
    context.options = {"preset": preset, "tune": "ull", "rc": "cbr",
                       "zerolatency": "1", "rc-lookahead": "0", "delay": "0",
                       "profile": profile, "forced-idr": "1"}
    return context


def frame_rate(frame, default):
    """Picture rate of the source, which the engine carries on every frame (frame.opaque).
    The rate control divides the bitrate by this rate: an encoder left at 50 while the source
    delivers 25 pictures sent half the bitrate that was set (measured 2.3 instead of 4 Mbit/s)."""
    rate = getattr(frame, "opaque", None)
    if isinstance(rate, (int, Fraction)) and not isinstance(rate, bool) and 1 <= rate <= 120:
        return Fraction(rate)
    return Fraction(default)


class NvencEncoder(H264Encoder):
    """Reuse aiortc's RTP packetizer, with a zero-buffer NVENC codec context."""
    def __init__(self, fps=25, profile="baseline", preset="p7"):
        super().__init__()
        self.fps = fps
        self.codec_fps = None
        self.profile, self.preset = profile,preset
        self.encode_times = deque(maxlen=100)
        self._lan_bitrate = 1_000_000

    @property
    def target_bitrate(self):
        return self._lan_bitrate

    @target_bitrate.setter
    def target_bitrate(self, value):
        # The base aiortc H264 encoder clamps to 3 Mbit/s. Our validated LAN
        # CLI supports up to 10 Mbit/s, including the 8 Mbit/s quality test.
        self._lan_bitrate = max(500_000,min(int(value),10_000_000))

    def _encode_frame(self, frame, force_keyframe):
        fps = frame_rate(frame, self.fps)
        if self.codec and (frame.width != self.codec.width or frame.height != self.codec.height
                           or fps != self.codec_fps
                           or abs(self.target_bitrate - self.codec.bit_rate) > self.codec.bit_rate * .1):
            self.codec = None
            # A new encoder starts with a keyframe; viewers must not wait for a requested one.
            force_keyframe = True
        frame.pict_type = av.video.frame.PictureType.I if force_keyframe else av.video.frame.PictureType.NONE
        start = time.perf_counter()
        if self.codec is None:
            self.codec = nvenc_context(frame.width, frame.height, self.target_bitrate, fps,self.profile,self.preset)
            self.codec_fps = fps
        data = b"".join(bytes(packet) for packet in self.codec.encode(frame))
        self.encode_times.append((time.perf_counter() - start) * 1000)
        if getattr(self, "timings", None) is not None:
            self.timings.add("nvenc_encode_in_process", start)
        if not data:
            raise RuntimeError("NVENC hat ein Bild gepuffert; Low-Latency-Einstellung prüfen")
        yield from self._split_bitstream(data)


def check_nvenc(fps=25):
    context = nvenc_context(1280, 720, 1_000_000, fps)
    for i in range(3):
        frame = av.VideoFrame(1280, 720, "nv12")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.pts = i * 90000 // fps
        frame.time_base = Fraction(1, 90000)
        if not context.encode(frame):
            raise RuntimeError("NVENC liefert kein Bild ohne zusätzlichen Puffer")


def install(mode="auto", fps=25, codec="h264", bitrate=2_000_000,h264_profile="high",nvenc_preset="p7",av1_preset=8,av1_lp=6,qsv_preset="medium"):
    global _backend, _fps, _reason, _codec, _bitrate, _h264_profile, _nvenc_preset
    global _av1_preset, _av1_lp, _qsv_preset
    _qsv_preset=qsv_preset
    _av1_preset,_av1_lp = av1_preset,av1_lp
    _codec = codec
    _bitrate = bitrate
    _fps, _backend, _reason = fps, "cpu", None
    _h264_profile, _nvenc_preset = h264_profile,nvenc_preset
    if codec == "h264":
        from aiortc.codecs import CODECS
        for capability in CODECS["video"]:
            if capability.mimeType.lower() == "video/h264":
                profile = capability.parameters["profile-level-id"]
                capability.parameters["profile-level-id"] = profile[:4]+("20" if fps > 30 else "1f")
    if codec == "av1":
        register_av1(fps)
        if mode in ("auto","qsv"):
            try:
                check_qsv(fps,qsv_preset)
                _backend="qsv"
            except Exception as exc:
                if mode=="qsv":raise RuntimeError(f"Intel AV1 hardware encoder unavailable: {exc}") from exc
                _reason=str(exc)
                _backend="svt-av1"
                log.warning("Intel AV1 unavailable; CPU AV1 selected: %s",exc)
        else:
            _backend = "svt-av1"
    elif mode == "qsv":
        from .h264_qsv import QsvH264Encoder
        probe=QsvH264Encoder(fps,bitrate,qsv_preset,h264_profile)
        frame=av.VideoFrame(1280,720,"nv12");frame.pts=0;frame.time_base=Fraction(1,90000)
        try:probe.encode(frame,True);_backend="qsv"
        finally:probe.close()
    elif mode != "cpu":
        try:
            check_nvenc(fps)
            _backend = "nvenc"
        except Exception as exc:
            if mode == "nvenc":
                raise RuntimeError(f"NVENC nicht verfügbar: {exc}") from exc
            _reason = str(exc)
            try:
                from .h264_qsv import QsvH264Encoder
                probe=QsvH264Encoder(fps,bitrate,qsv_preset,h264_profile)
                frame=av.VideoFrame(1280,720,"nv12");frame.pts=0;frame.time_base=Fraction(1,90000)
                try:probe.encode(frame,True);_backend="qsv"
                finally:probe.close()
            except Exception:
                _backend="cpu"
                log.warning("Hardware H264 unavailable; CPU selected")

    if codec == "h264" and _backend == "nvenc" and h264_profile != "baseline":
        from aiortc.codecs import CODECS
        from aiortc.rtcrtpparameters import RTCRtpCodecParameters,RTCRtcpFeedback
        prefix = "6400" if h264_profile == "high" else "4d00"
        identifier = prefix+("20" if fps > 30 else "1f")
        if not any(c.mimeType.lower()=="video/h264" and c.parameters.get("profile-level-id")==identifier for c in CODECS["video"]):
            CODECS["video"].extend([
                RTCRtpCodecParameters(mimeType="video/H264",clockRate=90000,payloadType=111,
                    parameters={"level-asymmetry-allowed":"1","packetization-mode":"1","profile-level-id":identifier},
                    rtcpFeedback=[RTCRtcpFeedback(type="nack"),RTCRtcpFeedback(type="nack",parameter="pli"),RTCRtcpFeedback(type="goog-remb")]),
                RTCRtpCodecParameters(mimeType="video/rtx",clockRate=90000,payloadType=112,parameters={"apt":111})])

    def get_encoder(codec):
        global _active
        if _packet_mode:
            from fastvideo.publisher import PacketEncoder
            return PacketEncoder("audio" if codec.mimeType.lower().startswith("audio/") else "video")
        if codec.mimeType.lower() == "audio/opus":
            return TimedOpusEncoder()
        if codec.mimeType.lower() == "video/av1":
            _active = Av1Encoder(_fps, bitrate,_av1_preset,_av1_lp,backend=_backend,qsv_preset=_qsv_preset)
            return _active
        if codec.mimeType.lower() == "video/h264" and _backend == "nvenc":
            _active = NvencEncoder(_fps,_h264_profile,_nvenc_preset)
            return _active
        return default_get_encoder(codec)

    # No dependency files are edited. The adapter is tested against the pinned
    # aiortc version and replaces only the factory in this application process.
    aiortc.rtcrtpsender.get_encoder = get_encoder
    log.info("Videoencoder: %s", snapshot()["encoder"])


def snapshot():
    values = list(_active.encode_times) if _active is not None else []
    return {"encoder": ("AV1 / Intel Quick Sync Hardware (Low Delay)" if _backend=="qsv" else "AV1 / CPU / SVT-AV1 Low Delay") if _codec == "av1" else "H.264 / NVIDIA NVENC (Ultra Low Latency)" if _backend == "nvenc" else "H.264 / Intel Quick Sync" if _backend == "qsv" else "H.264 / CPU",
            "video_codec": _codec,
            "h264_profile": _h264_profile if _backend == "nvenc" else "baseline" if _codec == "h264" else None,
            "nvenc_preset": _nvenc_preset if _backend == "nvenc" else None,
            "av1_preset": _av1_preset if _codec == "av1" and _backend=="svt-av1" else None,
            "av1_parallelism": _av1_lp if _codec == "av1" and _backend=="svt-av1" else None,
            "video_target_bitrate_bps": _bitrate if _codec == "av1" or _backend == "nvenc" else min(_bitrate,3_000_000),
            "qsv_preset":_qsv_preset if _backend=="qsv" else None,
            "encoder_backend": _backend, "encoder_fallback_reason": _reason,
            "encode_ms_mean": round(sum(values) / len(values), 2) if values else None}
