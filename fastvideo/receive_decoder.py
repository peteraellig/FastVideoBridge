"""Hardware receive decoding with explicit capability checks and software fallback."""
from collections import deque
from fractions import Fraction
import logging
import os
import re
import threading
import weakref
import av
from av.codec.hwaccel import HWAccel, hwdevices_available
from .diagnostics import Timings

_decoders = weakref.WeakValueDictionary()
log = logging.getLogger(__name__)

def decoder_status(thread_id):
    decoder = _decoders.get(thread_id)
    return decoder.snapshot() if decoder else None

DEFAULT_BACKENDS = ('qsv', 'cuda', 'd3d11va')
_START_CODE = re.compile(b'\x00\x00\x00?\x01')


def is_keyframe(kind, data):
    """Does this Annex-B picture start a decodable sequence (H.264 IDR, H.265 IRAP)?"""
    if kind == 'av1':
        return False
    for nal in _START_CODE.split(data)[1:]:
        if not nal:
            continue
        if kind == 'h264' and nal[0] & 0x1f == 5:
            return True
        if kind == 'hevc' and 16 <= (nal[0] >> 1) & 63 <= 21:
            return True
    return False


def configured_backends():
    # FASTVIDEO_DECODERS=d3d11va,cuda forces an order (tests, or a machine with a broken driver).
    value = os.environ.get('FASTVIDEO_DECODERS', '')
    chosen = tuple(name.strip() for name in value.split(',') if name.strip() in DEFAULT_BACKENDS)
    return chosen or DEFAULT_BACKENDS


