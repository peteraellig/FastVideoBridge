import asyncio
from collections import deque
from fractions import Fraction
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import threading
import time

from aiortc import VideoStreamTrack, AudioStreamTrack
from aiortc.mediastreams import MediaStreamError
import av
import cv2
import numpy as np

from .media_clock import media_time
from .diagnostics import Timings
from .timestamp import stamp, stamp_yuv
from .sync import Timeline, SourceStartup, AudioBuffer, RATE, BLOCK

log = logging.getLogger(__name__)
# Avoid twenty worker threads competing with vMix and the AV1 upload for every resize.
cv2.setNumThreads(1)


class Engine:
    """Receive once, keep only the latest processed frame (no growing queue)."""
    def __init__(self, sources, fps=25, width=1280, height=720, timestamp_marker=False):
        self.sources = sources
        self.fps, self.width, self.height = Fraction(fps), width, height
        self.output_mode = "follow"
        self.timestamp_marker = timestamp_marker
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.thread = None
        self.source = None
        self.latest = None
        self.sequence = 0
        self.video_notification = None
        self.total_frames = 0
        self.error = None
        self.received_at = None
        self.ticks = deque(maxlen=100)
        self.origin = media_time()
        self.timeline = Timeline()
        self.audio = AudioBuffer()
        self.audio_received_at = None
        self.audio_rate = None
        self.audio_level = None
        self.input_format = None
        self.last_video_source_time = None
        self.shared_nv12 = False
        self.video_frame_cache = None
        self.frame_reformatter = av.video.reformatter.VideoReformatter()
        # Bound native conversion parallelism; retain CPU capacity for capture/audio.
        self.colour_threads = min(4, max(1, (os.cpu_count() or 1)//2))
        self.frame_worker = ThreadPoolExecutor(max_workers=1,thread_name_prefix='bridge-input-frame')
        self.timings = Timings()
        self.receiver_timings = None
        self.input_frames = 0
        self.selection_drops = 0
        self.deadline_drops = 0
        self.track_skips = 0
        self.source_gap_frames = 0
        self.previous_source_time = None

    def probe_format(self, source):
        # Warm the hardware with the real format before establishing the live A/V clock.
        receiver=self.sources.receiver(source)
        try:
            until=media_time()+2
            while media_time()<until:
                item=receiver.read_event(timeout_ms=100)
                if item and item.kind=='video':
                    if item.fps:self.fps=Fraction(item.fps).limit_denominator(1001)
                    self.width,self.height=((item.data.width,item.data.height) if isinstance(item.data,av.VideoFrame) else (item.data.shape[1],item.data.shape[0])) if self.output_mode=='follow' else (1280,720)
                    return
        finally:receiver.close()

    def make_video_frame(self,sequence,image,shared_nv12,capture_ms=None):
        # This serial worker owns the conversion cache and reformatter. Each
        # consumer awaits its frame: at most one request per encoder is pending.
        started=time.perf_counter()
        if shared_nv12:
            cached=self.video_frame_cache
            if cached is None or cached[0]!=sequence:
                # Capture publishes an owned image. Conversion only reads it;
                # avoid copying the entire BGR surface before producing NV12.
                # Cropped/non-contiguous arrays still need PyAV's packing copy.
                if isinstance(image,av.VideoFrame):
                    # Native picture straight to NV12 in one step; the timestamp marker goes into the
                    # YUV result (no BGR detour, as in FastVideo).
                    input_frame,stamped=self._prepare_native_image(image,capture_ms)
                    converted=self.frame_reformatter.reformat(input_frame,width=self.width,height=self.height,
                                                              format='nv12',threads=self.colour_threads)
                    if self.timestamp_marker and not stamped:stamp_yuv(converted,capture_ms)
                else:
                    input_frame=(av.VideoFrame.from_numpy_buffer(image,format='bgr24')
                                 if image.flags.c_contiguous else av.VideoFrame.from_ndarray(image,format='bgr24'))
                    converted=self.frame_reformatter.reformat(input_frame,format='nv12',threads=self.colour_threads)
                self.timings.add('video_colour_convert_shared',started)
                cached=(sequence,converted);self.video_frame_cache=cached
            # Encoders may change pict_type/PTS and pixels independently.
            frame=av.VideoFrame(cached[1].width,cached[1].height,'nv12')
            # GIL-free copy (PyAV's plane.update holds the GIL for the whole frame).
            for target,source in zip(frame.planes,cached[1].planes):np.copyto(np.frombuffer(target,np.uint8),np.frombuffer(source,np.uint8))
        elif isinstance(image,av.VideoFrame):
            source,stamped=self._prepare_native_image(image,capture_ms)
            # Own output pixels even when the requested format already matches.
            converted=self.frame_reformatter.reformat(source,width=self.width,height=self.height,format='yuv420p',threads=self.colour_threads)
            frame=av.VideoFrame(converted.width,converted.height,'yuv420p')
            for target,plane in zip(frame.planes,converted.planes):np.copyto(np.frombuffer(target,np.uint8),np.frombuffer(plane,np.uint8))
            if self.timestamp_marker and not stamped:stamp_yuv(frame,capture_ms)
        else:
            frame=av.VideoFrame.from_ndarray(image,format='bgr24')
        self.timings.add('video_frame_copy',started)
        return frame

    def _prepare_native_image(self,image,capture_ms):
        # Encoder worker only. Same aspect ratio is scaled during the YUV conversion and the
        # timestamp marker is written into the YUV result afterwards (returns stamped=False).
        # BGR is needed only for letterboxing another aspect ratio.
        same_aspect=image.width*self.height==image.height*self.width
        if same_aspect:return image,False
        scale=min(self.width/image.width,self.height/image.height)
        w=max(2,int(image.width*scale));h=max(2,int(image.height*scale))
        resized=self.frame_reformatter.reformat(image,width=w,height=h,format='bgr24',threads=self.colour_threads).to_ndarray()
        output=np.zeros((self.height,self.width,3),np.uint8)
        x,y=(self.width-w)//2,(self.height-h)//2;output[y:y+h,x:x+w]=resized
        if self.timestamp_marker:stamp(output,capture_ms)
        return av.VideoFrame.from_ndarray(output,format='bgr24'),self.timestamp_marker

    def close(self):
        self.stop()
        # Cancelled track consumers can leave a native conversion running.
        self.frame_worker.shutdown(wait=True)

    def start(self, source):
        self.stop()
        # Channel count for Setup -> Input audio: unknown until this source delivers sound
        # (the internal test picture always has two channels).
        from . import sources as source_settings
        source_settings.AUDIO_CHANNELS = 2 if source.get('kind') == 'test' else 0
        receiver = self.sources.receiver(source)
        self.receiver_timings = getattr(receiver, "timings", None)
        self.receiver_capture_stats = getattr(receiver, "capture_stats", None)
        self.source = dict(source)
        self.halt.clear()
        self.error = None
        self.received_at = None
        self.ticks.clear()
        self.total_frames = 0
        self.timeline = Timeline()
        self.audio = AudioBuffer()
        self.audio_received_at = None
        self.audio_rate = None
        self.audio_level = None
        self.input_format = None
        self.last_video_source_time = None
        self.timings = Timings()
        self.input_frames = 0
        self.selection_drops = 0
        self.deadline_drops = 0
        self.track_skips = 0
        self.source_gap_frames = 0
        self.previous_source_time = None
        self.thread = threading.Thread(target=self._capture, args=(receiver,), daemon=True, name="FastVideo-capture")
        self.thread.start()

    def stop(self):
        self.halt.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("Empfangsthread reagiert nicht; FastVideo neu starten")
        self.thread = None
        self.source = None
        with self.lock:
            self.latest = None
        self._notify_video()

    def _capture(self, receiver):
        next_output = 0
        delayed = deque()
        pending_audio = deque(maxlen=50)
        startup = SourceStartup()
        try:
            while not self.halt.is_set():
                # Release due pictures before waiting for another SDK event.
                self._release_ready(delayed, media_time())
                # New SDK events wake the wait immediately; the timeout only has to
                # reach the next picture deadline. Polling every 1 ms made other
                # threads (NVENC, colour conversion) wait for the GIL.
                timeout_ms = 20 if not delayed else min(20, max(1, int((delayed[0][0]-media_time())*1000)))
                read_started = time.perf_counter()
                item = receiver.read_event(timeout_ms=timeout_ms)
                self.timings.add("receive_wait_and_copy", read_started)
                # An audio packet or picture preparation must not hold a due frame.
                self._release_ready(delayed, media_time())
                if item is not None:
                    if item.kind == "audio":
                        pending_audio.append(item)
                    else:
                        self.input_frames += 1
                        if self.timeline.source_origin is None and not startup.observe(item.source_time, media_time()):
                            # Drain stale startup pictures before establishing
                            # the common clock or publishing anything downstream.
                            continue
                        if self.previous_source_time is not None and item.fps:
                            delta = (item.source_time-self.previous_source_time)*item.fps/10_000_000
                            self.source_gap_frames += max(0, round(delta)-1)
                        self.previous_source_time = item.source_time
                        image, capture_ms = item.data, item.capture_ms
                        iw,ih=(image.width,image.height) if isinstance(image,av.VideoFrame) else (image.shape[1],image.shape[0])
                        self.input_format = {"width":iw,"height":ih,"fps":float(item.fps),"scan":getattr(item,"scan","")}
                        self.fps = Fraction(item.fps).limit_denominator(1001) if item.fps else self.fps
                        if self.output_mode == "follow":
                            self.width, self.height = iw, ih
                        else:
                            self.width, self.height = 1280, 720
                        self.timeline.observe_video(item.source_time, media_time())
                        self.last_video_source_time=item.source_time
                    while pending_audio and self.timeline.source_origin is not None:
                        audio_started = time.perf_counter()
                        audio=pending_audio.popleft()
                        end=audio.source_time+audio.data.shape[1]*10_000_000/audio.sample_rate if audio.sample_rate else audio.source_time
                        if 8000<=audio.sample_rate<=192000 and end<=self.timeline.source_origin:continue
                        self._audio(audio)
                        self.timings.add("audio_prepare", audio_started)
                    now = media_time()
                    if item.kind == "video":
                        self.received_at = now
                    # Select pictures by media time, not jittery SDK arrival time.
                    pts = self.timeline.ticks(item.source_time,90000) if item.kind == "video" else None
                    if pts is not None:
                        step = Fraction(90000, 1) / self.fps
                        next_output = (next_output + step
                                       if pts - next_output < step
                                       else pts + step)
                        if isinstance(image,av.VideoFrame):
                            output=image
                        else:
                            prepare_started = time.perf_counter()
                            ih, iw = image.shape[:2]
                            scale = min(self.width / iw, self.height / ih)
                            w = max(2, min(self.width, int(iw * scale)))
                            h = max(2, min(self.height, int(ih * scale)))
                            resized = image if (w,h) == (iw,ih) else cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
                            if (iw,ih)==(self.width,self.height):
                                # Receiver already owns a copy of the native SDK buffer.
                                output=image
                            else:
                                output = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                                x, y = (self.width - w) // 2, (self.height - h) // 2
                                output[y:y + h, x:x + w] = resized
                            if self.timestamp_marker:
                                stamp(output, capture_ms)
                            self.timings.add("video_resize_and_copy", prepare_started)
                        delayed.append((self.timeline.deadline(pts,90000), output, capture_ms, pts, self.fps))
                    elif pts is not None:
                        self.selection_drops += 1
                self._release_ready(delayed, media_time())
        except Exception as exc:
            self.error = str(exc)
            log.exception("Videoempfang abgebrochen")
        finally:
            self.halt.set()
            self._notify_video()
            receiver.close()

    def _release_ready(self, delayed, now):
        ready = None
        while delayed and delayed[0][0] <= now:
            if ready is not None:
                self.deadline_drops += 1
            ready = delayed.popleft()
        if ready:
            deadline, output, capture_ms, pts = ready[:4]
            rate = ready[4] if len(ready)>4 else self.fps
            self.timings.add("release_lateness", time.perf_counter()-(now-deadline))
            with self.lock:
                self.sequence += 1
                self.latest = (self.sequence, output, capture_ms, pts, rate)
            self.total_frames += 1
            self.ticks.append(now)
            self._notify_video()

    def _notify_video(self):
        notification = self.video_notification
        if notification:
            loop, event = notification
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # The consumer event loop was closed during shutdown.
                self.video_notification = None

    def _audio(self, item):
        if not 8000 <= item.sample_rate <= 192000:
            raise ValueError("Ungültige Audio-Abtastrate")
        start = self.timeline.ticks(item.source_time,RATE)
        if self.last_video_source_time is not None and abs(item.source_time-self.last_video_source_time)/10_000_000 > 2:
            raise ValueError("Audio- und Video-Quellzeitcodes passen nicht zusammen")
        # Convert each source packet's duration, never reset a stream resampler
        # timestamp separately from video. Source clock drives packet placement.
        samples = np.nan_to_num(item.data,nan=0,posinf=0,neginf=0)*item.gain
        count = round(samples.shape[1]*RATE/item.sample_rate)
        if count != samples.shape[1]:
            position = np.arange(count)*item.sample_rate/RATE
            samples = np.stack([np.interp(position,np.arange(samples.shape[1]),channel) for channel in samples]).astype(np.float32)
        with self.lock:
            self.audio.append(start,samples)
        self.audio_received_at = media_time()
        self.audio_rate = item.sample_rate
        peak = float(np.abs(samples).max())
        self.audio_level = round(20*np.log10(peak),1) if peak > 0 else -120.0

    def snapshot(self):
        alive = bool(self.thread and self.thread.is_alive())
        age = (media_time() - self.received_at) if self.received_at else None
        ticks = list(self.ticks)
        fps = (len(ticks) - 1) / (ticks[-1] - ticks[0]) if len(ticks) > 1 else 0
        return {"running": alive, "source": self.source, "error": self.error,
                "source_age_ms": round(age * 1000) if age is not None else None,
                "signal": bool(alive and age is not None and age < 2),
                "output_fps": round(fps, 1), "frames": self.total_frames,
                "width": self.width, "height": self.height, "output_mode":self.output_mode, "format_fps":float(self.fps),
                "timestamp_marker": self.timestamp_marker,
                "audio_signal": bool(self.audio_received_at and media_time()-self.audio_received_at < 1),
                "audio_sample_rate": self.audio_rate, "audio_peak_dbfs": self.audio_level,
                "input_format": self.input_format,
                "audio_codec": "Opus Stereo / 48 kHz / 128 kbit/s",
                "av_sync": "Gemeinsame Quellzeitcodes + RTP/RTCP-Medienzeit",
                "av_playout_buffer_ms": round(self.timeline.lead*1000),
                "source_clock_phase_ms": round(self.timeline.phase*1000,2),
                "audio_discontinuities": self.audio.discontinuities,
                "audio_buffer_drops": self.audio.dropped,
                "audio_missing_samples": self.audio.missing,
                "audio_read_pts": self.audio.last_read,
                "audio_buffer_end_pts": self.audio.last_read_buffer_end,
                "audio_last_missing_ms": round(self.audio.last_read_missing*1000/RATE,2),
                "audio_buffer_lead_ms": (round((self.audio.last_read_buffer_end-self.audio.last_read-BLOCK)*1000/RATE,2)
                                         if self.audio.last_read_buffer_end is not None and self.audio.last_read is not None else None),
                "pipeline_timings": self.timings.snapshot(),
                "receiver_timings": self.receiver_timings.snapshot() if self.receiver_timings else {},
                "capture_queue_counters": (self.receiver_capture_stats() if
                    getattr(self, "receiver_capture_stats", None) else {}),
                "input_frame_count": self.input_frames,
                "source_timecode_gap_frames": self.source_gap_frames,
                "frame_selection_drops": self.selection_drops,
                "frame_deadline_drops": self.deadline_drops,
                "video_track_skips": self.track_skips}


class VideoTrack(VideoStreamTrack):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.last_sequence = -1

    async def recv(self):
        loop = asyncio.get_running_loop()
        notification = self.engine.video_notification
        if notification is None or notification[0] is not loop:
            self.engine.video_notification = (loop, asyncio.Event())
        event = self.engine.video_notification[1]
        while self.readyState == "live":
            # Clear before checking latest, so a concurrent publication cannot
            # be lost between the sequence check and awaiting the notification.
            event.clear()
            if self.engine.halt.is_set() or not self.engine.thread or not self.engine.thread.is_alive():
                raise MediaStreamError
            with self.engine.lock:
                latest = self.engine.latest
            if latest and latest[0] != self.last_sequence:
                if self.last_sequence >= 0:
                    self.engine.track_skips += max(0, latest[0]-self.last_sequence-1)
                self.last_sequence, image, capture_ms, pts = latest[:4]
                rate = latest[4] if len(latest)>4 else self.engine.fps
                frame = await loop.run_in_executor(self.engine.frame_worker,self.engine.make_video_frame,
                                                  self.last_sequence,image,self.engine.shared_nv12,capture_ms)
                frame.opaque = rate
                frame.pts = pts
                frame.time_base = Fraction(1, 90000)
                return frame
            await event.wait()
        raise MediaStreamError


class AudioTrack(AudioStreamTrack):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.next_sample = None

    async def recv(self):
        while self.readyState == "live":
            if not self.engine.thread or not self.engine.thread.is_alive():
                raise MediaStreamError
            timeline = self.engine.timeline
            if timeline.mono is None:
                await asyncio.sleep(.004)
                continue
            now = media_time()
            current = int((now-timeline.mono-timeline.phase-timeline.lead)*RATE)
            if self.next_sample is None or current-self.next_sample > RATE//5:
                self.next_sample = max(0,current//BLOCK*BLOCK)
            due = timeline.deadline(self.next_sample,RATE)
            await asyncio.sleep(max(0,due-media_time()))
            # A short late-arrival grace keeps source jitter / CPU scheduling
            # from replacing valid audio with silence. PTS never moves with it.
            # The first SDI audio callback can follow its video callback. Apply
            # the same bounded grace before the first packet has arrived too.
            while media_time()<due+.020:
                with self.engine.lock:
                    complete=self.engine.audio.contains(self.next_sample)
                if complete:break
                await asyncio.sleep(.001)
            with self.engine.lock:
                samples = self.engine.audio.read(self.next_sample)
            pcm = np.clip(np.rint(samples.T*32768),-32768,32767).astype(np.int16).reshape(1,-1)
            frame = av.AudioFrame.from_ndarray(pcm,format="s16",layout="stereo")
            frame.sample_rate, frame.time_base = RATE,Fraction(1,RATE)
            frame.pts = self.next_sample
            self.next_sample += BLOCK
            return frame
        raise MediaStreamError
