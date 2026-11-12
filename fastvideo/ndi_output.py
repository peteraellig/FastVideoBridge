"""Full NDI output of decoded WHIP video and stereo audio."""
import ctypes as C
import threading
import time
import av
import numpy as np
from .sources import NDI, NDIVideo, NDIAudio, bind, woven_frame
from .diagnostics import Timings

class SendSettings(C.Structure):
    _fields_ = [('name', C.c_char_p), ('groups', C.c_char_p),
                ('clock_video', C.c_bool), ('clock_audio', C.c_bool)]

class NdiSender:
    def __init__(self, name, interlaced=False):
        self.runtime = NDI()
        self.interlaced = interlaced
        self.handle = None
        self.audio_minus20 = False
        self.video_lock = threading.Lock()
        self.audio_lock = threading.Lock()
        self.pending_video = None
        self.nv12_buffers = [None, None]
        self.nv12_slot = 0
        self.timings = Timings()
        self.reformatter = av.video.reformatter.VideoReformatter()
        self.resampler = av.AudioResampler(format='fltp', layout='stereo', rate=48000)
        try:
            dll = self.runtime.dll
            create = bind(dll, 'NDIlib_send_create', [C.POINTER(SendSettings)], C.c_void_p)
            self.send_video = bind(dll, 'NDIlib_send_send_video_async_v2', [C.c_void_p, C.POINTER(NDIVideo)], None)
            self.send_audio = bind(dll, 'NDIlib_send_send_audio_v2', [C.c_void_p, C.POINTER(NDIAudio)], None)
            self.destroy = bind(dll, 'NDIlib_send_destroy', [C.c_void_p], None)
            get_source = bind(dll, 'NDIlib_send_get_source_name', [C.c_void_p], C.c_void_p)
            # WHIP delivery already paces media; do not clock it a second time.
            self.handle = create(C.byref(SendSettings(name.encode(), None, False, False)))
            if not self.handle:
                raise RuntimeError('NDI output could not start')
            from .sources import NDISource
            source = C.cast(get_source(self.handle), C.POINTER(NDISource)).contents
            self.address = source.name.decode('utf-8', 'replace')
            self.url = (source.url or b'').decode('utf-8', 'replace')
        except BaseException:
            self.close()
            raise

    def video(self, frame, timestamp, fps):
        started = time.perf_counter()
        # Convert the next image while the SDK compresses/transmits the previous.
        # Keep each submitted frame alive until the next send synchronizes it.
        if frame.format.name == 'nv12' and len(frame.planes) == 2 and frame.planes[0].line_size == frame.planes[1].line_size:
            # Send the hardware decoder's YUV surface without colour conversion.
            # SDK NV12 requires adjacent Y/UV planes, unlike padded GPU copy-back.
            y, uv = frame.planes
            stride = y.line_size
            ysize, uvsize = stride*frame.height, stride*((frame.height+1)//2)
            if stride < frame.width or y.buffer_size < ysize or uv.buffer_size < uvsize:
                raise ValueError('Truncated NV12 surface')
            pointer, owner = y.buffer_ptr, frame
            if uv.buffer_ptr != pointer+ysize:
                self.nv12_slot = 1-self.nv12_slot
                size = ysize+uvsize
                buffer = self.nv12_buffers[self.nv12_slot]
                if buffer is None or buffer.size != size:
                    buffer = np.empty(size, dtype=np.uint8)
                    self.nv12_buffers[self.nv12_slot] = buffer
                pointer, owner = buffer.ctypes.data, buffer
                # Two buffers: preparing the next surface must never overwrite
                # the previous one while the asynchronous SDK still reads it.
                C.memmove(pointer, y.buffer_ptr, ysize)
                C.memmove(pointer+ysize, uv.buffer_ptr, uvsize)
            fourcc = b'NV12'
            converted = frame
            self.timings.add('video_nv12_prepare', started)
        else:
            converted = self.reformatter.reformat(frame, format='uyvy422', threads=1)
            self.timings.add('video_colour_convert', started)
            plane = converted.planes[0]
            pointer, stride, owner = plane.buffer_ptr, plane.line_size, converted
            fourcc = b'UYVY'
        native = NDIVideo(width=converted.width, height=converted.height,
            fourcc=int.from_bytes(fourcc, 'little'), fps_n=fps.numerator,
            fps_d=fps.denominator, aspect=converted.width/converted.height,
            frame_format=0 if woven_frame(self.interlaced, converted.height, fps) else 1, timecode=timestamp, data=pointer,
            stride=stride)
        started = time.perf_counter()
        with self.video_lock:
            self.timings.add('video_lock_wait', started)
            started = time.perf_counter()
            if self.handle:
                self.send_video(self.handle, C.byref(native))
                self.pending_video = (owner, native)
            self.timings.add('video_native_send', started)

    def audio(self, frame, timestamp):
        elapsed = 0
        for output in self.resampler.resample(frame):
            # NDI +4 dBu = 1.0; digital PCM has 20 dB headroom above reference.
            gain = 1 if self.audio_minus20 else 10
            data = np.ascontiguousarray(output.to_ndarray(), dtype=np.float32)
            if gain != 1:
                data = data * gain
            native = NDIAudio(sample_rate=48000, channels=2, samples=output.samples,
                timecode=timestamp+elapsed, data=data.ctypes.data, stride=data.strides[0])
            with self.audio_lock:
                if self.handle:
                    self.send_audio(self.handle, C.byref(native))
            elapsed += round(output.samples*10000000/48000)

    def close(self):
        with self.video_lock, self.audio_lock:
            if self.handle:
                # NULL synchronizes outstanding SDK access before releasing pixels.
                self.send_video(self.handle, None)
                self.pending_video = None
                self.nv12_buffers = [None, None]
                self.destroy(self.handle)
                self.handle = None
                self.runtime.close()
            elif getattr(self, 'runtime', None):
                self.runtime.close()
            self.runtime = None