class ReceiveDecoder:
    # Native QSV first: its copy-back uses streaming loads (gpu_copy) and releases the GIL.
    # NVIDIA-only PCs then use the CUDA hwaccel (GIL held <=2.9 ms per picture); D3D11VA's
    # copy-back held the GIL up to 5.6-6 ms. The native cuvid decoder is not used: it
    # returns every picture one packet late (+20 ms at 50 fps), even with low_delay.
    def __init__(self, kind, backends=None):
        self.kind = kind
        self.backends = list(backends or configured_backends()) + ['software']
        self.codec = None
        self.backend = 'initializing'
        self.fallback_reason = ''
        self.frames = 0
        self.timings = Timings()
        self.history = deque(maxlen=160)
        self.last_pts = None
        # Self-healing: a decoder that takes packets but returns nothing, or a timestamp
        # that jumped backwards, used to freeze the output silently while status stayed green.
        self.silent = 0
        self.filtered = 0
        self.backend_frames = 0
        self.keys_waiting = 0
        self.stall_resets = 0
        self.timestamp_resets = 0
        self.last_stall = 0.0

    def _switch(self, reason, retry=False):
        # last_pts stays: the history replay must not output pictures that were already shown.
        log.warning('Receive decoder %s: %s %s; %s', self.kind, self.backend, reason,
                    'rebuilding it' if retry else 'trying the next decoder')
        if retry or self.backend == 'software':
            self.backends.insert(0, self.backend)
        self.fallback_reason = f'{self.backend}: {reason}'
        self.codec, self.silent, self.backend_frames, self.keys_waiting = None, 0, 0, 0
        self._next()

    def _rebuild(self, reason):
        import time
        now = time.monotonic()
        self.stall_resets += 1
        # A second stall within 10 s moves on to the next backend instead of retrying this one.
        retry = now - self.last_stall > 10
        self.last_stall = now
        self._switch(reason, retry)

    def _next(self):
        if not self.backends:
            self.backends = ['software']  # the CPU decoder is always the last resort
        while self.backends:
            backend = self.backends.pop(0)
            try:
                if backend != 'software' and backend not in hwdevices_available():
                    raise RuntimeError('Hardware API is unavailable')
                name = ('libdav1d' if self.kind == 'av1' else self.kind) if backend == 'software' else self.kind
                if backend == 'qsv':
                    # Native QSV decoder (not an hwaccel): returns system-memory NV12.
                    context = av.CodecContext.create(self.kind + '_qsv', 'r')
                    context.options = {'async_depth': '1', 'gpu_copy': 'on'}
                else:
                    context = av.CodecContext.create(name, 'r', hwaccel=None if backend == 'software' else HWAccel(backend, allow_software_fallback=False))
                context.flags |= av.codec.context.Flags.low_delay
                context.thread_count = 2 if backend == 'software' else 1
                context.thread_type = 'SLICE'
                if backend not in ('software', 'qsv') and not context.is_hwaccel:
                    raise RuntimeError('Codec has no active hardware configuration')
                self.codec, self.backend = context, backend
                return
            except Exception as exc:
                self.fallback_reason = f'{backend}: {type(exc).__name__}'
        raise RuntimeError('No decoder available')

    def _replay_packets(self):
        # Replay from the newest keyframe only: older pictures cannot help a fresh decoder.
        packets = list(self.history)
        starts = [index for index, item in enumerate(packets) if item[3]]
        return packets[starts[-1]:] if starts else packets

    def decode(self, packet):
        import time
        _decoders[threading.get_ident()] = self
        data, pts, base = bytes(packet), packet.pts, packet.time_base
        key = is_keyframe(self.kind, data)
        self.history.append((data, pts, base, key))
        while sum(len(item[0]) for item in self.history) > 16 * 1024 * 1024:
            self.history.popleft()
        started = time.perf_counter()
        replay = False
        if self.codec is None:
            self._next()
        elif self.backend_frames and self.silent >= 50:
            # About one second of packets without a single picture: the decoder is stuck.
            self._rebuild('stalled')
            replay = True
        elif not self.backend_frames and self.keys_waiting >= 3:
            # Keyframes went in but no picture ever came out: this decoder does not work here.
            self._switch('no pictures after 3 keyframes')
            replay = True
        if key and not self.backend_frames:
            self.keys_waiting += 1
        while True:
            try:
                packets = self._replay_packets() if replay else [(data, pts, base, key)]
                frames = []
                for raw, timestamp, time_base, _ in packets:
                    item = av.Packet(raw); item.pts = timestamp; item.time_base = time_base
                    try:
                        frames.extend(self.codec.decode(item))
                    except av.InvalidDataError:
                        if not replay: raise
                result = [frame for frame in frames if self.last_pts is None or frame.pts is None or frame.pts > self.last_pts]
                self.filtered = self.filtered + 1 if frames and not result else 0
                if self.filtered >= 5:
                    # Timestamps jumped backwards for good (e.g. a sender clock restart):
                    # follow the new timeline instead of discarding every picture.
                    log.warning('Receive decoder %s: timestamps jumped back from %s to %s; following', self.kind, self.last_pts, frames[0].pts)
                    self.timestamp_resets += 1
                    self.filtered, result = 0, frames
                self.silent = 0 if result else self.silent + 1
                if result:
                    # The bitstream declares its source cadence even when RTP
                    # pictures were skipped. Carry it across the decoder queue.
                    rate = self.codec.framerate
                    if rate is not None and 1 <= rate <= 120:
                        for frame in result:
                            frame.opaque = {'bridge_source_fps': Fraction(rate)}
                    self.last_pts = result[-1].pts
                    self.frames += len(result)
                    self.backend_frames += len(result)
                    self.keys_waiting = 0
                self.timings.add('decode_including_gpu_download', started)
                return result
            except av.FFmpegError as exc:
                if isinstance(exc, av.InvalidDataError):
                    # Before the first keyframe (joining a running stream) or a damaged
                    # picture: not a broken decoder. Stalls are caught above instead.
                    self.silent += 1
                    return []
                if self.backend == 'software':
                    return []
                self._switch(type(exc).__name__)
                replay = True

    def snapshot(self):
        return dict(codec=self.kind, backend=self.backend, hardware=self.frames>0 and self.backend not in ('software','initializing'),
                    frames=self.frames, fallback_reason=self.fallback_reason, timings=self.timings.snapshot(),
                    stall_resets=self.stall_resets, timestamp_resets=self.timestamp_resets)
