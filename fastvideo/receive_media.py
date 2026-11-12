"""Receiver media process: decode and SDI/OMT/NDI output away from the WebRTC network stack.

PyAV re-acquires the GIL several times per decoded picture. In the same process as
aiortc's Python RTP/SRTP handling that cost the decoder several milliseconds per frame.
Only compressed pictures and decoded audio cross the pipe; decoded video never does.
"""
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av


def _send(stream, lock, value):
    from .isolated_encoder import send
    with lock:
        send(stream, value)


class MediaWorker:
    """Network-process handle with the interface the receiver expects from an output sender."""

    def __init__(self, output_kind, name, sdi_device, output_mode, on_error, sdi_interlaced=False):
        self.output_kind = output_kind
        self.on_error = on_error
        self.state = {}
        self.state_at = 0.0
        self.preview = None
        self.address = ''
        self._audio_minus20 = False
        self.write_lock = threading.Lock()
        self.ready = threading.Event()
        self.failure = None
        self.closed = False
        # True once the media process ended by itself; the receiver then starts a new one.
        self.dead = False
        root = Path(__file__).resolve().parents[1]
        (root / 'work').mkdir(exist_ok=True)
        with open(root / 'work' / 'receive-media.err', 'ab') as errors:
            self.process = subprocess.Popen([sys.executable, '-m', 'fastvideo.receive_media'],
                                            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        threading.Thread(target=self._read, daemon=True, name='Receive-media-results').start()
        self._command(('config', dict(output_kind=output_kind, name=name, sdi_device=sdi_device, output_mode=output_mode, sdi_interlaced=sdi_interlaced)))
        if not self.ready.wait(30):
            self.close()
            raise RuntimeError('Receiver media process did not start')
        if self.failure:
            self.close()
            raise ValueError(self.failure)

    def _command(self, value):
        if self.closed or self.process.poll() is not None:
            return
        try:
            _send(self.process.stdin, self.write_lock, value)
        except (OSError, ValueError):
            pass

    def _read(self):
        from .isolated_encoder import receive
        try:
            while True:
                message = receive(self.process.stdout)
                kind = message[0]
                if kind == 'ready':
                    self.address = message[1]
                    self.ready.set()
                elif kind == 'failed':
                    self.failure = message[1]
                    self.ready.set()
                elif kind == 'status':
                    self.state = message[1]
                    self.state_at = time.monotonic()
                    if message[2] is not None:
                        self.preview = message[2]
                elif kind == 'error' and self.on_error:
                    self.on_error(message[1])
        except (EOFError, OSError, ValueError):
            pass
        finally:
            started = self.ready.is_set() and not self.failure
            self.ready.set()
            if started and not self.closed:
                # Decoder and output ended without being asked to (driver fault, native crash).
                # Before, only a warning text appeared and the output stayed dead until the
                # operator restarted the receiver.
                self.dead = True
                if self.on_error:
                    self.on_error('Output process ended unexpectedly')

    # Called from aiortc's decoder thread with each complete compressed picture.
    def video(self, kind, data, pts, timestamp, output):
        self._command(('v', kind, data, pts, timestamp, output))

    def audio(self, frame, timestamp):
        # to_ndarray is exact; raw plane buffers carry padding that PyAV refuses on update().
        samples = frame.to_ndarray()
        self._command(('a', samples.tobytes(), str(samples.dtype), samples.shape, frame.format.name,
                       frame.layout.name, frame.sample_rate, frame.pts, timestamp))

    @property
    def audio_minus20(self):
        return self._audio_minus20

    @audio_minus20.setter
    def audio_minus20(self, value):
        self._audio_minus20 = value
        self._command(('minus20', value))

    def set_output_mode(self, mode):
        self._command(('mode', mode))

    def reset_stream(self):
        # New session: fresh decoder, empty queues, and a new SDI timeline.
        self._command(('reset',))

    # Status the receiver reads from a sender object.
    @property
    def timings(self):
        native = self.state.get('native', {})
        return SimpleNamespace(snapshot=lambda: native)

    @property
    def vmx(self):
        threads = self.state.get('vmx_threads')
        return SimpleNamespace(threads=threads) if threads else None

    def snapshot(self):
        return self.state.get('sdi')

    def close(self):
        if self.closed:
            return
        self._command(('stop',))
        self.closed = True
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


# Compressed pictures that may wait for the decoder (two seconds at 50 pictures per second).
# A short decoder stall stays below this and is caught up again; lasting overload is cut off.
MAX_WAITING = 100


def cadence(state, seconds, declared):
    """Source picture rate from the picture timestamps (state: previous_video_time, video_deltas, fps).
    The bitstream's declared rate covers start-up and skipped pictures, but a sender may declare its
    encoder rate (50) while sending 25 woven 1080i frames: then the timestamps win. Without that the
    SDI output paired two different frames into one interlaced frame (.102, 2026-10-16)."""
    if state.previous_video_time is not None:
        delta = seconds - state.previous_video_time
        if Fraction(1, 120) <= delta <= 1:
            # Missing pictures produce multiples of the source interval; the minimum is the format.
            state.video_deltas.append(delta)
    state.previous_video_time = seconds
    measured = (1 / min(state.video_deltas)).limit_denominator(1001) if len(state.video_deltas) >= 10 else None
    if declared is not None and 1 <= declared <= 120:
        declared = Fraction(declared).limit_denominator(1001)
        return measured if measured is not None and measured < declared * Fraction(3, 4) else declared
    return measured or state.fps


class _Media:
    """Runs inside the media process."""

    def __init__(self, config):
        from .diagnostics import Timings
        self.output_kind = config['output_kind']
        self.output_mode = config['output_mode']
        self.sender = self._make_sender(config)
        self.timings = Timings()
        self.decoder = None
        self.decoder_kind = None
        self.packets = queue.Queue()
        self.audio_queue = queue.Queue(maxsize=50)
        self.pending = deque()
        self.pending_ready = threading.Condition()
        self.fps = Fraction(50)
        self.video_deltas = deque(maxlen=20)
        self.previous_video_time = None
        self.frames = 0
        self.drops = 0
        self.queue_max = 0
        self.video_format = None
        self.input_video_format = None
        self.last_output = 0
        self.output_times = deque(maxlen=100)
        self.preview_source = None
        self.stopping = False
        self.write_lock = threading.Lock()
        self.generation = 0
        self.audio_frames = 0
        self.packets_in = self.packets_decoded = self.packets_stale = self.decode_errors = 0
        self.packets_skipped = 0
        self.resync = False
        self.last_error, self.last_error_at = '', 0.0

    def _make_sender(self, config):
        if config['output_kind'] == 'sdi':
            from .sdi_output import SdiSender
            return SdiSender(config['sdi_device'], config.get('sdi_interlaced', False))
        if config['output_kind'] == 'ndi':
            from .ndi_output import NdiSender
            return NdiSender(config['name'], config.get('sdi_interlaced', False))
        from .whip_receive import OmtSender
        return OmtSender(config['name'], config.get('sdi_interlaced', False))

    def send(self, value):
        from .isolated_encoder import send
        with self.write_lock:
            send(sys.stdout.buffer, value)

    def reset(self):
        self.generation += 1
        self.decoder = None
        self.decoder_kind = None
        with self.pending_ready:
            self.pending.clear()
        self.previous_video_time = None
        self.video_deltas.clear()
        if self.output_kind == 'sdi':
            self.sender.reset_stream()

    def accept_packet(self, item):
        """Queue one compressed picture for the decoder. A decoder that stays slower than the
        source let this queue, and with it the delay, grow without limit. Above MAX_WAITING
        pictures the waiting ones are discarded and decoding continues at the next keyframe:
        a short freeze instead of a picture that falls further and further behind."""
        if self.packets.qsize() >= MAX_WAITING:
            try:
                while True:
                    if self.packets.get_nowait() is not None:
                        self.packets_skipped += 1
            except queue.Empty:
                pass
            self.resync = True
        self.packets.put(item)

    def observe_video_time(self, frame):
        seconds = frame.pts * frame.time_base
        declared = (frame.opaque or {}).get('bridge_source_fps') if isinstance(frame.opaque, dict) else None
        self.fps = cadence(self, seconds, declared)

    def decode_loop(self):
        from .receive_decoder import ReceiveDecoder, is_keyframe
        while True:
            item = self.packets.get()
            if item is None:
                return
            generation, kind, data, pts, timestamp, output = item
            if generation != self.generation:
                self.packets_stale += 1
                continue
            if self.resync:
                # After an overflow: continue with a fresh decoder at the next keyframe.
                # (AV1 keyframes are not recognised here; its decoder waits for one by itself.)
                if kind != 'av1' and not is_keyframe(kind, data):
                    self.packets_skipped += 1
                    continue
                self.resync = False
                self.decoder = None
            if self.decoder is None or self.decoder_kind != kind:
                self.decoder, self.decoder_kind = ReceiveDecoder(kind), kind
            packet = av.Packet(data)
            packet.pts, packet.time_base = pts, Fraction(1, 90000)
            try:
                decoded = self.decoder.decode(packet)
            except Exception as exc:
                # Log a repeating error once per 10 s, not for every picture.
                signature, now = repr(exc), time.monotonic()
                if signature != self.last_error or now - self.last_error_at > 10:
                    traceback.print_exc()
                    sys.stderr.flush()
                    self.last_error, self.last_error_at = signature, now
                self.decode_errors += 1
                continue
            self.packets_decoded += 1
            for frame in decoded:
                self.observe_video_time(frame)
                self.input_video_format = {'width': frame.width, 'height': frame.height}
                if not output:
                    continue
                with self.pending_ready:
                    # Bound latency: keep at most two pictures waiting for output.
                    while len(self.pending) >= 2:
                        self.pending.popleft()
                        self.drops += 1
                    self.pending.append((generation, frame, timestamp, self.fps))
                    self.queue_max = max(self.queue_max, len(self.pending))
                    self.pending_ready.notify()

    def scale_output(self, frame):
        from .whip_receive import WhipReceive
        return WhipReceive.scale_output(frame)

    def video_loop(self):
        while True:
            with self.pending_ready:
                while not self.pending and not self.stopping:
                    self.pending_ready.wait()
                if self.stopping:
                    return
                generation, frame, timestamp, fps = self.pending.popleft()
            if generation != self.generation:
                continue
            started = time.perf_counter()
            try:
                if self.output_mode == '720p' and (frame.width, frame.height) != (1280, 720):
                    resized = time.perf_counter()
                    frame = self.scale_output(frame)
                    self.timings.add('video_output_resize', resized)
                self.sender.video(frame, timestamp, fps)
            except Exception as error:
                self.send(('error', str(error) or type(error).__name__))
                continue
            self.timings.add('video_output_including_thread_wait', started)
            self.frames += 1
            self.last_output = time.monotonic()
            self.output_times.append(self.last_output)
            self.video_format = {'width': frame.width, 'height': frame.height}
            lead = getattr(self.sender, 'schedule_lead_ms', None) if self.output_kind == 'sdi' else None
            self.preview_source = (frame, time.time() * 1000 + (lead or 0))

    def audio_loop(self):
        while True:
            item = self.audio_queue.get()
            if item is None:
                return
            data, dtype, shape, fmt, layout, rate, pts, timestamp = item
            started = time.perf_counter()
            try:
                import numpy as np
                frame = av.AudioFrame.from_ndarray(np.frombuffer(data, dtype=dtype).reshape(shape), format=fmt, layout=layout)
                frame.sample_rate = rate
                frame.pts, frame.time_base = pts, Fraction(1, rate)
                self.sender.audio(frame, timestamp)
            except Exception as error:
                self.send(('error', 'Audio output: ' + (str(error) or type(error).__name__)))
                continue
            self.timings.add('audio_media_output', started)
            self.audio_frames += 1

    def preview_jpeg(self):
        import cv2
        source = self.preview_source
        if not source:
            return None
        frame, wall = source
        small = frame.reformat(width=240, height=135, format='bgr24').to_ndarray()
        ok, encoded = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 65])
        return (time.monotonic(), encoded.tobytes(), wall) if ok else None

    def status_loop(self):
        # This thread must never die: a dead status thread froze the last (green) status.
        last_preview = 0
        failed = False
        while not self.stopping:
            time.sleep(.25)
            try:
                state, preview, last_preview = self._status(last_preview)
                failed = False
            except Exception:
                if not failed:
                    traceback.print_exc()
                    sys.stderr.flush()
                failed = True
                continue
            try:
                self.send(('status', state, preview))
            except (OSError, ValueError):
                return

    def _status(self, last_preview):
        now = time.monotonic()
        # Two output times can be equal (same clock tick): never divide by a zero span.
        recent = [t for t in list(self.output_times) if now - t < 2]
        span = recent[-1] - recent[0] if len(recent) > 1 else 0
        vmx = getattr(self.sender, 'vmx', None)
        decoder = self.decoder
        state = {'native': self.sender.timings.snapshot(),
                 'sdi': self.sender.snapshot() if self.output_kind == 'sdi' else None,
                 'vmx_threads': vmx.threads if vmx and self.fps <= 60 else None,
                 'decoder': decoder.snapshot() if decoder else None,
                 'output': self.timings.snapshot(),
                 'video_format': self.video_format, 'input_video_format': self.input_video_format,
                 'fps': float(self.fps), 'frames': self.frames, 'drops': self.drops, 'queue_max': self.queue_max,
                 'audio_frames': self.audio_frames,
                 'packets': {'in': self.packets_in, 'decoded': self.packets_decoded,
                             'stale': self.packets_stale, 'errors': self.decode_errors,
                             'skipped': self.packets_skipped},
                 'output_fps': round((len(recent) - 1) / span, 1) if span > 0 else 0,
                 'video_age_s': now - self.last_output if self.last_output else None}
        preview = None
        if now - last_preview >= .5:
            try:
                preview = self.preview_jpeg()
            except Exception:
                preview = None
            last_preview = now
        return state, preview, last_preview

    def run(self):
        from .isolated_encoder import receive
        workers = [threading.Thread(target=self.decode_loop, daemon=True, name='media-decode'),
                   threading.Thread(target=self.video_loop, daemon=True, name='media-video'),
                   threading.Thread(target=self.audio_loop, daemon=True, name='media-audio'),
                   threading.Thread(target=self.status_loop, daemon=True, name='media-status')]
        for worker in workers:
            worker.start()
        try:
            while True:
                message = receive(sys.stdin.buffer)
                kind = message[0]
                if kind == 'v':
                    self.packets_in += 1
                    self.accept_packet((self.generation,) + tuple(message[1:]))
                elif kind == 'a':
                    try:
                        self.audio_queue.put_nowait(message[1:])
                    except queue.Full:
                        pass
                elif kind == 'reset':
                    self.reset()
                elif kind == 'mode':
                    self.output_mode = message[1]
                elif kind == 'minus20':
                    self.sender.audio_minus20 = message[1]
                elif kind == 'stop':
                    break
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self.stopping = True
            self.packets.put(None)
            self.audio_queue.put(None)
            with self.pending_ready:
                self.pending_ready.notify_all()
            for worker in workers[:3]:
                worker.join(timeout=3)
            self.sender.close()


def main():
    from .isolated_encoder import receive, send
    from .process_priority import opt_out
    opt_out()
    message = receive(sys.stdin.buffer)
    if message[0] != 'config':
        raise SystemExit('Receiver media process requires its configuration first')
    try:
        media = _Media(message[1])
    except Exception as error:
        send(sys.stdout.buffer, ('failed', str(error) or 'Selected output runtime could not start'))
        return
    media.send(('ready', getattr(media.sender, 'address', '') or ''))
    media.run()


if __name__ == '__main__':
    main()
