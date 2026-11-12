"""Intel QSV H.264 encoder prepared for a shared fallback publisher."""
from collections import deque
from fractions import Fraction
import time
import av
from aiortc.codecs.h264 import H264Encoder
from .diagnostics import Timings

def qsv_context(width, height, bitrate, fps, profile, preset):
    c = av.CodecContext.create("h264_qsv", "w")
    c.width, c.height = width, height
    c.pix_fmt = "nv12"
    c.framerate, c.time_base = Fraction(fps), Fraction(1,90000)
    c.bit_rate, c.max_b_frames, c.gop_size = bitrate, 0, round(fps)
    c.options = {"preset":preset,"profile":profile,
        "async_depth":"1",
        "look_ahead":"0","low_delay_brc":"0","forced_idr":"1"}
    return c


class QsvH264Encoder(H264Encoder):
    def __init__(self, fps=50, bitrate=6_000_000, preset="medium", profile="high"):
        super().__init__()
        self.fps, self.preset, self.profile = fps, preset, profile
        self.signature = None
        self._bitrate = bitrate
        self.encode_times = deque(maxlen=250)
        self.timings = Timings()
    @property
    def target_bitrate(self):
        return self._bitrate
    @target_bitrate.setter
    def target_bitrate(self, value):
        self._bitrate = max(500_000, min(int(value), 12_000_000))
    def _encode_frame(self, frame, force_keyframe):
        # The source picture rate travels on the frame; the rate control must use it
        # (an encoder left at 50 sends half the bitrate for a 25p source).
        from .encoder import frame_rate
        signature = (frame.width, frame.height, frame_rate(frame, self.fps))
        if self.codec is not None and signature != self.signature:
            self.close()
            force_keyframe = True
        if self.codec is None:
            self.codec = qsv_context(frame.width, frame.height, self._bitrate, signature[2], self.profile, self.preset)
            self.signature = signature
        frame.pict_type = av.video.frame.PictureType.I if force_keyframe else av.video.frame.PictureType.NONE
        started = time.perf_counter()
        packets = self.codec.encode(frame)
        self.encode_times.append((time.perf_counter()-started)*1000)
        self.timings.add("codec_convert_upload_encode", started)
        if len(packets) != 1:
            raise RuntimeError("H264 QSV must emit one picture per input in low-delay mode")
        yield from self._split_bitstream(bytes(packets[0]))
    def close(self):
        if self.codec is not None:
            try:self.codec.encode(None)
            finally:self.codec = None
