"""Small on-demand input preview, independent of viewer encoding and queues."""
import asyncio
import time

import cv2
import av
from .media_clock import media_time


class InputPreview:
    def __init__(self, engine):
        self.engine = engine
        self.lock = asyncio.Lock()
        self.generated_at = 0
        self.jpeg = None
        self.work_ms = 0
        self.key = None
        self.frame_wall_ms = None
        self.enabled = True
        # Own scaler, reused: building a new one per thumbnail cost more than the scaling
        # (seen with 1080p BGRA from NDI/OMT in FastVideo). Only _encode uses it, under self.lock.
        self.reformatter = av.video.reformatter.VideoReformatter()

    def _encode(self, image):
        started = time.perf_counter()
        # A control thumbnail does not need to average every source pixel.
        # Bilinear sampling avoids scanning the whole 720p/1080p frame.
        if isinstance(image,av.VideoFrame):
            # Convert only the thumbnail, independently of audio and encoding.
            # Keep reformatter state private: the same immutable source frame
            # can be read simultaneously by the encoder preparation worker.
            small=self.reformatter.reformat(image,width=240,height=135,format='bgr24',threads=1).to_ndarray()
        else:
            small = cv2.resize(image, (240, 135), interpolation=cv2.INTER_LINEAR)
        ok, encoded = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if not ok:
            raise RuntimeError('Preview JPEG encoding failed')
        return encoded.tobytes(), (time.perf_counter() - started) * 1000

    async def get(self):
        async with self.lock:
            if not self.enabled:
                return None
            with self.engine.lock:
                frame = self.engine.latest
                received_at = self.engine.received_at
            if frame is None or received_at is None or media_time() - received_at > 2:
                self.jpeg = None
                self.key = None
                return None
            key = (frame[0], frame[2], id(frame[1]))
            if self.jpeg is not None and (self.key == key or media_time() - self.generated_at < .2):
                return self.jpeg
            # Only the reference is read under the capture lock; resizing runs elsewhere.
            self.jpeg, self.work_ms = await asyncio.to_thread(self._encode, frame[1])
            self.key = key
            # Wall time the capture card delivered this picture.
            self.frame_wall_ms = frame[2]
            self.generated_at = media_time()
            return self.jpeg


class ReceiverPreview(InputPreview):
    """Reuse an already decoded receive frame; no extra media connection."""
    def __init__(self, receiver):
        super().__init__(None)
        self.receiver = receiver

    def _encode(self, frame):
        started = time.perf_counter()
        small = self.reformatter.reformat(frame, width=240, height=135, format='bgr24').to_ndarray()
        ok, encoded = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if not ok:raise RuntimeError('Receiver preview JPEG encoding failed')
        return encoded.tobytes(), (time.perf_counter()-started)*1000

    async def get(self):
        # Only a media-process worker (it has .state) delivers finished thumbnails.
        worker = getattr(self.receiver, 'sender', None)
        media = getattr(worker, 'preview', None) if hasattr(worker, 'state') else None
        if media is not None:
            # The media process sends a finished thumbnail with its output wall time.
            created, jpeg, wall = media
            if time.monotonic() - created > 2:
                return None
            self.frame_wall_ms = wall
            return jpeg
        async with self.lock:
            latest = self.receiver.preview_frame
            if not latest or time.monotonic()-latest[0] > 2:
                self.jpeg = None;self.key = None
                return None
            key = (latest[0], latest[1].pts)
            if self.jpeg is not None and (self.key == key or time.monotonic()-self.generated_at < .5):
                return self.jpeg
            jpeg, work_ms = await asyncio.to_thread(self._encode, latest[1])
            if self.receiver.preview_frame is None:return None
            self.jpeg, self.work_ms, self.key = jpeg, work_ms, key
            self.frame_wall_ms = latest[2] if len(latest) > 2 else None
            self.generated_at = time.monotonic()
            return jpeg
